#!/usr/bin/env python3
"""RAG reference-intelligence prototype.

Builds a lightweight source reference index (types/methods/properties/SQL),
seeds it from the frozen retrieval candidates, follows only query/task-relevant
typed edges, and packs provenance-bearing evidence under the token budget.

No production code is changed. No gold is read by the policy.
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

TYPE_RE = re.compile(r"^\s*(?:(?:public|private|protected|internal|static|partial|abstract|sealed|readonly|unsafe|new)\s+)*(class|struct|interface|record|enum)\s+([A-Za-z_]\w*)")
PROP_RE = re.compile(r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|required|new|sealed|abstract)\s+)*(?:DbSet\s*<\s*([A-Za-z_]\w*)\s*>|([A-Za-z_][\w<>,\[\]?\.]*))\s+([A-Za-z_]\w*)\s*\{\s*(?:get|set|init)")
NS_RE = re.compile(r"^\s*namespace\s+([\w\.]+)")
CALL_RE = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)\s*\(")
DOT_RE = re.compile(r"\.\s*([A-Z][A-Za-z0-9_]*)\s*\(")
SQL_RE = re.compile(r"\b(?:execute|exec)\s+(?:\[?dbo\]?\.)?\[?(sp_[A-Za-z0-9_]+)", re.I)
CREATE_RE = re.compile(r"CREATE\s+PROCEDURE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z0-9_]+)", re.I)
CONTROL = {"if", "for", "foreach", "while", "switch", "catch", "using", "lock", "return", "new", "sizeof", "typeof", "nameof"}
HELPER = re.compile(r"^(?:Log|Check|Caching|Build|Render|Dispose|ToString|Get|Set|Add|Remove|Write|Read|Parse|Try|Ensure|Validate|Is|Has|Can|Should|CreateInstance|Convert|Format|ToString)", re.I)
WRITE_RE = re.compile(r"(?:SaveChanges|ExecuteSqlCommand|\.Add\s*\(|\.Remove\s*\(|Insert|Update|Delete|Restore|LogEdit|LogDelete)", re.I)
READ_RE = re.compile(r"(?:FirstOrDefault|SingleOrDefault|ToList|Find\s*\(|Select\s*\(|Any\s*\(|Count\s*\()", re.I)
SQL_INTENT = {"proc", "procedure", "sql", "database", "db", "table", "stored"}
WRITE_INTENT = {"update", "write", "save", "edit", "create", "delete", "restore", "insert", "ghi", "sua", "cập", "cap", "xoa", "xóa", "tao", "tạo", "luu", "lưu", "anh", "ảnh", "huong", "hưởng"}
READ_INTENT = {"read", "view", "show", "get", "list", "doc", "đọc", "hien", "hiển", "xem", "tim", "tìm"}
FLOW_INTENT = {"call", "route", "through", "goi", "gọi", "qua", "đi", "flow", "path"}
STOP = set("la là co có cua của va và nhung những nao nào gi gì di đi qua dau đâu khi thì cho trong ra sao nhu như the thế mot một cac các duoc được bi bị tu từ voi với hay khong không phai phải moi mọi lan lần sua sửa doi đổi the thể anh ảnh huong hưởng ngoai ngoài vao vào noi nơi nay này do đó o ở tai tại bang bằng de để neu nếu vi vì nen nên rang rằng cung cũng chi chỉ con còn da đã dang đang se sẽ boi bởi theo tren trên duoi dưới giua giữa sau truoc trước khien khiến lam làm cai cái chay chạy".split())


def qterms(q):
    out = [stem(t) for t in tokens(q) if t not in STOP]
    # Domain aliases are query-only; they never expose gold paths.
    t = set(out)
    if {"personal", "nhan", "nhanvien", "nhansu", "hoso"} & t:
        out.append("employee")
    if {"nhanvien"} & t:
        out.extend(["employee", "detail"])
    return out


def intent_of(question):
    t = set(qterms(question)) | set(tokens(question))
    intent = set()
    if t & WRITE_INTENT: intent.add("write")
    if t & READ_INTENT: intent.add("read")
    if t & SQL_INTENT: intent.add("sql")
    if t & FLOW_INTENT: intent.add("flow")
    return intent or {"generic"}


class RefIndex:
    def __init__(self, corpus: Path):
        self.corpus = corpus
        self.lines = {}
        self.types = []
        self.methods = []
        self.props = []
        self.sqls = []
        self.type_by = collections.defaultdict(list)
        self.method_by = collections.defaultdict(list)
        self.prop_by_name = collections.defaultdict(list)
        self._load()

    def text_lines(self, path):
        if path not in self.lines:
            self.lines[path] = (self.corpus / path).read_text(errors="replace").splitlines()
        return self.lines[path]

    def _load(self):
        for p in sorted(self.corpus.rglob("*.cs")):
            rel = str(p.relative_to(self.corpus)); lines = self.text_lines(rel)
            depth = []; d = 0
            for line in lines: depth.append(d); d += line.count("{") - line.count("}")
            ns = ""; local = []
            for i, line in enumerate(lines):
                nm = NS_RE.match(line)
                if nm: ns = nm.group(1)
                tm = TYPE_RE.match(line)
                if tm:
                    b = i
                    while b < len(lines) and "{" not in lines[b] and b - i <= 3: b += 1
                    if b >= len(lines): continue
                    dd = 0; last = b
                    for j in range(b, min(len(lines), i + 2500)):
                        dd += lines[j].count("{") - lines[j].count("}"); last = j
                        if dd <= 0: break
                    if dd > 0: last = len(lines) - 1
                    t = {"path": rel, "name": tm.group(2), "kind": tm.group(1), "start": i + 1, "end": last + 1, "depth": depth[i], "ns": ns}
                    self.types.append(t); local.append(t); self.type_by[t["name"]].append(t)
                    continue
                parent = min([t for t in local if t["start"] <= i + 1 <= t["end"] and depth[i] == t["depth"] + 1], key=lambda t: t["end"] - t["start"], default=None)
                if parent is None: continue
                pm = PROP_RE.match(line)
                if pm:
                    typ = pm.group(1) or pm.group(2)
                    name = pm.group(3)
                    self.props.append({"path": rel, "name": name, "type": typ, "start": i + 1, "end": i + 1, "parent": parent["name"], "ns": ns})
                    self.prop_by_name[name].append(self.props[-1])
                    continue
                if "(" in line:
                    pre = line[:line.index("(")]; mm = re.search(r"([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*$", pre)
                    if not mm or mm.group(1) in CONTROL or len(mm.group(1)) <= 2: continue
                    b = i
                    while b < len(lines) and "{" not in lines[b] and b - i <= 3: b += 1
                    if b >= len(lines): continue
                    dd = 0; last = b
                    for j in range(b, min(len(lines), i + 2500)):
                        dd += lines[j].count("{") - lines[j].count("}"); last = j
                        if dd <= 0: break
                    if dd > 0: last = len(lines) - 1
                    text = "\n".join(lines[i:last + 1])
                    calls = set(CALL_RE.findall(text)) | set(DOT_RE.findall(text))
                    procs = set(SQL_RE.findall(text))
                    m = {"path": rel, "name": mm.group(1), "start": i + 1, "end": last + 1, "parent": parent["name"], "ns": ns, "calls": calls, "procs": procs, "text": text}
                    self.methods.append(m); self.method_by[m["name"]].append(m)
        for p in sorted(self.corpus.rglob("*.sql")):
            rel = str(p.relative_to(self.corpus)); lines = self.text_lines(rel); text = "\n".join(lines)
            name = CREATE_RE.search(text)
            self.sqls.append({"path": rel, "name": name.group(1) if name else p.stem, "start": 1, "end": len(lines), "text": text, "tables": set(re.findall(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+\[?dbo\]?\.?\[?([A-Za-z_]\w+)", text, re.I))})

    def type_at(self, path, start, end):
        for t in self.type_by.values():
            for x in t:
                if x["path"] == path and x["start"] <= start and end <= x["end"]:
                    return x
        return None

    def method_at(self, path, start, end):
        for m in self.method_by.values():
            for x in m:
                if x["path"] == path and x["start"] <= start and end <= x["end"]:
                    return x
        return None

    def sql_for_proc(self, proc):
        key = stem(proc.replace("sp_", ""))
        return [s for s in self.sqls if stem(s["name"].replace("sp_", "")) == key or key in stem(s["path"])]

    def type_target(self, typ):
        typ = re.sub(r"^(?:DbSet|List|ICollection|IEnumerable|IQueryable|HashSet)\s*<\s*(.+?)\s*>$", r"\1", typ)
        typ = typ.split(".")[-1].split("<")[0].strip()
        return self.type_by.get(typ, [])


def blocks(source, path, start, end, min_lines=3):
    lines = source.text_lines(path); out = []; bs = None
    for i in range(start - 1, end):
        if lines[i].strip():
            if bs is None: bs = i
        elif bs is not None:
            be = i
            while be > bs and not lines[be - 1].strip(): be -= 1
            if be - bs + 1 >= min_lines: out.append((bs + 1, be))
            bs = None
    if bs is not None and end - bs >= min_lines: out.append((bs + 1, end))
    return out


def add(store, path, start, end, kind, meta, relation=None, seed=None):
    if start is None or end is None or end < start or end - start + 1 < 3 or end - start + 1 > 500:
        return None
    key = (path, start, end)
    if key in store: return store[key]
    u = store[key] = {"path": path, "start_line": start, "last_line": end, "size": end - start + 1, "kind": kind, "meta": meta or {}, "relation": relation, "seed": seed}
    return u


def expand(case, items, refs: RefIndex, source: Source):
    question = case["question"]; qt = qterms(question); qs = set(qt); intent = intent_of(question)
    store = {}
    # Initial candidate units: classes/methods + members/blocks; keep all for scoring.
    for item in items:
        path = item["path"]; s = item["start_line"]; e = item["last_line"]; meta = item.get("metadata") or {}; st = meta.get("symbol_type")
        if item.get("rg") or st == "value" or e - s + 1 <= 2: continue
        if path.endswith(".sql"):
            add(store, path, s, e, "sql", meta, ("initial", item["call"]), None)
            continue
        add(store, path, s, e, st or "unit", meta, ("initial", item["call"]), None)
        if st == "class" or e - s + 1 > 200:
            for d in source.declarations(path):
                if d["start"] < s or d["end"] > e or d.get("parent") != meta.get("symbol_name"): continue
                add(store, path, d["start"], d["end"], d["kind"], {"symbol_name": d["name"], "symbol_type": d["kind"], "scope": d.get("parent")}, ("member", item["call"]), None)
                if d["kind"] == "method" and d["end"] - d["start"] + 1 > 80:
                    for bs, be in blocks(source, path, d["start"], d["end"]):
                        add(store, path, bs, be, "block", {"symbol_name": f"{d['name']}#{bs}", "symbol_type": "block", "scope": d.get("parent")}, ("block", d["name"]), None)
                        add(store, path, d["start"], be, "prefix", {"symbol_name": f"{d['name']}#p{be}", "symbol_type": "prefix", "scope": d.get("parent")}, ("prefix", d["name"]), None)
            first = min((d["start"] for d in source.declarations(path) if d.get("parent") == meta.get("symbol_name") and d["kind"] == "method"), default=s + 40)
            add(store, path, s, min(e, max(s + 2, first - 1)), "header", {"symbol_name": meta.get("symbol_name"), "symbol_type": "header"}, ("header", item["call"]), None)
        elif st == "function" and e - s + 1 > 80:
            for bs, be in blocks(source, path, s, e):
                add(store, path, bs, be, "block", {"symbol_name": f"{meta.get('symbol_name') or ''}#{bs}", "symbol_type": "block"}, ("block", meta.get("symbol_name")), None)
                add(store, path, s, be, "prefix", {"symbol_name": f"{meta.get('symbol_name') or ''}#p{be}", "symbol_type": "prefix"}, ("prefix", meta.get("symbol_name")), None)

    # Seed units: strongest initial units plus exact code-symbol declarations.
    def base_score(u):
        names = {stem(t) for t in tokens(u["meta"].get("symbol_name") or "")} | {stem(t) for t in tokens(u["path"])}
        body = {stem(t) for t in tokens("\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]]))}
        return len(qs & (names | body)) / max(1, len(qs)) + (0.8 if any(len(n) >= 6 and stem(n) in names for n in code_tokens(question)) else 0)
    seeds = sorted(store.values(), key=lambda u: (-base_score(u), u["size"]))[:12]
    for name in code_tokens(question):
        if len(name) < 6: continue
        for t in refs.type_by.get(name, []):
            u = add(store, t["path"], t["start"], t["end"], "type", {"symbol_name": t["name"], "symbol_type": "type"}, ("exact_symbol", name), name)
            if u: seeds.append(u)
        for m in refs.method_by.get(name, []):
            u = add(store, m["path"], m["start"], m["end"], "method", {"symbol_name": m["name"], "symbol_type": "method", "scope": m.get("parent")}, ("exact_symbol", name), name)
            if u: seeds.append(u)

    def relevant_target(name):
        nt = {stem(t) for t in tokens(name)}
        return bool(nt & qs) or any(x in name.lower() for x in ("employee", "organization", "context", "sql", "proc"))

    queue = [(u, 0) for u in seeds]
    seen = set()
    while queue:
        u, depth = queue.pop(0)
        if depth >= 2 or not u.get("seed") and base_score(u) < 0.15: continue
        key = (u["path"], u["start_line"], u["last_line"])
        if key in seen: continue
        seen.add(key)
        typ = refs.type_at(u["path"], u["start_line"], u["last_line"])
        meth = refs.method_at(u["path"], u["start_line"], u["last_line"])
        if typ:
            first = min([m["start"] for m in refs.methods if m["path"] == typ["path"] and m["parent"] == typ["name"]], default=typ["start"] + 40)
            add(store, typ["path"], typ["start"], min(typ["end"], max(typ["start"] + 2, first - 1)), "header", {"symbol_name": typ["name"], "symbol_type": "header"}, ("type_header", typ["name"]), u)
            for p in [p for p in refs.props if p["path"] == typ["path"] and p["parent"] == typ["name"]]:
                if not (set(tokens(p["name"])) & qs or p["name"] in ("Employees", "EmployeeDetails", "Context")):
                    continue
                for t in refs.type_target(p["type"]):
                    if not t["path"].startswith(("DBContexts/", "Services/")): continue
                    v = add(store, t["path"], t["start"], min(t["end"], t["start"] + 80), "entity", {"symbol_name": t["name"], "symbol_type": "type"}, ("property_type", p["name"]), u)
                    if v: queue.append((v, depth + 1))
        if meth:
            for call in sorted(meth["calls"]):
                if HELPER.match(call) and not (set(tokens(call)) & qs): continue
                if not (relevant_target(call) or "write" in intent or "flow" in intent): continue
                for m in refs.method_by.get(call, []):
                    v = add(store, m["path"], m["start"], m["end"], "method", {"symbol_name": m["name"], "symbol_type": "method", "scope": m.get("parent")}, ("call", call), u)
                    if v:
                        queue.append((v, depth + 1))
                        if m["end"] - m["start"] + 1 > 80:
                            for bs, be in blocks(source, m["path"], m["start"], m["end"]):
                                add(store, m["path"], bs, be, "block", {"symbol_name": f"{m['name']}#{bs}", "symbol_type": "block"}, ("call_block", call), u)
            for proc in sorted(meth["procs"]):
                for s in refs.sql_for_proc(proc):
                    for bs, be in blocks(source, s["path"], s["start"], s["end"], 5):
                        add(store, s["path"], bs, be, "sql", {"symbol_name": f"{s['name']}#{bs}", "symbol_type": "sql"}, ("sql_proc", proc), u)
                    if s["end"] - s["start"] + 1 <= 140:
                        add(store, s["path"], s["start"], s["end"], "sql", {"symbol_name": s["name"], "symbol_type": "sql"}, ("sql_proc_full", proc), u)
            for pn in set(re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", meth["text"])):
                for p in refs.prop_by_name.get(pn, []):
                    if not (set(tokens(pn)) & qs or pn in ("Employees", "EmployeeDetails", "Context")): continue
                    for t in refs.type_target(p["type"]):
                        if not t["path"].startswith(("DBContexts/", )): continue
                        v = add(store, t["path"], t["start"], min(t["end"], t["start"] + 80), "entity", {"symbol_name": t["name"], "symbol_type": "type"}, ("identifier_type", pn), u)
                        if v: queue.append((v, depth + 1))
    # Score with query, intent, relation and structural granularity.
    docs = {}; df = collections.Counter()
    for k, u in store.items():
        body = {stem(t) for t in tokens("\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]]))}
        names = {stem(t) for t in tokens(u["meta"].get("symbol_name") or "")} | {stem(t) for t in tokens(u["path"])}
        u["body"], u["names"] = body, names; docs[k] = body | names
        for t in docs[k]: df[t] += 1
    n = max(1, len(store)); w = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in qs}; total = sum(w.values()) or 1
    for u in store.values():
        nh = sum(w[t] for t in qs if t in u["names"]); bh = sum(w[t] for t in qs if t in u["body"])
        symbol = (u["meta"].get("symbol_name") or "").lower()
        pathstem = Path(u["path"]).stem.lower()
        exact = 1.2 if any(
            len(nm) >= 6 and (nm.lower() == symbol or (u["kind"] == "sql" and nm.lower() in pathstem))
            for nm in code_tokens(question)
        ) else 0
        text = "\n".join(source.text_lines(u["path"])[u["start_line"] - 1:u["last_line"]])
        action = 0.0
        if "write" in intent and WRITE_RE.search(text): action += 0.8
        if "read" in intent and READ_RE.search(text): action += 0.5
        if "sql" in intent and (u["kind"] == "sql" or SQL_RE.search(text)): action += 1.0
        if u["kind"] in ("entity", "header") and (set(u["names"]) & qs): action += 0.5
        rel = 0.45 if u["relation"] and u["relation"][0] in ("call", "sql_proc") else (0.25 if u["relation"] else 0.0)
        size_prior = 0.4 if 5 <= u["size"] <= 180 else (0.15 if u["size"] < 5 else 0.1)
        u["score"] = (0.8 * nh + 0.5 * bh) / total + exact + action + rel + size_prior
        u["exact"] = exact > 0
        u["initial"] = bool(u.get("relation") and u["relation"][0] in ("initial", "member"))
        u["type_context"] = bool(u["kind"] in ("type", "header") and any(
            p["parent"] == u["meta"].get("symbol_name") and p["type"] in ("CoreDBContext", "HRMLocalContext") for p in refs.props
        ))
        if u["kind"] == "entity" and not (u["names"] & qs):
            u["score"] -= 0.8
        if u["kind"] in ("type", "header") and any(p["parent"] == u["meta"].get("symbol_name") and p["type"] in ("CoreDBContext", "HRMLocalContext") for p in refs.props):
            u["score"] += 1.0
        if u["kind"] == "prefix": u["score"] += 0.25
        if u["kind"] == "block": u["score"] += 0.1
    return store, intent


def select(store, budget=10):
    order = sorted(store.values(), key=lambda u: (-u["score"], u["size"], u["path"], u["start_line"]))
    out = []; seen = set(); per_file = collections.Counter(); per_seed = collections.Counter()
    def take(u):
        k = (u["path"], u["start_line"], u["last_line"])
        if k in seen or len(out) >= budget: return
        if any(x["path"] == u["path"] and min(x["last_line"], u["last_line"]) - max(x["start_line"], u["start_line"]) + 1 > 0.7 * min(x["size"], u["size"]) for x in out): return
        out.append(u); seen.add(k); per_file[u["path"]] += 1
    # Preserve exact/initial evidence before reference distractors.
    exact_type_taken = False
    for u in order:
        if u.get("exact") and u["kind"] in ("method", "function", "sql", "type"):
            if u["kind"] == "type":
                if exact_type_taken or not u.get("type_context"): continue
                exact_type_taken = True
            take(u)
    for u in order:
        if len(out) >= budget: break
        k = (u["path"], u["start_line"], u["last_line"])
        if k in seen: continue
        if any(x["path"] == u["path"] and min(x["last_line"], u["last_line"]) - max(x["start_line"], u["start_line"]) + 1 > 0.7 * min(x["size"], u["size"]) for x in out): continue
        seed = u.get("seed")
        sk = (seed["path"], seed["start_line"]) if isinstance(seed, dict) else ("global",)
        if per_seed[sk] >= 4 or per_file[u["path"]] >= 3: continue
        out.append(u); seen.add(k); per_file[u["path"]] += 1; per_seed[sk] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True); ap.add_argument("--probe", type=Path, required=True)
    ap.add_argument("--receipt", type=Path, required=True); ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True); ap.add_argument("--ids")
    ap.add_argument("--token-counter", type=Path); ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--token-budget", type=int, default=3716)
    a = ap.parse_args()
    probe = json.loads(a.probe.read_text()); req = json.loads(a.receipt.read_text())
    allowed = set(a.ids.split(",")) if a.ids else None
    cases = {c["id"]: c for c in json.loads(a.cases.read_text())["cases"] if allowed is None or c["id"] in allowed}
    pool = {}
    for q in probe["queries"]:
        pool.setdefault(q["id"], []).extend({**it, "call": q["call"], "rank": i + 1, "rg": False} for i, it in enumerate(q["items"]))
    refs = RefIndex(a.root); source = Source(a.root); results = []
    for cid, case in cases.items():
        store, intent = expand(case, pool.get(cid, []), refs, source)
        chosen = select(store)
        if a.token_counter and a.tokenizer:
            chosen = trim_tokens(chosen, source, a.token_counter, a.tokenizer, a.token_budget)
        g = gold_for(case); hit = matched(chosen, g); st = strict_hits(chosen, g); text = source.pack_text(chosen)
        results.append({
            "id": cid, "gold_spans": len(g), "selected_spans": len(hit), "recall": len(hit)/len(g) if g else None,
            "strict_spans": len(st), "strict_recall": len(st)/len(g) if g else None,
            "intent": sorted(intent), "tokens": None,
            "units": [{"path": u["path"], "start_line": u["start_line"], "last_line": u["last_line"], "size": u["size"], "kind": u["kind"], "symbol": u["meta"].get("symbol_name"), "score": round(u["score"], 6), "relation": u["relation"], "seed": ((u["seed"]["path"], u["seed"]["start_line"]) if isinstance(u.get("seed"), dict) else (u["seed"] if u.get("seed") else None))} for u in chosen],
            "chars": len(text),
        })
    if a.token_counter and a.tokenizer:
        counts = run_counter(a.token_counter, a.tokenizer, [source.pack_text([{"path": u["path"], "start_line": u["start_line"], "last_line": u["last_line"]} for u in r["units"]]) for r in results])
        for r, n in zip(results, counts): r["tokens"] = n
    def mean(k):
        v=[r[k] for r in results if r[k] is not None]; return sum(v)/len(v) if v else None
    out={"schema":"rag-refint/v1","policy":"reference","config":{"uses_gold_in_runtime":False,"token_budget":a.token_budget},"identity":{"probe_sha256":sha256_file(a.probe),"requal_receipt_sha256":sha256_file(a.receipt),"cases_sha256":sha256_file(a.cases),"corpus_manifest_sha256":sha256_file(a.root/'.zvec-grep'/'manifest.json')},"summary":{"cases":len(results),"gold_spans":sum(r["gold_spans"] for r in results),"selected_spans":sum(r["selected_spans"] for r in results),"strict_spans":sum(r["strict_spans"] for r in results),"macro_recall":mean("recall"),"macro_strict":mean("strict_recall"),"tokens_mean":mean("tokens"),"source_files_read":len(source.reads),"source_bytes_read":source.read_bytes},"results":results}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(out["summary"],indent=1)); return 0

if __name__ == "__main__": raise SystemExit(main())
