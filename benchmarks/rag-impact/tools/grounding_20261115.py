#!/usr/bin/env python3
"""Typed, provenance-bearing evidence grounding (experimental).

Runtime input is question + frozen source + independently produced verified
bindings. No labels, required groups, gold paths or case IDs are read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from refint_20261115 import RefIndex, blocks
from selection_s1_20261115 import Source, run_counter, sha256_file, trim_tokens
from typed_resolution_20261115 import (
    KIND_METHOD, KIND_PROPERTY, KIND_SQL_BODY, KIND_TYPE, KIND_UNKNOWN,
    ROLE_CONTROL, ROLE_PROPERTY, ROLE_SQL, ROLE_SUPPORTING, ROLE_TYPE,
    ROLE_UNKNOWN, ROLE_VIEW, ROLE_WRAPPER,
    merge_typed_units, resolve_query, typed_candidates,
)


def overlaps(a, b):
    return a["path"] == b["path"] and a["start_line"] <= b["end_line"] and b["start_line"] <= a["end_line"]


def _unit(path, start, end, role, kind, resolved, provenance, score, chain=None):
    return {
        "path": path, "start_line": start, "end_line": end, "last_line": end,
        "role": role, "roles": [role], "entity_kind": kind,
        "resolved": resolved, "provenance": [provenance], "liveness": "LIVE",
        "ambiguity": [], "score": score, "chain": chain or [],
    }


def _window_units(method, source, spec):
    """Return write/flow windows for a method, not one arbitrary replacement."""
    start, end = method["start"], method["end"]
    name = method["name"]
    out = []
    if not (spec.wants_method or spec.wants_sql):
        return out
    if end - start + 1 <= 80:
        return out
    lines = source.text_lines(method["path"])
    saves = [i + 1 for i in range(start - 1, end) if "SaveChanges" in lines[i]]
    if saves:
        prev = start - 1
        for sv in saves:
            if sv > prev + 1:
                a0, b0 = prev + 1, sv
                if b0 - a0 + 1 > 80:
                    out.append(_unit(method["path"], a0, a0 + 70, ROLE_CONTROL, KIND_METHOD,
                                     {"name": name, "window": "savechanges_first"},
                                     {"edge": "savechanges_window", "method": name}, 3.35))
                    out.append(_unit(method["path"], b0 - 70, b0, ROLE_CONTROL, KIND_METHOD,
                                     {"name": name, "window": "savechanges_second"},
                                     {"edge": "savechanges_window", "method": name}, 3.30))
                else:
                    out.append(_unit(method["path"], a0, b0, ROLE_CONTROL, KIND_METHOD,
                                     {"name": name, "window": "savechanges"},
                                     {"edge": "savechanges_window", "method": name}, 3.35))
            prev = sv
    for idx, (bs, be) in enumerate(blocks(source, method["path"], start, end, 4)):
        text = "\n".join(lines[bs - 1:be])
        bonus = 0.5 if idx == 0 else 0.0
        if "SaveChanges" in text:
            bonus += 0.4
        out.append(_unit(method["path"], bs, be, ROLE_CONTROL, KIND_METHOD,
                         {"name": name, "window": "block"},
                         {"edge": "method_block", "method": name}, 2.7 + bonus))
    return out


def _primary_methods(spec, refs, bmap):
    out = {}
    for name in sorted(spec.method_names):
        ms = [m for m in refs.method_by.get(name, []) if m["name"] == name]
        if not ms:
            continue
        def rank(m):
            key = (m["path"], m["start"], m["end"])
            return (1 if bmap.get(key) else 0,
                    1 if "/Services/" in m["path"] else 0,
                    m["end"] - m["start"] + 1)
        out[name] = max(ms, key=rank)
    return out


def analyze(question, refs, binds, source_ref):
    spec = resolve_query(question, refs)
    cands = typed_candidates(spec, refs, source_ref)

    # Verified bindings only. A commented method cannot be a binding source.
    bmap = {}
    for b in binds.get("bindings", []):
        if b.get("state") == "VERIFIED":
            key = (b["source"]["path"], b["source"]["start"], b["source"]["end"])
            bmap.setdefault(key, []).append(b)

    # Method candidates: one primary declaration per exact name; service
    # implementation and verified-binding source outrank thin controller
    # distractors. Other declarations are retained as ambiguity trace only.
    primaries = _primary_methods(spec, refs, bmap)
    spec.ambiguous = bool(primaries and any(
        len([m for m in refs.method_by.get(n, []) if m["name"] == n]) > 1
        for n in primaries
    ))
    cands = [c for c in cands if not (c.get("entity_kind") == KIND_METHOD
                                      and c.get("role") == ROLE_CONTROL
                                      and (c.get("resolved") or {}).get("name") in primaries)]
    for mname, m in primaries.items():
        cands.append(_unit(m["path"], m["start"], m["end"], ROLE_CONTROL, KIND_METHOD,
                           {"name": mname, "parent": m.get("parent")},
                           {"edge": "exact_method", "name": mname}, 3.0))
        cands.extend(_window_units(m, source_ref, spec))

    # Binding closure: only for SQL/flow intent, never for a negative claim.
    if spec.wants_sql and not spec.negative and (spec.sql_names or spec.method_names):
        for b in binds.get("bindings", []):
            if b.get("state") != "VERIFIED":
                continue
            if spec.sql_names and b.get("proc") not in spec.sql_names:
                continue
            # Add the source method if this binding belongs to a named method.
            src = b["source"]
            if spec.method_names and src.get("name") not in spec.method_names and not any(
                m["path"] == src["path"] and m["start"] == src["start"] and m["end"] == src["end"]
                for mn in spec.method_names for m in refs.method_by.get(mn, [])
            ):
                continue
            for mid in b.get("chain", [])[1:]:
                ms = list(refs.method_by.get(mid, []))
                scoped = [mm for mm in ms if mm.get("parent") == src.get("parent")]
                for mm in (scoped or ms[:1]):
                    cands.append(_unit(mm["path"], mm["start"], mm["end"], ROLE_WRAPPER,
                                       KIND_METHOD, {"name": mid, "chain": b.get("chain", [])},
                                       {"edge": "binding_chain", "proc": b.get("proc")}, 2.75))
            for t in b.get("targets", []):
                role = ROLE_SQL if t["path"].endswith(".sql") else ROLE_SUPPORTING
                kind = KIND_SQL_BODY if role == ROLE_SQL else KIND_UNKNOWN
                cands.append(_unit(t["path"], t["start"], t["end"], role, kind,
                                   {"name": t.get("name"), "object": "sql"},
                                   {"edge": "binding_target", "proc": b.get("proc")}, 2.8))
            # SQL exact method: mark source as a control with binding provenance.
            cands.append(_unit(src["path"], src["start"], src["end"], ROLE_CONTROL,
                               KIND_METHOD, {"name": src.get("name")},
                               {"edge": "binding_source", "proc": b.get("proc")}, 3.0))

    # For exact SQL/view queries add the typed object and its verified callers.
    for sname in spec.sql_names:
        for s in refs.sqls:
            if s["name"] != sname:
                continue
            for b in binds.get("bindings", []):
                if b.get("state") != "VERIFIED" or b.get("proc") != s["name"]:
                    continue
                if b.get("depth", 0) != 0:
                    continue
                src = b["source"]
                if spec.method_names and src.get("name") not in spec.method_names:
                    continue
                cands.append(_unit(src["path"], src["start"], src["end"], ROLE_WRAPPER,
                                   KIND_METHOD, {"name": src.get("name")},
                                   {"edge": "sql_caller", "proc": s["name"]}, 2.7))
    for vname in spec.view_names:
        for s in refs.sqls:
            if s["name"] == vname and re.search(r"CREATE\s+VIEW", s.get("text", ""), re.I):
                cands.append(_unit(s["path"], s["start"], s["end"], ROLE_VIEW, "VIEW",
                                   {"name": s["name"], "object": "view"},
                                   {"edge": "exact_view", "name": s["name"]}, 3.0))

    if spec.negative:
        cands = [c for c in cands if c["role"] == ROLE_CONTROL]
    negative_basis = "open_world"
    negative_scope = None
    if spec.negative and primaries:
        pm = next(iter(primaries.values()))
        if not pm.get("dynamic_sql"):
            negative_basis = "method_body_complete"
            negative_scope = {"path": pm["path"], "start": pm["start"], "end": pm["end"],
                              "dynamic_sql": False}

    cands = merge_typed_units(cands)
    # Intent filtering. Property intent wins over a substring view match.
    if spec.property_mapping and not spec.wants_method and not spec.wants_sql:
        keep = {ROLE_TYPE, ROLE_PROPERTY}
    elif spec.property_mapping and spec.wants_method and not spec.wants_sql:
        keep = {ROLE_CONTROL, ROLE_WRAPPER, ROLE_TYPE, ROLE_PROPERTY}
    elif spec.wants_view and not spec.wants_method:
        keep = {ROLE_VIEW}
    elif spec.wants_sql:
        keep = {ROLE_SQL, ROLE_VIEW, ROLE_WRAPPER, ROLE_CONTROL, ROLE_SUPPORTING}
    elif spec.wants_method:
        keep = {ROLE_CONTROL, ROLE_WRAPPER, ROLE_SQL, ROLE_VIEW, ROLE_SUPPORTING}
    else:
        keep = set()
    cands = [c for c in cands if c["role"] in keep]
    return {
        "spec": spec.as_dict(),
        "intent": sorted(spec.intent),
        "code_tokens": spec.identifiers,
        "unknown_sql": bool(spec.unknown_sql_names),
        "unknown_sql_names": sorted(spec.unknown_sql_names),
        "candidates": cands,
        "abstain": not cands,
        "negative_basis": negative_basis,
        "negative_scope": negative_scope,
        "trace": {"query": question, "resolved": spec.as_dict(), "candidate_count": len(cands)},
    }


def select(analysis, source, counter=None, tokenizer=None, budget=3000):
    role_rank = {ROLE_SQL: 7, ROLE_VIEW: 7, ROLE_PROPERTY: 6, ROLE_TYPE: 5,
                 ROLE_WRAPPER: 4, ROLE_CONTROL: 3, ROLE_SUPPORTING: 1, ROLE_UNKNOWN: 0}
    cands = sorted(
        analysis["candidates"],
        key=lambda x: (0 if (x.get("entity_kind") == KIND_METHOD and not (x.get("resolved") or {}).get("window")) else 1,
                       -role_rank.get(x["role"], 0), -x.get("score", 0),
                       x["end_line"] - x["start_line"] + 1, x["path"]),
    )
    out = []
    for x in cands:
        if any(overlaps(x, y) for y in out):
            # Same range necessarily; merge roles rather than drop provenance.
            for y in out:
                if overlaps(x, y):
                    y["roles"] = sorted(set(y.get("roles", [y["role"]]) + x.get("roles", [x["role"]])))
                    for p in x.get("provenance", []):
                        if p not in y.get("provenance", []):
                            y.setdefault("provenance", []).append(p)
            continue
        out.append(x)
        if len(out) >= 4:
            break
    if counter and tokenizer and out:
        counts = run_counter(counter, tokenizer, [source.pack_text([x]) for x in out])
        for x, n in zip(out, counts):
            x["tokens"] = n
        while out and sum(x.get("tokens", 0) for x in out) > budget:
            out.pop()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--bindings", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--token-counter", type=Path)
    ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--budget", type=int, default=3000)
    a = ap.parse_args()
    refs = RefIndex(a.root)
    source = Source(a.root)
    binds = json.loads(a.bindings.read_text())
    rows = []
    for case in json.loads(a.cases.read_text())["cases"]:
        an = analyze(case["question"], refs, binds, source)
        chosen = select(an, source, a.token_counter, a.tokenizer, a.budget)
        rows.append({
            "id": case["id"], "question": case["question"],
            "abstain": bool(an["abstain"] or not chosen),
            "unknown_sql": an["unknown_sql"], "unknown_sql_names": an["unknown_sql_names"],
            "intent": an["intent"], "resolved": an["spec"],
            "units": chosen, "candidate_count": len(an["candidates"]),
            "negative_basis": an["negative_basis"], "negative_scope": an["negative_scope"],
            "trace": an["trace"],
        })
    out = {
        "schema": "rag-evidence-grounding/v2",
        "policy": "typed-reference-binding-grounding",
        "config": {"budget": a.budget, "uses_gold_in_runtime": False,
                   "uses_roles_in_runtime": False, "evaluation_contract": "gq-eval-v2"},
        "identity": {
            "cases_sha256": sha256_file(a.cases),
            "bindings_sha256": sha256_file(a.bindings),
            "corpus_manifest_sha256": sha256_file(a.root / ".zvec-grep" / "manifest.json"),
        },
        "results": rows,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"cases": len(rows), "abstain": sum(r["abstain"] for r in rows),
                      "units": sum(len(r["units"]) for r in rows)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
