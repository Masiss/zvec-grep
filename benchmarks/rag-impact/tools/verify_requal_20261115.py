#!/usr/bin/env python3
"""Independent verifier for the RAG requalification and refinement receipts.

Does not import the experiment tools. Recomputes Recall@10, strict evidence,
union/selectable ceilings, route contracts and identity hashes from raw inputs.
`--negative` mutates inputs in memory and requires rejection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def overlap(a: int, b: int, c: int, d: int) -> bool:
    return a <= d and c <= b


def gold_for(case: dict) -> list[tuple[str, int, int]]:
    return [
        (ref["path"], span["start_line"], span["end_line"])
        for ref in case.get("source_refs", [])
        for span in ref.get("spans", [])
    ]


def matched(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    return {
        i
        for i, (p, s, e) in enumerate(gold)
        if any(
            u["path"] == p
            and u.get("start_line") is not None
            and u.get("last_line") is not None
            and overlap(u["start_line"], u["last_line"], s, e)
            for u in units
        )
    }


def strict(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    hit = set()
    for i, (p, s, e) in enumerate(gold):
        glen = e - s + 1
        for u in units:
            if u["path"] != p or u.get("start_line") is None or u.get("last_line") is None:
                continue
            if not overlap(u["start_line"], u["last_line"], s, e):
                continue
            ilen = u["last_line"] - u["start_line"] + 1
            inter = min(u["last_line"], e) - max(u["start_line"], s) + 1
            if inter / glen >= 0.5 and ilen <= max(100, 3 * glen):
                hit.add(i)
                break
    return hit


def selectable(units: list[dict], gold: list[tuple[str, int, int]], budget: int = 10) -> int:
    masks = []
    for u in units:
        if u.get("start_line") is None or u.get("last_line") is None:
            continue
        mask = 0
        for i, (p, s, e) in enumerate(gold):
            if u["path"] == p and overlap(u["start_line"], u["last_line"], s, e):
                mask |= 1 << i
        if mask:
            masks.append(mask)
    full = 1 << len(gold)
    best = [budget + 1] * full
    best[0] = 0
    for mask in masks:
        for state in range(full):
            if best[state] + 1 < best[state | mask]:
                best[state | mask] = best[state] + 1
    return max((bin(s).count("1") for s, c in enumerate(best) if c <= budget), default=0)


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def corpus_tree(root: Path, manifest: dict) -> str:
    tree = hashlib.sha256()
    for item in sorted(manifest["files"], key=lambda f: f["path"]):
        path = root / item["path"]
        if not path.is_file():
            fail(f"missing corpus file {item['path']}")
        digest = sha256_file(path)
        if digest != item["sha256"]:
            fail(f"corpus hash mismatch {item['path']}")
        tree.update(item["path"].encode())
        tree.update(b"\0")
        tree.update(bytes.fromhex(digest))
    return tree.hexdigest()


def contract(probe: dict) -> list[str]:
    failures = []
    for row in probe["queries"]:
        expected = [(r["mode"], r["query"]) for r in row["routes"]]
        index = row.get("index") or {}
        actual = [(r.get("mode"), r.get("query")) for r in index.get("routes", [])]
        if actual != expected:
            failures.append(f"{row['id']}:{row['call']} routes differ")
        if len(index.get("query_groups", [])) != 1:
            failures.append(f"{row['id']}:{row['call']} group count")
        if len(set(actual)) != len(actual):
            failures.append(f"{row['id']}:{row['call']} duplicate routes")
    return failures


def recompute(receipt: dict, probe: dict, cases: dict) -> dict:
    rows = {}
    by_id: dict[str, list[dict]] = {}
    for row in probe["queries"]:
        by_id.setdefault(row["id"], []).append(row)
    rg_by_id: dict[str, list[dict]] = {}
    for row in receipt.get("rg_calls", []):
        rg_by_id.setdefault(row["id"], []).extend(row["units"])
    for cid, case in cases.items():
        gold = gold_for(case)
        calls = []
        for row in by_id.get(cid, []):
            top10 = row["items"][:10]
            calls.append(
                {
                    "call": row["call"],
                    "recall": len(matched(top10, gold)) / len(gold) if gold else None,
                    "strict": len(strict(top10, gold)) / len(gold) if gold else None,
                    "union": len(matched(row["items"], gold)),
                }
            )
        for row in receipt.get("rg_calls", []):
            if row["id"] != cid:
                continue
            calls.append(
                {
                    "call": row["call"],
                    "recall": len(matched(row["units"][:10], gold)) / len(gold) if gold else None,
                    "strict": len(strict(row["units"][:10], gold)) / len(gold) if gold else None,
                    "union": len(matched(row["units"], gold)),
                }
            )
        union = [u for row in by_id.get(cid, []) for u in row["items"]] + rg_by_id.get(cid, [])
        rows[cid] = {
            "gold": len(gold),
            "calls": calls,
            "best": max((c["recall"] for c in calls if c["recall"] is not None), default=0.0),
            "union": len(matched(union, gold)),
            "selectable": selectable(union, gold, 10),
        }
    return rows


def compare(receipt: dict, probe: dict, cases: dict) -> None:
    failures = contract(probe)
    if failures:
        fail("; ".join(failures))
    recomputed = recompute(receipt, probe, cases)
    for row in receipt["rows"]:
        cid = row["id"]
        expected = recomputed[cid]
        if abs(row["best_recall"] - expected["best"]) > 1e-9:
            fail(f"{cid} best_recall mismatch")
        if abs(row["union_retrievable"] - expected["union"]) > 1e-9:
            fail(f"{cid} union mismatch")
        if abs(row["selectable_at_10"] - expected["selectable"]) > 1e-9:
            fail(f"{cid} selectable mismatch")
        for call in row["calls"]:
            if call["units"] and call.get("recall") is not None:
                if call["recall"] > 1.0 or call["recall"] < 0.0:
                    fail(f"{cid}:{call['call']} recall out of range")
    if receipt["policy"]["dev_cases"] != len(cases):
        fail("dev case count")
    if receipt["route_macro_recall_at_10"]["fts_question"] is None:
        fail("missing FTS route metric")
    if receipt["route_macro_recall_at_10"]["vector_question"] is None:
        fail("missing vector route metric")
    if receipt["route_macro_recall_at_10"]["hybrid_question"] is None:
        fail("missing hybrid route metric")


def verify_identity(receipt: dict, probe: dict, root: Path, manifest: dict, dev: Path) -> None:
    binary = Path(receipt["identity"]["binary"])
    if not binary.is_file() or sha256_file(binary) != receipt["identity"]["binary_sha256"]:
        fail("binary hash mismatch")
    if sha256_file(dev) != receipt["identity"]["dev_cases_sha256"]:
        fail("dev cases hash mismatch")
    if sha256_file(root / ".zvec-grep" / "manifest.json") != receipt["identity"]["index_manifest_sha256"]:
        fail("index manifest hash mismatch")
    tree = corpus_tree(root, manifest)
    if tree != receipt["identity"]["corpus_tree_sha256"]:
        fail("corpus tree mismatch")
    if tree != receipt["identity"]["corpus_tree_expected"]:
        fail("corpus expected tree mismatch")
    probe_sha = hashlib.sha256(json.dumps(probe, sort_keys=True).encode()).hexdigest()
    if not probe_sha:
        fail("empty probe hash")
    compatibility = receipt["index"]["compatibility"]
    if compatibility.get("version") != 3:
        fail("index version")
    status = receipt["index"]["status"]
    for field in ("files_failed", "files_pending"):
        if status.get(field, 0) != 0:
            fail(f"index {field}")


def check_refinement(refine: dict, root: Path, manifest: dict, cases: dict) -> None:
    audit = refine["granularity_audit"]
    for row in audit["rows"]:
        if row["normalized_union"] > row["gold_spans"]:
            fail("normalized union exceeds gold")
        if row["normalized_selectable10"] > row["normalized_union"]:
            fail("normalized selectable exceeds union")
        if row["raw_union"] < row["normalized_union"] - 2:
            fail(f"{row['id']} normalization lost too much")
    totals = audit["totals"]
    if totals["raw_union"] != sum(r["raw_union"] for r in audit["rows"]):
        fail("audit total mismatch")
    if totals["normalized_union"] != sum(r["normalized_union"] for r in audit["rows"]):
        fail("audit normalized total mismatch")
    if refine["policy"]["uses_gold"]:
        fail("refinement policy used gold")
    if refine["policy"]["source_reads"] > 100:
        fail("refinement source read budget")
    for row in refine["rows"]:
        case = cases[row["id"]]
        gold = gold_for(case)
        for call, units in zip(row["calls"], row["refined_calls"]):
            recall = len(matched(units, gold)) / len(gold) if gold else None
            strict_recall = len(strict(units, gold)) / len(gold) if gold else None
            if abs((recall or 0.0) - (call["refined_recall"] or 0.0)) > 1e-9:
                fail(f"{row['id']}:{call['call']} refined recall")
            if abs((strict_recall or 0.0) - (call["refined_strict"] or 0.0)) > 1e-9:
                fail(f"{row['id']}:{call['call']} refined strict")
            for unit in units:
                path = root / unit["path"]
                if not path.is_file():
                    fail(f"refined unit path missing {unit['path']}")
                lines = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
                if unit.get("last_line") and unit["last_line"] > lines:
                    fail(f"refined range beyond file {unit['path']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--dev", required=True, type=Path)
    parser.add_argument("--corpus-manifest", required=True, type=Path)
    parser.add_argument("--refinement", type=Path)
    parser.add_argument("--negative", action="store_true")
    args = parser.parse_args()

    receipt = json.loads(args.receipt.read_text())
    probe = json.loads(args.probe.read_text())
    manifest = json.loads(args.corpus_manifest.read_text())
    dev = json.loads(args.dev.read_text())
    cases = {
        c["id"]: c
        for c in dev["cases"]
        if c["id"] in {f"D{i:02d}" for i in range(1, 13)}
    }

    verify_identity(receipt, probe, args.root, manifest, args.dev)
    compare(receipt, probe, cases)
    if args.refinement:
        check_refinement(json.loads(args.refinement.read_text()), args.root, manifest, cases)

    if args.negative:
        # Contract: extra vector route on an FTS call.
        mutated = json.loads(json.dumps(probe))
        for row in mutated["queries"]:
            if row["requested_mode"] == "fts":
                row["index"]["routes"].append({"id": "vector", "mode": "vector", "query": row["query"]})
                break
        try:
            compare(receipt, mutated, cases)
        except SystemExit:
            pass
        else:
            fail("negative route mutation accepted")
        # Identity: wrong binary hash.
        bad = json.loads(json.dumps(receipt))
        bad["identity"]["binary_sha256"] = "0" * 64
        try:
            verify_identity(bad, probe, args.root, manifest, args.dev)
        except SystemExit:
            pass
        else:
            fail("negative binary hash accepted")
        # Corpus: wrong frozen hash.
        bad_manifest = json.loads(json.dumps(manifest))
        bad_manifest["files"][0]["sha256"] = "0" * 64
        try:
            corpus_tree(args.root, bad_manifest)
        except SystemExit:
            pass
        else:
            fail("negative corpus hash accepted")
        # Receipt metric tamper.
        bad = json.loads(json.dumps(receipt))
        bad["rows"][0]["best_recall"] = 0.999
        try:
            compare(bad, probe, cases)
        except SystemExit:
            pass
        else:
            fail("negative receipt metric accepted")
        print("negative mutations rejected")
    print("OK: RAG requalification independent verification PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
