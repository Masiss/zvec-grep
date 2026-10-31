#!/usr/bin/env python3
"""Run the one owner-authorized bounded M4 0–6 scalar-open latency diagnostic."""
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
import tempfile
from typing import Any

BASE = pathlib.Path(__file__).resolve().parents[1]
RUNNER_PATH = BASE / "tools/m4_containment_runner.py"
BUILD = BASE / ".work/build/durability-restore-20261020"
BINARY = BUILD / "m4_native_scalar_open_latency_diagnostic"
RESOURCE_ROOT = pathlib.Path(os.environ.get("M4_RESOURCE_ROOT") or tempfile.gettempdir()).resolve(strict=True)
RESULTS_ROOT = BASE / "results/scalar-open-latency-diagnostic-20261021"
OP_ID = "m4-scalar-open-latency-diagnostic-20261021-r1"
ARTIFACT_ROOT = RESULTS_ROOT / OP_ID
WORKSPACE_PARENT: pathlib.Path | None = None
WORKSPACE: pathlib.Path | None = None
MEMORY_RESERVE_BYTES = 3 * 1024**3
DISK_RESERVE_BYTES = 8 * 1024**3
WORKLOAD = {
    "scalar_counts": list(range(7)),
    "vector_types": ["HNSW", "FLAT"],
    "repetitions": 3,
    "api_opens": 84,
    "documents": 0,
    "queries": 0,
}

spec = importlib.util.spec_from_file_location("m4_containment_runner", RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

if ARTIFACT_ROOT.exists():
    raise SystemExit("refusing to reuse an existing diagnostic artifact path")
if not BINARY.is_file():
    raise SystemExit("latency diagnostic binary is missing; no launch")

ARTIFACT_ROOT.mkdir(parents=True, mode=0o700)
os.chmod(ARTIFACT_ROOT, 0o700)
binary_sha = hashlib.sha256(BINARY.read_bytes()).hexdigest()
context = {
    "schema_version": 1,
    "mode": runner.CONTENTION_MODE,
    "approval_status": "approved",
    "approval_reference": "Owner instruction in active conversation: run one bounded 0–6 scalar-open latency diagnostic under the amended exploratory contract; accept EXPLORATORY / NON-QUALIFYING and WORKSPACE_CAPACITY_UNVERIFIED.",
    "execution_authorization_reference": "Owner instruction in active conversation: run this diagnostic once; no retry or resource-only rehearsal.",
    "risk_acceptance_reference": "Retain operation-approved 3 GiB MemAvailable / 8 GiB free-disk reserves, 1 GiB MemoryMax, swap 0, CPU 200%, tasks 128, 20 s workload timeout, correctness, safety telemetry and verified cleanup; host load and positive swap/reclaim deltas are observations; workspace capacity may remain UNKNOWN/INCOMPLETE.",
    "resource_basis": "One serial API-level latency diagnostic: scalar counts 0–6 x HNSW/FLAT x three repetitions; each case creates, closes, reopens, closes one empty collection; 84 API opens total; no documents or queries; schema construction, close, shutdown and cleanup excluded from per-API timers.",
    "policy_sha256": hashlib.sha256(runner.POLICY_PATH.read_bytes()).hexdigest(),
    "operation_id": OP_ID,
    "operation_root": str(ARTIFACT_ROOT.resolve()),
    "resource_root": str(RESOURCE_ROOT),
    "purpose": runner.M4_SCALAR_LATENCY_PURPOSE,
    "binary_path": str(BINARY.resolve()),
    "binary_sha256": binary_sha,
    "approved_binary_sha256": binary_sha,
    "workload": WORKLOAD,
    "memory_max_bytes": 1024**3,
    "memory_swap_max_bytes": 0,
    "cpu_max": "200000 100000",
    "pids_max": 128,
    "workspace_max_allocated_bytes": 1024**3,
    "workspace_max_apparent_bytes": 1024**3,
    "host_memory_reserve_bytes": MEMORY_RESERVE_BYTES,
    "disk_reserve_bytes": DISK_RESERVE_BYTES,
    "runtime_limit_seconds": 20,
    "max_monitor_gap_seconds": 2,
}
runner.validate_contention_context(context)
runner.write_json(ARTIFACT_ROOT / "operation-plan.json", {
    "context": context,
    "context_digest": runner.contention_context_digest(context),
    "binary_sha256": binary_sha,
    "source_sha256": {
        str(path.relative_to(BASE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (pathlib.Path(__file__), RUNNER_PATH,
                     BASE / "tests/native_scalar_open_resource_rehearsal.cc",
                     BASE / "CMakeLists.txt", runner.POLICY_PATH)
    },
    "evidence_class": runner.M4_SCALAR_LATENCY_EVIDENCE,
    "workspace_capacity_status_if_not_proven": "WORKSPACE_CAPACITY_UNVERIFIED",
    "automatic_retry": False,
})


def capture_host_context(path: pathlib.Path) -> dict[str, Any]:
    ps = subprocess.run(
        ["ps", "-eo", "pid,ppid,comm,pcpu,pmem,rss,stat", "--sort=-%cpu"],
        capture_output=True, text=True, timeout=5,
    )
    record = {
        "timestamp_utc": runner.utc_now(),
        "host_snapshot": runner.host_snapshot(RESOURCE_ROOT),
        "process_affinity": sorted(os.sched_getaffinity(0)),
        "top_processes_by_cpu": ps.stdout.splitlines()[:21] if ps.returncode == 0 else None,
        "process_snapshot_error": None if ps.returncode == 0 else ps.stderr,
    }
    runner.write_json(path, record)
    return record


# Fresh, operation-bound admission. A formal refusal is recorded and consumes
# this one opportunity; there is no wait loop or automatic retry.
host_before = capture_host_context(ARTIFACT_ROOT / "host-before.json")
readiness = runner.contention_decision(host_before["host_snapshot"], context, "baseline")
runner.write_json(ARTIFACT_ROOT / "fresh-readiness.json", {
    "workload": WORKLOAD,
    "context_digest": runner.contention_context_digest(context),
    "decision": readiness,
})
if not readiness["continuation_allowed"]:
    runner.write_json(ARTIFACT_ROOT / "operation-result.json", {
        "status": "PREFLIGHT_BLOCKED",
        "benchmark_started": False,
        "gate_failures": readiness["blocking_failures"],
        "evidence_class": runner.M4_SCALAR_LATENCY_EVIDENCE,
        "workspace_capacity_status": "NOT_APPLICABLE",
        "recovery": "No retry; this one authorized opportunity is spent.",
    })
    raise SystemExit(2)

# Create a unique private workspace using tempfile's exclusive directory API,
# only after admission. The selected resource root is supplied by the operator.
WORKSPACE_PARENT = pathlib.Path(tempfile.mkdtemp(prefix=f"{OP_ID}-", dir=RESOURCE_ROOT))
os.chmod(WORKSPACE_PARENT, 0o700)
WORKSPACE = WORKSPACE_PARENT / "workspace"
WORKSPACE.mkdir(mode=0o700)
os.chmod(WORKSPACE, 0o700)
runner.write_json(ARTIFACT_ROOT / "workspace-layout.json", {
    "resource_root": str(RESOURCE_ROOT),
    "workspace_parent": str(WORKSPACE_PARENT),
    "workspace_root": str(WORKSPACE),
    "creation": "tempfile.mkdtemp exclusive unique directory; mode 0700",
})
receipt_dir = ARTIFACT_ROOT / "aa-first" / "containment"
command = [str(BINARY.resolve()), str(WORKSPACE.resolve() / "root"),
           "--m4-scalar-open-latency-diagnostic"]
env = {k: v for k, v in os.environ.items()
       if k in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH"}}

try:
    result = runner.run_arm(
        command, receipt_dir, RESOURCE_ROOT, WORKSPACE, WORKSPACE,
        20.0, 100, runner.contention_budget(context), env,
        enforce_host_gates=True, policy_context=context,
    )
finally:
    host_after = capture_host_context(ARTIFACT_ROOT / "host-after.json")

stdout_path = receipt_dir / "stdout.log"
parsed: list[dict[str, Any]] = []
parse_errors: list[str] = []
if stdout_path.is_file():
    for line_number, line in enumerate(stdout_path.read_text(errors="replace").splitlines(), 1):
        try:
            parsed.append(json.loads(line))
        except (ValueError, TypeError) as exc:
            parse_errors.append(f"line {line_number}: {type(exc).__name__}: {exc}")
samples = [row for row in parsed if row.get("row_kind") == "api_latency"]
summaries = [row for row in parsed if row.get("row_kind") == "summary"]
expected_cells = {
    (count, vector_type, repetition, operation)
    for count in range(7)
    for vector_type in ("hnsw", "flat")
    for repetition in range(3)
    for operation in ("create_and_open", "reopen")
}
observed_cells = {
    (row.get("scalar_count"), str(row.get("vector_type", "")).lower(),
     row.get("repetition"), row.get("operation"))
    for row in samples
}
valid_values = all(
    isinstance(row.get("latency_us"), (int, float))
    and not isinstance(row.get("latency_us"), bool)
    and math.isfinite(row["latency_us"])
    and row["latency_us"] >= 0
    for row in samples
)
workload_complete = bool(
    result.get("workload_returncode") == 0
    and len(samples) == 84
    and len(summaries) == 1
    and summaries[0].get("api_opens") == 84
    and summaries[0].get("api_latency_samples") == 84
    and observed_cells == expected_cells
    and valid_values
    and not parse_errors
)
analysis: dict[str, Any] = {
    "analysis_status": "COMPLETE" if workload_complete else "INCOMPLETE_NO_LATENCY_CLAIM",
    "workload_complete": workload_complete,
    "api_latency_rows": len(samples),
    "expected_api_latency_rows": 84,
    "summary_rows": summaries,
    "parse_errors": parse_errors,
    "evidence_class": runner.M4_SCALAR_LATENCY_EVIDENCE,
    "workspace_capacity_status": "WORKSPACE_CAPACITY_UNVERIFIED",
    "measurement_semantics": "Per-call wall intervals for C API create_and_open and reopen only; three observations per count/type/operation cell. Report every observation, median and range; no stable-tail or causal claims.",
}
if workload_complete:
    groups: dict[tuple[int, str, str], list[float]] = {}
    for row in samples:
        key = (row["scalar_count"], row["vector_type"], row["operation"])
        groups.setdefault(key, []).append(float(row["latency_us"]))
    analysis["groups"] = [
        {
            "scalar_count": count,
            "vector_type": vector_type,
            "operation": operation,
            "n": len(values),
            "values_us": values,
            "median_us": statistics.median(values),
            "min_us": min(values),
            "max_us": max(values),
        }
        for (count, vector_type, operation), values in sorted(groups.items())
    ]
else:
    analysis["groups"] = []
    analysis["observed_cells"] = len(observed_cells)
runner.write_json(ARTIFACT_ROOT / "latency-analysis.json", analysis)

# C++ removes its per-case root on normal completion. Remove only empty owned
# directory shells; retain any nonempty or ambiguous workspace for inspection.
cleanup_verified = False
if WORKSPACE.exists() and WORKSPACE.is_dir() and not any(WORKSPACE.iterdir()):
    WORKSPACE.rmdir()
if WORKSPACE_PARENT.exists() and WORKSPACE_PARENT.is_dir() and not any(WORKSPACE_PARENT.iterdir()):
    WORKSPACE_PARENT.rmdir()
cleanup_verified = not WORKSPACE_PARENT.exists()
runner.write_json(ARTIFACT_ROOT / "workspace-cleanup.json", {
    "verified": cleanup_verified,
    "path": str(WORKSPACE_PARENT),
    "method": "remove only empty owned directories with rmdir; never recursively remove workspace data",
})
result["workload_complete"] = workload_complete
result["latency_sample_count"] = len(samples)
result["evidence_class"] = runner.M4_SCALAR_LATENCY_EVIDENCE
result["workspace_capacity_status"] = "WORKSPACE_CAPACITY_UNVERIFIED"
result["workspace_cleanup_verified"] = cleanup_verified
if not cleanup_verified:
    result["status"] = "CLEANUP_UNVERIFIED"
    result["operation_outcome"] = "OPERATION_BLOCKED"
runner.write_json(ARTIFACT_ROOT / "operation-result.json", result)
print(json.dumps({
    "operation_id": OP_ID,
    "status": result.get("status"),
    "workload_complete": workload_complete,
    "latency_sample_count": len(samples),
    "workspace_capacity_status": result["workspace_capacity_status"],
    "workspace_cleanup_verified": cleanup_verified,
    "artifact_root": str(ARTIFACT_ROOT),
}, indent=2, sort_keys=True))
raise SystemExit(0 if workload_complete and cleanup_verified and result.get("status") in {
    "PASS", "COMPLETED_WITH_WORKSPACE_UNCERTAINTY", "COMPLETED_WITH_WORKSPACE_WARNINGS"
} else 2)
