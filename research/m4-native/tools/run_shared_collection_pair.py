#!/usr/bin/env python3
"""Run one bounded exploratory immutable/shared M4 comparison per operation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import secrets
import subprocess
import time
from typing import Any

BASE = pathlib.Path(__file__).resolve().parents[1]
RUNNER_PATH = BASE / "tools/m4_containment_runner.py"
CAPTURE_PATH = BASE / "results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/capture_host_baseline.py"
BUILD = BASE / ".work/build/m4-stage-trace-bridge-test"
BINARY = BUILD / "m4_bridge_integration"
DICTIONARY = BASE.parent.parent / ".local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
POLICY_PATH = BASE / "docs/benchmark-policy.json"
UTC_DATE = time.strftime("%Y%m%d", time.gmtime())
RESULTS = BASE / f"results/shared-collection-pair-{UTC_DATE}"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def context_for(runner, contract, operation_id, operation_root, arm_role, binary_sha, dictionary_sha):
    ctx = {
        "schema_version": 1,
        "mode": runner.CONTENTION_MODE,
        "approval_status": contract["approval_status"],
        "approval_reference": contract["approval_reference"],
        "execution_authorization_reference": contract["execution_authorization_reference"],
        "risk_acceptance_reference": contract["risk_acceptance_reference"],
        "resource_basis": "owner-approved single exploratory pair; operation-specific 1-GiB cgroup and 3-GiB host reserve",
        "policy_sha256": sha256(POLICY_PATH),
        "operation_id": operation_id,
        "operation_root": str(operation_root.resolve()),
        "resource_root": contract["resource_root"],
        "purpose": contract["purpose"],
        "binary_path": str(BINARY.resolve()),
        "binary_sha256": binary_sha,
        "approved_binary_sha256": binary_sha,
        "dictionary_path": str(DICTIONARY.resolve()),
        "dictionary_tree_sha256": dictionary_sha,
        "approved_dictionary_tree_sha256": dictionary_sha,
        "workload": contract["workload"],
        "arm_role": arm_role,
        "io_pressure_mode": contract["io_pressure_mode"],
    }
    ctx.update(contract["resource_controls"])
    return runner.validate_contention_context(ctx)


def host_context():
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


def workload_json(stdout_path: pathlib.Path) -> dict[str, Any]:
    candidates = []
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append(value)
    for value in candidates:
        if ("docs" in value or "documents" in value) and "rounds" in value:
            return value
    raise RuntimeError(f"no sustained-workload receipt in {stdout_path}")


def verify_arm(result: dict[str, Any], data: dict[str, Any], role: str) -> None:
    allowed = {"PASS", "COMPLETED_WITH_WORKSPACE_UNCERTAINTY", "COMPLETED_WITH_WORKSPACE_WARNINGS"}
    if result.get("status") not in allowed or result.get("workload_returncode") != 0:
        raise RuntimeError(f"{role} arm did not complete safely: {result.get('status')} / {result.get('reason')}")
    cleanup = result.get("controller_cleanup") or result.get("unit_cleanup") or {}
    if cleanup.get("verified") is not True:
        raise RuntimeError(f"{role} arm cleanup is unverified")
    if data.get("docs", data.get("documents")) != 500 or data.get("rounds") != 5:
        raise RuntimeError(f"{role} output does not match 500x5 contract")
    if data.get("coherence_errors") != 0:
        raise RuntimeError(f"{role} correctness/coherence failure: {data.get('coherence_errors')}")
    if role == "shared-collection":
        if data.get("completed_rounds") != 5 or data.get("failures") != 0:
            raise RuntimeError("shared-collection arm incomplete or failed")
        if data.get("native_live_docs") != 500 or data.get("m4_live_docs") != 500:
            raise RuntimeError("shared-collection final live counts mismatch")
        for field in ("writer_commit_ms", "writer_gc_ms", "writer_cycle_ms"):
            values = data.get(field)
            if not isinstance(values, list) or len(values) != 5:
                raise RuntimeError(f"shared-collection arm missing five {field} samples")
        for field in ("reader_total_ms", "reader_thread_cpu_ms", "reader_pin_wall_ms",
                      "reader_snapshot_lock_wait_ms", "reader_m4_query_lock_wait_ms",
                      "reader_vector_stage_ms", "reader_fts_stage_ms", "reader_merge_stage_ms"):
            values = data.get(field)
            if (not isinstance(values, list) or len(values) != 20 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float)) or
                        not math.isfinite(value) or value < 0 for value in values)):
                raise RuntimeError(f"shared-collection arm missing valid 20-sample {field} series")
    else:
        if data.get("queries") != 20 or len(data.get("commit_samples_ms", [])) != 5:
            raise RuntimeError("immutable-shard arm missing required writer/reader samples")
        if len(data.get("reader_samples_ms", [])) != 20:
            raise RuntimeError("immutable-shard arm missing concurrent-reader samples")


def recovery_capture(runner, context, out_path, resource_root):
    capture = load_module("m4_shared_pair_host_capture", CAPTURE_PATH)
    samples, capture_meta = capture.capture(
        31.0, None,
        sample_function=lambda: runner.host_snapshot(resource_root),
    )
    write_json(out_path, {"capture": capture_meta, "sample_count": len(samples),
                          "sample_interval_seconds": capture.SAMPLE_S,
                          "raw_samples": samples, "windows": [],
                          "status": "CAPTURED_PENDING_VALIDATION",
                          "io_pressure_interpretation": "observational only; no PSI admission gate"})
    if len(samples) < 300:
        raise RuntimeError(f"inter-arm host capture too sparse: {len(samples)} samples")
    start_ns = samples[0]["monotonic_ns"]
    windows = []
    for i in range(3):
        lo, hi = start_ns + i * 10_000_000_000, start_ns + (i + 1) * 10_000_000_000
        bucket = [s for s in samples if lo <= s["monotonic_ns"] < hi]
        if len(bucket) < 80 or bucket[-1]["monotonic_ns"] - bucket[0]["monotonic_ns"] < 9_000_000_000:
            raise RuntimeError(f"inter-arm recovery window {i + 1} has insufficient valid coverage")
        times = [s["monotonic_ns"] for s in bucket]
        max_gap_ns = max((b - a for a, b in zip(times[:-1], times[1:], strict=True)), default=0)
        if max_gap_ns > context["max_monitor_gap_seconds"] * 1e9:
            raise RuntimeError(f"inter-arm recovery window {i + 1} telemetry gap exceeds contract")
        vm_keys = ("pswpin_pages", "pswpout_pages", "pgscan_direct", "oom_kill")
        if any(bucket[j]["vmstat"][key] < bucket[j - 1]["vmstat"][key]
               for j in range(1, len(bucket)) for key in vm_keys):
            raise RuntimeError(f"inter-arm recovery window {i + 1} vmstat counter reset")
        worst = dict(bucket[-1])
        worst["meminfo_kib"] = dict(bucket[-1]["meminfo_kib"])
        worst["meminfo_kib"]["MemAvailable_kib"] = min(s["meminfo_kib"]["MemAvailable_kib"] for s in bucket)
        worst["meminfo_kib"]["SwapFree_kib"] = min(s["meminfo_kib"]["SwapFree_kib"] for s in bucket)
        worst["resource_root_free_bytes"] = min(s["resource_root_free_bytes"] for s in bucket)
        worst["loadavg"] = [max(s["loadavg"][0] for s in bucket), *bucket[-1]["loadavg"][1:]]
        for family in ("io_pressure", "memory_pressure", "cpu_pressure"):
            worst[family] = {kind: dict(values) for kind, values in bucket[-1][family].items()}
            for kind, values in worst[family].items():
                values["avg10"] = max(s[family][kind]["avg10"] for s in bucket)
                values["total"] = max(s[family][kind]["total"] for s in bucket)
        decision = runner.contention_decision(worst, context, "recovery")
        blocked = [] if decision["continuation_allowed"] else decision["blocking_failures"]
        windows.append({
            "index": i + 1,
            "sample_count": len(bucket),
            "duration_seconds": (times[-1] - times[0]) / 1e9,
            "max_sample_gap_seconds": max_gap_ns / 1e9,
            "memavailable_min_kib": worst["meminfo_kib"]["MemAvailable_kib"],
            "var_tmp_free_min_bytes": worst["resource_root_free_bytes"],
            "io_full_avg10_range_percent": [min(s["io_pressure"]["full"]["avg10"] for s in bucket),
                                            max(s["io_pressure"]["full"]["avg10"] for s in bucket)],
            "decision": decision,
            "blocking_failures": blocked,
            "status": "VALID" if not blocked else "BLOCKED",
        })
    if any(w["status"] != "VALID" for w in windows):
        raise RuntimeError("inter-arm capacity/integrity recovery gate failed")
    write_json(out_path, {"capture": capture_meta, "sample_count": len(samples),
                          "sample_interval_seconds": capture.SAMPLE_S, "windows": windows,
                          "raw_samples": samples,
                          "io_pressure_interpretation": "observational only; no PSI admission gate"})
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true", help="validate pinned inputs and command bindings without host preflight or workload launch")
    args = parser.parse_args()
    if not BINARY.is_file() or not DICTIONARY.is_dir():
        raise SystemExit(f"missing built harness or Jieba dictionary: {BINARY} / {DICTIONARY}")
    runner = load_module("m4_shared_pair_containment", RUNNER_PATH)
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load approved operation contract: {exc}") from exc
    contract = policy["shared_collection_pair"]
    resource_root = pathlib.Path(contract["resource_root"]).resolve(strict=True)
    operation_id = f"m4-shared-collection-pair-{UTC_DATE}-{time.strftime('%H%M%S', time.gmtime())}-{secrets.token_hex(3)}"
    operation_root = (RESULTS / operation_id).resolve()
    workspace_base = (resource_root / operation_id).resolve()
    binary_sha, dictionary_sha = sha256(BINARY), runner.directory_sha256(DICTIONARY)
    order = ["immutable-shard", "shared-collection"]
    if secrets.randbelow(2):
        order.reverse()
    for role in order:
        ctx = context_for(runner, contract, operation_id, operation_root, role, binary_sha, dictionary_sha)
        mode = contract["workload"]["baseline_mode"] if role == "immutable-shard" else contract["workload"]["candidate_mode"]
        command = [str(BINARY.resolve()), str((workspace_base / ("pair-first" if role == order[0] else "pair-second") / "root").resolve()), str(DICTIONARY.resolve()), mode, "500", "5"]
        if (len(command) != 6 or command[-3:] != [mode, "500", "5"] or
                ctx["binary_sha256"] != sha256(pathlib.Path(command[0]))):
            raise RuntimeError(f"invalid {role} command binding")
    if args.validate_only:
        print(json.dumps({"status": "VALIDATED_NO_LAUNCH", "policy_sha256": sha256(POLICY_PATH),
                          "binary_sha256": binary_sha, "dictionary_tree_sha256": dictionary_sha,
                          "order_sample": order, "modes": [contract["workload"]["baseline_mode"],
                          contract["workload"]["candidate_mode"]]}, sort_keys=True))
        return 0
    operation_root.mkdir(parents=True, exist_ok=False)
    workspace_base.mkdir(parents=True, exist_ok=False)
    plan = {
        "schema_version": 1, "operation_id": operation_id,
        "status": "PREFLIGHT_PENDING", "policy_sha256": sha256(POLICY_PATH),
        "binary_path": str(BINARY.resolve()), "binary_sha256": binary_sha,
        "dictionary_path": str(DICTIONARY.resolve()), "dictionary_tree_sha256": dictionary_sha,
        "randomized_arm_order": order, "workload": contract["workload"],
        "resource_controls": contract["resource_controls"],
        "io_pressure_mode": contract["io_pressure_mode"],
        "operator_host_context": host_context(),
        "start_host_snapshot": runner.host_snapshot(resource_root),
        "evidence_class": contract["evidence_class"],
    }
    first_context = context_for(runner, contract, operation_id, operation_root, order[0], binary_sha, dictionary_sha)
    preflight = runner.contention_decision(plan["start_host_snapshot"], first_context, "pair-first")
    plan["preflight_decision"] = preflight
    inherited_unsafe_env = [key for key in os.environ if key == "LD_PRELOAD" or key.startswith("M4_FAULT")]
    if plan["operator_host_context"]["preexisting_m4_units"]:
        preflight["blocking_failures"].append({"gate_id": "M4-PAIR-NO-OVERLAP", "detail": "preexisting owned M4 units exist"})
        preflight["continuation_allowed"] = False
    if inherited_unsafe_env:
        preflight["blocking_failures"].append({"gate_id": "M4-PAIR-ENVIRONMENT", "variable_names": inherited_unsafe_env})
        preflight["continuation_allowed"] = False
    if not preflight["continuation_allowed"]:
        plan["status"] = "PREFLIGHT_BLOCKED"
        plan["benchmark_started"] = False
        write_json(operation_root / "operation-plan.json", plan)
        files = sorted(p for p in operation_root.rglob("*") if p.is_file())
        (operation_root / "SHA256SUMS.txt").write_text(
            "".join(f"{sha256(p)}  {p.relative_to(operation_root).as_posix()}\n" for p in files), encoding="utf-8")
        write_json(RESULTS / "latest-operation.json", {"operation_id": operation_id,
                                                         "operation_root": str(operation_root),
                                                         "status": plan["status"]})
        return 2
    plan["status"] = "RUNNING"
    write_json(operation_root / "operation-plan.json", plan)
    (operation_root / "host-before.json").write_text(json.dumps(plan["start_host_snapshot"], indent=2) + "\n")
    arm_results: list[dict[str, Any]] = []
    try:
        for index, role in enumerate(order):
            arm_name = "pair-first" if index == 0 else "pair-second"
            arm_context = context_for(runner, contract, operation_id, operation_root, role, binary_sha, dictionary_sha)
            arm_workspace = workspace_base / arm_name
            arm_workspace.mkdir(parents=True, exist_ok=False)
            mode = contract["workload"]["baseline_mode"] if role == "immutable-shard" else contract["workload"]["candidate_mode"]
            command = [str(BINARY.resolve()), str(arm_workspace / "root"), str(DICTIONARY.resolve()), mode, "500", "5"]
            receipt_dir = operation_root / arm_name / "containment"
            arm_env = os.environ.copy()
            arm_env.pop("M4_BRIDGE_TRACE", None)
            result = runner.run_arm(
                command, receipt_dir, resource_root, arm_workspace, arm_workspace,
                contract["resource_controls"]["runtime_limit_seconds"],
                sample_interval_ms=100, budget=runner.contention_budget(arm_context),
                env=arm_env, enforce_host_gates=True, policy_context=arm_context)
            data = workload_json(receipt_dir / "stdout.log") if result.get("workload_returncode") == 0 else {}
            arm = {"arm_name": arm_name, "role": role, "result": result, "workload": data}
            arm_results.append(arm)
            write_json(operation_root / f"{arm_name}-summary.json", arm)
            if result.get("status") == "PREFLIGHT_BLOCKED":
                raise RuntimeError(f"{arm_name} blocked before workload launch")
            verify_arm(result, data, role)
            if index == 0:
                recovery_context = context_for(runner, contract, operation_id, operation_root,
                                               order[1], binary_sha, dictionary_sha)
                recovery_capture(runner, recovery_context, operation_root / "interarm-recovery.json", resource_root)
        plan["status"] = "COMPLETED_WITH_WORKSPACE_CAVEATS" if any(
            arm["result"]["status"] != "PASS" for arm in arm_results) else "COMPLETED"
        plan["benchmark_started"] = True
        plan["arms"] = [{"arm_name": a["arm_name"], "role": a["role"],
                         "status": a["result"]["status"],
                         "workload_returncode": a["result"].get("workload_returncode")} for a in arm_results]
        plan["end_host_snapshot"] = runner.host_snapshot(resource_root)
        plan["end_operator_host_context"] = host_context()
        write_json(operation_root / "operation-plan.json", plan)
        return 0
    except BaseException as exc:
        plan["status"] = "OPERATION_BLOCKED" if not arm_results else "OPERATION_INCOMPLETE"
        plan["benchmark_started"] = bool(arm_results)
        plan["failure"] = f"{type(exc).__name__}: {exc}"
        plan["arms_completed"] = [{"arm_name": a["arm_name"], "role": a["role"],
                                   "status": a["result"].get("status"),
                                   "workload_returncode": a["result"].get("workload_returncode")} for a in arm_results]
        plan["end_host_snapshot"] = runner.host_snapshot(resource_root)
        plan["end_operator_host_context"] = host_context()
        write_json(operation_root / "operation-plan.json", plan)
        return 1
    finally:
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
