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


def run(results: dict) -> dict:
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
            ],
            check=True,
        )
        return json.loads(out.read_text(encoding="utf-8"))


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
    perfect = {
        "dataset": "e3-01-v1",
        "run": {"route": "self-perfect", "limit": 50, "model": "synthetic", "root": "/x"},
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

    print("OK: perfect, trap, partial-chain, boundary, path, and duplicate metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
