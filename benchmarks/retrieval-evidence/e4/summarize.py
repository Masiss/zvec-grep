#!/usr/bin/env python3
"""Aggregate repeated E4 route runs and per-query paired deltas."""
from __future__ import annotations
import argparse, json, statistics, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from evaluate import query_metrics  # noqa: E402

METRICS = ["recall_at_5","recall_at_10","recall_at_50","primary_recall_at_10","solved_at_10","solved_at_50","mrr_at_10","ndcg_at_10","hard_negative_rate_at_10","no_answer_fp_at_10","no_answer_any_result_at_10"]

def runs(matrix: Path, prefix: str, route: str) -> list[Path]:
    return [p for p in sorted(matrix.glob(f"{prefix}-{route}-r*.json")) if ".score." not in p.name]

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--matrix-dir", type=Path, required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--dataset", type=Path, default=HERE.parent / "dataset/queries.json")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    dataset = json.loads(args.dataset.read_text())["queries"]
    out = {"prefix": args.prefix, "routes": {}, "per_query": {}}
    for route in ("fts", "vector", "hybrid"):
        files = runs(args.matrix_dir, args.prefix, route)
        if not files:
            continue
        per_run = []
        per_query_runs = {}
        for path in files:
            run = json.loads(path.read_text())
            rows = {row["query_id"]: row for row in run["results"]}
            metrics = [query_metrics(q, rows[q["id"]]["items"], rows[q["id"]].get("latency_ms")) for q in dataset]
            answerable = [m for m in metrics if m["answerable"]]
            agg = {}
            for key in METRICS:
                values = [float(m[key]) for m in answerable if isinstance(m.get(key), (int, float))]
                if not values:
                    noans = [float(m[key]) for m in metrics if not m["answerable"] and isinstance(m.get(key), (int, float))]
                    values = noans
                agg[key] = statistics.fmean(values) if values else None
            latencies = [m["latency_ms"] for m in metrics if isinstance(m.get("latency_ms"), (int, float))]
            agg["latency_ms_p50"] = statistics.median(latencies) if latencies else None
            agg["latency_ms_p95"] = sorted(latencies)[max(0, __import__("math").ceil(0.95 * len(latencies)) - 1)] if latencies else None
            per_run.append(agg)
            for m in metrics:
                per_query_runs.setdefault(m["id"], []).append(m)
        summary = {}
        for key in METRICS:
            vals = [r[key] for r in per_run if r[key] is not None]
            if vals:
                summary[key] = {"mean": statistics.fmean(vals), "stdev": statistics.stdev(vals) if len(vals)>1 else 0.0, "min": min(vals), "max": max(vals)}
        for key in ("latency_ms_p50", "latency_ms_p95"):
            vals = [r[key] for r in per_run if r[key] is not None]
            if vals:
                summary[key] = {"mean": statistics.fmean(vals), "stdev": statistics.stdev(vals) if len(vals)>1 else 0.0, "min": min(vals), "max": max(vals)}
        summary["runs"] = [str(f.name) for f in files]
        summary["per_query"] = {
            qid: {k: (statistics.fmean(vals) if (vals := [m[k] for m in ms if isinstance(m.get(k), (int,float))]) else None) for k in ("recall_at_10","solved_at_10","mrr_at_10","ndcg_at_10")}
            for qid, ms in per_query_runs.items()
        }
        out["routes"][route] = summary
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
