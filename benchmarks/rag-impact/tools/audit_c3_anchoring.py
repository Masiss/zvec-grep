#!/usr/bin/env python3
"""audit_c3_anchoring.py — which frozen required steps are machine-measured?

Reads only frozen benchmark metadata: `cases/c3-steps.json`,
`scope-manifest.json` and `cases/dev.json`. For every case it re-runs the
grader's own step locator (`locate_step`) and reports, per step:

  * ADDRESSABLE   — at least one backticked identifier was found inside the
    case's frozen gold spans, so C1/C3/C6 can measure it;
  * NO_IDENTIFIERS — the step has no backticked identifier, so the current token
    locator cannot anchor it. Some of these rows may still be checkable by
    other deterministic rules (a negative claim by absence, a prose-described
    anchor), but the frozen policy does not implement those.
  * OUTSIDE_GOLD   — identifiers exist but none occurs inside this case's frozen
    gold spans (a negative claim, a conditional, or evidence outside gold).

The audit also verifies each case's corpus bytes against the per-anchor
`sha256` in the manifest; a drifted corpus is reported, not silently
reclassified.

The audit exists to make that exclusion visible: an answer can omit an
UNADDRESSABLE positive requirement and still reach a structurally perfect
score. The compensating control is the semantic evaluator's completeness
dimension, not a change to the frozen checklist or thresholds.

Usage: audit_c3_anchoring.py <labels-dir> <corpus-root> --out report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r4_grade as grade  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", help="comma-separated case ids (default: D01-D12)")
    args = parser.parse_args(argv)

    labels = args.labels
    materials = grade.load_materials(labels / "cases" / "c3-steps.json")
    manifest = grade.load_json(labels / "scope-manifest.json")
    anchor_paths = {anchor["id"]: anchor["path"]
                    for anchor in manifest.get("source_anchors", [])}
    dev = {case["id"]: case for case in
           grade.load_json(labels / "cases" / "dev.json").get("cases", [])}
    if args.cases:
        case_ids = [c.strip() for c in args.cases.split(",") if c.strip()]
    else:
        case_ids = [case for case in materials.get("c3_steps", {}) if case in dev]

    def backticked(text: str) -> list[str]:
        return [token.strip() for token in grade.step_tokens(text)]

    report = {}
    step_rows = []
    for case in case_ids:
        entries = manifest.get("case_spans", {}).get(case) or []
        if not entries or any(not isinstance(e, dict) or "anchor" not in e for e in entries):
            report[case] = {"authored": False, "reason": "no authored gold spans"}
            continue
        anchor_hashes = {anchor["id"]: anchor.get("sha256")
                         for anchor in manifest.get("source_anchors", [])}
        gold = [(anchor_paths[e["anchor"]], (int(start), int(end)))
                for e in entries for start, end in e["spans"]]
        corpus_mismatch = []
        for anchor_id in {e["anchor"] for e in entries}:
            target = args.corpus / anchor_paths[anchor_id]
            expected = anchor_hashes.get(anchor_id)
            actual = (grade.sha256_bytes(target.read_bytes())
                      if target.is_file() else None)
            if expected is not None and actual != expected:
                corpus_mismatch.append({"anchor": anchor_id,
                                        "expected": expected, "actual": actual})
        source_lines = {}
        for path, _span in gold:
            target = args.corpus / path
            if target.is_file() and path not in source_lines:
                source_lines[path] = target.read_text(
                    encoding="utf-8", errors="replace").split("\n")
        steps = []
        for index, text in enumerate(materials["c3_steps"].get(case, []), start=1):
            anchors = grade.locate_step(text, gold, source_lines)
            tokens = backticked(text)
            if anchors:
                state = "ADDRESSABLE"
            elif not tokens:
                state = "NO_IDENTIFIERS"      # cannot be anchored by construction
            else:
                state = "OUTSIDE_GOLD"        # identifiers exist, none in this case's gold
            row = {
                "case": case,
                "step": index,
                "text": text,
                "tokens": tokens,
                "anchors": len(anchors),
                "addressable": bool(anchors),
                "state": state,
            }
            steps.append(row)
            step_rows.append(row)
        report[case] = {
            "authored": True,
            "corpus_verified": not corpus_mismatch,
            "corpus_mismatch": corpus_mismatch,
            "steps": steps,
            "addressable": sum(1 for step in steps if step["addressable"]),
            "unaddressable": [step["step"] for step in steps if not step["addressable"]],
            "no_identifiers": [step["step"] for step in steps
                               if step["state"] == "NO_IDENTIFIERS"],
            "outside_gold": [step["step"] for step in steps
                             if step["state"] == "OUTSIDE_GOLD"],
        }

    measured = [row for row in step_rows]
    summary = {
        "cases": len([case for case in report if report[case].get("authored")]),
        "steps": len(measured),
        "corpus_mismatch_cases": sorted(
            case for case, entry in report.items() if entry.get("corpus_mismatch")),
        "addressable": sum(1 for row in measured if row["addressable"]),
        "unaddressable": sum(1 for row in measured if not row["addressable"]),
        "no_identifiers": sum(1 for row in measured
                              if row["state"] == "NO_IDENTIFIERS"),
        "outside_gold": sum(1 for row in measured
                            if row["state"] == "OUTSIDE_GOLD"),
        "by_case": {case: {"steps": entry.get("steps") and len(entry["steps"]),
                           "addressable": entry.get("addressable"),
                           "unaddressable": entry.get("unaddressable", []),
                           "no_identifiers": entry.get("no_identifiers", []),
                           "outside_gold": entry.get("outside_gold", [])}
                    for case, entry in report.items()},
        "policy": ("Steps the current token locator cannot anchor are excluded from "
                   "C1/C3/C6 by the frozen measurement policy; the semantic "
                   "completeness dimension is the control for missing requirements "
                   "that have no anchor. 'Unaddressable' here means 'not addressable "
                   "by this locator', not 'unmeasurable in principle'."),
    }
    document = {"schema_version": 1, "kind": "r4-c3-anchoring-audit",
                "summary": summary, "report": report}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
