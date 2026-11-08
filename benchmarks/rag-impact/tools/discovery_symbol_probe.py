#!/usr/bin/env python3
"""Discovery H1: question symbols + depth-50 + code-aware top-10."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG / "tools"))
import discovery_depth_probe as dd  # noqa: E402

DEV = RAG / "cases" / "dev.json"
B0 = RAG / "results" / "b0-official.json"
OUT = RAG / "results" / "discovery-symbol-probe.json"
WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)


def question_symbols(question: str) -> list[str]:
    result = []
    for word in WORD_RE.findall(question):
        if len(word) < 4 or not (word[0].isalpha() or word[0] == "_"):
            continue
        if (
            "_" in word
            or any(ch.isupper() for ch in word[1:])
            or word.lower().startswith("sp_")
            or word[0].isupper()
        ):
            if word not in result:
                result.append(word)
        if len(result) >= 4:
            break
    return result


def run(cmd: list[str], env: dict) -> str:
    proc = subprocess.run(cmd, cwd=dd.CORPUS, env=env, capture_output=True, text=True, timeout=120)
    return proc.stdout


def main() -> int:
    dev = json.loads(DEV.read_text())
    b0 = json.loads(B0.read_text())
    cases = {case["id"]: case for case in dev["cases"]}
    env = dd.os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(Path.home() / ".local/share/zvec-grep/rollback-20261101T130300Z")
    env["ZVEC_GREP_HOME"] = str(dd.CORPUS.parent / "home")
    env["ZVEC_GREP_MODEL_CACHE"] = str(dd.MODEL_CACHE)
    env["TMPDIR"] = str(dd.CORPUS.parent / "tmp")
    env["ZVEC_GREP_MODE"] = "direct"
    rows = []
    for b0_case in b0["quality"]["dev_real_cases"]:
        case_id = b0_case["id"]
        case = cases[case_id]
        gold = []
        for reference in case.get("source_refs", []):
            for span in reference.get("spans", []):
                gold.append((reference["path"], span["start_line"], span["end_line"]))
        query = case["question"]
        query_terms = {w.lower() for w in WORD_RE.findall(query) if len(w) >= 2}
        units = []
        seen = set()
        calls = []
        started = time.perf_counter()
        for label, cmd, kind in dd.query_calls(query):
            text = run(cmd, env)
            parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
            calls.append({"call": label, "units": len(parsed)})
            for unit in parsed:
                key = (unit["path"], unit["start_line"], unit["end_line"])
                if key not in seen:
                    seen.add(key)
                    units.append(unit)
        symbols = question_symbols(query)
        for symbol in symbols:
            for label, cmd, kind in (
                ("fts_symbol", [dd.BIN, "--fts", symbol, "--limit", "50", "--preview", "detail"], "fts"),
                ("rg_symbol", [dd.BIN, "--rg", "-n", "-F", symbol], "rg"),
            ):
                text = run(cmd, env)
                parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
                calls.append({"call": f"{label}:{symbol}", "units": len(parsed)})
                for unit in parsed:
                    key = (unit["path"], unit["start_line"], unit["end_line"])
                    if key not in seen:
                        seen.add(key)
                        units.append(unit)
        # Code-aware score over the cited source; files are read once.
        file_text = {}
        files_read = 0
        bytes_read = 0
        scored = []
        for order, unit in enumerate(units):
            path = unit["path"]
            if path not in file_text:
                target = dd.CORPUS / path
                if not target.is_file():
                    file_text[path] = None
                else:
                    data = target.read_text(encoding="utf-8", errors="replace")
                    file_text[path] = data
                    files_read += 1
                    bytes_read += len(data.encode("utf-8"))
            data = file_text.get(path)
            if data is None:
                continue
            lines = data.splitlines()
            start = max(0, unit["start_line"] - 1)
            end = min(len(lines), unit["end_line"])
            content = "\n".join(lines[start:end]).lower()
            path_hits = sum(1 for term in query_terms if term in path.lower())
            content_hits = sum(1 for term in query_terms if term in content)
            symbol_hits = sum(1 for sym in symbols if sym.lower() in content)
            score = path_hits * 2 + content_hits + symbol_hits * 5
            scored.append((score, -order, unit))
        scored.sort(reverse=True)
        selected = [unit for _, _, unit in scored[:10]]
        pool = 0
        for unit in units:
            for index, span in enumerate(gold):
                if unit["path"] == span[0] and dd.overlap(unit["start_line"], unit["end_line"], span[1], span[2]):
                    pool |= 1 << index
        selected_mask = 0
        for unit in selected:
            for index, span in enumerate(gold):
                if unit["path"] == span[0] and dd.overlap(unit["start_line"], unit["end_line"], span[1], span[2]):
                    selected_mask |= 1 << index
        rows.append(
            {
                "id": case_id,
                "gold_spans": len(gold),
                "pool_units": len(units),
                "pool_gold": pool.bit_count(),
                "selectable_gold": dd.selectable(units, gold),
                "selected_gold": selected_mask.bit_count(),
                "selected": selected,
                "symbols": symbols,
                "calls": calls,
                "files_read": files_read,
                "bytes_read": bytes_read,
                "wall_ms": (time.perf_counter() - started) * 1000.0,
            }
        )
    total = sum(row["gold_spans"] for row in rows)
    receipt = {
        "schema_version": 1,
        "status": "DISCOVERY_SYMBOL_H1",
        "hypothesis": "question symbols + depth-50 + code-aware top-10",
        "cases": rows,
        "totals": {
            "gold_spans": total,
            "pool_gold": sum(row["pool_gold"] for row in rows),
            "selectable_gold": sum(row["selectable_gold"] for row in rows),
            "selected_gold": sum(row["selected_gold"] for row in rows),
        },
        "macro": {
            "pool_recall": sum(row["pool_gold"] / row["gold_spans"] for row in rows) / len(rows),
            "selectable_recall10": sum(row["selectable_gold"] / row["gold_spans"] for row in rows) / len(rows),
            "selected_recall10": sum(row["selected_gold"] / row["gold_spans"] for row in rows) / len(rows),
        },
        "cost": {
            "files_read": sum(row["files_read"] for row in rows),
            "bytes_read": sum(row["bytes_read"] for row in rows),
            "wall_ms": sum(row["wall_ms"] for row in rows),
        },
        "limits": ["development split only", "no gold in query/ranking", "no model/index change", "held-out untouched"],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"totals": receipt["totals"], "macro": receipt["macro"], "cost": receipt["cost"]}, indent=2))
    for row in rows:
        print(row["id"], "pool", row["pool_gold"], "/", row["gold_spans"], "selectable", row["selectable_gold"], "selected", row["selected_gold"], "symbols", row["symbols"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
