"""Receipt builders shared by the lifecycle CLI compatibility shim."""
from __future__ import annotations

from typing import Any


def failure_receipt(
    operation_id: str,
    plan: dict[str, Any],
    arm_results: list[dict[str, Any]],
    error_text: str,
    quota_safety_stop: bool,
) -> dict[str, Any]:
    diagnostic = plan["operation_mode"] == "native_query_stage_diagnostic"
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "operation_mode": plan["operation_mode"],
        "status": ("DIAGNOSTIC_BLOCKED" if quota_safety_stop else "DIAGNOSTIC_ERROR")
        if diagnostic
        else "OPERATION_BLOCKED"
        if quota_safety_stop
        else "OPERATION_ERROR",
        "failure_class": "SAFETY_STOP" if quota_safety_stop else "IMPLEMENTATION_OR_OPERATION_ERROR",
        "operation_outcome": "SAFETY_STOP" if quota_safety_stop else "OPERATION_ERROR",
        "benchmark_started": any(
            arm.get("runner", {}).get("benchmark_started") for arm in arm_results
        ),
        "error": error_text,
        "arms": arm_results,
        "retry": "do not automatically retry after a refusal or safety stop; any future operation requires a fresh owner authorization and ID",
    }


def failure_receipt_filename(plan: dict[str, Any]) -> str:
    return (
        "native-query-stage-result.json"
        if plan["operation_mode"] == "native_query_stage_diagnostic"
        else "pair-result.json"
    )


def diagnostic_result(
    operation_id: str,
    plan: dict[str, Any],
    arm: dict[str, Any],
    post_cleanup_quota: dict[str, Any],
    source_commit: str,
) -> dict[str, Any]:
    workload_report = arm["workload_report"]
    runner_status = arm["runner"]["status"]
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "operation_mode": plan["operation_mode"],
        "status": "DIAGNOSTIC_COMPLETED"
        if runner_status == "PASS"
        else "DIAGNOSTIC_COMPLETED_WITH_CAVEATS",
        "measurement_status": runner_status,
        "scope": "single native Gate B lifecycle arm for FTS stage attribution only",
        "pair_comparison_performed": False,
        "query_latency_comparison": "NOT_RUN_NATIVE_ONLY_DIAGNOSTIC",
        "performance_eligible": False,
        "corpus": {
            "commit": source_commit,
            "manifest_sha256": plan["corpus_manifest_sha256"],
            "file_count": plan["corpus_file_count"],
            "bytes": plan["corpus_bytes"],
        },
        "execution_order": ["native"],
        "artifacts": plan["artifacts"],
        "native_profile_trace": arm.get("native_profile_trace"),
        "native_arm": arm,
        "native_workload_report": workload_report,
        "post_cleanup_workspace_quota": post_cleanup_quota,
        "limitations": [
            "single native-only diagnostic; no paired M4 comparison",
            "no performance or architecture-win claim",
            "if target stalls are not reproduced or stage spans are incomplete, attribution remains inconclusive",
        ],
    }


def pair_result(
    operation_id: str,
    plan: dict[str, Any],
    arm_results: list[dict[str, Any]],
    query_latency_gate: dict[str, Any],
    source_commit: str,
) -> dict[str, Any]:
    by_role = {arm["role"]: arm for arm in arm_results}
    native = by_role["native"]["workload_report"]
    m4 = by_role["m4"]["workload_report"]
    parity = {
        "mutations_equal": native["mutations"] == m4["mutations"],
        "indexed_file_count_equal": native["indexed_file_count"] == m4["indexed_file_count"],
        "final_marker_paths_equal": native["final_marker_paths"] == m4["final_marker_paths"],
        "post_reopen_marker_paths_equal": native["post_reopen_marker_paths"]
        == m4["post_reopen_marker_paths"],
        "same_pass_names": [p["name"] for p in native["passes"]]
        == [p["name"] for p in m4["passes"]],
    }
    parity["pass"] = all(parity.values())
    arm_statuses = [arm["runner"]["status"] for arm in arm_results]
    all_arms_clean_pass = all(status == "PASS" for status in arm_statuses)
    query_p95_comparable = query_latency_gate["comparable"]
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "status": "CORRECTNESS_MISMATCH"
        if not parity["pass"]
        else "PASS"
        if all_arms_clean_pass
        else "PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS",
        "measurement_status": "PASS"
        if all_arms_clean_pass
        else "COMPLETED_WITH_WORKSPACE_CAVEATS",
        "query_latency_comparison": "DESCRIPTIVE_ONLY"
        if query_p95_comparable
        else "NOT_COMPARABLE_SAMPLING_INCOMPLETE",
        "query_latency_p95_comparable_across_arms": query_p95_comparable,
        "query_latency_gate": query_latency_gate,
        "performance_eligible": False,
        "performance_interpretation": "one descriptive exploratory pair; not qualification or a causal/architecture-win claim",
        "corpus": {
            "commit": source_commit,
            "manifest_sha256": plan["corpus_manifest_sha256"],
            "file_count": plan["corpus_file_count"],
            "bytes": plan["corpus_bytes"],
        },
        "execution_order": plan["execution_order"],
        "artifacts": plan["artifacts"],
        "parity": parity,
        "arms": arm_results,
        "limitations": [
            "one repository snapshot",
            "one pair",
            "no cgroup io.stat/io.max",
            "host I/O pressure is observational",
            "fixed deterministic embedding model",
        ],
    }
