#!/usr/bin/env python3
"""Summarize E6 route repeats; refuse any report without a PASS contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

KEYS = (
    "recall_at_5",
    "recall_at_10",
    "recall_at_50",
    "primary_recall_at_10",
    "group_coverage_at_10",
    "required_anchor_coverage_at_10",
    "solved_at_10",
    "solved_at_50",
    "mrr_at_10",
    "ndcg_at_10",
    "no_answer_fp_at_10",
    "no_answer_any_result_at_10",
)


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def top_hits(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for row in raw["results"]:
        items = [
            (item.get("path"), item.get("start_line"), item.get("end_line"))
            for item in sorted(row["items"], key=lambda item: item.get("rank", 10**9))[:10]
        ]
        out[row["query_id"]] = hashlib.sha256(
            json.dumps(items, sort_keys=True).encode()
        ).hexdigest()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = sorted(args.report_dir.glob("*-r*.report.json"))
    if not reports:
        print("no reports", file=sys.stderr)
        return 2
    routes: dict[str, list[dict]] = {}
    failures = []
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        contract = report.get("measurement_contract")
        if not isinstance(contract, dict) or contract.get("status") != "PASS":
            failures.append(f"{path.name}: contract missing or not PASS")
            continue
        if contract.get("intended_route") != report.get("route"):
            failures.append(f"{path.name}: declared route differs from contract")
            continue
        route = report["route"]
        raw = path.with_suffix("").with_suffix(".json")
        routes.setdefault(route, []).append(
            {
                "report": str(path),
                "raw": str(raw),
                "overall": report["overall"],
                "top10": top_hits(raw),
            }
        )
    summary = {"routes": {}, "failures": failures, "status": "PASS"}
    for route, runs in sorted(routes.items()):
        overall = {
            key: {
                "mean": mean([float(run["overall"][key]) for run in runs]),
                "stdev": stdev([float(run["overall"][key]) for run in runs]),
                "min": min(float(run["overall"][key]) for run in runs),
                "max": max(float(run["overall"][key]) for run in runs),
            }
            for key in KEYS
            if all(key in run["overall"] for run in runs)
        }
        query_ids = sorted(runs[0]["top10"])
        stability = {
            query_id: all(
                run["top10"][query_id] == runs[0]["top10"][query_id] for run in runs
            )
            for query_id in query_ids
        }
        summary["routes"][route] = {
            "repeats": len(runs),
            "overall": overall,
            "top10_exact_repeat_match": sum(stability.values()),
            "top10_queries": len(stability),
            "latency_ms_p50": mean(
                [float(run["overall"]["latency_ms_p50"]) for run in runs]
            ),
        }
    if failures:
        summary["status"] = "FAIL"
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
