#!/usr/bin/env python3
"""Compare corrected E6 route summaries and per-query deltas against Gemma."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

METRICS = ("recall_at_10", "solved_at_10", "mrr_at_10", "ndcg_at_10", "no_answer_fp_at_10")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def route_report(directory: Path, route: str) -> dict:
    return load(directory / f"{route}-r1.report.json")


def per_query(report: dict) -> dict[str, dict]:
    return {row["id"]: row for row in report["queries"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemma", required=True, type=Path)
    parser.add_argument("--candidate", action="append", required=True, help="name=dir")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reference = {route: route_report(args.gemma, route) for route in ("fts", "vector", "hybrid")}
    out = {"gemma": str(args.gemma), "routes": {}, "candidates": {}}
    for route, report in reference.items():
        out["routes"][route] = report["overall"]
    for spec in args.candidate:
        name, directory = spec.split("=", 1)
        directory = Path(directory)
        entry = {"path": str(directory), "routes": {}}
        for route in ("fts", "vector", "hybrid"):
            report = route_report(directory, route)
            overall = report["overall"]
            deltas = {
                key: overall[key] - reference[route]["overall"][key] for key in METRICS
            }
            ref_queries = per_query(reference[route])
            cand_queries = per_query(report)
            wins = losses = ties = 0
            per_query_delta = {}
            for query_id in ref_queries:
                base = ref_queries[query_id]
                cand = cand_queries[query_id]
                if not base.get("answerable", True):
                    continue
                delta = float(cand["solved_at_10"]) - float(base["solved_at_10"])
                if delta > 0:
                    wins += 1
                elif delta < 0:
                    losses += 1
                else:
                    ties += 1
                per_query_delta[query_id] = {
                    "solved_at_10": delta,
                    "recall_at_10": cand["recall_at_10"] - base["recall_at_10"],
                    "ndcg_at_10": cand["ndcg_at_10"] - base["ndcg_at_10"],
                }
            entry["routes"][route] = {
                "overall": overall,
                "deltas": deltas,
                "solved_wins_losses_ties": [wins, losses, ties],
                "per_query": per_query_delta,
            }
        out["candidates"][name] = entry
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
