#!/usr/bin/env python3
"""Bounded evidence-refinement experiment on the RAG requalification probe.

Frozen inputs only. This is not a production ranking/Selection change and it
does not touch the index, engine, gold, corpus or held-out data.

Policy (uses no gold):
- start from the raw Discovery top-10 of each call;
- replace a >200-line declaration/container with the best query-token-matching
  declaration parsed from that same file and range (bounded source read);
- prepend at most two exact-symbol declarations returned by the read-only
  symbol API for question code tokens;
- dedupe and cap at 10.

Gates are fixed in the accompanying report before this run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
TYPE_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|partial|abstract|sealed|\s)*"
    r"(?:class|struct|interface|record|enum)\s+(\w+)"
)
METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|sealed|\s)+"
    r"(?:[\w<>,\[\]?\.]+\s+)+(\w+)\s*\("
)
PROP_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|required|\s)+"
    r"[\w<>,\[\]?\.]+\s+(\w+)\s*\{\s*get"
)
CONTAINER_MIN = 200


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokens(text: str) -> set[str]:
    return {w.lower() for w in WORD_RE.findall(text) if len(w) > 1}


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def gold_for(case: dict) -> list[tuple[str, int, int]]:
    return [
        (ref["path"], span["start_line"], span["end_line"])
        for ref in case.get("source_refs", [])
        for span in ref.get("spans", [])
    ]


def matched(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    return {
        index
        for index, (path, start, end) in enumerate(gold)
        if any(
            u["path"] == path
            and u.get("start_line") is not None
            and u.get("last_line") is not None
            and overlap(u["start_line"], u["last_line"], start, end)
            for u in units
        )
    }


def strict(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    hit = set()
    for index, (path, start, end) in enumerate(gold):
        gold_len = end - start + 1
        for u in units:
            if (
                u["path"] == path
                and u.get("start_line") is not None
                and u.get("last_line") is not None
                and overlap(u["start_line"], u["last_line"], start, end)
            ):
                inter = min(u["last_line"], end) - max(u["start_line"], start) + 1
                unit_len = u["last_line"] - u["start_line"] + 1
                if inter / gold_len >= 0.5 and unit_len <= max(100, 3 * gold_len):
                    hit.add(index)
                    break
    return hit


def selectable(units: list[dict], gold: list[tuple[str, int, int]], budget: int = 10) -> int:
    masks = []
    for u in units:
        if u.get("start_line") is None or u.get("last_line") is None:
            continue
        mask = 0
        for index, (path, start, end) in enumerate(gold):
            if u["path"] == path and overlap(u["start_line"], u["last_line"], start, end):
                mask |= 1 << index
        if mask:
            masks.append(mask)
    full = 1 << len(gold)
    best = [budget + 1] * full
    best[0] = 0
    for mask in masks:
        for state in range(full):
            if best[state] + 1 < best[state | mask]:
                best[state | mask] = best[state] + 1
    return max((bin(state).count("1") for state, cost in enumerate(best) if cost <= budget), default=0)


class Parser:
    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, list[str]] = {}
        self.read_files: set[str] = set()
        self.read_bytes = 0

    def lines(self, path: str) -> list[str]:
        if path not in self.cache:
            target = self.root / path
            data = target.read_text(encoding="utf-8", errors="replace")
            self.read_files.add(path)
            self.read_bytes += len(data.encode("utf-8"))
            self.cache[path] = data.splitlines()
        return self.cache[path]

    def declarations(self, path: str, start: int, end: int) -> list[dict]:
        lines = self.lines(path)
        found = []
        for index in range(max(0, start - 1), min(len(lines), end)):
            line = lines[index]
            match = TYPE_RE.match(line) or METHOD_RE.match(line) or PROP_RE.match(line)
            if not match:
                continue
            brace = index
            while brace < len(lines) and "{" not in lines[brace] and brace - index <= 3:
                brace += 1
            if brace >= len(lines) or "{" not in lines[brace]:
                continue
            depth = 0
            last = brace
            for cursor in range(brace, min(len(lines), end)):
                depth += lines[cursor].count("{") - lines[cursor].count("}")
                last = cursor
                if depth <= 0:
                    break
            if depth > 0:
                last = min(len(lines) - 1, end - 1)
            kind = "type" if TYPE_RE.match(line) else ("method" if METHOD_RE.match(line) else "property")
            found.append(
                {
                    "path": path,
                    "start_line": index + 1,
                    "last_line": last + 1,
                    "kind": kind,
                    "text": "\n".join(lines[index : last + 1]),
                }
            )
        return found


def refine_unit(unit: dict, query_tokens: set[str], parser: Parser) -> dict:
    start, last = unit.get("start_line"), unit.get("last_line")
    if start is None or last is None or last - start + 1 <= CONTAINER_MIN:
        return unit
    candidates = parser.declarations(unit["path"], start, last)
    if not candidates:
        return unit
    best = None
    for candidate in candidates:
        score = len(tokens(candidate["text"]) & query_tokens)
        size = candidate["last_line"] - candidate["start_line"] + 1
        # Normalize by size so a class/container that contains every query token
        # does not outrank the declaration that actually matches them.
        density = score / max(1.0, size / 50.0)
        kind_bonus = 0.25 if candidate["kind"] != "type" else 0.0
        key = (density + kind_bonus, score, -size, -candidate["start_line"])
        if score and (best is None or key > best[0]):
            best = (key, candidate)
    if best is None:
        return unit
    candidate = best[1]
    return {
        **{k: v for k, v in unit.items() if k not in ("line_count", "content_chars")},
        "start_line": candidate["start_line"],
        "last_line": candidate["last_line"],
        "line_count": candidate["last_line"] - candidate["start_line"] + 1,
        "refined_from": [start, last],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--symbols", required=True, type=Path)
    parser.add_argument("--dev", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    probe = json.loads(args.probe.read_text())
    symbols = json.loads(args.symbols.read_text())
    cases = {
        c["id"]: c
        for c in json.loads(args.dev.read_text())["cases"]
        if c["id"] in {f"D{i:02d}" for i in range(1, 13)}
    }
    sym_by_case = {row["id"]: row["items"] for row in symbols["queries"]}
    raw_by_case: dict[str, list[dict]] = {}
    for row in probe["queries"]:
        raw_by_case.setdefault(row["id"], []).append(row)

    source = Parser(args.root)
    rows = []
    audit_rows = []
    for cid, case in cases.items():
        gold = gold_for(case)
        q = case["question"]
        qtokens = tokens(q)
        calls = []
        refined_calls = []
        for raw in raw_by_case.get(cid, []):
            top10 = raw["items"][:10]
            refined = []
            seen = set()
            # Exact-symbol declarations for question code tokens come first.
            for item in sym_by_case.get(cid, []):
                if (item.get("line_count") or 10**9) > 200:
                    continue
                key = (item["path"], item["start_line"], item["last_line"])
                if key in seen:
                    continue
                seen.add(key)
                refined.append(
                    {
                        "path": item["path"],
                        "start_line": item["start_line"],
                        "last_line": item["last_line"],
                        "line_count": item["line_count"],
                        "source": "exact_symbol",
                    }
                )
                if len(refined) >= 2:
                    break
            for unit in top10:
                refined_unit = refine_unit(unit, qtokens, source)
                key = (refined_unit["path"], refined_unit["start_line"], refined_unit["last_line"])
                if key in seen:
                    continue
                seen.add(key)
                refined.append(refined_unit)
                if len(refined) >= 10:
                    break
            calls.append(
                {
                    "call": raw["call"],
                    "raw_recall": len(matched(top10, gold)) / len(gold) if gold else None,
                    "raw_strict": len(strict(top10, gold)) / len(gold) if gold else None,
                    "refined_recall": len(matched(refined, gold)) / len(gold) if gold else None,
                    "refined_strict": len(strict(refined, gold)) / len(gold) if gold else None,
                    "raw_chars": sum(u.get("content_chars") or 0 for u in top10),
                    "refined_chars": sum(
                        u.get("content_chars") or (u.get("last_line", 0) - u.get("start_line", 0) + 1) * 40
                        for u in refined
                    ),
                    "raw_lines": sum((u.get("line_count") or 0) for u in top10),
                    "refined_lines": sum((u.get("line_count") or 0) for u in refined),
                }
            )
            refined_calls.append(refined)
        raw_units = [u for raw in raw_by_case.get(cid, []) for u in raw["items"]]
        normalized = []
        seen_norm = set()
        for unit in raw_units:
            start, last = unit.get("start_line"), unit.get("last_line")
            if start is None or last is None or last - start + 1 <= CONTAINER_MIN:
                pieces = [unit]
            else:
                pieces = source.declarations(unit["path"], start, last) or [unit]
            for piece in pieces:
                key = (piece["path"], piece.get("start_line"), piece.get("last_line"))
                if key in seen_norm:
                    continue
                seen_norm.add(key)
                normalized.append(
                    {
                        "path": piece["path"],
                        "start_line": piece.get("start_line"),
                        "last_line": piece.get("last_line"),
                    }
                )
        audit_rows.append(
            {
                "id": cid,
                "gold_spans": len(gold),
                "raw_union": len(matched(raw_units, gold)),
                "raw_selectable10": selectable(raw_units, gold, 10),
                "normalized_union": len(matched(normalized, gold)),
                "normalized_selectable10": selectable(normalized, gold, 10),
                "raw_units": len(raw_units),
                "normalized_units": len(normalized),
                "normalized_miss": [
                    [p, s, e]
                    for (p, s, e), index in zip(gold, range(len(gold)))
                    if index not in matched(normalized, gold)
                ],
            }
        )
        raw_best = max((c["raw_recall"] for c in calls if c["raw_recall"] is not None), default=0.0)
        refined_best = max((c["refined_recall"] for c in calls if c["refined_recall"] is not None), default=0.0)
        strict_best = max((c["refined_strict"] for c in calls if c["refined_strict"] is not None), default=0.0)
        rows.append(
            {
                "id": cid,
                "gold_spans": len(gold),
                "calls": calls,
                "raw_best_recall": raw_best,
                "refined_best_recall": refined_best,
                "refined_best_strict": strict_best,
                "raw_top10_chars_best": max((c["raw_chars"] for c in calls), default=0),
                "refined_top10_chars_best": max((c["refined_chars"] for c in calls), default=0),
                "gold": gold,
                "refined_calls": refined_calls,
            }
        )

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    receipt = {
        "schema": "rag-refine-receipt/v1",
        "policy": {
            "container_min_lines": CONTAINER_MIN,
            "max_symbol_items": 2,
            "max_items": 10,
            "uses_gold": False,
            "source_reads": len(source.read_files),
            "source_read_bytes": source.read_bytes,
        },
        "granularity_audit": {
            "container_min_lines": CONTAINER_MIN,
            "rows": audit_rows,
            "totals": {
                "gold": sum(r["gold_spans"] for r in audit_rows),
                "raw_union": sum(r["raw_union"] for r in audit_rows),
                "raw_selectable10": sum(r["raw_selectable10"] for r in audit_rows),
                "normalized_union": sum(r["normalized_union"] for r in audit_rows),
                "normalized_selectable10": sum(r["normalized_selectable10"] for r in audit_rows),
            },
            "macro": {
                "raw_union": mean([r["raw_union"] / r["gold_spans"] for r in audit_rows]),
                "normalized_union": mean(
                    [r["normalized_union"] / r["gold_spans"] for r in audit_rows]
                ),
            },
        },
        "baseline": {
            "best_recall": mean([r["raw_best_recall"] for r in rows]),
            "best_strict": mean(
                [
                    max((c["raw_strict"] for c in r["calls"] if c["raw_strict"] is not None), default=0.0)
                    for r in rows
                ]
            ),
            "mean_call_chars": mean([c["raw_chars"] for r in rows for c in r["calls"]]),
            "mean_call_lines": mean([c["raw_lines"] for r in rows for c in r["calls"]]),
        },
        "refined": {
            "best_recall": mean([r["refined_best_recall"] for r in rows]),
            "best_strict": mean([r["refined_best_strict"] for r in rows]),
            "mean_call_chars": mean([c["refined_chars"] for r in rows for c in r["calls"]]),
            "mean_call_lines": mean([c["refined_lines"] for r in rows for c in r["calls"]]),
        },
        "determinism_sha256": sha256_text(
            json.dumps([[r["id"], r["refined_best_recall"], r["refined_best_strict"]] for r in rows])
        ),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: receipt[k] for k in ("baseline", "refined", "policy")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
