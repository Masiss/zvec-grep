#!/usr/bin/env python3
"""One-shot, resource-contained runner for the P1 native lifecycle baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime

def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

ROOT = _repo_root()
PROTOCOL_PATH = pathlib.Path(__file__).with_name("protocol.json")
TEST_NAME = "workspace_runtime::tests::freshness::p1_native_lifecycle_baseline_once"
OP_ROOT = ROOT / ".tmp/native-lifecycle-baseline-20261027"
AUTH_LEDGER_DIR = ROOT / ".tmp/p1-native-lifecycle-authorizations"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def authorization_ledger_path(message: int) -> pathlib.Path:
    return AUTH_LEDGER_DIR / f"message-{message}-consumed.json"


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, stdout=subprocess.PIPE
    ).stdout


def read_meminfo() -> dict[str, int]:
    result = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        fields = value.split()
        if fields and fields[0].isdigit():
            result[key] = int(fields[0]) * (1024 if len(fields) > 1 and fields[1] == "kB" else 1)
    return result


def read_pressure(path: pathlib.Path) -> dict[str, dict[str, float]] | None:
    try:
        parsed: dict[str, dict[str, float]] = {}
        for line in path.read_text().splitlines():
            fields = line.split()
            values = {}
            for field in fields[1:]:
                key, value = field.split("=", 1)
                if key in ("avg10", "avg60", "avg300"):
                    values[key] = float(value)
            parsed[fields[0]] = values
        return parsed
    except (OSError, ValueError, IndexError):
        return None


def host_snapshot() -> dict:
    ps = subprocess.run(
        ["ps", "-eo", "pid,ppid,stat,%cpu,%mem,rss,etime,comm,args", "--sort=-%cpu"],
        check=False, capture_output=True, text=True,
    )
    stat = os.statvfs(OP_ROOT if OP_ROOT.exists() else ROOT / ".tmp")
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "loadavg": list(os.getloadavg()),
        "meminfo_bytes": read_meminfo(),
        "pressure": {
            name: read_pressure(pathlib.Path(f"/proc/pressure/{name}"))
            for name in ("cpu", "memory", "io")
        },
        "filesystem_available_bytes": stat.f_bavail * stat.f_frsize,
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "top_processes": ps.stdout.splitlines()[:21],
    }


def frozen_manifest(protocol: dict) -> list[tuple[str, str, int]]:
    corpus = protocol["corpus"]
    entries = []
    for item in git("ls-tree", "-rlz", corpus["source_commit"]).split(b"\0"):
        if not item:
            continue
        meta, raw_path = item.split(b"\t", 1)
        _mode, kind, blob, size = meta.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if kind == "blob" and path.startswith(corpus["path_prefix"]) and pathlib.PurePosixPath(path).suffix in corpus["extensions"]:
            entries.append((path, blob, int(size)))
    entries.sort()
    entries = entries[:corpus["file_count"]]
    encoded = "".join(f"{path}\t{blob}\t{size}\n" for path, blob, size in entries).encode()
    if (
        len(entries) != corpus["file_count"]
        or sum(item[2] for item in entries) != corpus["source_bytes"]
        or sha256(encoded) != corpus["manifest_sha256"]
    ):
        raise RuntimeError("pinned native lifecycle corpus does not match its frozen manifest")
    return entries


def find_test_binary() -> tuple[pathlib.Path, pathlib.Path]:
    binaries = sorted((ROOT / "rust/target/debug/deps").glob("zg_daemon-*"))
    libraries = sorted((ROOT / "rust/target/debug/build").glob("zvec-rust-sys-*/out/zvec-prebuilt/libzvec_c_api.so"))
    if not libraries:
        raise RuntimeError("compiled libzvec_c_api.so is missing")
    library = max(libraries, key=lambda item: item.stat().st_mtime_ns).parent.resolve()
    env = os.environ.copy()
    old_library_path = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = str(library) + (os.pathsep + old_library_path if old_library_path else "")
    matches = []
    for binary in binaries:
        try:
            if not binary.is_file() or not os.access(binary, os.X_OK):
                continue
        except OSError:
            continue
        listing = subprocess.run([str(binary), "--list"], cwd=ROOT, env=env, capture_output=True, text=True)
        if listing.returncode == 0 and any(line == TEST_NAME + ": test" for line in listing.stdout.splitlines()):
            matches.append(binary.resolve())
    if not matches:
        raise RuntimeError(f"no compiled test binary contains {TEST_NAME}")
    matches.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)))
    if len(matches) > 1 and matches[-1].stat().st_mtime_ns == matches[-2].stat().st_mtime_ns:
        raise RuntimeError("multiple newest native lifecycle test binaries; refusing ambiguous selection")
    return matches[-1], library


def extract_corpus(destination: pathlib.Path, entries: list[tuple[str, str, int]], protocol: dict) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    corpus = protocol["corpus"]
    archive = git("archive", "--format=tar", corpus["source_commit"], "--", *(entry[0] for entry in entries))
    total = 0
    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as tar:
        for member in tar:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"unsafe corpus archive path: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError(f"unsafe/non-regular corpus archive entry: {member.name}")
            if path.suffix not in corpus["extensions"]:
                continue
            stream = tar.extractfile(member)
            if stream is None:
                raise RuntimeError(f"missing corpus file data: {member.name}")
            with stream:
                data = stream.read()
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            total += len(data)
    if total != corpus["source_bytes"]:
        raise RuntimeError(f"materialized corpus byte mismatch: {total}")


def cgroup_path_for(unit: str) -> pathlib.Path | None:
    result = subprocess.run(
        ["systemctl", "--user", "show", f"{unit}.scope", "--property=ControlGroup", "--value"],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value.startswith("/"):
        return None
    path = pathlib.Path("/sys/fs/cgroup") / value.lstrip("/")
    return path if path.is_dir() else None


def parse_key_values(path: pathlib.Path) -> dict[str, str] | None:
    try:
        result = {}
        for line in path.read_text().splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2:
                result[fields[0]] = fields[1]
        return result
    except OSError:
        return None


def process_snapshot(pid: int, ticks_per_second: int) -> dict | None:
    proc = pathlib.Path(f"/proc/{pid}")
    try:
        status = parse_key_values(proc / "status") or {}
        stat_fields = (proc / "stat").read_text().split()
        # utime/stime are fields 14/15 in proc(5); stat_fields starts at field 1.
        cpu_seconds = (int(stat_fields[13]) + int(stat_fields[14])) / ticks_per_second
        smaps = parse_key_values(proc / "smaps_rollup") or {}
        return {
            "pid": pid,
            "cpu_seconds": cpu_seconds,
            "rss_bytes": int(status.get("VmRSS:", "0 kB").split()[0]) * 1024,
            "rss_hwm_bytes": int(status.get("VmHWM:", "0 kB").split()[0]) * 1024,
            "swap_bytes": int(status.get("VmSwap:", "0 kB").split()[0]) * 1024,
            "pss_bytes": int(smaps.get("Pss:", "0 kB").split()[0]) * 1024,
        }
    except (OSError, ValueError, IndexError):
        return None


def cgroup_snapshot(path: pathlib.Path | None) -> dict:
    if path is None or not path.is_dir():
        return {"available": False}
    files = {}
    for name in (
        "memory.current", "memory.peak", "memory.max", "memory.swap.current",
        "memory.swap.max", "memory.events", "memory.stat", "cpu.stat", "pids.current",
        "pids.peak", "pids.max", "io.stat", "cgroup.events",
    ):
        try:
            files[name] = (path / name).read_text().strip()
        except OSError:
            files[name] = None
    try:
        pids = [int(value) for value in (path / "cgroup.procs").read_text().split()]
    except (OSError, ValueError):
        pids = []
    ticks = os.sysconf("SC_CLK_TCK")
    return {
        "available": True,
        "path": str(path),
        "files": files,
        "processes": [value for pid in pids if (value := process_snapshot(pid, ticks)) is not None],
    }


def available_memory_bytes() -> int:
    return read_meminfo().get("MemAvailable", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--launch", action="store_true")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--authorization-message", type=int, required=True)
    args = parser.parse_args()

    protocol_bytes = PROTOCOL_PATH.read_bytes()
    try:
        protocol = json.loads(protocol_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SystemExit(f"invalid P1 native lifecycle protocol: {error}") from error
    if args.authorization_message != protocol["authorization"]["user_message"]:
        raise SystemExit("authorization reference does not match the frozen protocol")
    auth_ledger = authorization_ledger_path(args.authorization_message)
    if not re.fullmatch(r"p1-native-lifecycle-20261027t[0-9]{6}z-[0-9a-f]{8}", args.operation_id):
        raise SystemExit("operation ID must be fresh, lowercase, and match the protocol")
    if auth_ledger.exists():
        raise SystemExit(f"single-run authorization already consumed: {auth_ledger}")
    head = git("rev-parse", "HEAD").decode().strip()
    if head != protocol["code_candidate"]["base_head"]:
        raise SystemExit(f"code candidate HEAD changed: expected {protocol['code_candidate']['base_head']}, got {head}")
    diff_sha = sha256(git("diff", "--binary", "HEAD"))
    expected_diff_sha = protocol["code_candidate"].get("tracked_worktree_diff_sha256")
    if expected_diff_sha is not None and diff_sha != expected_diff_sha:
        raise SystemExit(
            f"tracked worktree diff changed: expected {expected_diff_sha}, got {diff_sha}"
        )
    entries = frozen_manifest(protocol)
    binary, library = find_test_binary()
    package = ROOT / ".tmp/phase1-wip-package-20261027T070707Z/zvec-zvec-grep-0.0.1.tgz"
    if not package.is_file() or sha256(package.read_bytes()) != protocol["code_candidate"]["package_artifact_sha256"]:
        raise SystemExit("frozen local candidate package is missing or its digest changed")
    operation_root = OP_ROOT / args.operation_id
    if operation_root.exists():
        raise SystemExit(f"operation root already exists; refusing reuse: {operation_root}")
    if not shutil.which("systemd-run") or not shutil.which("systemctl"):
        raise SystemExit("systemd user scope tools unavailable")
    disk_free = shutil.disk_usage(ROOT / ".tmp").free
    memory_available = available_memory_bytes()
    envelope = protocol["resource_envelope"]
    minimum_free_at_launch = envelope.get(
        "minimum_free_bytes_at_launch",
        envelope["minimum_free_bytes_before_and_during_run"],
    )
    if disk_free < minimum_free_at_launch:
        raise SystemExit(
            f"launch disk cushion preflight blocked: free={disk_free}, minimum={minimum_free_at_launch}"
        )
    if memory_available < envelope["minimum_memavailable_bytes_before_and_during_run"]:
        raise SystemExit(f"host memory reserve preflight blocked: available={memory_available}")
    binary_sha = sha256(binary.read_bytes())
    guard_path = pathlib.Path(__file__).with_name("exec_guard.py")
    guard_sha = sha256(guard_path.read_bytes())
    for tool in ("/usr/bin/python3", "/usr/bin/time", "/usr/bin/timeout"):
        try:
            available = pathlib.Path(tool).is_file() and os.access(tool, os.X_OK)
        except OSError as error:
            raise SystemExit(f"cannot inspect required runtime tool {tool}: {error}") from error
        if not available:
            raise SystemExit(f"required runtime tool unavailable: {tool}")
    validation = {
        "benchmark_started": False,
        "operation_id": args.operation_id,
        "authorization_message": args.authorization_message,
        "protocol_sha256": sha256(protocol_bytes),
        "base_head": head,
        "tracked_diff_sha256": diff_sha,
        "package_sha256": protocol["code_candidate"]["package_artifact_sha256"],
        "test_binary": str(binary),
        "test_binary_sha256": binary_sha,
        "exec_guard_sha256": guard_sha,
        "shared_library_directory": str(library),
        "corpus_file_count": len(entries),
        "corpus_bytes": sum(entry[2] for entry in entries),
        "corpus_manifest_sha256": protocol["corpus"]["manifest_sha256"],
        "disk_available_bytes": disk_free,
        "host_memavailable_bytes": memory_available,
        "resource_envelope": envelope,
        "authorization_ledger": str(auth_ledger),
    }
    if args.validate_only:
        print(json.dumps(validation, indent=2))
        return 0

    OP_ROOT.mkdir(parents=True, exist_ok=True)
    operation_root.mkdir(mode=0o700)
    workspace = operation_root / "workspace"
    index_root = operation_root / "index"
    tmpdir = operation_root / "tmp"
    tmpdir.mkdir()
    extract_corpus(workspace, entries, protocol)
    disk_after_materialization = shutil.disk_usage(ROOT / ".tmp").free
    validation["disk_available_after_materialization_bytes"] = disk_after_materialization
    if disk_after_materialization < minimum_free_at_launch:
        raise SystemExit(
            "launch disk cushion preflight failed after source materialization: "
            f"free={disk_after_materialization}, minimum={minimum_free_at_launch}; no workload launched"
        )
    report = operation_root / "native-baseline.json"
    if index_root.exists() or report.exists():
        raise SystemExit("fresh index/report path invariant violated")

    unit = f"p1-native-{args.operation_id[-8:]}"
    timeout_seconds = envelope["runtime_limit_seconds"]
    env = {
        "TMPDIR": str(tmpdir),
        "P1_NATIVE_OPERATION_ID": args.operation_id,
        "P1_NATIVE_WORKSPACE": str(workspace),
        "P1_NATIVE_INDEX_ROOT": str(index_root),
        "P1_NATIVE_REPORT": str(report),
        "P1_NATIVE_SOURCE_COMMIT": head,
        "P1_NATIVE_CORPUS_COMMIT": protocol["corpus"]["source_commit"],
        "P1_NATIVE_QUERY_INTERVAL_MS": str(protocol["query"]["interval_ms"]),
        "P1_NATIVE_QUERY_MAX_SAMPLES": str(protocol["query"]["max_samples"]),
        "P1_NATIVE_QUERY_SLO_MAX_US": str(protocol["query"]["slo_max_us"]),
        "P1_NATIVE_FRESHNESS_SLO_MAX_US": str(protocol["freshness"]["slo_max_us"]),
        "P1_NATIVE_WATCHER_DEADLINE_S": str(protocol["stop_conditions"]["watcher_event_observation_deadline_seconds"]),
        "P1_NATIVE_WRITER_IDLE_DEADLINE_S": str(protocol["stop_conditions"]["writer_idle_deadline_seconds"]),
        "P1_NATIVE_QUERY_DEADLINE_S": str(protocol["stop_conditions"]["query_visibility_deadline_seconds"]),
        "P1_NATIVE_START_DEADLINE_S": str(protocol["stop_conditions"]["initial_index_and_watcher_activation_deadline_seconds"]),
        "P1_NATIVE_RESOURCE_ROOT": str(ROOT / ".tmp"),
        "P1_NATIVE_MIN_FREE_BYTES": str(envelope["minimum_free_bytes_before_and_during_run"]),
        "P1_NATIVE_MIN_FREE_BYTES_AT_LAUNCH": str(minimum_free_at_launch),
        "P1_NATIVE_MIN_MEMAVAILABLE_BYTES": str(envelope["minimum_memavailable_bytes_before_and_during_run"]),
        "P1_NATIVE_EXPECTED_MEMORY_MAX": str(envelope["memory_max_bytes"]),
        "P1_NATIVE_EXPECTED_SWAP_MAX": str(envelope["memory_swap_max_bytes"]),
        "P1_NATIVE_EXPECTED_CPU_QUOTA_PERCENT": str(envelope["cpu_quota_percent"]),
        "P1_NATIVE_EXPECTED_PIDS_MAX": str(envelope["pids_max"]),
        "LD_LIBRARY_PATH": str(library) + (os.pathsep + os.environ.get("LD_LIBRARY_PATH", "") if os.environ.get("LD_LIBRARY_PATH") else ""),
        "RUST_BACKTRACE": "1",
    }
    command = [
        "systemd-run", "--user", "--scope", f"--unit={unit}",
        f"--property=MemoryMax={envelope['memory_max_bytes']}",
        f"--property=MemorySwapMax={envelope['memory_swap_max_bytes']}",
        f"--property=CPUQuota={envelope['cpu_quota_percent']}%",
        f"--property=TasksMax={envelope['pids_max']}",
        "--", "/usr/bin/env",
        *(f"{key}={value}" for key, value in env.items()),
        "/usr/bin/python3", str(guard_path), "--",
        "/usr/bin/time", "-v", "/usr/bin/timeout",
        "--signal=TERM", f"--kill-after={envelope['runtime_timeout_grace_seconds']}s",
        f"{timeout_seconds}s", str(binary), "--exact", TEST_NAME, "--ignored", "--nocapture",
    ]
    validation["command"] = command
    validation["operation_root"] = str(operation_root)
    validation["workspace_path"] = str(workspace)
    validation["index_path"] = str(index_root)
    validation["report_path"] = str(report)
    validation["launch_armed_utc"] = datetime.now(UTC).isoformat()
    # Consume this owner's one authorized attempt immediately before launch. No automatic retry exists.
    auth_ledger.parent.mkdir(parents=True, exist_ok=True)
    with auth_ledger.open("x", encoding="utf-8") as stream:
        json.dump({"operation_id": args.operation_id, "authorization_message": args.authorization_message,
                   "status": "CONSUMED_BEFORE_LAUNCH", "timestamp_utc": validation["launch_armed_utc"]}, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    validation["benchmark_started"] = True
    validation["runner_manifest_sha256"] = sha256(pathlib.Path(__file__).read_bytes())
    validation_path = operation_root / "run-manifest.json"
    validation_path.write_text(json.dumps(validation, indent=2) + "\n")
    log_path = operation_root / "workload.log"
    telemetry_path = operation_root / "containment.jsonl"
    before = host_snapshot()
    (operation_root / "host-before.json").write_text(json.dumps(before, indent=2) + "\n")

    with log_path.open("w", encoding="utf-8") as log, telemetry_path.open("w", encoding="utf-8") as telemetry:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True, bufsize=1, shell=False)
        reason = None
        cgroup_path = None
        interval = envelope["telemetry_interval_ms"] / 1000
        while process.poll() is None:
            now = datetime.now(UTC).isoformat()
            if cgroup_path is None:
                cgroup_path = cgroup_path_for(unit)
            try:
                disk_now = shutil.disk_usage(ROOT / ".tmp").free
                memory_now = available_memory_bytes()
                sample = {
                    "timestamp_utc": now,
                    "filesystem_available_bytes": disk_now,
                    "host_memavailable_bytes": memory_now,
                    "cgroup": cgroup_snapshot(cgroup_path),
                }
                telemetry.write(json.dumps(sample) + "\n")
                telemetry.flush()
                if disk_now < envelope["minimum_free_bytes_before_and_during_run"]:
                    reason = f"disk_reserve_breach:{disk_now}"
                elif memory_now < envelope["minimum_memavailable_bytes_before_and_during_run"]:
                    reason = f"host_memavailable_breach:{memory_now}"
                if reason:
                    subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"], check=False, timeout=15)
                    break
            except (OSError, subprocess.TimeoutExpired) as error:
                reason = f"containment_monitor_error:{error}"
                subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"], check=False, timeout=15)
                break
            time.sleep(interval)
        returncode = process.wait()
    after = host_snapshot()
    (operation_root / "host-after.json").write_text(json.dumps(after, indent=2) + "\n")
    final_cgroup_path = cgroup_path_for(unit) or cgroup_path
    result = {
        **validation,
        "runner_returncode": returncode,
        "runner_stop_reason": reason,
        "test_report_exists": report.is_file(),
        "test_report_sha256": sha256(report.read_bytes()) if report.is_file() else None,
        "workload_log": str(log_path),
        "telemetry_log": str(telemetry_path),
        "host_before": str(operation_root / "host-before.json"),
        "host_after": str(operation_root / "host-after.json"),
        "final_cgroup": cgroup_snapshot(final_cgroup_path),
        "authorization_ledger": str(auth_ledger),
        "result_status": "COMPLETED" if returncode == 0 and report.is_file() and reason is None else "PARTIAL_OR_BLOCKED",
    }
    (operation_root / "runner-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("operation_id", "runner_returncode", "runner_stop_reason", "test_report_exists", "result_status")}, indent=2))
    return returncode if reason is None else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"PREFLIGHT_OR_RUNNER_ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
