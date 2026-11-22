#!/usr/bin/env python3
"""Reproduce rg/prototype output, quality, latency and peak RSS measurements."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import statistics
import subprocess
import sys
import time


BASE = pathlib.Path(__file__).resolve().parent
EXECUTABLE = pathlib.Path(sys.argv[1]).resolve()


def identity(directory: pathlib.Path, cases_path: pathlib.Path) -> dict[str, str]:
    paths = [cases_path, *(p for p in (directory / "corpus").rglob("*") if p.is_file())]
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def timed(command: list[str]) -> tuple[subprocess.CompletedProcess[str], int, int, int]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        ["/usr/bin/time", "-f", "RSS_KB:%M", *command],
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter_ns() - started
    match = re.search(r"(?:^|\n)RSS_KB:(\d+)(?:\n|$)", completed.stderr)
    if match is None:
        raise SystemExit(f"missing peak-RSS measurement for {command!r}: {completed.stderr}")
    return completed, elapsed, len(completed.stdout.encode()), int(match.group(1))


def main() -> int:
    all_results = []
    aggregate = {
        "dev": {"valid_tasks": 0, "candidate_declaration_tp": 0, "candidate_declaration_fp": 0, "candidate_declaration_fn": 0, "candidate_caller_tp": 0, "candidate_caller_fp": 0, "candidate_caller_fn": 0},
        "validation": {"valid_tasks": 0, "candidate_declaration_tp": 0, "candidate_declaration_fp": 0, "candidate_declaration_fn": 0, "candidate_caller_tp": 0, "candidate_caller_fp": 0, "candidate_caller_fn": 0},
    }
    for summary in aggregate.values():
        summary["excluded_tasks"] = 0
    for label, relative in (("dev", "fixtures"), ("validation", "validation")):
        directory = BASE / relative
        cases_path = directory / "cases.json"
        frozen = json.loads(cases_path.read_text())
        recorded = json.loads((directory / "baseline-rg.json").read_text())
        current_identity = identity(directory, cases_path)
        if current_identity != recorded["identity"]:
            raise SystemExit(f"frozen corpus identity mismatch: {label}")
        corpus = BASE / frozen["corpus"]
        available_paths = {
            path.relative_to(corpus).as_posix()
            for path in corpus.rglob("*")
            if path.is_file()
        }
        for task in frozen["tasks"]:
            candidate_samples = []
            rg_samples = []
            candidate_result = None
            rg_output = ""
            candidate_rss = []
            rg_rss = []
            for _ in range(3):
                candidate, elapsed, output_bytes, rss_kb = timed(
                    [str(EXECUTABLE), str(corpus), task["query"]]
                )
                if candidate.returncode != 0:
                    raise SystemExit(candidate.stderr)
                candidate_result = json.loads(candidate.stdout)
                candidate_samples.append((elapsed, output_bytes))
                candidate_rss.append(rss_kb)
                rg, elapsed, output_bytes, rss_kb = timed(
                    ["rg", "-n", "-F", "--", task["query"], str(corpus)]
                )
                if rg.returncode not in (0, 1):
                    raise SystemExit(rg.stderr)
                rg_output = rg.stdout
                rg_samples.append((elapsed, output_bytes))
                rg_rss.append(rss_kb)

            assert candidate_result is not None
            expected_declarations = set(task["expected_declarations"])
            expected_callers = set(task["expected_callers"])
            missing_sources = sorted(
                {
                    symbol.rsplit("::", 1)[0]
                    for symbol in expected_declarations
                    if symbol.rsplit("::", 1)[0] not in available_paths
                }
            )
            actual_declarations = {
                f"{item['path']}::{item['name']}"
                for item in candidate_result["declarations"]
            }
            actual_callers = {
                f"{item['path']}::{caller['name']}"
                for item in candidate_result["declarations"]
                for caller in item["callers"]
            }
            tp = actual_declarations & expected_declarations
            fp = actual_declarations - expected_declarations
            fn = expected_declarations - actual_declarations
            caller_tp = actual_callers & expected_callers
            caller_fp = actual_callers - expected_callers
            caller_fn = expected_callers - actual_callers
            baseline_lines = rg_output.splitlines()
            rg_found_declarations = {
                symbol
                for symbol in expected_declarations
                if any(symbol.rsplit("::", 1)[1] in line for line in baseline_lines)
            }
            if missing_sources:
                aggregate[label]["excluded_tasks"] += 1
            else:
                aggregate[label]["valid_tasks"] += 1
                for key, count in (
                    ("candidate_declaration_tp", len(tp)),
                    ("candidate_declaration_fp", len(fp)),
                    ("candidate_declaration_fn", len(fn)),
                    ("candidate_caller_tp", len(caller_tp)),
                    ("candidate_caller_fp", len(caller_fp)),
                    ("candidate_caller_fn", len(caller_fn)),
                ):
                    aggregate[label][key] += count

            all_results.append(
                {
                    "suite": label,
                    "id": task["id"],
                    "query": task["query"],
                    "expected_declarations": sorted(expected_declarations),
                    "missing_expected_source_files": missing_sources,
                    "candidate_declarations": sorted(actual_declarations),
                    "candidate_declaration_tp": sorted(tp),
                    "candidate_declaration_fp": sorted(fp),
                    "candidate_declaration_fn": sorted(fn),
                    "rg_expected_declarations_seen": sorted(rg_found_declarations),
                    "rg_raw_match_count": len(baseline_lines),
                    "rg_output_bytes": statistics.median(sample[1] for sample in rg_samples),
                    "rg_latency_ms_median": round(statistics.median(sample[0] for sample in rg_samples) / 1e6, 3),
                    "rg_peak_rss_kb_median": statistics.median(rg_rss),
                    "candidate_callers": sorted(actual_callers),
                    "expected_callers": sorted(expected_callers),
                    "candidate_caller_tp": sorted(caller_tp),
                    "candidate_caller_fp": sorted(caller_fp),
                    "candidate_caller_fn": sorted(caller_fn),
                    "candidate_output_bytes": statistics.median(sample[1] for sample in candidate_samples),
                    "candidate_latency_ms_median": round(statistics.median(sample[0] for sample in candidate_samples) / 1e6, 3),
                    "candidate_peak_rss_kb_median": statistics.median(candidate_rss),
                    "candidate_internal_latency_ms": round(candidate_result["elapsed_ns"] / 1e6, 3),
                    "files_scanned": candidate_result["files_scanned"],
                    "rg_raw_matches": baseline_lines,
                }
            )

    print(json.dumps({"schema": 1, "repetitions": 3, "aggregate": aggregate, "tasks": all_results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
