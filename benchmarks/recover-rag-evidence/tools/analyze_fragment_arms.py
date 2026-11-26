#!/usr/bin/env python3
"""Grade RAG recovery arms: coverage, precision, tokens, oracle and resources."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path

LINE = re.compile(r"line: (\d+)")
ARMS = ("F0", "F1", "F2", "F3")


def rng_from_debug(value: object) -> tuple[int, int] | None:
    nums = LINE.findall(str(value))
    return (int(nums[0]), int(nums[1])) if len(nums) >= 2 else None


def rng_from_pack(value: object) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    t = value.get("text") if isinstance(value.get("text"), dict) else value if value.get("kind") == "text" else None
    if isinstance(t, dict):
        return int(t["start_line"]), int(t["end_line"])
    return None


def gold_spans(case: dict) -> list[tuple[str, int, int]]:
    return [(r["path"], s["start_line"], s["end_line"])
            for r in case["source_refs"] for s in r["spans"]]


def delivered_from_trace(root: Path, trace_path: Path) -> list[dict]:
    items = []
    for line in trace_path.read_text().splitlines():
        row = json.loads(line)
        if row.get("stage") == "context_selected":
            rng = rng_from_debug(row.get("range"))
            if rng and row.get("path"):
                items.append({"path": row["path"], "start": rng[0], "end": rng[1]})
    picked = []
    used = 0
    for item in items[:10]:
        p = root / item["path"]
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        body = "\n".join(lines[item["start"] - 1:item["end"]])
        size = len(body.encode())
        if used + size > 24 * 1024:
            continue
        picked.append(item)
        used += size
    return picked


def delivered_from_pack(pack_path: Path) -> list[dict]:
    if not pack_path.exists():
        return []
    pack = json.loads(pack_path.read_text())
    out = []
    for item in pack.get("items", []):
        rng = rng_from_pack(item.get("range"))
        if rng:
            out.append({"path": str(item["relative_path"]), "start": rng[0], "end": rng[1]})
    return out


def score_ranges(root: Path, delivered: list[dict], gold: list[tuple[str, int, int]]) -> dict:
    total_bytes = 0
    gold_bytes = 0
    hit_any = 0
    exact = 0
    for g in gold:
        covered_by = [d for d in delivered
                      if d["path"] == g[0] and d["start"] <= g[2] and d["end"] >= g[1]]
        if covered_by:
            hit_any += 1
        if any(d["start"] <= g[1] and d["end"] >= g[2] for d in covered_by):
            exact += 1
    for d in delivered:
        p = root / d["path"]
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        for n in range(d["start"], d["end"] + 1):
            if n < 1 or n > len(lines):
                continue
            size = len((lines[n - 1] + "\n").encode())
            total_bytes += size
            if any(g[0] == d["path"] and g[1] <= n <= g[2] for g in gold):
                gold_bytes += size
    return {
        "exact_coverage": round(exact / len(gold), 3) if gold else 0,
        "range_recall": round(hit_any / len(gold), 3) if gold else 0,
        "precision": round(gold_bytes / total_bytes, 4) if total_bytes else 0,
        "delivered_bytes": total_bytes,
        "gold_bytes": gold_bytes,
    }


def oracle_ceiling(root: Path, trace_path: Path, gold: list[tuple[str, int, int]]) -> float:
    loaded = []
    for line in trace_path.read_text().splitlines():
        row = json.loads(line)
        if row.get("stage") == "loaded":
            rng = rng_from_debug(row.get("range"))
            if rng and row.get("path"):
                loaded.append({"path": row["path"], "start": rng[0], "end": rng[1]})
    remaining = set(range(len(gold)))
    used = 0
    count = 0
    for d in loaded:
        if count >= 10:
            break
        covers = {i for i, g in enumerate(gold)
                  if d["path"] == g[0] and d["start"] <= g[2] and d["end"] >= g[1]} & remaining
        if not covers:
            continue
        p = root / d["path"]
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        size = len(("\n".join(lines[d["start"] - 1:d["end"]]) + "\n").encode())
        if used + size > 24 * 1024:
            continue
        used += size
        count += 1
        remaining -= covers
    return round((len(gold) - len(remaining)) / len(gold), 3) if gold else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--split", required=True, choices=("rust", "csharp"))
    ap.add_argument("--counter", required=True, type=Path)
    ap.add_argument("--tokenizer", required=True, type=Path)
    args = ap.parse_args()

    art = args.artifacts.resolve()
    root = args.corpus.resolve()
    cases_doc = json.loads(args.cases.read_text())
    dev12 = {f"D{i:02d}" for i in range(1, 13)}
    cases = {c["id"]: c for c in cases_doc["cases"]
             if args.split == "rust" or c["id"] in dev12}
    out = {"split": args.split, "arms": {}, "cases": {}}

    for cid, case in cases.items():
        gold = gold_spans(case)
        row = {}
        for arm in ARMS:
            trace = art / "traces2" / args.split / arm / f"{cid}.jsonl"
            pack = art / "packs" / args.split / arm / f"{cid}.json"
            delivered = delivered_from_pack(pack) if pack.exists() else delivered_from_trace(root, trace)
            m = score_ranges(root, delivered, gold)
            m["oracle"] = oracle_ceiling(root, trace, gold)
            text = ""
            for d in delivered:
                p = root / d["path"]
                if not p.exists():
                    continue
                lines = p.read_text(errors="replace").splitlines()
                text += f"[{d['path']}:{d['start']}-{d['end']}]\n" + "\n".join(lines[d["start"] - 1:d["end"]]) + "\n"
            pack_txt = art / "results" / f"pack-{args.split}-{arm}-{cid}.txt"
            pack_txt.parent.mkdir(parents=True, exist_ok=True)
            pack_txt.write_text(text)
            cp = subprocess.run([str(args.counter.resolve()), str(args.tokenizer.resolve()), str(pack_txt)],
                                capture_output=True, text=True)
            m["tokens"] = json.loads(cp.stdout)["tokens"] if cp.returncode == 0 else None
            row[arm] = m
        out["cases"][cid] = row

    for arm in ARMS:
        rows = [out["cases"][c][arm] for c in cases]
        out["arms"][arm] = {
            k: round(statistics.mean(v[k] for v in rows), 4)
            for k in ("exact_coverage", "range_recall", "precision", "delivered_bytes", "tokens", "oracle")
        }
    for arm in ARMS:
        ts = []
        for cid in cases:
            tm = art / "outputs2" / args.split / arm / f"{cid}.time"
            if not tm.exists():
                continue
            parts = tm.read_text().split()
            if len(parts) >= 4:
                ts.append((float(parts[0]), float(parts[1]), float(parts[2]), int(parts[3])))
        if ts:
            out["arms"][arm].update({
                "p95_latency_s": round(sorted(t[0] for t in ts)[int(0.95 * (len(ts) - 1))], 3),
                "cpu_user_sys_s": round(sum(t[1] + t[2] for t in ts), 2),
                "peak_rss_kb": max(t[3] for t in ts),
            })
    dest = art / "results" / f"rag-arms-{args.split}.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["arms"], indent=2))
    print("receipt", dest)


if __name__ == "__main__":
    main()
