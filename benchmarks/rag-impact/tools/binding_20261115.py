#!/usr/bin/env python3
"""Bounded method -> SQL binding with explicit resolution states.

Only an explicit SQL invocation in a method body is VERIFIED. A wrapper chain is
VERIFIED only when every call target is unique and the terminal direct edge is
verified. Name-only matches are never promoted to VERIFIED.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from refint_20261115 import RefIndex, SQL_RE, stem

def resolve_call(refs, cur, call):
    targets = refs.method_by.get(call, [])
    if len(targets) > 1:
        scoped = [t for t in targets if t.get("parent") == cur.get("parent") and t.get("ns") == cur.get("ns")]
        if len(scoped) == 1:
            return scoped, "same_type_scope"
        scoped = [t for t in targets if t.get("ns") == cur.get("ns")]
        if len(scoped) == 1:
            return scoped, "same_namespace_scope"
    return targets, "global"


def bindings(refs: RefIndex, max_depth=3):
    direct = []  # (method, proc, targets, state, reason)
    for m in refs.methods:
        for proc in sorted(m["procs"]):
            targets = refs.sql_for_proc(proc)
            if len(targets) == 1:
                state, reason = "VERIFIED", "explicit_exec_one_definition"
            elif len(targets) > 1:
                state, reason = "AMBIGUOUS", "explicit_exec_multiple_definitions"
            else:
                state, reason = "UNRESOLVED", "explicit_exec_no_definition"
            direct.append({"source": m, "proc": proc, "targets": targets, "state": state, "reason": reason, "depth": 0, "chain": [m["name"]]})
    # index direct bindings by method identity
    bykey = {(b["source"]["path"], b["source"]["start"], b["source"]["end"]): b for b in direct if b["state"] == "VERIFIED"}
    wrappers = []
    for m in refs.methods:
        # BFS only through resolved call targets; stop at depth max_depth
        queue = [(m, [], 0)]
        seen = set()
        while queue:
            cur, chain, depth = queue.pop(0)
            key = (cur["path"], cur["start"], cur["end"])
            if key in seen or depth >= max_depth: continue
            seen.add(key)
            for call in sorted(cur["calls"]):
                targets, scope = resolve_call(refs, cur, call)
                if not targets: continue
                for t in targets:
                    tk = (t["path"], t["start"], t["end"])
                    b = bykey.get(tk)
                    if b:
                        state = "VERIFIED" if len(targets) == 1 else "AMBIGUOUS"
                        reason = ("wrapper_chain_unique" if len(targets) == 1 else "wrapper_chain_ambiguous_call") + f"_{scope}"
                        wrappers.append({"source": m, "proc": b["proc"], "targets": b["targets"], "state": state, "reason": reason, "depth": depth + 1, "chain": chain + [cur["name"], t["name"]]})
                    else:
                        queue.append((t, chain + [cur["name"]], depth + 1))
    # name-only candidates (never verified)
    names = []
    for m in refs.methods:
        for s in refs.sqls:
            if stem(m["name"]) == stem(s["name"].replace("sp_", "")):
                names.append({"source": m, "proc": s["name"], "targets": [s], "state": "AMBIGUOUS", "reason": "name_only_not_invocation", "depth": 0, "chain": [m["name"]]})
    # dedupe wrapper chains by source+proc+state
    uniq = {}
    for b in wrappers:
        k = (b["source"]["path"], b["source"]["start"], b["source"]["end"], b["proc"], b["state"])
        if k not in uniq or len(b["chain"]) < len(uniq[k]["chain"]): uniq[k] = b
    return direct + list(uniq.values()) + names

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",type=Path,required=True); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args()
    refs=RefIndex(a.root); bs=bindings(refs)
    rows=[]
    for b in bs:
        m=b["source"]; rows.append({"state":b["state"],"reason":b["reason"],"depth":b["depth"],"chain":b["chain"],"proc":b["proc"],"source":{"path":m["path"],"start":m["start"],"end":m["end"],"name":m["name"],"parent":m.get("parent")},"targets":[{"path":t["path"],"start":t["start"],"end":t["end"],"name":t["name"]} for t in b["targets"]]})
    out={"schema":"rag-sql-binding/v1","counts":{"direct":sum(1 for b in bs if b["reason"].startswith("explicit")),"wrapper":sum(1 for b in bs if b["reason"].startswith("wrapper")),"name_only":sum(1 for b in bs if b["reason"].startswith("name_only")),"verified":sum(1 for b in bs if b["state"]=="VERIFIED"),"ambiguous":sum(1 for b in bs if b["state"]=="AMBIGUOUS"),"unresolved":sum(1 for b in bs if b["state"]=="UNRESOLVED")},"bindings":rows}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,ensure_ascii=False,indent=1)+"\n"); print(json.dumps(out["counts"],indent=1))
if __name__=="__main__": raise SystemExit(main())
