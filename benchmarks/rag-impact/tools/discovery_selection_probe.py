#!/usr/bin/env python3
"""Selection policies A/B over the H1 discovery pool."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG / "tools"))
import discovery_depth_probe as dd  # noqa: E402
import discovery_symbol_probe as ds  # noqa: E402

DEV = RAG / "cases" / "dev.json"
B0 = RAG / "results" / "b0-official.json"
OUT = RAG / "results" / "discovery-selection-probe.json"


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
        started = time.perf_counter()
        units = {}
        for label, cmd, kind in dd.query_calls(query):
            text = ds.run(cmd, env)
            parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
            for rank, unit in enumerate(parsed):
                key = (unit["path"], unit["start_line"], unit["end_line"])
                entry = units.setdefault(key, {"unit": unit, "calls": {}})
                entry["calls"].setdefault(label, rank)
        symbols = ds.question_symbols(query)
        for symbol in symbols:
            for label, cmd, kind in (
                ("fts_symbol", [dd.BIN, "--fts", symbol, "--limit", "50", "--preview", "detail"], "fts"),
                ("rg_symbol", [dd.BIN, "--rg", "-n", "-F", symbol], "rg"),
            ):
                text = ds.run(cmd, env)
                parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
                for rank, unit in enumerate(parsed):
                    key = (unit["path"], unit["start_line"], unit["end_line"])
                    entry = units.setdefault(key, {"unit": unit, "calls": {}})
                    entry["calls"].setdefault(f"{label}:{symbol}", rank)
        pool = [entry["unit"] for entry in units.values()]
        def rrf(entry: dict) -> float:
            return sum(1.0 / (60.0 + rank) for rank in entry["calls"].values())
        rrf_ranked = sorted(units.values(), key=lambda entry: (-rrf(entry), min(entry["calls"].values()), entry["unit"]["path"]))
        selected_a = [entry["unit"] for entry in rrf_ranked[:10]]
        # Symbol policy: source reads once, exact question-symbol occurrence.
        file_text = {}
        files_read = 0
        bytes_read = 0
        symbol_entries = []
        other_entries = []
        for entry in rrf_ranked:
            path = entry["unit"]["path"]
            if path not in file_text:
                target = dd.CORPUS / path
                if target.is_file():
                    data = target.read_text(encoding="utf-8", errors="replace")
                    file_text[path] = data
                    files_read += 1
                    bytes_read += len(data.encode("utf-8"))
                else:
                    file_text[path] = None
            data = file_text[path]
            exact = False
            if data is not None:
                lines = data.splitlines()
                start = max(0, entry["unit"]["start_line"] - 1)
                end = min(len(lines), entry["unit"]["end_line"])
                content = "\n".join(lines[start:end]).lower()
                exact = any(symbol.lower() in content for symbol in symbols)
            (symbol_entries if exact else other_entries).append(entry["unit"])
        selected_b = (symbol_entries + other_entries)[:10]
        def mask(selected):
            value = 0
            for unit in selected:
                for index, span in enumerate(gold):
                    if unit["path"] == span[0] and dd.overlap(unit["start_line"], unit["end_line"], span[1], span[2]):
                        value |= 1 << index
            return value
        pool_mask = 0
        for unit in pool:
            for index, span in enumerate(gold):
                if unit["path"] == span[0] and dd.overlap(unit["start_line"], unit["end_line"], span[1], span[2]):
                    pool_mask |= 1 << index
        rows.append(
            {
                "id": case_id,
                "gold_spans": len(gold),
                "pool_gold": pool_mask.bit_count(),
                "selectable_gold": dd.selectable(pool, gold),
                "selected_a_gold": mask(selected_a).bit_count(),
                "selected_b_gold": mask(selected_b).bit_count(),
                "symbols": symbols,
                "pool_units": len(pool),
                "files_read": files_read,
                "bytes_read": bytes_read,
                "wall_ms": (time.perf_counter() - started) * 1000.0,
                "selected_a": selected_a,
                "selected_b": selected_b,
            }
        )
    total = sum(row["gold_spans"] for row in rows)
    def macro(key: str) -> float:
        return sum(row[key] / row["gold_spans"] for row in rows) / len(rows)
    receipt = {
        "schema_version": 1,
        "status": "DISCOVERY_SELECTION_A_B",
        "cases": rows,
        "totals": {
            "gold_spans": total,
            "pool_gold": sum(row["pool_gold"] for row in rows),
            "selectable_gold": sum(row["selectable_gold"] for row in rows),
            "selected_a_gold": sum(row["selected_a_gold"] for row in rows),
            "selected_b_gold": sum(row["selected_b_gold"] for row in rows),
        },
        "macro": {
            "pool_recall": macro("pool_gold"),
            "selectable_recall10": macro("selectable_gold"),
            "selected_a_recall10": macro("selected_a_gold"),
            "selected_b_recall10": macro("selected_b_gold"),
        },
        "cost": {
            "files_read": sum(row["files_read"] for row in rows),
            "bytes_read": sum(row["bytes_read"] for row in rows),
            "wall_ms": sum(row["wall_ms"] for row in rows),
        },
        "limits": ["development split only", "no gold in policy", "no model/index change", "held-out untouched"],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"totals": receipt["totals"], "macro": receipt["macro"], "cost": receipt["cost"]}, indent=2))
    for row in rows:
        print(row["id"], "pool", row["pool_gold"], "A", row["selected_a_gold"], "B", row["selected_b_gold"], "/", row["gold_spans"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
