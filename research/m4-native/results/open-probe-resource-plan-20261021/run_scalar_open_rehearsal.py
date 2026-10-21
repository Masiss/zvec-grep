#!/usr/bin/env python3
"""One-use, owner-approved 84-open resource rehearsal; never a latency benchmark."""
import hashlib
import importlib.util
import json
import os
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parents[2]
RUNNER_PATH = BASE / "tools/m4_containment_runner.py"
BUILD = BASE / ".work/build/durability-restore-20261020"
BINARY = BUILD / "m4_native_scalar_open_resource_rehearsal"
RESOURCE_ROOT = pathlib.Path("/var/tmp").resolve(strict=True)  # noqa: S108 -- explicitly approved M4 scratch filesystem
OP_ID = "m4-scalar-open-rehearsal-20261021"
ARTIFACT_ROOT = pathlib.Path(__file__).resolve().parent / OP_ID
OPERATION_ROOT = ARTIFACT_ROOT.resolve()
WORKSPACE_PARENT = RESOURCE_ROOT / OP_ID
WORKSPACE = WORKSPACE_PARENT / "workspace"

spec = importlib.util.spec_from_file_location("m4_containment_runner", RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

if ARTIFACT_ROOT.exists() or WORKSPACE_PARENT.exists():
    raise SystemExit("refusing to reuse an existing rehearsal artifact/workspace path")
if not BINARY.is_file():
    raise SystemExit("native rehearsal binary is missing")
ARTIFACT_ROOT.mkdir(parents=True)
WORKSPACE.mkdir(parents=True)
binary_sha = hashlib.sha256(BINARY.read_bytes()).hexdigest()
context = {
    "schema_version": 1,
    "mode": runner.CONTENTION_MODE,
    "approval_status": "approved",
    "approval_reference": "User approval in active conversation: 3 GiB MemAvailable / 8 GiB free disk for this rehearsal only",
    "execution_authorization_reference": "User instruction: perform one fresh readiness check and at most one 0-6 scalar resource rehearsal",
    "risk_acceptance_reference": "User instruction: retain 1 GiB RAM, swap 0, CPU 200%, 128 tasks, correctness/telemetry/distress/verified cleanup; no retry on formal FAIL",
    "resource_basis": "One serial M4 rehearsal: seven scalar counts x HNSW/FLAT x three reps; create/close/reopen/close; 84 native opens, no docs or queries",
    "policy_sha256": hashlib.sha256(runner.POLICY_PATH.read_bytes()).hexdigest(),
    "operation_id": OP_ID,
    "operation_root": str(OPERATION_ROOT),
    "resource_root": str(RESOURCE_ROOT),
    "purpose": "m4-scalar-open-resource-rehearsal",
    "binary_path": str(BINARY.resolve()),
    "binary_sha256": binary_sha,
    "approved_binary_sha256": binary_sha,
    "workload": {"scalar_counts": list(range(7)), "vector_types": ["HNSW", "FLAT"],
                  "repetitions": 3, "api_opens": 84, "documents": 0, "queries": 0},
    "memory_max_bytes": 1024**3,
    "memory_swap_max_bytes": 0,
    "cpu_max": "200000 100000",
    "pids_max": 128,
    "workspace_max_allocated_bytes": 1024**3,
    "workspace_max_apparent_bytes": 1024**3,
    "host_memory_reserve_bytes": 3 * 1024**3,
    "disk_reserve_bytes": 8 * 1024**3,
    "runtime_limit_seconds": 20,
    "max_monitor_gap_seconds": 2,
}
runner.validate_contention_context(context)
readiness_state = runner.host_snapshot(RESOURCE_ROOT)
readiness = runner.contention_decision(readiness_state, context, "baseline")
runner.write_json(ARTIFACT_ROOT / "fresh-readiness.json", {
    "timestamp_utc": runner.utc_now(), "workload": context["workload"],
    "context_digest": runner.contention_context_digest(context),
    "state": readiness_state, "decision": readiness,
})
if not readiness["continuation_allowed"]:
    runner.write_json(ARTIFACT_ROOT / "operation-result.json", {
        "status": "PREFLIGHT_BLOCKED", "benchmark_started": False,
        "gate_failures": readiness["blocking_failures"], "recovery": "No retry; await owner-directed new readiness check.",
    })
    if WORKSPACE.exists() and not any(WORKSPACE.iterdir()):
        WORKSPACE.rmdir()
    if WORKSPACE_PARENT.exists() and not any(WORKSPACE_PARENT.iterdir()):
        WORKSPACE_PARENT.rmdir()
    runner.write_json(ARTIFACT_ROOT / "workspace-cleanup.json", {
        "verified": not WORKSPACE_PARENT.exists(), "path": str(WORKSPACE_PARENT),
        "method": "pre-launch empty-directory check then rmdir; no recursive deletion",
    })
    print(json.dumps({"status": "PREFLIGHT_BLOCKED", "failures": readiness["blocking_failures"]}, indent=2))
    raise SystemExit(2)

receipt_dir = ARTIFACT_ROOT / "aa-first" / "containment"
command = [str(BINARY.resolve()), str(WORKSPACE.resolve() / "root"), "--m4-scalar-open-resource-rehearsal"]
env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH"}}
result = runner.run_arm(command, receipt_dir, RESOURCE_ROOT, WORKSPACE, WORKSPACE,
                        20.0, 100, runner.contention_budget(context), env,
                        enforce_host_gates=True, policy_context=context)
runner.write_json(ARTIFACT_ROOT / "operation-result.json", result)
if result.get("status") == "PASS":
    # Independent owned-path cleanup verification; retain everything on any ambiguity.
    if WORKSPACE.exists() and not any(WORKSPACE.iterdir()):
        WORKSPACE.rmdir()
        if WORKSPACE_PARENT.exists() and not any(WORKSPACE_PARENT.iterdir()):
            WORKSPACE_PARENT.rmdir()
    clean = not WORKSPACE_PARENT.exists()
    runner.write_json(ARTIFACT_ROOT / "workspace-cleanup.json", {
        "verified": clean, "path": str(WORKSPACE_PARENT),
        "method": "empty-directory check then rmdir; no recursive deletion",
    })
    if not clean:
        result["status"] = "CLEANUP_UNVERIFIED"
        result["operation_outcome"] = "OPERATION_BLOCKED"
        runner.write_json(ARTIFACT_ROOT / "operation-result.json", result)
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result.get("status") == "PASS" else 2)
