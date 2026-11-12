#!/usr/bin/env python3
"""Compare FTS/vector/hybrid runs on the frozen evidence dataset.

This is a descriptive attribution tool, not a new scoring rule.  It reuses the
same overlap and metric definitions as evaluate.py.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import KS, overlaps, query_metrics, ranked_hits  # noqa: E402


def declaration_type(candidate: dict) -> str:
    symbol_type = candidate.get("symbol_type")
    signature = candidate.get("signature") or ""
    if symbol_type in {"class", "interface", "enum", "module", "alias"}:
        return "container"
    if symbol_type == "function":
        return "method"
    if symbol_type == "value":
        if "{ get" in signature or "{ set" in signature or " get;" in signature:
            return "property"
        return "value"
    if candidate.get("kind") == "lexicalmatch":
        return "lexical"
    return "unknown"


def hit_type(items: list[dict], anchor: dict) -> str:
    for item in sorted(items, key=lambda value: value.get("rank", 10**9)):
        if overlaps(item, anchor):
            return declaration_type(item)
    return "unknown"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("dataset/queries.json"))
    parser.add_argument("--results", action="append", required=True,
                        help="route=/path/to/results.json, repeatable")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    dataset = load(args.dataset)
    queries = dataset["queries"]
    by_id = {query["id"]: query for query in queries}
    runs = {}
    for spec in args.results:
        route, path = spec.split("=", 1)
        runs[route] = load(Path(path))

    route_rows = {}
    hit_sets = {}
    type_counts = {}
    for route, run in runs.items():
        results = {row["query_id"]: row for row in run["results"]}
        metrics = []
        hit_sets[route] = {}
        type_counts[route] = Counter()
        for query in queries:
            items = results[query["id"]]["items"]
            metrics.append(query_metrics(query, items, results[query["id"]].get("latency_ms")))
            if query["answerable"]:
                hits, ids, _ = ranked_hits(query, items, args.k)
                hit_sets[route][query["id"]] = ids
                for anchor in hits:
                    type_counts[route][hit_type(items, anchor)] += 1
        answerable = [row for row in metrics if row["answerable"]]
        route_rows[route] = {
            "query_count": len(metrics),
            "answerable_count": len(answerable),
            "recall_at_k": sum(row[f"recall_at_{args.k}"] for row in answerable) / len(answerable),
            "primary_recall_at_k": sum(row[f"primary_recall_at_{args.k}"] for row in answerable) / len(answerable),
            "required_anchor_coverage_at_k": sum(row[f"required_anchor_coverage_at_{args.k}"] for row in answerable) / len(answerable),
            "solved_at_k": sum(row[f"solved_at_{args.k}"] for row in answerable) / len(answerable),
            "mrr_at_10": sum(row.get("mrr_at_10", 0.0) for row in answerable) / len(answerable),
            "ndcg_at_10": sum(row.get("ndcg_at_10", 0.0) for row in answerable) / len(answerable),
            "hard_negative_rate_at_10": sum(
                1 for row in answerable if row.get("hard_negative_hits_at_10", 0)
            ) / len(answerable),
            "no_answer_trap_fp_at_10": sum(
                row.get("no_answer_fp_at_10", 0.0) for row in metrics if not row["answerable"]
            ) / max(1, sum(1 for row in metrics if not row["answerable"])),
            "latency_ms_p50": statistics.median(
                [row["latency_ms"] for row in metrics if isinstance(row.get("latency_ms"), (int, float))]
            ) if any(isinstance(row.get("latency_ms"), (int, float)) for row in metrics) else None,
            "latency_ms_p95": sorted(
                [row["latency_ms"] for row in metrics if isinstance(row.get("latency_ms"), (int, float))]
            )[max(0, int(0.95 * len(metrics)) - 1)] if any(
                isinstance(row.get("latency_ms"), (int, float)) for row in metrics
            ) else None,
            "query_metrics": metrics,
        }

    fts = hit_sets.get("fts", {})
    vector = hit_sets.get("vector", {})
    contribution = {"fts_only": [], "vector_only": [], "both": [], "neither": []}
    for query in queries:
        if not query["answerable"] or query["id"] not in fts or query["id"] not in vector:
            continue
        f, v = fts[query["id"]], vector[query["id"]]
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
        "k": args.k,
        "routes": route_rows,
        "unique_contribution": contribution,
        "declaration_type_hits": {route: dict(counter) for route, counter in type_counts.items()},
        "per_query": {
            query["id"]: {
                "category": query["category"],
                "corpus": query["corpus"],
                "leakage": query["leakage"],
                "answerable": query["answerable"],
                "hits_at_k": {route: sorted(hit_sets.get(route, {}).get(query["id"], [])) for route in runs},
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
