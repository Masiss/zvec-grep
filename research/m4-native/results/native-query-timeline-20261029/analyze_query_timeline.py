#!/usr/bin/env python3
"""Attribute Rust FTS-call intervals to opt-in native query/flush timeline stages.

Uses Linux CLOCK_MONOTONIC bounds. Overlap is temporal evidence, not causal proof.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

COLLECTION_EVENT_RE = re.compile(
    r"NATIVE_PROFILE event=(\S+) stage=(\S+) object_id=(\S+) item_id=(-?\d+) "
    r"thread_id=(\d+) wall_start_ns=(\d+) wall_end_ns=(\d+) wall_ns=(\d+) "
    r"thread_cpu_ns=(\d+) clock_ok=(\d+)"
)
SEGMENT_EVENT_RE = re.compile(
    r"NATIVE_PROFILE event=(\S+) stage=(\S+) segment_id=(\d+) "
    r"wall_start_ns=(\d+) wall_end_ns=(\d+) wall_ns=(\d+) "
    r"thread_cpu_ns=(\d+) clock_ok=(\d+)"
)


def percentile(values: list[int], p: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (p * len(ordered) + 99) // 100)
    return ordered[rank - 1]


def stats(values: list[int]) -> dict[str, int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def union_ns(intervals: list[tuple[int, int]]) -> int:
    intervals = sorted((lo, hi) for lo, hi in intervals if hi > lo)
    if not intervals:
        return 0
    total = 0
    lo, hi = intervals[0]
    for next_lo, next_hi in intervals[1:]:
        if next_lo <= hi:
            hi = max(hi, next_hi)
        else:
            total += hi - lo
            lo, hi = next_lo, next_hi
    return total + hi - lo


def read_native_events(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    events: list[dict[str, Any]] = []
    matched = invalid = 0
    for line in path.read_text(errors="replace").splitlines():
        if "NATIVE_PROFILE event=" not in line:
            continue
        match = COLLECTION_EVENT_RE.search(line)
        if match:
            event, stage, object_id, item_id, thread_id, start, end, wall, cpu, clock_ok = match.groups()
            segment_id = None
        else:
            match = SEGMENT_EVENT_RE.search(line)
            if not match:
                continue
            event, stage, segment_id, start, end, wall, cpu, clock_ok = match.groups()
            object_id, item_id, thread_id = None, segment_id, None
        matched += 1
        start_i, end_i = int(start), int(end)
        if clock_ok != "1" or end_i < start_i or start_i == 0:
            invalid += 1
            continue
        events.append({
            "event": event,
            "stage": stage,
            "object_id": object_id,
            "item_id": int(item_id),
            "segment_id": int(segment_id) if segment_id is not None else None,
            "thread_id": int(thread_id) if thread_id is not None else None,
            "start_ns": start_i,
            "end_ns": end_i,
            "wall_ns": int(wall),
            "thread_cpu_ns": int(cpu),
        })
    return events, matched, invalid


def intersection(event: dict[str, Any], start: int, end: int) -> tuple[int, int] | None:
    lo = max(start, event["start_ns"])
    hi = min(end, event["end_ns"])
    return (lo, hi) if hi > lo else None


def event_summaries(events: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[f"{event['event']}:{event['stage']}"].append(event)
    result = {}
    for name, group in sorted(grouped.items()):
        walls = [e["wall_ns"] for e in group]
        cpus = [e["thread_cpu_ns"] for e in group]
        ratios = [e["thread_cpu_ns"] / e["wall_ns"] for e in group if e["wall_ns"]]
        result[name] = {
            "wall_ns": stats(walls),
            "thread_cpu_ns": stats(cpus),
            "thread_cpu_wall_ratio_p50": median(ratios) if ratios else None,
            "thread_cpu_wall_ratio_max": max(ratios) if ratios else None,
        }
    return result


def analyze_arm(arm: dict[str, Any], root: Path) -> dict[str, Any]:
    receipt = root / arm["arm_name"] / "containment"
    events, matched, invalid = read_native_events(receipt / "stderr.log")
    report = arm.get("workload_report", {})
    all_calls: list[int] = []
    all_cpu: list[int] = []
    by_family: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls_with_overlap": 0, "overlap_ns": [], "overlap_fraction": []}
    )
    long_calls: list[dict[str, Any]] = []
    passes: list[dict[str, Any]] = []
    origin_brackets: list[int] = []

    for pass_report in report.get("passes", []):
        name = pass_report.get("name")
        origin = pass_report.get("trace_origin_monotonic_ns")
        bracket = pass_report.get("trace_origin_monotonic_bracket_ns")
        if not isinstance(origin, int):
            continue
        if isinstance(bracket, int):
            origin_brackets.append(bracket)
        fts_records = pass_report.get("concurrent_query_native_fts_spans", [])
        response = pass_report.get("concurrent_query_attempt_response_us", [])
        service = pass_report.get("concurrent_query_service_us", [])
        queue = pass_report.get("concurrent_query_queue_delay_us", [])
        lag = pass_report.get("concurrent_query_start_lag_us", [])
        lock_wait = pass_report.get("concurrent_query_store_lock_wait_total_ns", [])
        calls = 0
        for query_index, wrapped in enumerate(fts_records):
            if not isinstance(wrapped, list) or len(wrapped) != 2:
                continue
            records, dropped = wrapped
            if not isinstance(records, list):
                continue
            for call_index, record in enumerate(records):
                if not isinstance(record, list) or len(record) != 3:
                    continue
                start_rel, end_rel, cpu_ns = map(int, record)
                start, end = origin + start_rel, origin + end_rel
                if end < start:
                    continue
                wall = end - start
                all_calls.append(wall)
                all_cpu.append(cpu_ns)
                calls += 1

                related = []
                for event in events:
                    overlap = intersection(event, start, end)
                    if overlap:
                        related.append((event, overlap[1] - overlap[0], overlap))
                owners = [x for x in related if x[0]["event"] == "collection_query" and x[0]["stage"] == "total"]
                owner = max(owners, key=lambda x: x[1])[0] if owners else None
                owner_tid = owner["thread_id"] if owner else None

                group_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
                group_counts: dict[str, int] = defaultdict(int)
                same_thread: dict[str, int] = defaultdict(int)
                for event, _, interval in related:
                    group = f"{event['event']}:{event['stage']}"
                    group_intervals[group].append(interval)
                    group_counts[group] += 1
                    if owner_tid is not None and event["thread_id"] is not None and event["thread_id"] == owner_tid:
                        same_thread[group] += 1
                group_union = {key: union_ns(value) for key, value in group_intervals.items()}
                for family, prefixes in {
                    "collection_query_stage": ("collection_query_stage:",),
                    "fts_recall_stage": ("fts_recall_stage:",),
                    "flush_stage": ("collection_flush_stage:", "segment_flush_stage:"),
                }.items():
                    matched_groups = [k for k in group_intervals if k.startswith(prefixes)]
                    intervals = [iv for key in matched_groups for iv in group_intervals[key]]
                    amount = union_ns(intervals)
                    summary = by_family[family]
                    if amount:
                        summary["calls_with_overlap"] += 1
                    summary["overlap_ns"].append(amount)
                    summary["overlap_fraction"].append(amount / wall if wall else 0.0)

                flush_intervals = [
                    iv for event, _, iv in related
                    if event["event"] in ("collection_flush_stage", "segment_flush_stage")
                ]
                flush_union = union_ns(flush_intervals)
                if wall >= 10_000_000:
                    related_sorted = sorted(related, key=lambda x: (x[1], x[0]["wall_ns"]), reverse=True)
                    long_calls.append({
                        "pass": name,
                        "query_index": query_index,
                        "call_index": call_index,
                        "wall_ns": wall,
                        "query_thread_cpu_ns": cpu_ns,
                        "query_thread_cpu_wall_ratio": cpu_ns / wall if wall else None,
                        "response_us": response[query_index] if query_index < len(response) else None,
                        "service_us": service[query_index] if query_index < len(service) else None,
                        "queue_delay_us": queue[query_index] if query_index < len(queue) else None,
                        "schedule_start_lag_us": lag[query_index] if query_index < len(lag) else None,
                        "store_lock_wait_total_ns": lock_wait[query_index] if query_index < len(lock_wait) else None,
                        "trace_origin_bracket_ns": bracket,
                        "collection_query_thread_id": owner_tid,
                        "collection_query_object_id": owner["object_id"] if owner else None,
                        "flush_stage_overlap_union_ns": flush_union,
                        "flush_stage_overlap_fraction": flush_union / wall if wall else None,
                        "overlap_by_event_stage": {
                            key: {
                                "event_count": group_counts[key],
                                "union_ns": group_union[key],
                                "same_query_thread_event_count": same_thread[key],
                            }
                            for key in sorted(group_union)
                        },
                        "largest_overlapping_native_events": [
                            {
                                "event": event["event"], "stage": event["stage"],
                                "overlap_ns": overlap_ns,
                                "event_wall_ns": event["wall_ns"],
                                "event_thread_cpu_ns": event["thread_cpu_ns"],
                                "event_thread_id": event["thread_id"],
                                "same_thread_as_collection_query": (
                                    event["thread_id"] == owner_tid
                                    if owner_tid is not None and event["thread_id"] is not None else None
                                ),
                                "object_id": event["object_id"], "item_id": event["item_id"],
                                "segment_id": event["segment_id"],
                            }
                            for event, overlap_ns, _ in related_sorted[:20]
                        ],
                        "span_dropped_count_for_query": dropped,
                    })

        passes.append({
            "pass": name,
            "origin_monotonic_ns": origin,
            "origin_bracket_ns": bracket,
            "offered": pass_report.get("concurrent_query_offered_slots"),
            "accepted": pass_report.get("concurrent_query_accepted_slots"),
            "dropped_slots": pass_report.get("concurrent_query_dropped_slots"),
            "started": pass_report.get("concurrent_query_started"),
            "completed": pass_report.get("concurrent_query_completed"),
            "reported_query_p95_us": pass_report.get("concurrent_query_p95_us"),
            "native_fts_span_records": len(fts_records),
            "native_fts_calls_traced": calls,
        })

    family_summary = {}
    for family, summary in sorted(by_family.items()):
        family_summary[family] = {
            "calls_with_overlap": summary["calls_with_overlap"],
            "overlap_ns_per_call": stats(summary["overlap_ns"]),
            "overlap_fraction_p50": median(summary["overlap_fraction"]) if summary["overlap_fraction"] else None,
            "overlap_fraction_max": max(summary["overlap_fraction"]) if summary["overlap_fraction"] else None,
        }
    long_calls.sort(key=lambda item: item["wall_ns"], reverse=True)
    return {
        "role": arm.get("role"),
        "arm_name": arm.get("arm_name"),
        "runner_status": arm.get("runner", {}).get("status"),
        "runner_reason": arm.get("runner", {}).get("reason"),
        "native_timeline_matched_lines": matched,
        "native_timeline_invalid_clock_records": invalid,
        "native_event_count": len(events),
        "native_event_summaries": event_summaries(events),
        "query_fts_call_count": len(all_calls),
        "query_fts_wall_ns": stats(all_calls),
        "query_thread_cpu_ns": stats(all_cpu),
        "query_thread_cpu_wall_ratio_p50": median([c / w for c, w in zip(all_cpu, all_calls) if w]) if all_calls else None,
        "query_calls_with_stage_overlap": family_summary,
        "calls_ge_10ms": sum(1 for wall in all_calls if wall >= 10_000_000),
        "calls_ge_100ms": sum(1 for wall in all_calls if wall >= 100_000_000),
        "top_fts_calls_ge_10ms": long_calls[:25],
        "origin_bracket_ns_max": max(origin_brackets) if origin_brackets else None,
        "passes": passes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation_root", type=Path)
    args = parser.parse_args()
    root = args.operation_root.resolve(strict=True)
    pair = json.loads((root / "pair-result.json").read_text())
    result = {
        "schema_version": 1,
        "operation_id": pair.get("operation_id"),
        "pair_status": pair.get("status"),
        "measurement_status": pair.get("measurement_status"),
        "performance_eligible": pair.get("performance_eligible"),
        "parity": pair.get("parity"),
        "diagnostic_only": True,
        "timebase": "Linux CLOCK_MONOTONIC; Rust pass-origin midpoint alignment carries the recorded bracket uncertainty.",
        "interpretation_limit": "Intervals can establish temporal overlap and per-thread CPU attribution only. They do not prove causation; absent overlap is limited by instrumentation coverage and clock alignment.",
        "arms": [analyze_arm(arm, root) for arm in pair.get("arms", [])],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
