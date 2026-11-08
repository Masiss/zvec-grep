#!/usr/bin/env python3
"""Audit the 11 gold spans missing from the H1 candidate pool.

Gold is used only for measurement/attribution. Exact/depth probes exist only to
classify why H1 missed a span; they are not used to build a policy query.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAG / "tools"))
import discovery_depth_probe as dd  # noqa: E402
import discovery_symbol_probe as ds  # noqa: E402

DEV = RAG / "cases" / "dev.json"
B0 = RAG / "results" / "b0-official.json"
OUT = RAG / "results" / "discovery-missing-audit.json"
METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|\s)+"
    r"[\w<>,\[\]\?\.]+\s+(\w+)\s*\([^;]*\)\s*$"
)


def pool_units(query: str, env: dict) -> list[dict]:
    units = []
    seen = set()
    for _label, cmd, kind in dd.query_calls(query):
        text = ds.run(cmd, env)
        parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
        for unit in parsed:
            key = (unit["path"], unit["start_line"], unit["end_line"])
            if key not in seen:
                seen.add(key)
                units.append(unit)
    for symbol in ds.question_symbols(query):
        for _label, cmd, kind in (
            ("fts_symbol", [dd.BIN, "--fts", symbol, "--limit", "50", "--preview", "detail"], "fts"),
            ("rg_symbol", [dd.BIN, "--rg", "-n", "-F", symbol], "rg"),
        ):
            text = ds.run(cmd, env)
            parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:50]
            for unit in parsed:
                key = (unit["path"], unit["start_line"], unit["end_line"])
                if key not in seen:
                    seen.add(key)
                    units.append(unit)
    return units


def method_at(path: Path, line: int) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for start in range(max(0, line - 3), min(len(lines), line + 2)):
        match = METHOD_RE.match(lines[start])
        if match:
            return match.group(1)
    return None


def exact_pattern(path: Path, line: int) -> str | None:
    method = method_at(path, line)
    if method:
        return method
    if path.suffix.lower() == ".sql":
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"(?:PROCEDURE|VIEW)\s+\[?dbo\]?\.?\[?(\w+)", text, re.I)
        if match:
            return match.group(1)
    return None


def depth_units(query: str, env: dict, limit: int = 200) -> list[dict]:
    units = []
    for _label, cmd, kind in dd.query_calls(query):
        cmd = [part if part != "50" else str(limit) for part in cmd]
        text = ds.run(cmd, env)
        parsed = (dd.parse_fts(text) if kind == "fts" else dd.parse_rg(text))[:limit]
        units.extend(parsed)
    return units


def hit(units: list[dict], path: str, start: int, end: int) -> bool:
    return any(
        unit["path"] == path and dd.overlap(unit["start_line"], unit["end_line"], start, end)
        for unit in units
    )


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
        units = pool_units(case["question"], env)
        for path, start, end in gold:
            if hit(units, path, start, end):
                continue
            file_in_pool = any(unit["path"] == path for unit in units)
            pattern = exact_pattern(dd.CORPUS / path, start)
            exact_units = []
            if pattern:
                exact_units = dd.parse_fts(
                    ds.run([dd.BIN, "--fts", pattern, "--limit", "50", "--preview", "detail"], env)
                )
                exact_units += dd.parse_rg(ds.run([dd.BIN, "--rg", "-n", "-F", pattern], env))
            exact_hit = hit(exact_units, path, start, end)
            deeper = depth_units(case["question"], env, 200)
            depth_hit = hit(deeper, path, start, end)
            if file_in_pool:
                layer = "range_coverage"
            elif depth_hit:
                layer = "retrieval_depth"
            elif exact_hit:
                layer = "query_formulation"
            else:
                layer = "file_discovery"
            rows.append(
                {
                    "case": case_id,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "exact_pattern": pattern,
                    "file_in_pool": file_in_pool,
                    "exact_hit": exact_hit,
                    "depth200_hit": depth_hit,
                    "layer": layer,
                }
            )
    totals = {}
    for row in rows:
        totals[row["layer"]] = totals.get(row["layer"], 0) + 1
    OUT.write_text(json.dumps({"schema_version": 1, "status": "DISCOVERY_MISSING_AUDIT", "totals": totals, "rows": rows}, indent=2) + "\n")
    print(json.dumps(totals, indent=2))
    for row in rows:
        print(row["case"], row["path"].split("/")[-1], row["start_line"], row["layer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
