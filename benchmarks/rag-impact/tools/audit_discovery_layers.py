#!/usr/bin/env python3
"""Read-only discovery-layer audit for the 16 non-retrievable gold spans.

For each discovery span, checks:
- whether the file exists in the frozen corpus manifest;
- whether the six frozen B0 calls ever returned the same file;
- whether an exact identifier probe via managed rg and FTS can reach the span;
- the resulting layer: corpus coverage, index retrieval, query formulation,
  file-hit/range miss, or unresolved.

No index writes, candidate changes, receipt edits or held-out access.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
B0 = RAG / "results" / "b0-official.json"
DEV = RAG / "cases" / "dev.json"
CORPUS = RAG / "corpus-manifest.json"
OUT = RAG / "results" / "discovery-layer-audit.json"

BIN = os.environ.get(
    "ZG_BIN",
    str(
        Path.home()
        / ".local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle/fixed-1.98.0-bin/zg"
    ),
)
CORPUS_ROOT = Path(
    os.environ.get(
        "ZG_CORPUS_ROOT",
        str(Path.home() / ".local/share/zvec-grep/rag-impact/20261108-b0-pilot/corpus"),
    )
)
MODEL_CACHE = Path.home() / ".local/share/zvec-grep/index-memory-diagnostics/laya-20261104/models"

FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")
RG_GROUP_RE = re.compile(r"^(\S.*?)\s*$")
RG_LINE_RE = re.compile(r"^\s+(\d+):")


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def parse_fts(text: str) -> list[tuple[str, int, int]]:
    units = []
    current = None
    for line in text.splitlines():
        match = FILE_RE.match(line)
        if match:
            current = match.group(1)
            continue
        match = RANGE_RE.match(line)
        if match and current:
            units.append((current, int(match.group(1)), int(match.group(2))))
    return units


def parse_rg(text: str) -> list[tuple[str, int, int]]:
    units = []
    current = None
    for line in text.splitlines():
        inline = re.match(r"^(.*?):(\d+):", line)
        if inline and not line.startswith(" "):
            units.append((inline.group(1), int(inline.group(2)), int(inline.group(2))))
            continue
        match = RG_LINE_RE.match(line)
        if match and current:
            units.append((current, int(match.group(1)), int(match.group(1))))
            continue
        if line and not line.startswith(" ") and not line.startswith(("Context:", "Coverage:", "Files:")):
            current = line
    return units


def probe(kind: str, pattern: str, path: str) -> list[tuple[str, int, int]]:
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(
        Path.home() / ".local/share/zvec-grep/rollback-20261101T130300Z"
    )
    env["ZVEC_GREP_HOME"] = str(CORPUS_ROOT.parent / "home")
    env["ZVEC_GREP_MODEL_CACHE"] = str(MODEL_CACHE)
    env["TMPDIR"] = str(CORPUS_ROOT.parent / "tmp")
    cmd = (
        [BIN, "--rg", "-n", "-F", pattern, path]
        if kind == "rg"
        else [BIN, "--fts", pattern, "-g", path, "--limit", "50", "--preview", "detail"]
    )
    proc = subprocess.run(
        cmd, cwd=CORPUS_ROOT, env=env, capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        return []
    return parse_rg(proc.stdout) if kind == "rg" else parse_fts(proc.stdout)


def main() -> int:
    b0 = json.loads(B0.read_text())
    dev = json.loads(DEV.read_text())
    corpus = json.loads(CORPUS.read_text())
    corpus_paths = {file["path"]: file for file in corpus["files"]}
    dev_by = {case["id"]: case for case in dev["cases"]}

    # Exact probe pattern per discovery span (general-purpose identifiers only).
    probes = {
        ("D01", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 215): "EmployeeEditThongTinCaNhan",
        ("D02", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 215): "EmployeeEditThongTinCaNhan",
        ("D02", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 287): "SaveChanges",
        ("D02", "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs", 17): "AddFromAssembly",
        ("D02", "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs", 41): "EmployeeDetails",
        ("D02", "DBContexts/HRIS/DBContext.Core/Entities/Employee/Employee.cs", 1): "Employee",
        ("D02", "DBContexts/HRIS/DBContext.Core/Entities/Employee/EmployeeDetail.cs", 1): "EmployeeDetail",
        ("D03", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 215): "Keyword",
        ("D05", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 215): "SaveChanges",
        ("D06", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 427): "UpdateOrgTotalEmployee",
        ("D06", "Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs", 1057): "UpdateOrgTotalEmployee",
        ("D07", "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs", 539): "DeleteEmployeeByIds",
        ("D07", "Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs", 1057): "UpdateOrgTotalEmployee",
        ("D07", "DB/Store/sp_UpdateOrgTotalEmployee.sql", 58): "sp_UpdateOrgTotalEmployee",
        ("D10", "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs", 9): "CoreDBContext",
        ("D12", "Services/HRIS/Service.Core/Executes/General/Organizations/OrganizationCommand.cs", 1057): "UpdateOrgTotalEmployee",
        ("D12", "DB/Store/sp_UpdateOrgTotalEmployee.sql", 1): "sp_UpdateOrgTotalEmployee",
    }

    rows = []
    for b0_case in b0["quality"]["dev_real_cases"]:
        case_id = b0_case["id"]
        gold = []
        for reference in dev_by[case_id].get("source_refs", []):
            for span in reference.get("spans", []):
                gold.append((reference["path"], span["start_line"], span["end_line"]))
        union = []
        for call in b0_case["calls"]:
            for unit in call.get("units") or []:
                if any(
                    unit["path"] == path
                    and overlap(unit["start_line"], unit["end_line"], start, end)
                    for path, start, end in gold
                ):
                    union.append(unit)
        for path, start, end in gold:
            if any(
                unit["path"] == path
                and overlap(unit["start_line"], unit["end_line"], start, end)
                for unit in union
            ):
                continue
            key = (case_id, path, start)
            pattern = probes.get(key)
            file_hit = any(
                unit["path"] == path
                for call in b0_case["calls"]
                for unit in (call.get("units") or [])
            )
            rg_hit = False
            fts_hit = False
            rg_units = []
            fts_units = []
            if pattern:
                rg_units = probe("rg", pattern, path)
                fts_units = probe("fts", pattern, path)
                rg_hit = any(
                    unit_path == path and overlap(unit_start, unit_end, start, end)
                    for unit_path, unit_start, unit_end in rg_units
                )
                fts_hit = any(
                    unit_path == path and overlap(unit_start, unit_end, start, end)
                    for unit_path, unit_start, unit_end in fts_units
                )
            if path not in corpus_paths:
                layer = "corpus_coverage"
            elif not fts_hit:
                layer = "index_retrieval"
            elif file_hit:
                layer = "file_hit_missing_range"
            else:
                layer = "query_formulation"
            rows.append(
                {
                    "case": case_id,
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "corpus_kind": corpus_paths.get(path, {}).get("kind"),
                    "b0_file_hit": file_hit,
                    "exact_probe": pattern,
                    "rg_reaches_span": rg_hit,
                    "fts_reaches_span": fts_hit,
                    "layer": layer,
                }
            )

    totals: dict[str, int] = {}
    for row in rows:
        totals[row["layer"]] = totals.get(row["layer"], 0) + 1
    receipt = {
        "schema_version": 1,
        "status": "READ_ONLY_DISCOVERY_LAYER_AUDIT",
        "probe": {
            "binary": BIN,
            "corpus_root": str(CORPUS_ROOT),
            "note": "managed rg and FTS exact-identifier probes; no index writes",
        },
        "totals": totals,
        "rows": rows,
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(totals, indent=2))
    for row in rows:
        print(
            row["case"],
            row["path"].split("/")[-1],
            f"{row['start_line']}-{row['end_line']}",
            row["layer"],
            "file_hit" if row["b0_file_hit"] else "no_file_hit",
            "rg" if row["rg_reaches_span"] else "no_rg",
            "fts" if row["fts_reaches_span"] else "no_fts",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
