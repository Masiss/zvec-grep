#!/usr/bin/env python3
"""RAG-S1b query-to-evidence signal prototype (fixed candidate pool).

Signals are extracted only from source ranges that are already in the frozen
probe/rg pool.  No candidate or source range outside the pool is added.

Direct references (calls/SQL strings) are followed only when the target is a
frozen candidate; unresolved edges are reported, never promoted to evidence.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
from pathlib import Path

from selection_s1_20261115 import (
    Source, code_tokens, gold_for, matched, run_counter, sha256_file, stem,
    strict_hits, tokens, trim_tokens,
)

CALL_RE = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)\s*\(")
DOT_CALL_RE = re.compile(r"\.\s*([A-Z][A-Za-z0-9_]*)\s*\(")
SQL_PROC_RE = re.compile(r"\b(?:execute|exec)\s+(?:\[?dbo\]?\.)?\[?(sp_[A-Za-z0-9_]+)", re.I)
CREATE_PROC_RE = re.compile(r"CREATE\s+PROCEDURE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z0-9_]+)", re.I)
CONTROL = {"if", "for", "foreach", "while", "switch", "catch", "using", "lock", "return", "new", "sizeof", "typeof", "nameof"}


def add_unit(store, path, start, end, meta, call, rank, ckey, kind):
    if start is None or end is None or end < start:
        return
    size = end - start + 1
    if size < 3 or size > 500:
        return
    key = (path, start, end)
    u = store.get(key)
    if u is None:
        u = store[key] = {
            "path": path, "start_line": start, "last_line": end, "size": size,
            "meta": meta or {}, "calls_set": set(), "best_rank": rank,
            "ckey": ckey, "kind": kind,
        }
    u["calls_set"].add(call)
    u["best_rank"] = min(u["best_rank"], rank)


def read_calls(source: Source, path: str, start: int, end: int):
    text = "\n".join(source.text_lines(path)[start - 1:end])
    calls = set()
    for match in list(CALL_RE.finditer(text)) + list(DOT_CALL_RE.finditer(text)):
        name = match.group(1)
        if name not in CONTROL and len(name) > 2:
            calls.add(name)
    procs = set(SQL_PROC_RE.findall(text))
    return calls, procs


def build_units(case, items, source: Source):
    question = case["question"]
    qlist = [stem(t) for t in tokens(question) if t]
    qset = set(qlist)
    store: dict[tuple, dict] = {}

    def has_query_signal(path: str, start: int, end: int, name: str = "") -> bool:
        text = {stem(t) for t in tokens("\n".join(source.text_lines(path)[start - 1:end]))}
        text |= {stem(t) for t in tokens(name)}
        return any(t in text for t in qset if len(t) >= 3)

    for item in items:
        path = item["path"]; start = item["start_line"]; end = item["last_line"]
        meta = item.get("metadata") or {}; stype = meta.get("symbol_type")
        if item.get("rg"):
            continue
        rank = item["rank"]
        if path.endswith(".sql"):
            add_unit(store, path, start, end, meta, item["call"], rank, (path, path), "sql")
            continue
        if stype == "value" or end - start + 1 <= 2:
            continue
        if stype == "class" or end - start + 1 > 200:
            ckey = (path, meta.get("symbol_name") or (start, end))
            children = []
            for d in source.declarations(path):
                if d["start"] < start or d["end"] > end:
                    continue
                if d.get("parent") != meta.get("symbol_name"):
                    continue
                children.append(d)
                add_unit(store, path, d["start"], d["end"],
                         {"symbol_name": d["name"], "symbol_type": d["kind"], "scope": d.get("parent")},
                         item["call"], rank, ckey, d["kind"])
                if d["kind"] == "method" and d["end"] - d["start"] + 1 > 80 and has_query_signal(path, d["start"], d["end"], d["name"]):
                    for bs, be in source.blocks(path, d["start"], d["end"]):
                        add_unit(store, path, bs, be,
                                 {"symbol_name": f"{d['name']}#{bs}", "symbol_type": "block", "scope": d.get("parent")},
                                 item["call"], rank, ckey, "block")
                        add_unit(store, path, d["start"], be,
                                 {"symbol_name": f"{d['name']}#prefix{be}", "symbol_type": "prefix", "scope": d.get("parent")},
                                 item["call"], rank, ckey, "prefix")
            first_method = min((d["start"] for d in children if d["kind"] == "method"), default=start + 40)
            header_end = min(end, max(start + 2, first_method - 1))
            symbol = meta.get("symbol_name") or ""
            if any(
                len(name) >= 6 and stem(name) == stem(symbol) and Path(path).stem.lower() == symbol.lower()
                for name in code_tokens(question)
            ):
                add_unit(store, path, start, header_end,
                         {"symbol_name": symbol, "symbol_type": "header", "scope": meta.get("scope")},
                         item["call"], rank, ckey, "header")
            add_unit(store, path, start, end, meta, item["call"], rank * 2, ckey, "type")
        else:
            ckey = None
            for d in source.declarations(path):
                if d["kind"] == "type" and d["start"] <= start <= d["end"]:
                    ckey = (path, d["name"])
                    break
            add_unit(store, path, start, end, meta, item["call"], rank, ckey, stype or "unit")
            if stype == "function" and end - start + 1 > 80 and has_query_signal(path, start, end, meta.get("symbol_name") or ""):
                for bs, be in source.blocks(path, start, end):
                    add_unit(store, path, bs, be,
                             {"symbol_name": f"{meta.get('symbol_name') or ''}#{bs}", "symbol_type": "block", "scope": meta.get("scope")},
                             item["call"], rank, ckey, "block")
                    add_unit(store, path, start, be,
                             {"symbol_name": f"{meta.get('symbol_name') or ''}#prefix{be}", "symbol_type": "prefix", "scope": meta.get("scope")},
                             item["call"], rank, ckey, "prefix")

    # Parse direct references and SQL strings for every structural unit.
    for u in store.values():
        calls, procs = read_calls(source, u["path"], u["start_line"], u["last_line"])
        u["calls"] = sorted(calls)
        u["sql_procs"] = sorted(procs)

    # Document frequency for query-term scoring.
    docs = {}; df = collections.Counter()
    for key, u in store.items():
        body = {stem(t) for t in tokens("\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]]))}
        names = {stem(t) for t in tokens(u["meta"].get("symbol_name") or "")} | {stem(t) for t in tokens(u["path"])} | {stem(t) for t in tokens(u["meta"].get("scope") or "")}
        u["body"], u["names"] = body, names
        docs[key] = body | names
        for t in docs[key]:
            df[t] += 1
    n = max(1, len(store))
    weight = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in qset}
    total = sum(weight.values()) or 1

    for u in store.values():
        name_hit = sum(weight[t] for t in qset if t in u["names"])
        body_hit = sum(weight[t] for t in qset if t in u["body"])
        call_hit = sum(weight[t] for t in qset if any(t in {stem(x) for x in tokens(c)} for c in u["calls"]))
        sql_hit = sum(weight[t] for t in qset if any(t in {stem(x) for x in tokens(p)} for p in u["sql_procs"]))
        body_text = "\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]])
        idents = {stem(t) for t in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", body_text)}
        id_hit = sum(weight[t] for t in qset if len(t) >= 4 and any(t in {stem(x) for x in tokens(ident)} for ident in idents))
        exact = 1.0 if any(
            len(name) >= 6 and {stem(t) for t in tokens(name)} <= u["names"]
            for name in code_tokens(question)
        ) else 0.0
        size_prior = 0.6 if 5 <= u["size"] <= 160 else (0.15 if u["size"] < 5 else 0.1)
        rank_prior = 0.4 * max(0.0, 1.0 - (u["best_rank"] - 1) / 50.0)
        u["lex"] = (0.8 * name_hit + 0.5 * body_hit) / total
        u["exact"] = exact
        u["score"] = 1.0 * u["lex"] + 2.5 * id_hit / total + 1.4 * exact + size_prior + rank_prior + 0.05 * len(u["calls_set"])
        if u["kind"] == "header":
            u["score"] += 2.4 if exact else 0.0
        if u["kind"] == "block":
            u["score"] += 0.15
        if u["kind"] == "prefix":
            u["score"] += 0.8
        if u["kind"] == "block" and u["size"] < 12:
            u["score"] -= 0.15
        u["edge_bonus"] = 0.0

    # Direct reference edges only between frozen candidates.
    by_method = collections.defaultdict(list)
    for u in store.values():
        if u["kind"] in ("method", "function") and u["meta"].get("symbol_name"):
            by_method[stem(u["meta"]["symbol_name"].split("#")[0])].append(u)
    sql_by_proc = collections.defaultdict(list)
    for u in store.values():
        if u["kind"] != "sql":
            continue
        text = "\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]])
        procs = set(CREATE_PROC_RE.findall(text)) | set(u["sql_procs"]) | {Path(u["path"]).stem}
        for proc in procs:
            sql_by_proc[stem(proc.replace("sp_", ""))].append(u)
    for u in store.values():
        u["edges"] = []
        for call in sorted(u["calls"]):
            for target in by_method.get(stem(call), []):
                if target is not u:
                    u["edges"].append(("call", call, target))
        for proc in sorted(u["sql_procs"]):
            for target in sql_by_proc.get(stem(proc.replace("sp_", "")), []):
                u["edges"].append(("sql", proc, target))

    # One-hop propagation from the strongest query-matched seeds only.
    # A call/SQL edge is admitted only when the edge name itself carries a
    # query term of length >=4; generic helpers are not promoted.
    seeds = sorted(store.values(), key=lambda u: (-u["score"], u["size"]))[:3]
    for seed in seeds:
        if seed["score"] < 0.35 and not seed["exact"]:
            continue
        for call in seed["calls"]:
            call_terms = {stem(t) for t in tokens(call)}
            if not any(t in qset and len(t) >= 4 for t in call_terms):
                continue
            for target in by_method.get(stem(call), []):
                if target is not seed:
                    target["edge_bonus"] = min(0.9, target["edge_bonus"] + 0.5 * seed["score"])
                    target.setdefault("edge_from", []).append(("call", call, seed["path"], seed["start_line"]))
        for proc in seed["sql_procs"]:
            proc_terms = {stem(t) for t in tokens(proc.replace("sp_", ""))}
            if not any(t in qset and len(t) >= 4 for t in proc_terms):
                continue
            for target in sql_by_proc.get(stem(proc.replace("sp_", "")), []):
                target["edge_bonus"] = min(0.9, target["edge_bonus"] + 0.5 * seed["score"])
                target.setdefault("edge_from", []).append(("sql", proc, seed["path"], seed["start_line"]))
    for u in store.values():
        u["score"] += u["edge_bonus"]

    # Prefer fine-grained blocks/headers for long methods/classes.
    for u in list(store.values()):
        if u["kind"] == "method" and u["size"] > 200:
            u["score"] -= 0.25
        if u["kind"] == "block" and u["size"] <= 120:
            u["score"] += 0.2
    return store, qset


def select_units(store, budget=10):
    ordered = sorted(store.values(), key=lambda u: (-u["score"], u["size"], u["path"], u["start_line"]))
    out = []
    seen = set()
    per_file = collections.Counter()
    for u in ordered:
        if len(out) >= budget:
            break
        key = (u["path"], u["start_line"], u["last_line"])
        if key in seen:
            continue
        if any(
            x["path"] == u["path"]
            and min(x["last_line"], u["last_line"]) - max(x["start_line"], u["start_line"]) + 1
            > 0.7 * min(x["size"], u["size"])
            for x in out
        ):
            continue
        if per_file[u["path"]] >= 3:
            continue
        out.append(u); seen.add(key); per_file[u["path"]] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--receipt", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ids")
    ap.add_argument("--token-counter", type=Path)
    ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--token-budget", type=int, default=3716)
    args = ap.parse_args()

    probe = json.loads(args.probe.read_text())
    receipt = json.loads(args.receipt.read_text())
    allowed = set(args.ids.split(",")) if args.ids else None
    cases = {c["id"]: c for c in json.loads(args.cases.read_text())["cases"] if allowed is None or c["id"] in allowed}
    pool = {}
    for q in probe["queries"]:
        pool.setdefault(q["id"], []).extend({**item, "call": q["call"], "rank": i + 1, "rg": False} for i, item in enumerate(q["items"]))
    for row in receipt.get("rg_calls", []):
        pool.setdefault(row["id"], []).extend({**unit, "score": 0.0, "metadata": {}, "matched_by": "rg", "call": row["call"], "rank": i + 1, "rg": True} for i, unit in enumerate(row["units"]))
    source = Source(args.root)
    results = []
    for cid, case in cases.items():
        items = pool.get(cid, [])
        store, qset = build_units(case, items, source)
        selected = select_units(store)
        if args.token_counter and args.tokenizer:
            selected = trim_tokens(selected, source, args.token_counter, args.tokenizer, args.token_budget)
        gold = gold_for(case)
        hit = matched(selected, gold)
        strict = strict_hits(selected, gold)
        text = source.pack_text(selected)
        results.append({
            "id": cid, "gold_spans": len(gold), "selected_spans": len(hit),
            "recall": len(hit) / len(gold) if gold else None,
            "strict_spans": len(strict), "strict_recall": len(strict) / len(gold) if gold else None,
            "units": [{
                "path": u["path"], "start_line": u["start_line"], "last_line": u["last_line"],
                "size": u["size"], "kind": u["kind"], "symbol": u["meta"].get("symbol_name"),
                "score": round(u["score"], 6), "calls": sorted(u["calls_set"]),
                "edges": sorted(e[0] + ":" + e[1] for e in u.get("edges", [])),
                "edge_from": sorted(
                    [
                        {"kind": e[0], "name": e[1], "path": e[2], "start_line": e[3]}
                        for e in u.get("edge_from", [])
                    ],
                    key=lambda e: (e["kind"], e["name"], e["path"], e["start_line"]),
                ),
            } for u in selected],
            "chars": len(text),
        })
    if args.token_counter and args.tokenizer:
        # Count each packed case in one batch.
        texts = []
        for r in results:
            units = [{"path": u["path"], "start_line": u["start_line"], "last_line": u["last_line"]} for u in r["units"]]
            texts.append(source.pack_text(units))
        counts = run_counter(args.token_counter, args.tokenizer, texts)
        for r, count in zip(results, counts):
            r["tokens"] = count

    def mean(k):
        vals = [r[k] for r in results if r[k] is not None]
        return sum(vals) / len(vals) if vals else None

    out = {
        "schema": "rag-selection-s1b/v1",
        "policy": "signal",
        "config": {"budget": 10, "token_budget": args.token_budget, "uses_gold_in_runtime": False},
        "identity": {
            "probe_sha256": sha256_file(args.probe),
            "requal_receipt_sha256": sha256_file(args.receipt),
            "cases_sha256": sha256_file(args.cases),
            "corpus_manifest_sha256": sha256_file(args.root / ".zvec-grep" / "manifest.json"),
        },
        "summary": {
            "cases": len(results), "gold_spans": sum(r["gold_spans"] for r in results),
            "selected_spans": sum(r["selected_spans"] for r in results),
            "strict_spans": sum(r["strict_spans"] for r in results),
            "macro_recall": mean("recall"), "macro_strict": mean("strict_recall"),
            "tokens_mean": mean("tokens") if any("tokens" in r for r in results) else None,
            "source_files_read": len(source.reads), "source_bytes_read": source.read_bytes,
            "determinism_sha256": __import__("hashlib").sha256(json.dumps([(r["id"], r["units"]) for r in results], sort_keys=True).encode()).hexdigest(),
        },
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
