#!/usr/bin/env python3
"""Classify development RAG failures from frozen packs and judge output."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent
ASSETS = ROOT / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/rag-r4-v1"
RAW = WORK / "development-r4-hybrid"
LEXICAL = WORK / "development-lexical-rg"
PACKS = WORK / "development-packs-v8"
PAIRS = WORK / "development-answer-pairs-host-codex-v1"
REPORT = WORK / "development-answer-report-v3.json"
OUTPUT = WORK / "development-failure-diagnosis-v1.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def overlaps(start: int, end: int, target: dict[str, Any]) -> bool:
    return max(start, target["start_line"]) <= min(end, target["end_line"])


def route_fragments(path: Path) -> dict[str, list[tuple[int, int]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: dict[str, list[tuple[int, int]]] = {}
    blocks = re.split(r"(?m)^File:\s*", text)[1:]
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        source_path = lines[0].strip()
        matches = [
            (int(start), int(end))
            for start, end in re.findall(r"(?m)^\s*Matched:\s*(\d+)-(\d+)", block)
        ]
        result[source_path] = matches
    return result


def hit_any(items: list[dict[str, Any]], target: dict[str, Any], *, field_start: str, field_end: str) -> bool:
    for item in items:
        if item.get("path") != target["path"]:
            continue
        start = item.get(field_start)
        end = item.get(field_end)
        if isinstance(start, list):
            start, end = start
        if isinstance(start, int) and isinstance(end, int) and overlaps(start, end, target):
            return True
    return False


def classify_step(
    *,
    profile: str,
    target: dict[str, Any],
    delivered: list[dict[str, Any]],
    raw_hybrid: list[dict[str, Any]],
    lexical_candidates: list[dict[str, Any]],
    route_hits: dict[str, dict[str, list[tuple[int, int]]]],
) -> str:
    if hit_any(delivered, target, field_start="start_line", field_end="end_line"):
        return "delivered"
    if profile == "lexical-rg":
        return (
            "lexical_selection_loss"
            if hit_any(lexical_candidates, target, field_start="start_line", field_end="end_line")
            else "lexical_acquisition_miss"
        )
    entity_hit = hit_any(raw_hybrid, target, field_start="range", field_end="range")
    content_hit = hit_any(
        raw_hybrid, target, field_start="content_range", field_end="content_range"
    )
    if content_hit:
        return "hybrid_delivery_cap_selection_loss"
    if entity_hit:
        return "hybrid_fragment_selection_loss"
    route_hit = any(
        any(overlaps(start, end, target) for start, end in route_hits[route].get(target["path"], []))
        for route in ("fts", "vector")
    )
    file_hit = any(target["path"] in route_hits[route] for route in ("fts", "vector"))
    if route_hit:
        return "hybrid_fusion_or_rank_loss"
    if file_hit:
        return "route_file_hit_fragment_miss"
    return "route_acquisition_miss"


def main() -> None:
    prompts = load(ASSETS / "prompts.json")
    gold = load(ASSETS / "gold.json")
    report = load(REPORT)
    gold_by_id = {row["task_id"]: row for row in gold["tasks"]}
    prompt_by_id = {row["task_id"]: row for row in prompts["tasks"]}
    report_by_id = {row["task_id"]: row for row in report["cases"]}
    categories: dict[str, Counter[str]] = {
        "lexical-rg": Counter(),
        "r4-hybrid": Counter(),
    }
    step_rows = []
    answer_rows = []

    for task_id, gold_task in gold_by_id.items():
        if prompt_by_id[task_id]["split"] != "development":
            continue
        targets_by_step = {}
        ranges = {row["range_id"]: row for row in gold_task["accepted_source_ranges"]}
        for step in gold_task["required_steps"]:
            targets_by_step[step["step_id"]] = [
                ranges[range_id] for range_id in step["accepted_range_ids"]
            ]
        lexical_delivery = load(PACKS / task_id / "lexical-rg.json")["evidence_items"]
        hybrid_delivery = load(PACKS / task_id / "r4-hybrid.json")["evidence_items"]
        sufficient_delivery = load(PACKS / task_id / "sufficient-pack.json")["evidence_items"]
        lexical_candidates = load(LEXICAL / task_id / "rg-ranked-candidates.json")
        raw_hybrid = load(RAW / task_id / "hybrid.json").get("result", {}).get("items", [])
        route_hits = {
            route: route_fragments(RAW / task_id / f"{route}-pool.txt")
            for route in ("fts", "vector")
        }
        for step_id, targets in targets_by_step.items():
            if not targets:
                continue
            for profile, delivered in (
                ("lexical-rg", lexical_delivery),
                ("r4-hybrid", hybrid_delivery),
            ):
                states = [
                    classify_step(
                        profile=profile,
                        target=target,
                        delivered=delivered,
                        raw_hybrid=raw_hybrid,
                        lexical_candidates=lexical_candidates,
                        route_hits=route_hits,
                    )
                    for target in targets
                ]
                if "delivered" not in states:
                    category = next((state for state in states if state != "delivered"), "missing")
                    categories[profile][category] += 1
                step_rows.append(
                    {
                        "task_id": task_id,
                        "step_id": step_id,
                        "profile": profile,
                        "target_states": states,
                        "step_delivered": "delivered" in states,
                    }
                )

        judged = report_by_id[task_id]
        for profile in ("lexical-rg", "r4-hybrid", "sufficient-pack"):
            trial = judged["profiles"][profile]["judge"]["scores"]
            evidence = judged["evidence"][profile][0]
            failed_dimensions = [name for name, score in trial.items() if score < 2]
            complete = evidence["evidence_complete"]
            if gold_task["outcome"] == "abstain":
                cause = (
                    "abstention_answer_failure"
                    if failed_dimensions
                    else "abstention_answer_pass"
                )
            elif not complete:
                cause = "evidence_incomplete"
            elif failed_dimensions:
                cause = "answer_or_citation_failure_with_complete_evidence"
            else:
                cause = "fully_correct"
            answer_rows.append(
                {
                    "task_id": task_id,
                    "profile": profile,
                    "cause": cause,
                    "failed_dimensions": failed_dimensions,
                    "evidence_complete": complete,
                    "evidence_coverage": [
                        evidence["covered_steps"],
                        evidence["required_steps"],
                    ],
                }
            )

    sufficient_answer_failures = [
        row for row in answer_rows
        if row["profile"] == "sufficient-pack"
        and row["cause"] == "answer_or_citation_failure_with_complete_evidence"
    ]
    result = {
        "schema_version": 1,
        "split": "development",
        "task_count": len(report_by_id),
        "answerable_failure_counts": {
            profile: dict(counts) for profile, counts in categories.items()
        },
        "answer_failure_rows": answer_rows,
        "required_step_source_rows": step_rows,
        "sufficient_pack_answer_failures": sufficient_answer_failures,
        "interpretation": (
            "A complete sufficient pack that still misses rubric dimensions is evidence of an answer-side "
            "failure. Route-pool, hybrid raw result, lexical candidate, and delivered-pack membership "
            "separate acquisition from ranking/fragment selection; this remains development-only evidence."
        ),
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "task_count": result["task_count"],
                "answerable_failure_counts": result["answerable_failure_counts"],
                "sufficient_pack_answer_failures": len(sufficient_answer_failures),
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
