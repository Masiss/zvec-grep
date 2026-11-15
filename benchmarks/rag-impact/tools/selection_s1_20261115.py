#!/usr/bin/env python3
"""RAG-S1 Selection feasibility prototype on a frozen v3 candidate pool.

The policy never reads `source_refs`, gold anchors or evaluation labels. Gold is
used only by `--policy oracle` (evaluation ceiling) and by metric scoring.

Policies:
- exact:      exact-symbol declarations for question code tokens.
- structural: exact plus declarations/blocks scored from the frozen pool,
              structural metadata, query terms and engine rank.
- oracle:     evaluation-only selectable ceiling (NOT a runtime policy).
- baseline:   fixed fts_code_tokens top-10 from the frozen probe.

Usage:
  selection_s1_20261115.py --root CORPUS --probe PROBE --receipt REQUAL \
    --cases DEV --out RECEIPT --policy structural
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
TYPE_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|partial|abstract|sealed|readonly|unsafe|new)\s+)*"
    r"(?:class|struct|interface|record|enum)\s+([A-Za-z_]\w*)"
)
PROP_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|required|new|sealed|abstract)\s+)*"
    r"[\w<>,\[\]?\.]+\s+([A-Za-z_]\w*)\s*\{\s*(?:get|set|init)"
)
CONTROL = {
    "if", "for", "foreach", "while", "switch", "catch", "using", "lock", "return",
    "new", "sizeof", "typeof", "nameof", "else",
}
STOP = set(
    "la là co có cua của va và nhung những nao nào gi gì di đi qua dau đâu khi thì cho trong ra sao "
    "nhu như the thế mot một cac các duoc được bi bị tu từ voi với hay khong không phai phải moi mọi "
    "lan lần sua sửa doi đổi the thể anh ảnh huong hưởng ngoai ngoài vao vào noi nơi nay này do đó o ở "
    "tai tại bang bằng de để neu nếu vi vì nen nên rang rằng cung cũng chi chỉ con còn da đã dang đang "
    "se sẽ boi bởi theo tren trên duoi dưới giua giữa sau truoc trước khien khiến lam làm cai cái chay chạy"
    .split()
)
CALL_WEIGHT = {
    "hybrid_question": 1.0,
    "fts_question": 0.95,
    "fts_code_tokens": 0.9,
    "vector_question": 0.8,
    "fts_first_phrase": 0.35,
    "rg_last_phrase": 0.25,
    "rg_code_tokens": 0.25,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    if not text:
        return []
    text = strip_accents(text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [t for t in text.split() if len(t) > 1]


def stem(token: str) -> str:
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("ed") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def query_terms(question: str) -> list[str]:
    return [stem(t) for t in tokens(question) if t not in STOP]


def code_tokens(question: str) -> list[str]:
    out = []
    for word in WORD_RE.findall(question):
        if len(word) < 3:
            continue
        if word[0].isupper() or "_" in word or any(ch.isupper() for ch in word[1:]):
            out.append(word)
    return out[:2]


def overlap(a: int, b: int, c: int, d: int) -> bool:
    return a <= d and c <= b


def gold_for(case: dict) -> list[tuple[str, int, int]]:
    out = []
    for ref in case.get("source_refs", []):
        for span in ref.get("spans", []):
            out.append((ref["path"], span["start_line"], span["end_line"]))
    for ref in case.get("source_evidence", []):
        if "spans" in ref:
            for span in ref["spans"]:
                out.append((ref["path"], span["start_line"], span["end_line"]))
        else:
            out.append((ref["path"], ref["start_line"], ref["end_line"]))
    return out


def matched(units, gold):
    return {
        i for i, (path, start, end) in enumerate(gold)
        if any(
            u["path"] == path and u.get("start_line") is not None and u.get("last_line") is not None
            and overlap(u["start_line"], u["last_line"], start, end)
            for u in units
        )
    }


def strict_hits(units, gold):
    hit = set()
    for i, (path, start, end) in enumerate(gold):
        glen = end - start + 1
        for u in units:
            if u["path"] != path or u.get("start_line") is None or u.get("last_line") is None:
                continue
            if not overlap(u["start_line"], u["last_line"], start, end):
                continue
            ilen = u["last_line"] - u["start_line"] + 1
            inter = min(u["last_line"], end) - max(u["start_line"], start) + 1
            if inter / glen >= 0.5 and ilen <= max(100, 3 * glen):
                hit.add(i)
                break
    return hit


def selectable(units, gold, budget=10):
    masks = []
    for u in units:
        mask = 0
        for i, (path, start, end) in enumerate(gold):
            if u["path"] == path and overlap(u["start_line"], u["last_line"], start, end):
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


class Source:
    def __init__(self, root: Path):
        self.root = root
        self.lines: dict[str, list[str]] = {}
        self.decls: dict[str, list[dict]] = {}
        self.reads: set[str] = set()
        self.read_bytes = 0

    def text_lines(self, path: str) -> list[str]:
        if path not in self.lines:
            data = (self.root / path).read_text(encoding="utf-8", errors="replace")
            self.reads.add(path)
            self.read_bytes += len(data.encode())
            self.lines[path] = data.splitlines()
        return self.lines[path]

    def declarations(self, path: str) -> list[dict]:
        if path in self.decls:
            return self.decls[path]
        lines = self.text_lines(path)
        depth = []
        d = 0
        for line in lines:
            depth.append(d)
            d += line.count("{") - line.count("}")
        out = []
        for i, line in enumerate(lines):
            tm, pm = TYPE_RE.match(line), PROP_RE.match(line)
            mm = None
            if "(" in line and not tm:
                pre = line[: line.index("(")]
                m = re.search(r"([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*$", pre)
                if (
                    m
                    and m.group(1) not in CONTROL
                    and len(m.group(1)) > 2
                    and not re.match(r"^\s*(?:if|for|foreach|while|switch|catch|using|lock|return|new)\b", line)
                ):
                    mm = m
            if not (tm or pm or mm):
                continue
            kind = "type" if tm else ("property" if pm else "method")
            name = (tm or pm or mm).group(1)
            b = i
            while b < len(lines) and "{" not in lines[b] and b - i <= 3:
                b += 1
            if b >= len(lines):
                continue
            dd = 0
            last = b
            for j in range(b, min(len(lines), i + 2500)):
                dd += lines[j].count("{") - lines[j].count("}")
                last = j
                if dd <= 0:
                    break
            if dd > 0:
                last = len(lines) - 1
            out.append(
                {
                    "name": name,
                    "kind": kind,
                    "start": i + 1,
                    "end": last + 1,
                    "depth": depth[i],
                }
            )
        types = [x for x in out if x["kind"] == "type"]
        for x in out:
            parents = [
                t for t in types
                if t is not x and t["start"] < x["start"] <= t["end"] and x["depth"] == t["depth"] + 1
            ]
            x["parent"] = min(parents, key=lambda t: t["end"] - t["start"])["name"] if parents else None
        self.decls[path] = out
        return out

    def blocks(self, path: str, start: int, end: int) -> list[tuple[int, int]]:
        lines = self.text_lines(path)
        out = []
        block_start = None
        for i in range(start - 1, end):
            if lines[i].strip():
                if block_start is None:
                    block_start = i
            elif block_start is not None:
                bstart, bend = block_start + 1, i
                while bend > bstart and not lines[bend - 1].strip():
                    bend -= 1
                if bend - bstart + 1 >= 3:
                    out.append((bstart, bend))
                block_start = None
        if block_start is not None and end - block_start >= 3:
            out.append((block_start + 1, end))
        return out

    def pack_text(self, units: list[dict]) -> str:
        parts = []
        for u in units:
            lines = self.text_lines(u["path"])
            parts.append(f"// {u['path']}:{u['start_line']}-{u['last_line']}")
            parts.append("\n".join(lines[u["start_line"] - 1 : u["last_line"]]))
        return "\n".join(parts)


def load_pool(probe: dict, receipt: dict) -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {}
    for q in probe["queries"]:
        for i, item in enumerate(q["items"]):
            pool.setdefault(q["id"], []).append(
                {**item, "call": q["call"], "rank": i + 1, "rg": False}
            )
    for row in receipt.get("rg_calls", []):
        for i, unit in enumerate(row["units"]):
            pool.setdefault(row["id"], []).append(
                {
                    **unit,
                    "score": 0.0,
                    "metadata": {},
                    "matched_by": "rg",
                    "call": row["call"],
                    "rank": i + 1,
                    "rg": True,
                }
            )
    return pool


def build_candidates(case: dict, items: list[dict], source: Source):
    question = case["question"]
    qlist = query_terms(question)
    qset = set(qlist)
    candidates: dict[tuple, dict] = {}
    classes: dict[tuple, set] = {}

    def add(path, start, end, meta, call, rank, ckey, kind):
        if start is None or end is None or end < start:
            return
        size = end - start + 1
        if size < 3 or size > 400:
            return
        key = (path, start, end)
        c = candidates.get(key)
        if c is None:
            c = candidates[key] = {
                "path": path,
                "start_line": start,
                "last_line": end,
                "size": size,
                "meta": meta or {},
                "calls": set(),
                "best_rank": rank,
                "ckey": ckey,
                "kind": kind,
            }
        c["calls"].add(call)
        c["best_rank"] = min(c["best_rank"], rank)

    for item in items:
        path = item["path"]
        start, end = item["start_line"], item["last_line"]
        meta = item.get("metadata") or {}
        stype = meta.get("symbol_type")
        weight = CALL_WEIGHT.get(item["call"], 0.3)
        rank = item["rank"] / max(0.1, weight)
        if item.get("rg"):
            # Managed rg line hits are not used as Selection candidates; they
            # lack structural provenance and would only expand into guessed
            # enclosing declarations.
            continue
        if stype == "value" or (end - start + 1) <= 2:
            continue
        if path.endswith(".sql"):
            add(path, start, end, meta, item["call"], rank, (path, path), "sql")
            continue
        if stype == "class" or (end - start + 1) > 200:
            ckey = (path, meta.get("symbol_name") or (start, end))
            if ckey not in classes:
                classes[ckey] = set(stem(t) for t in tokens("\n".join(source.text_lines(path)[start - 1 : end])))
            for d in source.declarations(path):
                if d["start"] < start or d["end"] > end:
                    continue
                if d.get("parent") == meta.get("symbol_name"):
                    add(path, d["start"], d["end"], {"symbol_name": d["name"], "symbol_type": d["kind"], "scope": d.get("parent")}, item["call"], rank, ckey, d["kind"])
                    if d["kind"] == "method" and d["end"] - d["start"] + 1 > 80:
                        for bs, be in source.blocks(path, d["start"], d["end"]):
                            add(path, bs, be, {"symbol_name": f"{d['name']}#{bs}", "symbol_type": "block", "scope": d.get("parent")}, item["call"], rank, ckey, "block")
            add(path, start, end, meta, item["call"], rank * 2, ckey, "type")
        else:
            ckey = None
            for d in source.declarations(path):
                if d["kind"] == "type" and d["start"] <= start <= d["end"]:
                    ckey = (path, d["name"])
                    if ckey not in classes:
                        classes[ckey] = set(stem(t) for t in tokens("\n".join(source.text_lines(path)[d["start"] - 1 : d["end"]])))
                    break
            add(path, start, end, meta, item["call"], rank, ckey, stype or "unit")
            if stype == "function" and end - start + 1 > 80:
                for bs, be in source.blocks(path, start, end):
                    add(path, bs, be, {"symbol_name": f"{meta.get('symbol_name') or ''}#{bs}", "symbol_type": "block", "scope": meta.get("scope")}, item["call"], rank, ckey, "block")

    docs = {}
    df = collections.Counter()
    for key, c in candidates.items():
        lines = source.text_lines(c["path"])
        body = set(stem(t) for t in tokens("\n".join(lines[c["start_line"] - 1 : c["last_line"]])))
        names = (
            set(stem(t) for t in tokens(c["meta"].get("symbol_name") or ""))
            | set(stem(t) for t in tokens(c["path"]))
            | set(stem(t) for t in tokens(c["meta"].get("scope") or ""))
        )
        c["body"], c["names"] = body, names
        docs[key] = body | names
        for t in docs[key]:
            df[t] += 1
    n = max(1, len(candidates))
    weight = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in qset}
    total = sum(weight.values()) or 1
    for ckey, ctext in classes.items():
        classes[ckey] = sum(weight[t] for t in qset if t in ctext)
    for c in candidates.values():
        name_hit = sum(weight[t] for t in qset if t in c["names"])
        body_hit = sum(weight[t] for t in qset if t in c["body"])
        exact = 0.8 if any(t in c["names"] for t in qlist if len(t) >= 6) else 0.0
        size_prior = 0.5 if 3 <= c["size"] <= 160 else (0.15 if c["size"] < 3 else 0.1)
        rank_prior = 0.5 * max(0.0, 1.0 - (c["best_rank"] - 1) / 50.0)
        class_lex = classes.get(c["ckey"], 0.0) / total
        c["lex"] = (0.9 * name_hit + 0.6 * body_hit) / total
        c["class_lex"] = class_lex
        c["score"] = 0.6 * class_lex + 2.0 * c["lex"] + exact + size_prior + rank_prior + 0.10 * len(c["calls"])
    return candidates, qlist


def exact_candidates(candidates, question):
    code_groups = [
        {stem(t) for t in tokens(name) if len(t) >= 4}
        for name in code_tokens(question)
    ]
    code_groups = [g for g in code_groups if g]
    if not code_groups:
        return []
    out = []
    for c in candidates.values():
        if c["kind"] in ("value", "property"):
            continue
        sym = {stem(t) for t in tokens((c["meta"].get("symbol_name") or "").replace("#", " "))}
        path = {stem(t) for t in tokens(c["path"])}
        if any(g <= sym for g in code_groups) or (c["kind"] == "sql" and any(g <= path for g in code_groups)):
            out.append(c)
    return out


def pick(units, budget=10, token_budget=3716, chars_per_token=4.0, cap_class=2, cap_file=3):
    ordered = sorted(units, key=lambda c: (-c["score"], c["size"], c["path"], c["start_line"]))
    out = []
    seen = set()
    per_class = collections.Counter()
    per_file = collections.Counter()
    chars = 0
    for c in ordered:
        if len(out) >= budget:
            break
        key = (c["path"], c["start_line"], c["last_line"])
        if key in seen:
            continue
        if any(
            x["path"] == c["path"]
            and min(x["last_line"], c["last_line"]) - max(x["start_line"], c["start_line"]) + 1
            > 0.7 * min(x["size"], c["size"])
            for x in out
        ):
            continue
        if per_class[c["ckey"]] >= cap_class or per_file[c["path"]] >= cap_file:
            continue
        projected = chars + c["size"] * 40
        if projected / chars_per_token > token_budget:
            continue
        out.append(c)
        seen.add(key)
        per_class[c["ckey"]] += 1
        per_file[c["path"]] += 1
        chars = projected
    return out


def run_counter(counter: Path, tokenizer: Path, texts: list[str]) -> list[int]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"texts": texts}, fh)
        tmp = Path(fh.name)
    try:
        proc = subprocess.run(
            [str(counter), str(tokenizer), str(tmp)],
            check=True, capture_output=True, text=True,
        )
        return json.loads(proc.stdout)["counts"]
    finally:
        tmp.unlink(missing_ok=True)


def trim_tokens(units: list[dict], source: Source, counter: Path, tokenizer: Path, budget: int) -> list[dict]:
    if not units:
        return units
    counts = run_counter(counter, tokenizer, [source.pack_text([u]) for u in units])
    for u, n in zip(units, counts):
        u["tokens"] = n
    while units and sum(u["tokens"] for u in units) > budget:
        units.pop()
    return units


def evaluate(case, units, source, policy, ceiling=None):
    gold = gold_for(case)
    hit = matched(units, gold)
    strict = strict_hits(units, gold)
    text = source.pack_text(units)
    return {
        "id": case["id"],
        "policy": policy,
        "gold_spans": len(gold),
        "selected_spans": len(hit),
        "recall": len(hit) / len(gold) if gold else None,
        "strict_spans": len(strict),
        "strict_recall": len(strict) / len(gold) if gold else None,
        "selectable_oracle": ceiling if ceiling is not None else (len(hit) if policy == "oracle" else selectable(units, gold, 10)),
        "units": [
            {
                "path": u["path"],
                "start_line": u["start_line"],
                "last_line": u["last_line"],
                "size": u["size"],
                "score": round(u.get("score", 0.0), 6),
                "symbol": u["meta"].get("symbol_name"),
                "kind": u["kind"],
                "calls": sorted(u.get("calls", [])),
            }
            for u in units
        ],
        "chars": len(text),
        "token_proxy": round(len(text) / 4.0, 2),
        "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--receipt", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--policy", default="structural", choices=["exact", "structural", "oracle", "baseline"])
    ap.add_argument("--token-counter", type=Path)
    ap.add_argument("--tokenizer", type=Path)
    ap.add_argument("--budget", type=int, default=10)
    ap.add_argument("--token-budget", type=int, default=3716)
    ap.add_argument("--ids", help="comma-separated case ids")
    args = ap.parse_args()

    probe = json.loads(args.probe.read_text())
    receipt = json.loads(args.receipt.read_text())
    allowed = set(args.ids.split(",")) if args.ids else None
    cases = {
        c["id"]: c
        for c in json.loads(args.cases.read_text())["cases"]
        if c.get("question")
        and (allowed is None or c["id"] in allowed)
    }
    pool = load_pool(probe, receipt)
    source = Source(args.root)
    results = []
    for cid, case in cases.items():
        items = pool.get(cid, [])
        candidates, qlist = build_candidates(case, items, source)
        if args.policy == "baseline":
            base = [q for q in probe["queries"] if q["id"] == cid and q["call"] == "fts_code_tokens"]
            selected = []
            if base:
                for item in base[0]["items"][: args.budget]:
                    selected.append(
                        {
                            **item,
                            "size": item["last_line"] - item["start_line"] + 1,
                            "meta": item.get("metadata") or {},
                            "kind": (item.get("metadata") or {}).get("symbol_type") or "unit",
                            "calls": {"fts_code_tokens"},
                            "ckey": (item["path"], item["start_line"]),
                            "score": item.get("score", 0.0),
                        }
                    )
        elif args.policy == "exact":
            selected = pick(exact_candidates(candidates, case["question"]), args.budget, args.token_budget)
        else:
            eligible = [
                c for c in candidates.values()
                if c["lex"] > 0 or c["kind"] == "sql" or c["best_rank"] <= 15
            ]
            if args.policy == "oracle":
                gold = gold_for(case)
                # Evaluation-only: greedy by marginal gold coverage, then selectable ceiling.
                selected = []
                covered = 0
                while len(selected) < args.budget:
                    best = None
                    best_gain = 0
                    for c in eligible:
                        mask = 0
                        for i, (path, start, end) in enumerate(gold):
                            if c["path"] == path and overlap(c["start_line"], c["last_line"], start, end):
                                mask |= 1 << i
                        gain = bin(mask & ~covered).count("1")
                        if gain > best_gain:
                            best, best_gain = c, gain
                    if not best:
                        break
                    selected.append(best)
                    for i, (path, start, end) in enumerate(gold):
                        if best["path"] == path and overlap(best["start_line"], best["last_line"], start, end):
                            covered |= 1 << i
            else:
                selected = pick(eligible, args.budget, args.token_budget)
        ceiling = selectable(
            [c for c in candidates.values() if c["lex"] > 0 or c["kind"] == "sql" or c["best_rank"] <= 15],
            gold_for(case),
            10,
        )
        if args.token_counter and args.tokenizer and args.policy in ("exact", "structural", "oracle"):
            selected = trim_tokens(selected, source, args.token_counter, args.tokenizer, args.token_budget)
        results.append(evaluate(case, selected, source, args.policy, ceiling))

    # Batch count the packed texts with the frozen tokenizer when available.
    if args.token_counter and args.tokenizer and results:
        counts = run_counter(args.token_counter, args.tokenizer, [r["text"] for r in results])
        for r, count in zip(results, counts):
            r["tokens"] = count
    for r in results:
        r.pop("text", None)
    def mean(key):
        vals = [r[key] for r in results if r[key] is not None]
        return sum(vals) / len(vals) if vals else None
    receipt_out = {
        "schema": "rag-selection-s1/v1",
        "policy": args.policy,
        "config": {
            "budget": args.budget,
            "token_budget": args.token_budget,
            "uses_gold_in_runtime": args.policy == "oracle",
            "source": "frozen raw probe + requal rg receipt",
        },
        "identity": {
            "probe_sha256": sha256_file(args.probe),
            "requal_receipt_sha256": sha256_file(args.receipt),
            "cases_sha256": sha256_file(args.cases),
            "corpus_manifest_sha256": sha256_file(args.root / ".zvec-grep" / "manifest.json"),
        },
        "summary": {
            "cases": len(results),
            "gold_spans": sum(r["gold_spans"] for r in results),
            "selected_spans": sum(r["selected_spans"] for r in results),
            "strict_spans": sum(r["strict_spans"] for r in results),
            "selectable_oracle": sum(r["selectable_oracle"] for r in results),
            "macro_recall": mean("recall"),
            "macro_strict": mean("strict_recall"),
            "tokens_mean": mean("tokens") if any("tokens" in r for r in results) else None,
            "token_proxy_mean": mean("token_proxy"),
            "source_files_read": len(source.reads),
            "source_bytes_read": source.read_bytes,
            "determinism_sha256": sha256_bytes(
                json.dumps([(r["id"], r["units"]) for r in results], sort_keys=True).encode()
            ),
        },
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt_out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(receipt_out["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
