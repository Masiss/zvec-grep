#!/usr/bin/env python3
"""Independent verifier for RAG-GS typed grounding readiness.

It audits the frozen candidate identity, typed resolver regression, eval-v2
arithmetic, source/range provenance, liveness/binding safety, negative-claim
basis and determinism. It does not run a new qualification campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from liveness_20261115 import LIVE, scan
from refint_20261115 import RefIndex
from typed_resolution_20261115 import resolve_query


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(msg: str):
    raise AssertionError(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--freeze", type=Path, required=True)
    ap.add_argument("--bindings", type=Path, required=True)
    ap.add_argument("--regression", type=Path, required=True)
    ap.add_argument("--evals", nargs="+", type=Path, required=True)
    ap.add_argument("--receipts", nargs="+", type=Path, required=True)
    ap.add_argument("--eval-contract-test", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    freeze = json.loads(a.freeze.read_text())
    checks = []
    failures = []

    def ok(name, cond, detail=""):
        checks.append({"name": name, "pass": bool(cond), "detail": detail})
        if not cond:
            failures.append({"name": name, "detail": detail})

    ok("freeze.frozen", freeze.get("frozen") is True)
    ok("freeze.schema", freeze.get("schema") == "rag-gs-candidate-freeze/v1")
    ok("freeze.corpus_identity", freeze.get("corpus_manifest_sha256") == sha(a.root / ".zvec-grep" / "manifest.json"),
       str(freeze.get("corpus_manifest_sha256")))
    ok("freeze.bindings_identity", freeze.get("bindings_sha256") == sha(a.bindings))
    tool_hashes = freeze.get("tools", {})
    tool_dir = Path(__file__).parent
    for name, expected in tool_hashes.items():
        p = tool_dir / name
        ok(f"freeze.tool.{name}", p.exists() and sha(p) == expected, str(p))

    reg = json.loads(a.regression.read_text())
    if a.eval_contract_test:
        ect = json.loads(a.eval_contract_test.read_text())
        ok("eval_contract.pass", ect.get("PASS") is True and all(x.get("pass") for x in ect.get("checks", [])))
    ok("regression.pass", reg.get("PASS") is True and reg.get("failures") == 0)
    ok("regression.size", reg.get("checks", 0) >= 15, str(reg.get("checks")))
    required_checks = {
        "synth.property_mapping_typed", "synth.view_exact", "synth.intent_switch_type",
        "synth.sibling_does_not_change_target", "synth.rename_preserves_kind",
        "synth.commented_method_absent", "synth.uncommented_method_live",
        "synth.source_drift_changes_range", "synth.multi_role_union",
        "corpus.property_not_view", "corpus.view_exact", "corpus.unknown_sql",
        "corpus.negative_no_sql_intent", "corpus.negative_control_only",
    }
    names = {r["name"] for r in reg.get("results", []) if r.get("pass")}
    ok("regression.required_cases", required_checks <= names, str(sorted(required_checks - names)))
    if freeze.get("regression_sha256"):
        ok("freeze.regression_identity", freeze["regression_sha256"] == sha(a.regression))

    if len(a.evals) != len(a.receipts):
        fail("evals and receipts length mismatch")
    eval_rows = []
    for ep, rp in zip(a.evals, a.receipts):
        ev = json.loads(ep.read_text())
        rec = json.loads(rp.read_text())
        ok(f"eval.{ep.name}.schema", ev.get("schema") == "rag-gq-eval/v2")
        ok(f"eval.{ep.name}.contract", ev.get("summary", {}).get("contract_version") == "gq-eval-v2")
        cases_path = Path(ev["cases"])
        ok(f"eval.{ep.name}.receipt_identity", rec.get("identity", {}).get("cases_sha256") == sha(cases_path), str(cases_path))
        rows = ev["results"]
        groups = sum(r["required_groups"] for r in rows)
        gh = sum(r["groups_hit"] for r in rows)
        rq = sum(r["required_units"] for r in rows)
        rh = sum(r["required_units_hit"] for r in rows)
        rs = sum(r["required_units_strict"] for r in rows)
        known = sum(r["known"] for r in rows)
        correct = sum(r["role_correct"] for r in rows)
        selected = sum(r["selected"] for r in rows)
        unknown = sum(r["unknown"] for r in rows)
        wrong = sum(r["wrong_kind"] for r in rows)
        mis = sum(r["misleading"] for r in rows)
        neg = sum(r["negative_claims"] for r in rows)
        safe = sum(r["negative_safe"] for r in rows)
        strict_complete = sum(r["strict_complete"] for r in rows)
        s = ev["summary"]
        def close(x, y):
            if x is None or y is None:
                return x == y
            return abs(float(x) - float(y)) < 1e-9
        ok(f"eval.{ep.name}.group_denominator", s.get("groups") == groups, f"{s.get('groups')} vs {groups}")
        ok(f"eval.{ep.name}.group_recall", close(s.get("required_group_recall"), gh / groups if groups else None))
        ok(f"eval.{ep.name}.required_recall", close(s.get("required_unit_recall"), rh / rq if rq else None))
        ok(f"eval.{ep.name}.strict_recall", close(s.get("strict_required_unit_recall"), rs / rq if rq else None))
        ok(f"eval.{ep.name}.role_precision", close(s.get("role_precision"), correct / known if known else None))
        ok(f"eval.{ep.name}.unknown_rate", close(s.get("unknown_rate"), unknown / selected if selected else None))
        ok(f"eval.{ep.name}.wrong_kind_rate", close(s.get("wrong_entity_kind_rate"), wrong / selected if selected else None))
        ok(f"eval.{ep.name}.misleading_rate", close(s.get("misleading_rate"), mis / selected if selected else None))
        ok(f"eval.{ep.name}.negative_safety", close(s.get("negative_claim_safety"), safe / neg if neg else None))
        ok(f"eval.{ep.name}.strict_completeness", close(s.get("strict_evidence_completeness"), strict_complete / len(rows) if rows else None))

        receipt_by_id = {x["id"]: x for x in rec.get("results", [])}
        allowed_roles = {"REQUIRED", "SUPPORTING", "OPTIONAL", "IRRELEVANT", "WRONG_ENTITY_KIND", "MISLEADING", "UNKNOWN", "CONTROL", "WRAPPER", "TYPE", "PROPERTY", "SQL", "VIEW", "DATA"}
        allowed_kinds = {"TYPE", "PROPERTY", "METHOD", "SQL_BODY", "VIEW", "DATA", "UNKNOWN"}
        for row in rows:
            rid = row["id"]
            rr = receipt_by_id.get(rid)
            ok(f"eval.{ep.name}.{rid}.receipt", rr is not None)
            if rr is None:
                continue
            for u in rr.get("units", []):
                p = a.root / u["path"]
                ok(f"eval.{ep.name}.{rid}.path", p.exists(), u["path"])
                if not p.exists():
                    continue
                n = len(p.read_text(errors="replace").splitlines())
                ok(f"eval.{ep.name}.{rid}.range", 1 <= u["start_line"] <= u["end_line"] <= n,
                   f"{u['start_line']}-{u['end_line']} / {n}")
                ok(f"eval.{ep.name}.{rid}.roles", bool(u.get("roles")) and set(u["roles"]) <= allowed_roles,
                   repr(u.get("roles")))
                ok(f"eval.{ep.name}.{rid}.kind", u.get("entity_kind") in allowed_kinds, repr(u.get("entity_kind")))
                ok(f"eval.{ep.name}.{rid}.provenance", bool(u.get("provenance")), repr(u.get("provenance")))
                ok(f"eval.{ep.name}.{rid}.liveness", u.get("liveness") in {"LIVE", "INACTIVE", "UNKNOWN"}, repr(u.get("liveness")))
            # Negative claims accepted only with a proved local method basis.
            for cl in row.get("claims", []):
                if cl.get("expected") == "REFUTED" and cl.get("evidence") == "REFUTED_SUPPORTED":
                    basis = cl.get("negative_basis") or rr.get("negative_basis")
                    scope = rr.get("negative_scope")
                    ok(f"eval.{ep.name}.{rid}.negative_basis", basis in {"method_body_complete", "closed_world"}, repr(basis))
                    ok(f"eval.{ep.name}.{rid}.negative_scope", isinstance(scope, dict) and scope.get("dynamic_sql") is False, repr(scope))
                    if isinstance(scope, dict):
                        sp = a.root / scope["path"]
                        ok(f"eval.{ep.name}.{rid}.negative_scope_path", sp.exists(), str(sp))
        eval_rows.append((ep, ev))

    # Liveness/binding safety re-check (Gate A invariants).
    b = json.loads(a.bindings.read_text())
    for x in b.get("bindings", []):
        if x.get("state") != "VERIFIED":
            continue
        src = x["source"]
        p = a.root / src["path"]
        ok(f"binding.path.{src['path']}", p.exists(), src["path"])
        if not p.exists():
            continue
        sc = scan(p.read_text(errors="replace"))
        ok(f"binding.live.{src['path']}.{src['start']}", sc.line_liveness[src["start"] - 1] == LIVE if src["start"] <= len(sc.line_liveness) else False)
        for t in x.get("targets", []):
            ok(f"binding.target.{t['path']}", (a.root / t["path"]).exists(), t["path"])

    # Determinism / no-gold runtime check.
    texts = [(tool_dir / "grounding_20261115.py").read_text(errors="replace"),
             (tool_dir / "typed_resolution_20261115.py").read_text(errors="replace")]
    ok("runtime.no_gold_reference", not any(re.search(r"\bgold_for\b|\brequired_groups\b|\banswer_key\b", t) for t in texts))
    q = "Which DbSet property maps EmployeeBaseView, and where is EmployeeBaseView declared?"
    refs = RefIndex(a.root)
    s1 = resolve_query(q, refs).as_dict(); s2 = resolve_query(q, refs).as_dict()
    ok("runtime.deterministic_resolver", s1 == s2)
    ok("runtime.exact_typed_property", s1["property_mapping"] and not s1["views"] and bool(s1["types"]) and bool(s1["properties"]),
       repr(s1))

    result = {
        "schema": "rag-gs-verification/v1",
        "checks": len(checks),
        "failures": failures,
        "PASS": not failures,
        "results": checks,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"checks": len(checks), "failures": len(failures), "PASS": not failures}, indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
