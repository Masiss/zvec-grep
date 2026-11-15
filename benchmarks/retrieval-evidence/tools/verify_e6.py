#!/usr/bin/env python3
"""Independent E6 verifier for retrieval runs and their measurement contract.

This deliberately does not import evaluate.py.  It re-derives recall, solved,
MRR and nDCG from the frozen queries and raw hits, checks the declared route
against the normalized/observed route plan, and rejects contaminated receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

KS = (5, 10, 50)


def overlaps(item: dict, anchor: dict) -> bool:
    start, end = item.get("start_line"), item.get("end_line")
    return (
        item.get("path") == anchor.get("path")
        and isinstance(start, int)
        and isinstance(end, int)
        and start <= anchor["end_line"]
        and end >= anchor["start_line"]
    )


def anchor_rank(item: dict, anchor: dict) -> int | None:
    if not overlaps(item, anchor):
        return None
    rank = item.get("rank")
    return rank if isinstance(rank, int) else None


def metrics(query: dict, items: list[dict]) -> dict:
    items = sorted(items, key=lambda item: item.get("rank", 10**9))
    evidence = query.get("evidence", [])
    required_groups = set(query.get("solved_when", {}).get("required_groups", []))
    required_ids = set(query.get("solved_when", {}).get("required_anchor_ids", []))
    min_primary = query.get("solved_when", {}).get("min_primary_evidence", 1)
    row = {"id": query["id"], "answerable": bool(query["answerable"])}
    if not query["answerable"]:
        negatives = query.get("hard_negatives", [])
        for k in (5, 10):
            traps = sum(
                1 for item in items[:k] for negative in negatives if overlaps(item, negative)
            )
            row[f"no_answer_fp_at_{k}"] = 1 if traps else 0
            row[f"no_answer_any_result_at_{k}"] = 1 if items[:k] else 0
        row["hard_negative_hits_at_10"] = sum(
            1 for item in items[:10] for negative in negatives if overlaps(item, negative)
        )
        return row

    def first_hits(k: int) -> tuple[set[int], set[str], int, int]:
        hit_indexes: set[int] = set()
        first_rank: int | None = None
        primary = 0
        for index, anchor in enumerate(evidence):
            ranks = [
                anchor_rank(item, anchor)
                for item in items[:k]
                if anchor_rank(item, anchor) is not None
            ]
            if ranks:
                hit_indexes.add(index)
                first_rank = min(first_rank or 10**9, min(ranks))
                if anchor.get("role") == "primary":
                    primary += 1
        ids = {evidence[index]["id"] for index in hit_indexes}
        return hit_indexes, ids, (first_rank or 0), primary

    for k in KS:
        hit_indexes, hit_ids, first, primary = first_hits(k)
        groups = {evidence[index]["group"] for index in hit_indexes}
        primary_total = sum(1 for anchor in evidence if anchor.get("role") == "primary")
        row[f"recall_at_{k}"] = len(hit_indexes) / len(evidence) if evidence else 0.0
        row[f"primary_recall_at_{k}"] = primary / primary_total if primary_total else 0.0
        row[f"group_coverage_at_{k}"] = (
            len(required_groups & groups) / len(required_groups) if required_groups else 0.0
        )
        row[f"required_anchor_coverage_at_{k}"] = (
            len(required_ids & hit_ids) / len(required_ids) if required_ids else 0.0
        )
        row[f"solved_at_{k}"] = int(required_ids.issubset(hit_ids) and primary >= min_primary)
        row[f"hard_negative_hits_at_{k}"] = sum(
            1
            for item in items[:k]
            for negative in query.get("hard_negatives", [])
            if overlaps(item, negative)
        )
        if k == 10:
            row["mrr_at_10"] = (1.0 / first) if first else 0.0
            gains = []
            for anchor in evidence:
                ranks = [
                    anchor_rank(item, anchor)
                    for item in items[:10]
                    if anchor_rank(item, anchor) is not None
                ]
                if ranks:
                    gains.append(
                        (2.0 if anchor.get("role") == "primary" else 1.0)
                        / math.log2(min(ranks) + 1)
                    )
            ideal = sorted(
                (2.0 if anchor.get("role") == "primary" else 1.0 for anchor in evidence),
                reverse=True,
            )[:10]
            idcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(ideal))
            row["ndcg_at_10"] = sum(gains) / idcg if idcg else 0.0
    return row


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def aggregate(rows: list[dict]) -> dict:
    answerable = [row for row in rows if row["answerable"]]
    no_answer = [row for row in rows if not row["answerable"]]
    out = {
        "answerable_queries": len(answerable),
        "no_answer_queries": len(no_answer),
    }
    for k in KS:
        for metric in (
            "recall",
            "primary_recall",
            "group_coverage",
            "required_anchor_coverage",
            "solved",
        ):
            key = f"{metric}_at_{k}"
            out[key] = mean([float(row[key]) for row in answerable])
    for key in ("mrr_at_10", "ndcg_at_10"):
        out[key] = mean([float(row[key]) for row in answerable])
    out["no_answer_fp_at_10"] = mean(
        [float(row["no_answer_fp_at_10"]) for row in no_answer]
    )
    out["no_answer_any_result_at_10"] = mean(
        [float(row["no_answer_any_result_at_10"]) for row in no_answer]
    )
    out["no_answer_hard_negative_hits_at_10"] = sum(
        int(row["hard_negative_hits_at_10"]) for row in no_answer
    )
    return out


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_layout(run: dict) -> str:
    root = Path(run.get("run", {}).get("root", ""))
    manifest = root / ".zvec-grep" / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        descriptor = data["index"]["descriptor"]["defaultEmbeddingModel"]
        return f"{descriptor['maxInputTokens']}:{descriptor['dimension']}"
    except Exception:
        return "unknown"


def route_modes(contract: dict) -> list[str]:
    return list(contract.get("actual", {}).get("route_modes", []))


def check_contract(run: dict, dataset_sha: str) -> list[str]:
    failures: list[str] = []
    contract = run.get("run", {}).get("measurement_contract")
    if not isinstance(contract, dict):
        return ["measurement contract missing"]
    if contract.get("status") != "PASS":
        failures.append(f"contract status {contract.get('status')!r}")
    if contract.get("intended_route") != run.get("run", {}).get("route"):
        failures.append("declared route differs from contract")
    expected = contract.get("expected", {}).get("route_modes")
    actual = contract.get("actual", {}).get("route_modes")
    if expected != actual:
        failures.append(f"expected/actual route modes differ: {expected!r} != {actual!r}")
    if contract.get("failures"):
        failures.append(f"{len(contract['failures'])} contract failures")
    dataset = contract.get("dataset", {})
    if dataset.get("sha256") != dataset_sha:
        failures.append("dataset sha256 differs from the verifier's dataset")
    if dataset.get("hash_status") != "PASS":
        failures.append("dataset hash status is not PASS")
    model = contract.get("model", {})
    if model.get("status") != "PASS" or model.get("requested") != model.get("indexed"):
        failures.append("index model identity is not PASS")
    return failures


def check_provenance(run: dict) -> list[str]:
    route = run.get("run", {}).get("route")
    expected = {
        "fts": {"fts", "lexical"},
        "vector": {"vector"},
        "hybrid": {"fts", "vector", "fts+vector", "lexical"},
        "hybrid-symbol": {"fts", "vector", "fts+vector", "lexical"},
    }.get(route)
    if expected is None:
        return [f"unknown route {route!r}"]
    failures = []
    for row in run.get("results", []):
        for item in row.get("items", []):
            matched = item.get("matched_by")
            if matched not in expected:
                failures.append(
                    f"{row.get('query_id')}: item {item.get('path')} matched_by={matched!r} "
                    f"outside {sorted(expected)}"
                )
            groups = item.get("query_group_ids")
            if not groups:
                failures.append(
                    f"{row.get('query_id')}: item {item.get('path')} has no query group"
                )
    return failures


def compare_report(overall: dict, report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    evaluator = report.get("overall", {})
    failures = []
    for key, value in overall.items():
        if key not in evaluator:
            continue
        if isinstance(value, (int, float)) and abs(float(value) - float(evaluator[key])) > 1e-9:
            failures.append(
                f"aggregate {key}: verifier={value} evaluator={evaluator[key]}"
            )
    return failures


def canonical_hits(run: dict) -> str:
    rows = []
    for row in sorted(run.get("results", []), key=lambda item: item["query_id"]):
        items = [
            (
                item.get("rank"),
                item.get("path"),
                item.get("start_line"),
                item.get("end_line"),
                item.get("score"),
            )
            for item in sorted(row.get("items", []), key=lambda item: item.get("rank", 10**9))
        ]
        rows.append((row["query_id"], items))
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--results", required=True, type=Path, nargs="+", action="append"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    dataset_bytes = args.dataset.read_bytes()
    dataset_sha = hashlib.sha256(dataset_bytes).hexdigest()
    dataset = json.loads(dataset_bytes)
    queries = dataset["queries"]
    by_id = {query["id"]: query for query in queries}

    output = {
        "dataset": dataset.get("dataset"),
        "dataset_sha256": dataset_sha,
        "results": [],
        "cross_model": [],
        "status": "PASS",
    }
    failures: list[str] = []
    loaded: list[tuple[Path, dict]] = []
    result_paths = [path for group in args.results for path in group]
    for path in result_paths:
        run = json.loads(path.read_text(encoding="utf-8"))
        run_failures = check_contract(run, dataset_sha) + check_provenance(run)
        rows = []
        for row in run.get("results", []):
            query = by_id.get(row.get("query_id"))
            if query is None:
                run_failures.append(f"unknown query {row.get('query_id')}")
                continue
            rows.append(metrics(query, row.get("items", [])))
        if len(rows) != len(queries):
            run_failures.append(f"{len(rows)} rows for {len(queries)} queries")
        overall = aggregate(rows)
        if args.report:
            run_failures.extend(compare_report(overall, args.report))
        entry = {
            "path": str(path),
            "sha256": file_sha256(path),
            "route": run.get("run", {}).get("route"),
            "contract_status": run.get("run", {})
            .get("measurement_contract", {})
            .get("status"),
            "route_modes": route_modes(run.get("run", {}).get("measurement_contract", {})),
            "index_layout": index_layout(run),
            "overall": overall,
            "failures": run_failures,
        }
        output["results"].append(entry)
        failures.extend(f"{path.name}: {failure}" for failure in run_failures)
        loaded.append((path, run))

    by_route: dict[str, list[tuple[Path, dict]]] = {}
    for path, run in loaded:
        route = run.get("run", {}).get("route")
        by_route.setdefault(route, []).append((path, run))
    for route, runs in by_route.items():
        if route != "fts":
            continue
        by_layout: dict[str, list[tuple[Path, dict]]] = {}
        for path, run in runs:
            by_layout.setdefault(index_layout(run), []).append((path, run))
        for layout, layout_runs in by_layout.items():
            if len(layout_runs) < 2:
                output["cross_model"].append(
                    {
                        "route": route,
                        "layout": layout,
                        "runs": {
                            f"{path.parent.name}/{path.name}": canonical_hits(run)
                            for path, run in layout_runs
                        },
                        "status": "SINGLE_RUN",
                    }
                )
                continue
            hashes = {
                f"{path.parent.name}/{path.name}": canonical_hits(run)
                for path, run in layout_runs
            }
            same = len(set(hashes.values())) == 1
            output["cross_model"].append(
                {
                    "route": route,
                    "layout": layout,
                    "runs": hashes,
                    "status": "PASS" if same else "FAIL",
                }
            )
            if not same:
                failures.append(
                    f"pure FTS hit sets differ across model collections at layout {layout}"
                )

    if failures:
        output["status"] = "FAIL"
        output["failures"] = failures
    text = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    if failures:
        print(f"E6 verification FAILED: {len(failures)} failures", file=sys.stderr)
        for failure in failures[:10]:
            print(f"  {failure}", file=sys.stderr)
        return 2
    print("E6 verification PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
