#!/usr/bin/env python3
"""Run frozen C# cases against rg, the previous prototype, and C# candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import statistics
import subprocess
import time


BASE = pathlib.Path(__file__).resolve().parent
FIXTURES = BASE / "csharp" / "fixtures"


def corpus_identity(folder: pathlib.Path) -> dict[str, str]:
    paths = [folder / "cases.json", *(p for p in folder.rglob("*") if p.is_file() and p.name != "baseline.json")]
    return {
        str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def timed(command: list[str]) -> tuple[subprocess.CompletedProcess[str], int, int, int]:
    started = time.perf_counter_ns()
    result = subprocess.run(
        ["/usr/bin/time", "-f", "RSS_KB:%M", *command],
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter_ns() - started
    rss = re.search(r"(?:^|\n)RSS_KB:(\d+)(?:\n|$)", result.stderr)
    if rss is None:
        raise RuntimeError(f"missing RSS measurement for {command!r}: {result.stderr}")
    return result, elapsed, len(result.stdout.encode()), int(rss.group(1))


def edge_set(values: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(item["caller"], item["callee"]) for item in values}


def unresolved_set(values: list[dict[str, object]]) -> set[tuple[str, str, str]]:
    return {(str(item["caller"]), str(item["callee_name"]), str(item["reason"])) for item in values}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("dev", "validation"), required=True)
    parser.add_argument("--exe", type=pathlib.Path, required=True)
    args = parser.parse_args()

    folder = FIXTURES / args.suite
    case_data = json.loads((folder / "cases.json").read_text())
    baseline_data = json.loads((folder / "baseline.json").read_text())
    actual_identity = corpus_identity(folder)
    if actual_identity != baseline_data["identity"]:
        raise SystemExit(f"frozen C# fixture identity changed: {args.suite}")
    root = folder

    task_results = []
    totals = {
        "declaration_tp": 0,
        "declaration_fp": 0,
        "declaration_fn": 0,
        "edge_tp": 0,
        "edge_fp": 0,
        "edge_fn": 0,
        "uncertainty_tp": 0,
        "uncertainty_fp": 0,
        "uncertainty_fn": 0,
    }
    for task in case_data["tasks"]:
        candidate_samples = []
        exact_samples = []
        insensitive_samples = []
        candidate = None
        exact_output = ""
        insensitive_output = ""
        candidate_rss = []
        exact_rss = []
        insensitive_rss = []
        for _ in range(3):
            candidate_run, elapsed, output_bytes, rss = timed(
                [str(args.exe.resolve()), str(root), task["query"]]
            )
            if candidate_run.returncode != 0:
                raise SystemExit(candidate_run.stderr)
            candidate = json.loads(candidate_run.stdout)["csharp"]
            candidate_samples.append((elapsed, output_bytes))
            candidate_rss.append(rss)

            exact_run, elapsed, output_bytes, rss = timed(
                ["rg", "-n", "-F", "--glob", "*.cs", "--", task["query"], str(root)]
            )
            if exact_run.returncode not in (0, 1):
                raise SystemExit(exact_run.stderr)
            exact_output = exact_run.stdout
            exact_samples.append((elapsed, output_bytes))
            exact_rss.append(rss)

            insensitive_run, elapsed, output_bytes, rss = timed(
                ["rg", "-n", "-i", "-F", "--glob", "*.cs", "--", task["query"], str(root)]
            )
            if insensitive_run.returncode not in (0, 1):
                raise SystemExit(insensitive_run.stderr)
            insensitive_output = insensitive_run.stdout
            insensitive_samples.append((elapsed, output_bytes))
            insensitive_rss.append(rss)

        assert candidate is not None
        actual_declarations = {item["symbol_id"] for item in candidate["declarations"]}
        expected_declarations = set(task["expected_declarations"])
        actual_edges = edge_set(candidate["call_edges"])
        expected_edges = edge_set(task["expected_edges"])
        actual_unresolved = unresolved_set(candidate["unresolved_calls"])
        expected_unresolved = unresolved_set(task["expected_unresolved"])

        declaration_tp = actual_declarations & expected_declarations
        declaration_fp = actual_declarations - expected_declarations
        declaration_fn = expected_declarations - actual_declarations
        edge_tp = actual_edges & expected_edges
        edge_fp = actual_edges - expected_edges
        edge_fn = expected_edges - actual_edges
        uncertainty_tp = actual_unresolved & expected_unresolved
        uncertainty_fp = actual_unresolved - expected_unresolved
        uncertainty_fn = expected_unresolved - actual_unresolved
        for key, values in (
            ("declaration_tp", declaration_tp),
            ("declaration_fp", declaration_fp),
            ("declaration_fn", declaration_fn),
            ("edge_tp", edge_tp),
            ("edge_fp", edge_fp),
            ("edge_fn", edge_fn),
            ("uncertainty_tp", uncertainty_tp),
            ("uncertainty_fp", uncertainty_fp),
            ("uncertainty_fn", uncertainty_fn),
        ):
            totals[key] += len(values)

        old = baseline_data["tasks"]
        old_task = next(item for item in old if item["id"] == task["id"])
        task_results.append(
            {
                "id": task["id"],
                "query": task["query"],
                "expected_declarations": sorted(expected_declarations),
                "candidate_declarations": sorted(actual_declarations),
                "declaration_tp": sorted(declaration_tp),
                "declaration_fp": sorted(declaration_fp),
                "declaration_fn": sorted(declaration_fn),
                "expected_edges": [list(pair) for pair in sorted(expected_edges)],
                "candidate_edges": [list(pair) for pair in sorted(actual_edges)],
                "edge_tp": [list(pair) for pair in sorted(edge_tp)],
                "edge_fp": [list(pair) for pair in sorted(edge_fp)],
                "edge_fn": [list(pair) for pair in sorted(edge_fn)],
                "expected_unresolved": [list(item) for item in sorted(expected_unresolved)],
                "candidate_unresolved": [list(item) for item in sorted(actual_unresolved)],
                "uncertainty_tp": [list(item) for item in sorted(uncertainty_tp)],
                "uncertainty_fp": [list(item) for item in sorted(uncertainty_fp)],
                "uncertainty_fn": [list(item) for item in sorted(uncertainty_fn)],
                "rg_exact_lines": exact_output.splitlines(),
                "rg_case_insensitive_lines": insensitive_output.splitlines(),
                "old_prototype_files_scanned": old_task["baselines"]["prototype_before"]["json"]["files_scanned"],
                "candidate_files_scanned": candidate["files_scanned"],
                "candidate_files_skipped": candidate["files_skipped"],
                "candidate_truncated": candidate["truncated"],
                "latency_ms_median": {
                    "rg_exact": round(statistics.median(sample[0] for sample in exact_samples) / 1e6, 3),
                    "rg_case_insensitive": round(statistics.median(sample[0] for sample in insensitive_samples) / 1e6, 3),
                    "candidate": round(statistics.median(sample[0] for sample in candidate_samples) / 1e6, 3),
                },
                "rss_kb_median": {
                    "rg_exact": statistics.median(exact_rss),
                    "rg_case_insensitive": statistics.median(insensitive_rss),
                    "candidate": statistics.median(candidate_rss),
                },
                "output_bytes_median": {
                    "rg_exact": statistics.median(sample[1] for sample in exact_samples),
                    "rg_case_insensitive": statistics.median(sample[1] for sample in insensitive_samples),
                    "candidate": statistics.median(sample[1] for sample in candidate_samples),
                },
            }
        )

    print(json.dumps({"schema": 1, "suite": args.suite, "repetitions": 3, "totals": totals, "tasks": task_results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
