#!/usr/bin/env python3
"""Synthetic metric tests for the E3-01 evaluator; no retrieval engine."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate import overlaps, query_metrics  # noqa: E402
DATASET = HERE.parent / "dataset" / "queries.json"


def run(results: dict, extra: list[str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "results.json"
        out = Path(directory) / "report.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                str(HERE / "evaluate.py"),
                "--dataset",
                str(DATASET),
                "--results",
                str(path),
                "--output",
                str(out),
                *(extra or []),
            ],
            check=True,
        )
        return json.loads(out.read_text(encoding="utf-8"))


def rejected(results: dict) -> bool:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "results.json"
        out = Path(directory) / "report.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        done = subprocess.run(
            [
                sys.executable,
                str(HERE / "evaluate.py"),
                "--dataset",
                str(DATASET),
                "--results",
                str(path),
                "--output",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        return done.returncode == 2 and not out.exists()


def candidates(anchors: list[dict]) -> list[dict]:
    return [
        {
            "rank": rank,
            "path": anchor["path"],
            "start_line": anchor["start_line"],
            "end_line": anchor["end_line"],
            "score": 1.0,
            "kind": "synthetic",
            "excerpt": "",
        }
        for rank, anchor in enumerate(anchors, start=1)
    ]


def main() -> int:
    queries = json.loads(DATASET.read_text(encoding="utf-8"))["queries"]
    contract = {
        "status": "PASS",
        "intended_route": "self-perfect",
        "expected": {"route_modes": ["synthetic"]},
        "actual": {"route_modes": ["synthetic"]},
    }
    perfect = {
        "dataset": "e3-01-v1",
        "run": {
            "route": "self-perfect",
            "limit": 50,
            "model": "synthetic",
            "root": "/x",
            "measurement_contract": contract,
        },
        "results": [
            {"query_id": query["id"], "latency_ms": 1.0, "items": candidates(query["evidence"])}
            for query in queries
        ],
    }
    report = run(perfect)["overall"]
    assert report["recall_at_10"] == 1.0
    assert report["required_anchor_coverage_at_10"] == 1.0
    assert report["solved_at_10"] == 1.0
    assert report["no_answer_fp_at_10"] == 0.0

    trap = json.loads(json.dumps(perfect))
    for row, query in zip(trap["results"], queries):
        if not query["answerable"]:
            row["items"] = candidates(query["hard_negatives"])
    report = run(trap)["overall"]
    assert report["no_answer_fp_at_10"] == 1.0, report
    assert report["no_answer_any_result_at_10"] == 1.0

    partial = json.loads(json.dumps(perfect))
    for row, query in zip(partial["results"], queries):
        if query["category"] == "multi_hop":
            row["items"] = candidates(query["evidence"][:1])
    report = run(partial)
    row = next(item for item in report["queries"] if item["category"] == "multi_hop")
    assert row["solved_at_10"] == 0, row
    assert row["required_anchor_coverage_at_10"] < 1.0, row
    # Boundary, path, and duplicate-fragment fixture.
    query = next(item for item in queries if item["answerable"] and len(item["evidence"]) >= 2)
    anchor = query["evidence"][0]
    boundary = {
        "path": anchor["path"],
        "start_line": anchor["end_line"],
        "end_line": anchor["end_line"],
    }
    assert overlaps(boundary, anchor)
    assert not overlaps({**boundary, "path": "wrong/" + anchor["path"]}, anchor)
    duplicate = [boundary, dict(boundary)]
    hits, ids, first = __import__("evaluate").ranked_hits(query, duplicate, 10)
    assert len(hits) == 1 and len(ids) == 1 and first == 1
    perfect_row = next(row for row in report["queries"] if row["id"] == query["id"])
    assert perfect_row["ndcg_at_10"] <= 1.0

    # The contract gate must reject missing, failed, or mismatched contracts.
    missing = json.loads(json.dumps(perfect))
    del missing["run"]["measurement_contract"]
    assert rejected(missing)
    failed = json.loads(json.dumps(perfect))
    failed["run"]["measurement_contract"]["status"] = "FAIL"
    assert rejected(failed)
    mismatch = json.loads(json.dumps(perfect))
    mismatch["run"]["measurement_contract"]["actual"]["route_modes"] = ["fts", "vector"]
    assert rejected(mismatch)
    # The explicit audit flag is the only way to score a historical result.
    legacy = json.loads(json.dumps(perfect))
    del legacy["run"]["measurement_contract"]
    run(legacy, ["--allow-uncontracted"])

    print("OK: metrics, contract pass, and contract rejection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
