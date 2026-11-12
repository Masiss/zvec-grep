#!/usr/bin/env python3
"""Aggregate repeated E3-02A route runs and report variance/attribution."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from attribute import declaration_type, hit_type  # noqa: E402
from evaluate import overlaps, query_metrics, ranked_hits  # noqa: E402


def metric_summary(values: list[float]) -> dict:
    return {
        "mean": statistics.fmean(values) if values else 0.0,
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def parse_time(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    def number(pattern: str) -> float | None:
        match = re.search(pattern, text)
        return float(match.group(1)) if match else None
    elapsed = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\d+):([0-9.]+)", text)
    seconds = None
    if elapsed:
        seconds = int(elapsed.group(1)) * 60 + float(elapsed.group(2))
    return {
        "wall_s": seconds,
        "user_s": number(r"User time \(seconds\): ([0-9.]+)"),
        "system_s": number(r"System time \(seconds\): ([0-9.]+)"),
        "max_rss_kb": number(r"Maximum resident set size \(kbytes\): (\d+)"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("dataset/queries.json"))
    parser.add_argument("--matrix-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    queries = dataset["queries"]
    by_id = {query["id"]: query for query in queries}
    routes = {}
    for route in ("fts", "vector", "hybrid"):
        paths = sorted(args.matrix_dir.glob(f"gemma-{route}-r*.json"))
        if not paths:
            continue
        runs = []
        for path in paths:
            run = json.loads(path.read_text(encoding="utf-8"))
            rows = {row["query_id"]: row for row in run["results"]}
            metrics = [query_metrics(query, rows[query["id"]]["items"], rows[query["id"]].get("latency_ms")) for query in queries]
            hit_ids = {}
            for query in queries:
                if query["answerable"]:
                    _, ids, _ = ranked_hits(query, rows[query["id"]]["items"], 10)
                    hit_ids[query["id"]] = ids
            time_path = path.with_suffix(".time")
            runs.append({
                "path": str(path),
                "run": run.get("run", {}),
                "metrics": metrics,
                "hit_ids": hit_ids,
                "resource": parse_time(time_path) if time_path.exists() else {},
            })
        keys = [
            "recall_at_5", "recall_at_10", "recall_at_50",
            "primary_recall_at_5", "primary_recall_at_10", "primary_recall_at_50",
            "required_anchor_coverage_at_5", "required_anchor_coverage_at_10", "required_anchor_coverage_at_50",
            "solved_at_5", "solved_at_10", "solved_at_50",
            "mrr_at_10", "ndcg_at_10", "hard_negative_hits_at_5", "hard_negative_hits_at_10", "hard_negative_hits_at_50",
        ]
        answerable_by_run = []
        for run in runs:
            answerable = [row for row in run["metrics"] if row["answerable"]]
            answerable_by_run.append(answerable)
        aggregate = {}
        for key in keys:
            per_run = [
                statistics.fmean([float(row[key]) for row in answerable])
                for answerable in answerable_by_run
            ]
            aggregate[key] = metric_summary(per_run)
        no_answer = [row for row in runs[0]["metrics"] if not row["answerable"]]
        aggregate["no_answer_trap_fp_at_10"] = statistics.fmean(
            [row["no_answer_fp_at_10"] for row in no_answer]
        ) if no_answer else 0.0
        aggregate["no_answer_any_result_at_10"] = statistics.fmean(
            [row["no_answer_any_result_at_10"] for row in no_answer]
        ) if no_answer else 0.0
        all_latencies = [row["latency_ms"] for run in runs for row in run["metrics"] if isinstance(row.get("latency_ms"), (int, float))]
        aggregate["latency_ms_p50"] = statistics.median(all_latencies) if all_latencies else None
        aggregate["latency_ms_p95"] = sorted(all_latencies)[max(0, int(0.95 * len(all_latencies)) - 1)] if all_latencies else None
        aggregate["resource"] = {
            key: metric_summary([run["resource"][key] for run in runs if run["resource"].get(key) is not None])
            for key in ("wall_s", "user_s", "system_s", "max_rss_kb")
        }
        # Stability: exact top-10 hit-set match across repeats, and Jaccard.
        stability = []
        for query in queries:
            if not query["answerable"]:
                continue
            sets = [run["hit_ids"].get(query["id"], set()) for run in runs]
            intersection = set.intersection(*sets) if sets else set()
            union = set.union(*sets) if sets else set()
            stability.append({
                "query_id": query["id"],
                "exact_repeat_match": all(item == sets[0] for item in sets),
                "jaccard": len(intersection) / len(union) if union else 1.0,
            })
        aggregate["stability"] = {
            "queries_exact_repeat_match": sum(1 for item in stability if item["exact_repeat_match"]),
            "answerable_queries": len(stability),
            "mean_jaccard": statistics.fmean([item["jaccard"] for item in stability]) if stability else 0.0,
        }
        # Attribution uses run 1; repeat stability above tells whether it moved.
        first = runs[0]
        type_counts = Counter()
        for query in queries:
            if not query["answerable"]:
                continue
            items = next(row for row in json.loads(Path(first["path"]).read_text())["results"] if row["query_id"] == query["id"])["items"]
            hits, _, _ = ranked_hits(query, items, 10)
            for anchor in hits:
                type_counts[hit_type(items, anchor)] += 1
        routes[route] = {
            "runs": len(runs),
            "aggregate": aggregate,
            "declaration_type_hits_run1": dict(type_counts),
            "first_hit_ids": {key: sorted(value) for key, value in first["hit_ids"].items()},
            "stability": stability,
        }
    fts = routes.get("fts", {}).get("first_hit_ids", {})
    vector = routes.get("vector", {}).get("first_hit_ids", {})
    contribution = {"fts_only": [], "vector_only": [], "both": [], "neither": []}
    for query in queries:
        if not query["answerable"]:
            continue
        f, v = fts.get(query["id"], set()), vector.get(query["id"], set())
        if f and not v:
            contribution["fts_only"].append(query["id"])
        elif v and not f:
            contribution["vector_only"].append(query["id"])
        elif f and v:
            contribution["both"].append(query["id"])
        else:
            contribution["neither"].append(query["id"])
    report = {
        "dataset": dataset.get("dataset"),
        "dataset_sha256": __import__("hashlib").sha256(args.dataset.read_bytes()).hexdigest(),
        "matrix_dir": str(args.matrix_dir),
        "routes": routes,
        "unique_contribution_run1": contribution,
        "per_query": {
            query["id"]: {
                "category": query["category"],
                "corpus": query["corpus"],
                "leakage": query["leakage"],
                "answerable": query["answerable"],
                "hits_run1": {route: sorted(routes.get(route, {}).get("first_hit_ids", {}).get(query["id"], [])) for route in routes},
            }
            for query in queries
        },
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
