#!/usr/bin/env python3
"""Read-only retrieval-vs-selection audit from frozen RAG receipts.

Computes per-case:
- B0 oracle best-call recall and the deployable fixed fts_code_tokens policy;
- retrievable-gold ceiling over the union of all six search calls;
- selectable Recall@10 ceiling over that union with an optimal 10-item pack;
- discovery/ranking/range misses for the fixed and candidate packs.

Writes results/retrieval-selection-ceiling.json. It does not modify frozen
receipts, corpus, gold, targets or held-out data.
"""

from __future__ import annotations

import json
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
B0 = RAG / "results" / "b0-official.json"
DEV = RAG / "cases" / "dev.json"
CAND = RAG / "results" / "candidate-pilot.json"
R4A = RAG / "results" / "candidate-r4a.json"
R4B = RAG / "results" / "candidate-r4b.json"
OUT = RAG / "results" / "retrieval-selection-ceiling.json"


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def gold_spans(case: dict) -> list[tuple[str, int, int]]:
    spans = []
    for reference in case.get("source_refs", []):
        for span in reference.get("spans", []):
            spans.append((reference["path"], span["start_line"], span["end_line"]))
    return spans


def covers(unit: dict, gold: tuple[str, int, int]) -> bool:
    path, start, end = gold
    return unit["path"] == path and overlap(unit["start_line"], unit["end_line"], start, end)


def item_covers(item: dict, gold: tuple[str, int, int]) -> bool:
    return covers(item, gold)


def same_file(items: list[dict], gold: tuple[str, int, int]) -> bool:
    return any(item["path"] == gold[0] for item in items)


def unit_mask(unit: dict, gold: list[tuple[str, int, int]]) -> int:
    mask = 0
    for index, span in enumerate(gold):
        if covers(unit, span):
            mask |= 1 << index
    return mask


def selectable_ceiling(units: list[dict], gold: list[tuple[str, int, int]], budget: int = 10) -> int:
    reachable: dict[int, int] = {0: 0}
    for unit in units:
        mask = unit_mask(unit, gold)
        if mask == 0:
            continue
        updated = dict(reachable)
        for covered, count in reachable.items():
            if count >= budget:
                continue
            combined = covered | mask
            next_count = count + 1
            if combined not in updated or updated[combined] > next_count:
                updated[combined] = next_count
        reachable = updated
    return max((covered.bit_count() for covered, count in reachable.items() if count <= budget), default=0)


def classify(pack: list[dict], union: list[dict], gold: list[tuple[str, int, int]]) -> dict:
    counts = {"packed": 0, "discovery": 0, "ranking": 0, "range": 0}
    for span in gold:
        if any(item_covers(item, span) for item in pack):
            counts["packed"] += 1
        elif not any(covers(unit, span) for unit in union):
            counts["discovery"] += 1
        elif same_file(pack, span):
            counts["range"] += 1
        else:
            counts["ranking"] += 1
    return counts


def main() -> int:
    b0 = json.loads(B0.read_text())
    dev = json.loads(DEV.read_text())
    cand = json.loads(CAND.read_text())
    r4a = json.loads(R4A.read_text())
    r4b = json.loads(R4B.read_text())
    dev_by = {case["id"]: case for case in dev["cases"]}
    cand_by = {case["id"]: case for case in cand["cases"]}
    r4a_by = {case["id"]: case for case in r4a["cases"]}
    r4b_by = {case["id"]: case for case in r4b["cases"]}

    rows = []
    for b0_case in b0["quality"]["dev_real_cases"]:
        case_id = b0_case["id"]
        gold = gold_spans(dev_by[case_id])
        union: list[dict] = []
        seen = set()
        for call in b0_case["calls"]:
            for unit in call.get("units") or []:
                key = (unit["path"], unit["start_line"], unit["end_line"])
                if key not in seen:
                    seen.add(key)
                    union.append(unit)
        union_coverage = 0
        for unit in union:
            union_coverage |= unit_mask(unit, gold)
        fixed = [
            unit
            for call in b0_case["calls"]
            if call["call"] == "fts_code_tokens"
            for unit in (call.get("units") or [])
        ][:10]
        packs = {
            "fixed_fts_code": fixed,
            "candidate": cand_by[case_id]["items"],
            "r4a": r4a_by[case_id]["r4a_items"],
            "r4b": r4b_by[case_id]["r4b_items"],
        }
        rows.append(
            {
                "id": case_id,
                "gold_spans": len(gold),
                "union_candidates": len(union),
                "retrievable_gold": union_coverage.bit_count(),
                "selectable_recall10_gold": selectable_ceiling(union, gold),
                "b0_oracle_best_call": b0_case["best_call"],
                "b0_oracle_gold": sum(
                    1
                    for span in gold
                    if any(
                        covers(unit, span)
                        for call in b0_case["calls"]
                        if call["call"] == b0_case["best_call"]
                        for unit in (call.get("units") or [])
                    )
                ),
                "policies": {
                    name: {
                        "packed_gold": classify(pack, union, gold)["packed"],
                        "misses": classify(pack, union, gold),
                        "files": len({item["path"] for item in pack}),
                    }
                    for name, pack in packs.items()
                },
            }
        )

    totals = {
        "gold_spans": sum(row["gold_spans"] for row in rows),
        "retrievable_gold": sum(row["retrievable_gold"] for row in rows),
        "selectable_recall10_gold": sum(row["selectable_recall10_gold"] for row in rows),
        "b0_oracle_gold": sum(row["b0_oracle_gold"] for row in rows),
    }
    macro = {
        "retrievable_recall": sum(row["retrievable_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "selectable_recall10": sum(row["selectable_recall10_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "b0_oracle": sum(row["b0_oracle_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "fixed_fts_code": sum(row["policies"]["fixed_fts_code"]["packed_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "candidate": sum(row["policies"]["candidate"]["packed_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "r4a": sum(row["policies"]["r4a"]["packed_gold"] / row["gold_spans"] for row in rows) / len(rows),
        "r4b": sum(row["policies"]["r4b"]["packed_gold"] / row["gold_spans"] for row in rows) / len(rows),
    }
    receipt = {
        "schema_version": 1,
        "status": "READ_ONLY_RETRIEVAL_SELECTION_AUDIT",
        "inputs": {
            "b0": "results/b0-official.json",
            "dev": "cases/dev.json",
            "candidate": "results/candidate-pilot.json",
            "r4a": "results/candidate-r4a.json",
            "r4b": "results/candidate-r4b.json",
        },
        "totals": totals,
        "macro": macro,
        "rows": rows,
        "notes": [
            "B0 best_call is per-case max recall; it is an oracle selector, not a deployable policy.",
            "fixed_fts_code_tokens is the best fixed call by mean recall; it is deployable.",
            "Retrievable ceiling uses the union of all six search calls; selectable ceiling uses an optimal 10-item subset.",
            "Candidate/R4 files are distinct files in the pack, not post-pack agent reads.",
        ],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"totals": totals, "macro": macro}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
