#!/usr/bin/env python3
"""Run the explicitly authorized, contained M4 compaction-contention comparison."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import secrets
import subprocess
import sys
from typing import Any

BASE = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = BASE / "docs/benchmark-policy.json"
BINARY = BASE / ".work/build/durability-restore-20261020/m4_compaction_writer_contention"
DICTIONARY = BASE.parent.parent / ".local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
RESULTS = BASE / "results/lock-contention-microbench"
sys.path.insert(0, str(BASE / "tools"))
import m4_containment_runner as runner  # noqa: E402


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def host_context() -> dict[str, Any]:
    ps = subprocess.run(["ps", "-eo", "pid,ppid,comm,%cpu,rss,args", "--sort=-%cpu"],
                        capture_output=True, text=True, check=True, timeout=5).stdout.splitlines()[:24]
    affinity = subprocess.run(["taskset", "-pc", str(os.getpid())], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    units = subprocess.run(["systemctl", "--user", "list-units", "m4-contained-*", "--all",
                            "--no-legend", "--plain"], capture_output=True, text=True,
                           timeout=5)
    if units.returncode != 0:
        raise RuntimeError(f"cannot verify user M4 units: {units.stderr.strip()}")
    active = [line for line in units.stdout.splitlines() if ".service" in line and "not-found" not in line]
    return {"top_processes": ps, "operator_affinity": affinity,
            "preexisting_m4_units": active, "unit_list_raw": units.stdout}


def read_workload(stdout_path: pathlib.Path) -> dict[str, Any]:
    # The executable emits one valid, pretty-printed JSON document across lines.
    try:
        value = json.loads(stdout_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid M4 compaction-contention JSON: {stdout_path}: {exc}") from exc
    if isinstance(value, dict) and value.get("mode") == "m4_compaction_contention":
        return value
    raise RuntimeError(f"missing M4 compaction-contention result: {stdout_path}")


def verify(result: dict[str, Any], data: dict[str, Any], contract: dict[str, Any]) -> None:
    allowed = {"PASS", "COMPLETED_WITH_WORKSPACE_UNCERTAINTY", "COMPLETED_WITH_WORKSPACE_WARNINGS"}
    if result.get("status") not in allowed or result.get("workload_returncode") != 0:
        raise RuntimeError(f"contained microbenchmark did not complete safely: {result.get('status')} / {result.get('reason')}")
    cleanup = result.get("controller_cleanup") or result.get("unit_cleanup") or {}
    if cleanup.get("verified") is not True:
        raise RuntimeError("contained microbenchmark cleanup is unverified")
    workload = contract["workload"]
    docs, rounds = workload["documents"], workload["rounds"]
    if (data.get("mode") != "m4_compaction_contention" or
            data.get("documents") != docs or data.get("rounds") != rounds or
            data.get("writers") != workload["writers"] or
            data.get("ops_per_writer") != workload["ops_per_writer"] or
            data.get("writer_operations") != rounds or
            data.get("reader_queries") != workload["readers"] * workload["queries_per_reader"] or
            data.get("coherence_errors") != 0 or data.get("failures") != 0):
        raise RuntimeError("contention workload counts/coherence do not match the contract")
    arms = data.get("arms")
    timing_schema = data.get("timing_schema_version")
    if timing_schema not in (None, 2, 3, 4, 5):
        raise RuntimeError(f"unsupported timing schema version: {timing_schema}")
    expected_modes = set(workload.get("compaction_modes", ("default", "snapshot")))
    if timing_schema in (None, 2, 3):
        # Historical receipts predate the explicit ApplyBatch arm.
        expected_modes.discard("apply_batch")
    arm_order = data.get("arm_order")
    if (not isinstance(arms, list) or not isinstance(arm_order, list) or
            [arm.get("name") for arm in arms] != arm_order or
            len(arm_order) != len(expected_modes) or set(arm_order) != expected_modes):
        raise RuntimeError("compaction arms or order do not match the registered workload")
    def valid_series(values: Any, count: int) -> bool:
        return (isinstance(values, list) and len(values) == count and
                all(not isinstance(v, bool) and isinstance(v, (int, float)) and
                    math.isfinite(v) and v >= 0 for v in values))
    compaction_timing_fields = (
        "total_ms", "total_cpu_ms", "state_mutex_wait_ms",
        "state_mutex_wait_cpu_ms", "query_mutex_wait_ms",
        "query_mutex_wait_cpu_ms", "pending_flush_ms", "pending_flush_cpu_ms",
        "merge_prepare_ms", "merge_prepare_cpu_ms", "merge_build_ms",
        "merge_build_cpu_ms", "publish_state_mutex_wait_ms",
        "publish_state_mutex_wait_cpu_ms", "publish_query_mutex_wait_ms",
        "publish_query_mutex_wait_cpu_ms", "publication_ms",
        "publication_cpu_ms")
    for arm in arms:
        compaction = arm.get("compaction_diagnostics")
        # Older operation receipts predate lock-stage compaction diagnostics;
        # retain their exact recorded validity while checking new receipts.
        if timing_schema in (2, 3, 4, 5) and not isinstance(compaction, dict):
            raise RuntimeError("missing compaction phase timing object")
        if compaction is not None and not isinstance(compaction, dict):
            raise RuntimeError("invalid compaction phase timing object")
        if compaction is not None:
            required_compaction_fields = compaction_timing_fields
            if timing_schema in (3, 4, 5):
                required_compaction_fields += (
                    "state_mutex_hold_ms", "state_mutex_hold_cpu_ms")
            for key in required_compaction_fields:
                value = compaction.get(key)
                if (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or
                        not math.isfinite(value) or value < 0):
                    raise RuntimeError("missing/invalid compaction phase timing")
            if timing_schema in (3, 4, 5):
                hold = compaction["state_mutex_hold_ms"]
                if (hold + 1e-6 < compaction["publication_ms"] or
                        hold > compaction["total_ms"] + 1e-6):
                    raise RuntimeError(
                        "compaction state-lock hold lies outside total/publication timing")
            flush_stages = compaction.get("flush_stage_timing")
            if flush_stages is not None:
                stage_fields = (
                    "segment_build_ms", "segment_build_cpu_ms",
                    "segment_write_ms", "segment_write_cpu_ms",
                    "segment_open_ms", "segment_open_cpu_ms",
                    "query_mutex_wait_ms", "query_mutex_wait_cpu_ms",
                    "stats_append_ms", "stats_append_cpu_ms",
                    "tombstone_write_ms", "tombstone_write_cpu_ms",
                    "manifest_write_ms", "manifest_write_cpu_ms",
                    "wal_reset_ms", "wal_reset_cpu_ms", "gc_ms",
                    "gc_cpu_ms", "delta_reset_ms", "delta_reset_cpu_ms")
                if not isinstance(flush_stages, dict) or flush_stages.get("schema_version") != 1:
                    raise RuntimeError("invalid optional flush-stage timing object")
                for key in stage_fields:
                    value = flush_stages.get(key)
                    if (isinstance(value, bool) or
                            not isinstance(value, (int, float)) or
                            not math.isfinite(value) or value < 0):
                        raise RuntimeError("missing/invalid flush-stage timing")
                stage_wall_sum = sum(flush_stages[key] for key in (
                    "segment_build_ms", "segment_write_ms", "segment_open_ms",
                    "query_mutex_wait_ms", "stats_append_ms",
                    "tombstone_write_ms", "manifest_write_ms", "wal_reset_ms",
                    "gc_ms", "delta_reset_ms"))
                if stage_wall_sum > compaction["pending_flush_ms"] + 1e-3:
                    raise RuntimeError("flush-stage timing exceeds pending-flush duration")
        if timing_schema == 5:
            explicit_flush = arm.get("post_writer_flush_diagnostics")
            if not isinstance(explicit_flush, dict):
                raise RuntimeError("missing post-writer flush diagnostics")
            flush_fields = (
                "total_ms", "total_cpu_ms", "state_mutex_wait_ms",
                "state_mutex_wait_cpu_ms", "state_mutex_hold_ms",
                "state_mutex_hold_cpu_ms", "query_mutex_wait_ms",
                "query_mutex_wait_cpu_ms", "pending_flush_ms",
                "pending_flush_cpu_ms")
            for key in flush_fields:
                value = explicit_flush.get(key)
                if (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or
                        not math.isfinite(value) or value < 0):
                    raise RuntimeError("missing/invalid post-writer flush timing")
            if (explicit_flush["state_mutex_hold_ms"] + 1e-6 <
                    explicit_flush["pending_flush_ms"] or
                    explicit_flush["state_mutex_hold_ms"] >
                    explicit_flush["total_ms"] + 1e-6):
                raise RuntimeError("post-writer flush lock timing lies outside total")
            flush_stages = explicit_flush.get("flush_stage_timing")
            if not isinstance(flush_stages, dict) or flush_stages.get("schema_version") != 1:
                raise RuntimeError("missing/invalid post-writer flush-stage timing")
            stage_fields = (
                "segment_build_ms", "segment_build_cpu_ms",
                "segment_write_ms", "segment_write_cpu_ms",
                "segment_open_ms", "segment_open_cpu_ms",
                "query_mutex_wait_ms", "query_mutex_wait_cpu_ms",
                "stats_append_ms", "stats_append_cpu_ms",
                "tombstone_write_ms", "tombstone_write_cpu_ms",
                "manifest_write_ms", "manifest_write_cpu_ms",
                "wal_reset_ms", "wal_reset_cpu_ms", "gc_ms",
                "gc_cpu_ms", "delta_reset_ms", "delta_reset_cpu_ms")
            for key in stage_fields:
                value = flush_stages.get(key)
                if (isinstance(value, bool) or
                        not isinstance(value, (int, float)) or
                        not math.isfinite(value) or value < 0):
                    raise RuntimeError("missing/invalid post-writer flush-stage timing")
            stage_wall_sum = sum(flush_stages[key] for key in (
                "segment_build_ms", "segment_write_ms", "segment_open_ms",
                "query_mutex_wait_ms", "stats_append_ms",
                "tombstone_write_ms", "manifest_write_ms", "wal_reset_ms",
                "gc_ms", "delta_reset_ms"))
            if stage_wall_sum > explicit_flush["pending_flush_ms"] + 1e-3:
                raise RuntimeError("post-writer flush stages exceed flush duration")
        api_calls = rounds
        if timing_schema in (4, 5):
            batch_api = arm.get("name") == "apply_batch"
            expected_api = "ApplyBatch" if batch_api else "Upsert"
            expected_calls = workload["writers"] if batch_api else rounds
            if (arm.get("writer_api") != expected_api or
                    arm.get("writer_api_calls") != expected_calls or
                    arm.get("logical_upserts") != rounds):
                raise RuntimeError("writer API/count metadata does not match the contract")
            api_calls = expected_calls
        required_writer_series = (
            "writer_samples", "state_wait_samples", "state_wait_cpu_samples",
            "wal_append_samples", "wal_append_cpu_samples", "query_wait_samples",
            "apply_samples", "auto_flush_samples")
        for key in required_writer_series:
            if not valid_series(arm.get(key), api_calls):
                raise RuntimeError(f"missing/invalid writer timing series: {key}")
        if timing_schema in (3, 4, 5):
            for key in ("state_hold_samples", "state_hold_cpu_samples"):
                if not valid_series(arm.get(key), api_calls):
                    raise RuntimeError(f"missing/invalid writer timing series: {key}")
        # New binaries also emit normalization and WAL-encoding timings. Keep
        # replay compatibility with older raw receipts that predate these fields.
        for key in ("normalize_samples", "normalize_cpu_samples",
                    "wal_encode_samples", "wal_encode_cpu_samples"):
            if timing_schema in (2, 3, 4, 5) and not valid_series(arm.get(key), api_calls):
                raise RuntimeError(f"missing/invalid writer timing series: {key}")
            if key in arm and not valid_series(arm[key], api_calls):
                raise RuntimeError(f"invalid optional writer timing series: {key}")
        reader_count = workload["readers"] * workload["queries_per_reader"]
        if (not valid_series(arm.get("reader_samples"), reader_count) or
                not valid_series(arm.get("reader_mutex_wait_samples"), reader_count)):
            raise RuntimeError("missing/invalid reader latency or mutex-wait series")
        if (arm.get("writer_failures") != 0 or arm.get("reader_failures") != 0 or
                arm.get("durability_failures") != 0 or arm.get("runs_after_flush", 0) < 1):
            raise RuntimeError("writer, reader, durability, or reopen verification failed")
        if (arm.get("name") == "default" and arm.get("compaction_status") != "ok") or (
                arm.get("name") == "snapshot" and arm.get("compaction_status") not in
                ("ok", "compaction snapshot superseded by concurrent state change")) or (
                arm.get("name") == "snapshot_rebase" and
                arm.get("compaction_status") != "ok") or (
                arm.get("name") == "apply_batch" and
                arm.get("compaction_status") != "ok"):
            raise RuntimeError("unexpected compaction outcome")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true",
                        help="validate binary, dictionary, policy binding and command without host preflight or launch")
    args = parser.parse_args()
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        contract = policy["lock_contention_microbench"]
        resource_root = pathlib.Path(contract["resource_root"]).resolve(strict=True)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise SystemExit(f"cannot load M4 benchmark contract/resource root: {exc}") from exc
    if not BINARY.is_file() or not DICTIONARY.is_dir():
        raise SystemExit(f"missing required input: {BINARY}, {DICTIONARY}")
    dictionary_sha = runner.directory_sha256(DICTIONARY)
    binary_sha = sha256(BINARY)
    now = dt.datetime.now(dt.UTC)
    operation_id = "m4-lock-contention-microbench-" + now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    operation_root = RESULTS / now.strftime("%Y%m%d") / operation_id
    resource_workspace = resource_root / operation_id
    workspace_root = resource_workspace / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=False)
    ctx = {
        "schema_version": 1, "mode": runner.CONTENTION_MODE,
        "approval_status": contract["approval_status"],
        "approval_reference": contract["approval_reference"],
        "execution_authorization_reference": contract["execution_authorization_reference"],
        "risk_acceptance_reference": contract["risk_acceptance_reference"],
        "resource_basis": "Four-arm 8,192-document M4 fixture; each arm runs 4 concurrent writers, 2 readers (32 searches each), one compaction, flush and reopen verification. Three arms use 32 individual WAL-durable Upsert calls; the explicit batching arm uses four atomic WAL-durable ApplyBatch calls for the same 32 logical mutations. The 512 MiB cgroup cap, fixed 3 GiB host reserve, and all containment/integrity/cleanup gates remain unchanged.",
        "policy_sha256": sha256(POLICY_PATH),
        "operation_id": operation_id, "operation_root": str(operation_root.resolve()),
        "resource_root": str(resource_root),
        "purpose": contract["purpose"], "binary_path": str(BINARY.resolve()),
        "binary_sha256": binary_sha, "approved_binary_sha256": binary_sha,
        "dictionary_path": str(DICTIONARY.resolve()),
        "dictionary_tree_sha256": dictionary_sha,
        "approved_dictionary_tree_sha256": dictionary_sha,
        "workload": contract["workload"], "arm_role": "microbench",
        "io_pressure_mode": contract["io_pressure_mode"],
        **contract["resource_controls"],
    }
    runner.validate_contention_context(ctx)
    command = [str(BINARY.resolve()), str((workspace_root / "root").resolve()),
               str(DICTIONARY.resolve()), contract["workload"]["mode"],
               str(contract["workload"]["documents"]), str(contract["workload"]["rounds"])]
    if args.validate_only:
        print(json.dumps({"status": "VALIDATED_NO_LAUNCH", "operation_id": operation_id,
                          "binary_sha256": binary_sha, "dictionary_tree_sha256": dictionary_sha,
                          "policy_sha256": ctx["policy_sha256"], "command": command,
                          "resource_controls": contract["resource_controls"]}, sort_keys=True))
        # Avoid leaving a scratch directory for validation-only.
        workspace_root.rmdir()
        resource_workspace.rmdir()
        return 0

    operation_root.mkdir(parents=True, exist_ok=False)
    receipt_dir = operation_root / "microbench" / "containment"
    plan: dict[str, Any] = {
        "schema_version": 1, "operation_id": operation_id, "status": "PREFLIGHT",
        "benchmark_started": False, "contract": contract, "policy_sha256": ctx["policy_sha256"],
        "binary_path": str(BINARY.resolve()), "binary_sha256": binary_sha,
        "dictionary_path": str(DICTIONARY.resolve()), "dictionary_tree_sha256": dictionary_sha,
        "command": command, "resource_controls": contract["resource_controls"],
        "resource_workspace": str(resource_workspace), "evidence_class": contract["evidence_class"],
    }
    write_json(operation_root / "operation-plan.json", plan)
    try:
        plan["operator_host_context_before"] = host_context()
        plan["start_host_snapshot"] = runner.host_snapshot(resource_root)
        if plan["operator_host_context_before"]["preexisting_m4_units"]:
            result = {"status": "PREFLIGHT_BLOCKED", "benchmark_started": False,
                      "reason": "preexisting owned M4 units; refusing overlapping operation",
                      "blocking_failures": [{"gate_id": "M4-MICROBENCH-NO-OVERLAP"}]}
        else:
            env = os.environ.copy()
            env.pop("M4_BRIDGE_TRACE", None)
            result = runner.run_arm(
                command, receipt_dir, resource_root, workspace_root, workspace_root,
                contract["resource_controls"]["runtime_limit_seconds"], sample_interval_ms=100,
                budget=runner.contention_budget(ctx), env=env, enforce_host_gates=True,
                policy_context=ctx)
            plan["benchmark_started"] = bool(result.get("benchmark_started"))
            plan["contained_result"] = result
            if result.get("workload_returncode") == 0:
                data = read_workload(receipt_dir / "stdout.log")
                plan["workload"] = data
                verify(result, data, contract)
        plan["contained_result"] = result
        plan["end_host_snapshot"] = runner.host_snapshot(resource_root)
        plan["operator_host_context_after"] = host_context()
        plan["status"] = result.get("status", "OPERATION_INCOMPLETE")
        plan["measurement_validated"] = result.get("workload_returncode") == 0 and "workload" in plan
        return 0 if plan["measurement_validated"] else 2 if not plan["benchmark_started"] else 1
    except BaseException as exc:
        plan["status"] = "OPERATION_BLOCKED" if not plan["benchmark_started"] else "OPERATION_INCOMPLETE"
        plan["failure"] = f"{type(exc).__name__}: {exc}"
        try:
            plan["end_host_snapshot"] = runner.host_snapshot(resource_root)
            plan["operator_host_context_after"] = host_context()
        except BaseException as telemetry_exc:
            plan["end_snapshot_error"] = f"{type(telemetry_exc).__name__}: {telemetry_exc}"
        return 1
    finally:
        write_json(operation_root / "operation-plan.json", plan)
        checksums = []
        for item in sorted(operation_root.rglob("*")):
            if item.is_file() and item.name != "SHA256SUMS.txt":
                checksums.append(f"{sha256(item)}  {item.relative_to(operation_root).as_posix()}")
        (operation_root / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        write_json(RESULTS / "latest-operation.json", {"operation_id": operation_id,
                                                       "operation_root": str(operation_root),
                                                       "status": plan["status"]})


if __name__ == "__main__":
    raise SystemExit(main())
