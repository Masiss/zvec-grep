#!/usr/bin/env python3
"""Evaluation contract v2 for typed grounding.

Differences from v1:
- non-gold / out-of-snapshot selections are UNKNOWN, never automatically
  MISLEADING;
- WRONG_ENTITY_KIND is a distinct label and metric;
- required groups use an explicit coverage threshold; strict completeness uses a
  separate threshold;
- negative claims require an explicit closed-world basis; absence of a match in
  an open snapshot is not accepted as refutation;
- denominators and role accuracy are reported separately.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROLES = {"REQUIRED", "SUPPORTING", "OPTIONAL", "IRRELEVANT", "WRONG_ENTITY_KIND", "MISLEADING", "UNKNOWN"}
ROLE_KIND = {
    "PROPERTY": "PROPERTY", "TYPE": "TYPE", "VIEW": "VIEW",
    "SQL": "SQL_BODY", "CONTROL": "METHOD", "WRAPPER": "METHOD", "DATA": "DATA",
}
LABEL_KIND = {
    "PROPERTY": "PROPERTY", "TYPE": "TYPE", "VIEW": "VIEW",
    "SQL": "SQL_BODY", "CONTROL": "METHOD", "WRAPPER": "METHOD", "DATA": "DATA",
}


def overlap(a, b):
    if a["path"] != b["path"]:
        return 0
    return max(0, min(a["end_line"], b["end_line"]) - max(a["start_line"], b["start_line"]) + 1)


def covers(a, b, threshold=0.5):
    return overlap(a, b) / max(1, b["end_line"] - b["start_line"] + 1) >= threshold


def role_of(unit, labels, threshold=0.5):
    hits = [x for x in labels if overlap(unit, x) > 0]
    if not hits:
        return "UNKNOWN", None
    order = {"REQUIRED": 7, "WRONG_ENTITY_KIND": 6, "MISLEADING": 5,
             "SUPPORTING": 4, "OPTIONAL": 3, "IRRELEVANT": 2, "UNKNOWN": 1}
    lab = max(hits, key=lambda x: (order.get(x.get("role"), 0), overlap(unit, x)))
    return lab.get("role", "UNKNOWN"), lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--coverage-threshold", type=float, default=0.5)
    ap.add_argument("--strict-threshold", type=float, default=0.9)
    a = ap.parse_args()

    cases = {c["id"]: c for c in json.loads(a.cases.read_text())["cases"]}
    rec = json.loads(a.receipt.read_text())
    agg = {k: 0 for k in [
        "groups", "groups_hit", "strict_groups_hit", "required_units", "required_units_hit",
        "required_units_strict", "selected", "known", "role_correct", "unknown",
        "misleading", "wrong_kind", "supporting", "optional", "irrelevant",
        "correct_abstain", "false_abstain", "false_answer", "claims", "claim_supported",
        "claim_unsafe", "unsupported_acceptance", "negative_claims", "negative_safe",
        "strict_complete_cases", "tokens",
    ]}
    rows = []
    for r in rec["results"]:
        c = cases[r["id"]]
        labels = c.get("units", [])
        units = r.get("units", [])
        role_pairs = [role_of(u, labels, a.coverage_threshold) for u in units]
        roles = [z[0] for z in role_pairs]
        hit_groups = []
        strict_groups = []
        for g in c.get("required_groups", []):
            if any(covers(u, x, a.coverage_threshold) for u in units for x in g["any_of"]):
                hit_groups.append(g["id"])
            if any(covers(u, x, a.strict_threshold) for u in units for x in g["any_of"]):
                strict_groups.append(g["id"])
        required = [u for u in labels if u.get("role") == "REQUIRED"]
        required_hit = [u for u in required if any(covers(s, u, a.coverage_threshold) for s in units)]
        required_strict = [u for u in required if any(covers(s, u, a.strict_threshold) for s in units)]

        roles_known = [z for z in roles if z != "UNKNOWN"]
        role_correct = sum(z in {"REQUIRED", "SUPPORTING", "OPTIONAL", "IRRELEVANT"} for z in roles_known)
        misleading = sum(z == "MISLEADING" for z in roles)
        wrong_kind = sum(z == "WRONG_ENTITY_KIND" for z in roles)
        # A selected unit with an explicit label kind mismatching the runtime kind
        # is also a wrong-entity-kind admission.
        for u, (z, lab) in zip(units, role_pairs):
            if lab and lab.get("entity_kind") and u.get("entity_kind") and lab["entity_kind"] != u.get("entity_kind"):
                wrong_kind += 1

        claim_rows = []
        groups = {g["id"]: g for g in c.get("required_groups", [])}
        for cl in c.get("claims", []):
            ids = [gid for gid in cl.get("evidence_groups", []) if gid in groups]
            all_hit = all(any(covers(u, x, a.coverage_threshold) for u in units for x in groups[gid]["any_of"]) for gid in ids)
            if cl.get("status") == "SUPPORTED":
                evidence = "SUPPORTED" if all_hit and not misleading and not wrong_kind else "INSUFFICIENT"
            elif cl.get("status") == "REFUTED":
                basis = cl.get("negative_basis") or r.get("negative_basis") or "open_world"
                if misleading or wrong_kind:
                    evidence = "UNSAFE_REFUTATION"
                elif not all_hit:
                    evidence = "INSUFFICIENT"
                elif basis not in {"closed_world", "method_body_complete"}:
                    evidence = "NEGATIVE_BASIS_UNPROVEN"
                else:
                    evidence = "REFUTED_SUPPORTED"
            elif cl.get("status") == "INSUFFICIENT":
                evidence = "UNSAFE_ANSWER" if (units and (misleading or wrong_kind)) else "CORRECT_INSUFFICIENT"
            else:
                evidence = "CORRECT_UNKNOWN" if r.get("abstain") else "UNSAFE_ANSWER"
            claim_rows.append({
                "id": cl["id"], "expected": cl["status"], "evidence": evidence,
                "required_groups": ids, "all_groups_hit": all_hit,
                "negative_basis": cl.get("negative_basis"),
            })

        failures = []
        if c.get("expected_abstain") and not r.get("abstain"):
            failures.append("INCORRECT_ABSTENTION")
        if not c.get("expected_abstain") and r.get("abstain"):
            failures.append("FALSE_ABSTENTION")
        if misleading:
            failures.append("MISLEADING_ADMISSION")
        if wrong_kind:
            failures.append("WRONG_ENTITY_KIND")
        if len(hit_groups) < len(c.get("required_groups", [])):
            failures.append("MISSING_REQUIRED_GROUP")
        if any(x["expected"] == "SUPPORTED" and x["evidence"] == "INSUFFICIENT" and not r.get("abstain") for x in claim_rows):
            failures.append("UNSUPPORTED_EVIDENCE")
        if any(x["expected"] == "REFUTED" and x["evidence"] in {"UNSAFE_REFUTATION", "NEGATIVE_BASIS_UNPROVEN"} for x in claim_rows):
            failures.append("NEGATIVE_CLAIM_UNSAFE")

        strict_complete = bool(c.get("required_groups")) and len(strict_groups) == len(c["required_groups"])
        if not c.get("required_groups"):
            strict_complete = bool(r.get("abstain")) if c.get("expected_abstain") else True
        row = {
            "id": r["id"], "abstain": bool(r.get("abstain")),
            "required_groups": len(c.get("required_groups", [])),
            "groups_hit": len(hit_groups), "strict_groups_hit": len(strict_groups),
            "required_units": len(required), "required_units_hit": len(required_hit),
            "required_units_strict": len(required_strict),
            "selected": len(units), "known": len(roles_known), "role_correct": role_correct,
            "unknown": roles.count("UNKNOWN"), "misleading": misleading, "wrong_kind": wrong_kind,
            "supporting": roles.count("SUPPORTING"), "optional": roles.count("OPTIONAL"),
            "irrelevant": roles.count("IRRELEVANT"),
            "correct_abstain": bool(r.get("abstain")) == bool(c.get("expected_abstain")),
            "false_abstain": (not c.get("expected_abstain")) and bool(r.get("abstain")),
            "false_answer": c.get("expected_abstain") and not r.get("abstain"),
            "claims": claim_rows,
            "claim_supported": sum(x["evidence"] in {"SUPPORTED", "REFUTED_SUPPORTED", "CORRECT_INSUFFICIENT", "CORRECT_UNKNOWN"} for x in claim_rows),
            "claim_unsafe": sum(x["evidence"].startswith("UNSAFE") for x in claim_rows),
            "unsupported_acceptance": sum(x["evidence"] in {"INSUFFICIENT", "NEGATIVE_BASIS_UNPROVEN"} and not r.get("abstain") for x in claim_rows),
            "negative_claims": sum(x["expected"] == "REFUTED" for x in claim_rows),
            "negative_safe": sum(x["expected"] == "REFUTED" and x["evidence"] == "REFUTED_SUPPORTED" for x in claim_rows),
            "strict_complete": strict_complete,
            "failure": failures,
            "tokens": sum(u.get("tokens") or 0 for u in units),
            "units": [{
                "path": u["path"], "start_line": u["start_line"], "end_line": u["end_line"],
                "role": z, "entity_kind": u.get("entity_kind"), "roles": u.get("roles", [u.get("role")]),
            } for u, z in zip(units, roles)],
        }
        rows.append(row)
        for k in ["groups", "groups_hit", "strict_groups_hit", "required_units", "required_units_hit",
                  "required_units_strict", "selected", "known", "role_correct", "unknown", "misleading",
                  "wrong_kind", "supporting", "optional", "irrelevant", "correct_abstain", "false_abstain",
                  "false_answer", "claims", "claim_supported", "claim_unsafe", "unsupported_acceptance",
                  "negative_claims", "negative_safe", "strict_complete_cases", "tokens"]:
            if k in row:
                agg[k] += len(row[k]) if k == "claims" else row[k]
        agg["groups"] += row["required_groups"]
        agg["strict_complete_cases"] += int(strict_complete)

    n = len(rows)
    summary = {
        **agg,
        "cases": n,
        "required_group_recall": agg["groups_hit"] / agg["groups"] if agg["groups"] else None,
        "strict_group_recall": agg["strict_groups_hit"] / agg["groups"] if agg["groups"] else None,
        "required_unit_recall": agg["required_units_hit"] / agg["required_units"] if agg["required_units"] else None,
        "strict_required_unit_recall": agg["required_units_strict"] / agg["required_units"] if agg["required_units"] else None,
        "candidate_coverage": agg["required_units_hit"] / agg["required_units"] if agg["required_units"] else None,
        "role_precision": agg["role_correct"] / agg["known"] if agg["known"] else None,
        "unknown_rate": agg["unknown"] / agg["selected"] if agg["selected"] else None,
        "wrong_entity_kind_rate": agg["wrong_kind"] / agg["selected"] if agg["selected"] else None,
        "misleading_rate": agg["misleading"] / agg["selected"] if agg["selected"] else None,
        "strict_evidence_completeness": agg["strict_complete_cases"] / n if n else None,
        "negative_claim_safety": (agg["negative_safe"] / agg["negative_claims"]) if agg["negative_claims"] else None,
        "token_mean": agg["tokens"] / n if n else None,
        "coverage_threshold": a.coverage_threshold,
        "strict_threshold": a.strict_threshold,
        "required_group_completeness_threshold": 1.0,
        "contract_version": "gq-eval-v2",
    }
    out = {
        "schema": "rag-gq-eval/v2",
        "receipt": str(a.receipt),
        "cases": str(a.cases),
        "contract": {
            "coverage_threshold": a.coverage_threshold,
            "strict_threshold": a.strict_threshold,
            "required_group_completeness": "all_required_groups",
            "non_gold_selection": "UNKNOWN_not_MISLEADING",
            "negative_claim": "requires_closed_world_basis",
        },
        "summary": summary,
        "results": rows,
    }
    a.out.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    raise SystemExit(main())
