#!/usr/bin/env python3
"""Grade instrumented stage traces and run ranking/budget ablations."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path

LINE = re.compile(r"line: (\d+)")


def parse_range(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    nums = LINE.findall(value)
    if len(nums) < 2:
        return None
    return int(nums[0]), int(nums[1])


def load_trace(path: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[row.get("stage", "unknown")].append(row)
    return out


def gold_spans(case: dict) -> list[tuple[str, int, int]]:
    out = []
    for ref in case["source_refs"]:
        for span in ref["spans"]:
            out.append((ref["path"], span["start_line"], span["end_line"]))
    return out


def entry_range(row: dict) -> tuple[int, int] | None:
    return parse_range(row.get("range"))


def file_hit(path: str, gold: list[tuple[str, int, int]]) -> bool:
    return any(g[0] == path for g in gold)


def overlap_hit(rng: tuple[int, int] | None, path: str, gold: list[tuple[str, int, int]]) -> bool:
    if rng is None:
        return False
    return any(g[0] == path and rng[0] <= g[2] and rng[1] >= g[1] for g in gold)


def exact_hit(rng: tuple[int, int] | None, path: str, gold: list[tuple[str, int, int]]) -> bool:
    if rng is None:
        return False
    return any(g[0] == path and rng[0] <= g[1] and rng[1] >= g[2] for g in gold)


def metrics(rows: list[dict], gold: list[tuple[str, int, int]], id_map: dict[str, dict]) -> dict:
    resolved: list[dict] = []
    for row in rows:
        if "path" in row:
            resolved.append(row)
        elif row.get("entity_id") in id_map:
            mapped = id_map[row["entity_id"]]
            resolved.append({"path": mapped["path"], "range": mapped["range"]})
    seen = set()
    unique = []
    for r in resolved:
        key = (r.get("path"), str(r.get("range")))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    gold_entities = []
    for g in gold:
        ids = {eid for eid, erow in id_map.items() if erow.get("path") == g[0] and overlap_hit(entry_range(erow), erow.get("path"), [g])}
        gold_entities.append(ids)
    seen_entities = set()
    for row in rows:
        if row.get("entity_id"):
            seen_entities.add(row["entity_id"])
        elif row.get("path"):
            seen_entities.update(eid for eid, erow in id_map.items() if erow.get("path") == row.get("path") and erow.get("range") == row.get("range"))
    entity_recall = round(sum(bool(ids & seen_entities) for ids in gold_entities) / len(gold_entities), 3) if gold_entities else 0
    return {
        "rows": len(rows),
        "unique_rows": len(unique),
        "entity_recall": entity_recall,
        "file_recall": round(sum(any(u.get("path") == g[0] for u in unique) for g in gold) / len(gold), 3) if gold else 0,
        "range_recall": round(sum(any(overlap_hit(entry_range(u), u.get("path"), [g]) for u in unique) for g in gold) / len(gold), 3) if gold else 0,
        "exact_coverage": round(sum(any(exact_hit(entry_range(u), u.get("path"), [g]) for u in unique) for g in gold) / len(gold), 3) if gold else 0,
        "unmapped": sum(1 for row in rows if "path" not in row and row.get("entity_id") not in id_map),
    }


def noise_bytes(root: Path, rows: list[dict], gold: list[tuple[str, int, int]]) -> tuple[int, int]:
    delivered = 0
    noise = 0
    for row in rows:
        path = row.get("path")
        rng = parse_range(row.get("range"))
        if not path or not rng:
            continue
        p = root / str(path)
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        for n in range(max(1, rng[0]), min(len(lines), rng[1]) + 1):
            size = len((lines[n - 1] + "\n").encode())
            delivered += size
            if not any(g[0] == path and g[1] <= n <= g[2] for g in gold):
                noise += size
    return delivered, noise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--split", required=True, choices=("rust", "csharp"))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--counter", type=Path)
    args = ap.parse_args()

    art = args.artifacts.resolve()
    corpus = args.corpus.resolve()
    cases_doc = json.loads(args.cases.read_text())
    dev12 = {f"D{i:02d}" for i in range(1, 13)}
    cases = {c["id"]: c for c in cases_doc["cases"] if (args.split == "rust" or c["id"] in dev12)}
    stages = ("raw", "fused", "loaded", "ranked", "context_group_items", "context_selected")
    out = {"split": args.split, "limit": args.limit, "cases": {}, "summary": {}}

    for cid, case in cases.items():
        trace = load_trace(art / "traces" / args.split / f"{cid}.{args.limit}.jsonl")
        gold = gold_spans(case)
        id_map = {}
        for stage in ("loaded", "ranked", "context_group_items", "context_selected"):
            for row in trace.get(stage, []):
                if row.get("entity_id") and "path" in row:
                    id_map[row["entity_id"]] = row
        row_out = {}
        for stage in stages:
            row_out[stage] = metrics(trace.get(stage, []), gold, id_map)
        # Ranking ablations on the same loaded/fused pool.
        loaded = trace.get("loaded", [])
        fused = trace.get("fused", [])
        ranked = trace.get("ranked", [])
        by_entity = {r["entity_id"]: r for r in loaded}

        def rows_for(scores: dict[str, float], n: int = 10) -> list[dict]:
            ids = sorted(scores, key=lambda k: -scores[k])[:n]
            return [by_entity[i] for i in ids if i in by_entity]

        cosine = {}
        for r in loaded:
            recalls = (r.get("trace") or {}).get("recall") or []
            scores = [x.get("score") or 0 for x in recalls if x.get("path") == "vector"]
            cosine[r["entity_id"]] = max(scores) if scores else 0.0
        rrf = {r["entity_id"]: r.get("score", 0) for r in fused}
        nodiv = {}
        for r in ranked:
            ranking = (r.get("trace") or {}).get("ranking") or {}
            nodiv[r["entity_id"]] = ranking.get("static_score", r.get("score", 0))
        for name, rows in (
            ("current_rule", ranked[:10]),
            ("pure_cosine", rows_for(cosine)),
            ("pure_rrf", rows_for(rrf)),
            ("rule_no_diversity", rows_for(nodiv)),
        ):
            row_out[f"rank_{name}"] = metrics(rows, gold, id_map)

        # Budget sweep on finalized context items.
        selected = trace.get("context_selected", [])
        sweep = {}
        for items in (5, 10, 20):
            for kib in (8, 16, 24, 32):
                picked = []
                used = 0
                for r in selected:
                    if len(picked) >= items:
                        break
                    rng = parse_range(r.get("range"))
                    if not rng or not r.get("path"):
                        continue
                    piece = f"[{r['path']}:{rng[0]}-{rng[1]}]\n"
                    p = corpus / str(r["path"])
                    if p.exists():
                        lines = p.read_text(errors="replace").splitlines()
                        piece += "\n".join(lines[max(0, rng[0] - 1):min(len(lines), rng[1])]) + "\n"
                    size = len(piece.encode())
                    if used + size > kib * 1024:
                        continue
                    picked.append(r)
                    used += size
                deliv, noise = noise_bytes(corpus, picked, gold)
                tokens = None
                if args.tokenizer and args.counter and picked:
                    pack = art / "results" / f"pack-{cid}-{args.split}-{items}-{kib}.txt"
                    pack.parent.mkdir(parents=True, exist_ok=True)
                    text = ""
                    for r in picked:
                        rng = parse_range(r["range"])
                        p = corpus / str(r["path"])
                        lines = p.read_text(errors="replace").splitlines() if p.exists() else []
                        text += f"[{r['path']}:{rng[0]}-{rng[1]}]\n" + "\n".join(lines[rng[0]-1:rng[1]]) + "\n"
                    pack.write_text(text)
                    cp = subprocess.run([str(args.counter.resolve()), str(args.tokenizer.resolve()), str(pack)], capture_output=True, text=True)
                    if cp.returncode == 0:
                        tokens = json.loads(cp.stdout)["tokens"]
                sweep[f"items{items}_kib{kib}"] = {
                    **metrics(picked, gold, id_map),
                    "delivered_bytes": deliv,
                    "noise_bytes": noise,
                    "noise_ratio": round(noise / deliv, 3) if deliv else 0,
                    "tokens": tokens,
                }
        row_out["budget"] = sweep
        out["cases"][cid] = row_out

    for key in list(stages) + [f"rank_{x}" for x in ("current_rule", "pure_cosine", "pure_rrf", "rule_no_diversity")]:
        out["summary"][key] = {
            m: round(statistics.mean([out["cases"][c][key][m] for c in cases]), 4)
            for m in ("file_recall", "entity_recall", "range_recall", "exact_coverage")
        }
    out["summary"]["final_noise_ratio"] = round(statistics.mean([
        out["cases"][c]["budget"]["items10_kib24"]["noise_ratio"] for c in cases
    ]), 4)
    dest = art / "results" / f"stage-analysis-{args.split}-L{args.limit}.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["summary"], indent=2))
    print("receipt", dest)


if __name__ == "__main__":
    main()
