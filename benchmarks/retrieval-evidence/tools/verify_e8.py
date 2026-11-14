#!/usr/bin/env python3
"""Independent E8 verification for CPU-vs-Vulkan embedding experiments.

This script does not import zg-engine. It validates raw vector-probe receipts,
checks finite/dimensional geometry, recomputes cosine/norm diagnostics, and
compares E6 query metrics. It can also be asked to verify that deliberately
corrupted receipts are rejected.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001 - verifier reports all malformed input
        fail(f"cannot read {path}: {error}")


def verify_vectors(cpu_path: Path, vulkan_path: Path) -> dict:
    cpu = load(cpu_path)
    vk = load(vulkan_path)
    if cpu.get("device") != "Cpu":
        fail(f"CPU receipt device is {cpu.get('device')!r}")
    if vk.get("device") != "Vulkan":
        fail(f"Vulkan receipt device is {vk.get('device')!r}")
    cpu_inputs = cpu.get("inputs")
    vk_inputs = vk.get("inputs")
    if not isinstance(cpu_inputs, list) or not cpu_inputs:
        fail("CPU receipt has no inputs")
    if not isinstance(vk_inputs, list) or len(vk_inputs) != len(cpu_inputs):
        fail("CPU/Vulkan input cardinality differs")
    rows = []
    for left, right in zip(cpu_inputs, vk_inputs):
        if left.get("id") != right.get("id"):
            fail("CPU/Vulkan input id order differs")
        if left.get("dim") != right.get("dim") or left.get("dim") != 768:
            fail(f"{left.get('id')}: unexpected dimension")
        if not left.get("finite") or not right.get("finite"):
            fail(f"{left.get('id')}: non-finite vector")
        a = left.get("vector")
        b = right.get("vector")
        if not isinstance(a, list) or not isinstance(b, list) or len(a) != left["dim"] or len(b) != left["dim"]:
            fail(f"{left.get('id')}: vector length mismatch")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in a + b):
            fail(f"{left.get('id')}: invalid vector component")
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            fail(f"{left.get('id')}: zero norm")
        cosine = dot / (na * nb)
        max_abs = max(abs(x - y) for x, y in zip(a, b))
        rows.append(
            {
                "id": left["id"],
                "tokens": left["tokens"],
                "cosine": cosine,
                "max_abs": max_abs,
                "norm_cpu": na,
                "norm_vulkan": nb,
                "relative_norm_delta": abs(na - nb) / na,
            }
        )
    summary = {
        "inputs": len(rows),
        "cosine_min": min(row["cosine"] for row in rows),
        "cosine_mean": sum(row["cosine"] for row in rows) / len(rows),
        "max_abs_max": max(row["max_abs"] for row in rows),
        "relative_norm_delta_max": max(row["relative_norm_delta"] for row in rows),
        "rows": rows,
    }
    if summary["cosine_min"] < 0.999:
        fail(f"cosine below pre-registered sanity floor: {summary['cosine_min']}")
    return summary


def query_metrics(path: Path) -> dict[str, dict]:
    report = load(path)
    if report.get("measurement_contract", {}).get("status") != "PASS":
        fail(f"{path}: measurement contract is not PASS")
    return {row["id"]: row for row in report.get("queries", [])}


def verify_retrieval(cpu_path: Path, vulkan_path: Path) -> dict:
    cpu = query_metrics(cpu_path)
    vk = query_metrics(vulkan_path)
    if set(cpu) != set(vk):
        fail("query id sets differ")
    metrics = [
        "recall_at_10",
        "primary_recall_at_10",
        "solved_at_10",
        "mrr_at_10",
        "ndcg_at_10",
        "recall_at_50",
        "group_coverage_at_10",
        "required_anchor_coverage_at_10",
        "no_answer_fp_at_10",
    ]
    diffs = []
    for query_id in sorted(cpu):
        changed = {}
        for metric in metrics:
            if metric in cpu[query_id] and cpu[query_id][metric] != vk[query_id].get(metric):
                changed[metric] = [cpu[query_id][metric], vk[query_id].get(metric)]
        if changed:
            diffs.append({"id": query_id, "changes": changed})
    return {"queries": len(cpu), "metric_differences": diffs}


def negate_vectors(path: Path) -> Path:
    payload = load(path)
    payload["inputs"][0]["vector"][0] = float("nan")
    target = path.with_name("e8-vectors-__negative__.json")
    target.write_text(json.dumps(payload))
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-vectors", type=Path)
    parser.add_argument("--vulkan-vectors", type=Path)
    parser.add_argument("--cpu-report", type=Path)
    parser.add_argument("--vulkan-report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--negative", action="store_true")
    args = parser.parse_args()
    if not args.cpu_vectors or not args.vulkan_vectors:
        fail("both --cpu-vectors and --vulkan-vectors are required")
    if args.negative:
        try:
            verify_vectors(negate_vectors(args.cpu_vectors), args.vulkan_vectors)
        except SystemExit as error:
            if error.code == 1:
                print("negative rejected as expected")
                return 0
            raise
        fail("corrupted vector receipt was accepted")
    output = {"vectors": verify_vectors(args.cpu_vectors, args.vulkan_vectors)}
    if args.cpu_report and args.vulkan_report:
        output["retrieval"] = verify_retrieval(args.cpu_report, args.vulkan_report)
    text = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
