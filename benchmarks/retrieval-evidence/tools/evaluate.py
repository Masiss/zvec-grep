#!/usr/bin/env python3
"""Score retrieval runs against the frozen E3-01 evidence dataset.

Candidate relevance is line-overlap with an annotated evidence range on the same
corpus-relative path.  A hard negative never becomes relevant.  No-answer
queries use false positives at K, because no corpus evidence exists by design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

KS = (5, 10, 50)


def overlaps(item: dict, anchor: dict) -> bool:
    start = item.get("start_line")
    end = item.get("end_line")
    return (
        item.get("path") == anchor.get("path")
        and isinstance(start, int)
        and isinstance(end, int)
        and start <= anchor["end_line"]
        and end >= anchor["start_line"]
    )


def ranked_hits(query: dict, items: list[dict], k: int) -> tuple[list[dict], set[str], int | None]:
    """Return unique evidence anchors hit, their ids, and first relevant rank."""
    evidence = query.get("evidence", [])
    hit_indexes: set[int] = set()
    first: int | None = None
    for rank, item in enumerate(items[:k], start=1):
        for index, anchor in enumerate(evidence):
            if index not in hit_indexes and overlaps(item, anchor):
                hit_indexes.add(index)
                if first is None:
                    first = rank
    hits = [evidence[index] for index in sorted(hit_indexes)]
    return hits, {anchor["id"] for anchor in hits}, first


def query_metrics(query: dict, items: list[dict], latency_ms: float | None = None) -> dict:
    evidence = query.get("evidence", [])
    required = set(query.get("solved_when", {}).get("required_groups", []))
    result = {
        "id": query["id"],
        "category": query["category"],
        "corpus": query.get("corpus", "unknown"),
        "leakage": query.get("leakage", "none"),
        "answerable": query["answerable"],
        "items": len(items),
        "latency_ms": latency_ms,
    }
    if not query["answerable"]:
        negatives = query.get("hard_negatives", [])
        for k in (5, 10):
            traps = sum(
                1 for item in items[:k] for negative in negatives if overlaps(item, negative)
            )
            result[f"no_answer_fp_at_{k}"] = 1 if traps else 0
            result[f"no_answer_any_result_at_{k}"] = 1 if items[:k] else 0
        result["hard_negative_hits"] = sum(
            1 for item in items[:10] for negative in negatives if overlaps(item, negative)
        )
        return result
    required_ids = set(query.get("solved_when", {}).get("required_anchor_ids", []))
    for k in KS:
        hits, hit_ids, first = ranked_hits(query, items, k)
        groups = {anchor["group"] for anchor in hits}
        primary_hits = sum(1 for anchor in hits if anchor["role"] == "primary")
        result[f"recall_at_{k}"] = len(hits) / len(evidence) if evidence else 0.0
        result[f"primary_recall_at_{k}"] = (
            primary_hits / sum(1 for anchor in evidence if anchor["role"] == "primary")
            if any(anchor["role"] == "primary" for anchor in evidence)
            else 0.0
        )
        result[f"group_coverage_at_{k}"] = len(required & groups) / len(required) if required else 0.0
        result[f"required_anchor_coverage_at_{k}"] = (
            len(required_ids & hit_ids) / len(required_ids) if required_ids else 0.0
        )
        result[f"solved_at_{k}"] = int(
            required_ids.issubset(hit_ids)
            and primary_hits >= query["solved_when"].get("min_primary_evidence", 1)
        )
        result[f"hard_negative_hits_at_{k}"] = sum(
            1 for item in items[:k] for negative in query.get("hard_negatives", []) if overlaps(item, negative)
        )
        if k == 10:
            result["mrr_at_10"] = (1.0 / first) if first else 0.0
            gains = []
            for anchor in hits:
                # nDCG must use the rank where each anchor first appears.
                anchor_rank = None
                for rank, item in enumerate(items[:k], start=1):
                    if (
                        item.get("path") == anchor["path"]
                        and isinstance(item.get("start_line"), int)
                        and item["start_line"] <= anchor["end_line"]
                        and item["end_line"] >= anchor["start_line"]
                    ):
                        anchor_rank = rank
                        break
                if anchor_rank:
                    gain = 2.0 if anchor["role"] == "primary" else 1.0
                    gains.append(gain / math.log2(anchor_rank + 1))
            ideal = sorted(
                [2.0 if a["role"] == "primary" else 1.0 for a in evidence], reverse=True
            )[:k]
            idcg = sum(gain / math.log2(i + 2) for i, gain in enumerate(ideal))
            result["ndcg_at_10"] = sum(gains) / idcg if idcg else 0.0
    return result


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def bootstrap_ci(values: list[float], seed: int, rounds: int = 2000) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    samples = []
    for _ in range(rounds):
        samples.append(mean([values[rng.randrange(len(values))] for _ in values]))
    samples.sort()
    return [samples[int(rounds * 0.025)], samples[int(rounds * 0.975)]]


def aggregate(rows: list[dict], seed: int) -> dict:
    answerable = [row for row in rows if row["answerable"]]
    no_answer = [row for row in rows if not row["answerable"]]
    out = {
        "answerable_queries": len(answerable),
        "no_answer_queries": len(no_answer),
    }
    for k in KS:
        for metric in ("recall", "primary_recall", "group_coverage", "required_anchor_coverage", "solved"):
            key = f"{metric}_at_{k}"
            values = [float(row[key]) for row in answerable]
            out[key] = mean(values)
            out[f"{key}_ci95"] = bootstrap_ci(values, seed)
    for metric in ("mrr_at_10", "ndcg_at_10"):
        values = [float(row[metric]) for row in answerable]
        out[metric] = mean(values)
        out[f"{metric}_ci95"] = bootstrap_ci(values, seed)
    latencies = [row["latency_ms"] for row in answerable if isinstance(row.get("latency_ms"), (int, float))]
    out["latency_ms_p50"] = statistics.median(latencies) if latencies else None
    out["latency_ms_p95"] = (
        sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
    )
    out["no_answer_fp_at_10"] = mean([float(row["no_answer_fp_at_10"]) for row in no_answer])
    out["no_answer_any_result_at_10"] = mean(
        [float(row["no_answer_any_result_at_10"]) for row in no_answer]
    )
    out["no_answer_hard_negative_hits_at_10"] = sum(
        int(row["hard_negative_hits"]) for row in no_answer
    )
    out["hard_negative_rate_at_10"] = mean(
        [1.0 if row.get("hard_negative_hits_at_10", 0) else 0.0 for row in rows]
    )
    return out


def group_rows(rows: list[dict], key: str, seed: int) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key))].append(row)
    return {name: aggregate(items, seed) for name, items in sorted(grouped.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("dataset/queries.json"))
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--bootstrap-seed", type=int, default=20261112)
    args = parser.parse_args()
    dataset_bytes = args.dataset.read_bytes()
    queries = json.loads(dataset_bytes)["queries"]
    run = json.loads(args.results.read_text(encoding="utf-8"))
    results_by_id = {item["query_id"]: item for item in run.get("results", [])}
    items_by_id = {key: value.get("items", []) for key, value in results_by_id.items()}
    missing = [query["id"] for query in queries if query["id"] not in items_by_id]
    if missing:
        print(f"missing results for {len(missing)} queries: {missing[:5]}", file=sys.stderr)
        return 2
    rows = [
        query_metrics(query, items_by_id[query["id"]], results_by_id[query["id"]].get("latency_ms"))
        for query in queries
    ]
    report = {
        "schema_version": 1,
        "dataset": run.get("dataset", "e3-01-v1"),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "route": run.get("run", {}).get("route", "unknown"),
        "retrieval": run.get("run", {}),
        "overall": aggregate(rows, args.bootstrap_seed),
        "by_category": group_rows(rows, "category", args.bootstrap_seed),
        "by_corpus": group_rows(rows, "corpus", args.bootstrap_seed),
        "by_leakage": group_rows(rows, "leakage", args.bootstrap_seed),
        "queries": rows,
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    if args.markdown:
        overall = report["overall"]
        lines = [
            f"# E3-01 evaluation: {report['route']}",
            "",
            f"Dataset SHA-256: `{report['dataset_sha256']}`",
            "",
            "| Metric | Value | 95% CI |",
            "|---|---:|---:|",
        ]
        for metric in ("recall_at_10", "primary_recall_at_10", "group_coverage_at_10", "required_anchor_coverage_at_10", "ndcg_at_10", "mrr_at_10"):
            ci = overall.get(f"{metric}_ci95", [0.0, 0.0])
            lines.append(f"| {metric} | {overall[metric]:.4f} | [{ci[0]:.4f}, {ci[1]:.4f}] |")
        lines += [
            "",
            f"No-answer trap false-positive rate @10: {overall['no_answer_fp_at_10']:.4f}",
            f"No-answer any-result rate @10: {overall['no_answer_any_result_at_10']:.4f}",
            f"No-answer hard-negative hits @10: {overall['no_answer_hard_negative_hits_at_10']}",
            f"Answerable hard-negative rate @10: {overall['hard_negative_rate_at_10']:.4f}",
            "",
        ]
        args.markdown.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
