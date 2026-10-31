"""Host, cgroup, quota, launch, and cleanup primitives for M4 adapters.

The functions are behavior-preserving extractions from the historical containment
runner. No policy threshold is defined here; callers pass approved budgets. The
module is standalone and adds ``benchmarks/harness-v2`` to ``sys.path`` only to
reuse the measurement clock/receipt helpers.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import signal
import stat
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

_MEASUREMENT_PATH = pathlib.Path(__file__).resolve().parents[1] / "measurement" / "primitives.py"
_MEASUREMENT_SPEC = importlib.util.spec_from_file_location(
    "harness_v2_measurement_primitives", _MEASUREMENT_PATH
)
if _MEASUREMENT_SPEC is None or _MEASUREMENT_SPEC.loader is None:
    raise RuntimeError(f"cannot load measurement primitives from {_MEASUREMENT_PATH}")
_MEASUREMENT = importlib.util.module_from_spec(_MEASUREMENT_SPEC)
_MEASUREMENT_SPEC.loader.exec_module(_MEASUREMENT)
read_text = _MEASUREMENT.read_text
utc_now = _MEASUREMENT.utc_now

CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")
BTRFS_SYSFS_ROOT = pathlib.Path("/sys/fs/btrfs")


def parse_kv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                out[fields[0].removesuffix(":")] = int(fields[1])
            except ValueError:
                continue
    return out


def parse_pressure(path: pathlib.Path) -> dict[str, dict[str, float]] | None:
    try:
        text = read_text(path)
    except OSError:
        return None
    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, value = field.split("=", 1)
            try:
                values[key] = float(value)
            except ValueError:
                continue
        result[fields[0]] = values
    return result or None


def read_meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        match = re.match(r"^(MemAvailable|SwapFree|MemTotal|SwapTotal):\s+(\d+)", line)
        if match:
            result[match.group(1) + "_kib"] = int(match.group(2))
    return result


def read_vmstat() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in pathlib.Path("/proc/vmstat").read_text().splitlines():
        key, _, value = line.partition(" ")
        if key in {"pswpin", "pswpout"}:
            result[key + "_pages"] = int(value)
        elif key in {"oom_kill", "pgscan_direct", "pgsteal_direct"} or key.startswith("allocstall_"):
            result[key] = int(value)
    return result


def read_diskstats() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in pathlib.Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        result[fields[2]] = {
            "major": int(fields[0]),
            "minor": int(fields[1]),
            "reads_completed": int(fields[3]),
            "sectors_read": int(fields[5]),
            "read_ms": int(fields[6]),
            "writes_completed": int(fields[7]),
            "sectors_written": int(fields[9]),
            "write_ms": int(fields[10]),
            "io_in_progress": int(fields[11]),
            "io_ms": int(fields[12]),
            "weighted_io_ms": int(fields[13]),
        }
    return result


def host_snapshot(resource_root: pathlib.Path) -> dict[str, Any]:
    mem = read_meminfo()
    try:
        load = [float(x) for x in pathlib.Path("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        load = []
    stats = os.statvfs(resource_root)
    # These host context metrics are optional observations, never an admission,
    # runtime-abort, or recovery prerequisite.
    return {
        "timestamp_utc": utc_now(),
        "monotonic_ns": time.monotonic_ns(),
        "meminfo_kib": mem,
        "loadavg": load,
        "io_pressure": parse_pressure(pathlib.Path("/proc/pressure/io")),
        "memory_pressure": parse_pressure(pathlib.Path("/proc/pressure/memory")),
        "resource_root_free_bytes": stats.f_bavail * stats.f_frsize,
        "vmstat": read_vmstat(),
        "diskstats": read_diskstats(),
        "cpu_pressure": parse_pressure(pathlib.Path("/proc/pressure/cpu")),
    }


def cgroup_relative(pid: int | None = None) -> str:
    path = pathlib.Path(f"/proc/{pid}/cgroup" if pid else "/proc/self/cgroup")
    for line in path.read_text().splitlines():
        if line.startswith("0::"):
            return line[3:].lstrip("/")
    raise RuntimeError(f"no unified cgroup membership in {path}")


def cgroup_path_from_unit(unit: str, cgroup_root: pathlib.Path = CGROUP_ROOT) -> pathlib.Path:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ControlGroup", "--value"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    rel = result.stdout.strip().lstrip("/")
    if not rel or ".." in pathlib.PurePosixPath(rel).parts:
        raise RuntimeError(f"systemd returned unsafe/empty ControlGroup for {unit}: {rel!r}")
    path = (cgroup_root / rel).resolve(strict=True)
    if path != cgroup_root and cgroup_root not in path.parents:
        raise RuntimeError(f"ControlGroup escaped cgroup2 mount: {path}")
    return path


def proc_cgroup_contains(pid: int, expected: str) -> bool:
    return cgroup_relative(pid).strip("/") == expected.strip("/")


def signal_verified_member(pid: int, expected_rel: str, sig: int) -> bool:
    """Use pidfd plus a fresh cgroup check so exit/PID reuse cannot redirect a signal."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("pidfd signaling is required for safe process-tree termination")
    pidfd = os.pidfd_open(pid)
    try:
        if not proc_cgroup_contains(pid, expected_rel):
            return False
        signal.pidfd_send_signal(pidfd, sig)
        return True
    finally:
        os.close(pidfd)


def kill_owned_cgroup(
    cg: pathlib.Path,
    expected_rel: str,
    grace_seconds: float = 1.0,
    preserve_pids: set[int] | None = None,
) -> dict[str, Any]:
    """Signal only members verified in this arm; optionally keep its bootstrap process alive."""
    preserve = preserve_pids or set()
    signalled: list[int] = []
    killed: list[int] = []
    errors: list[str] = []
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            pids = [int(x) for x in read_text(cg / "cgroup.procs").splitlines() if x.strip()]
        except OSError:
            pids = []
        targets = [pid for pid in pids if pid not in preserve]
        if not targets and (preserve or not pids):
            return {
                "term_pids": signalled,
                "kill_pids": killed,
                "preserved_pids": sorted(preserve & set(pids)),
                "kill_fallback": False,
                "errors": errors,
            }
        for pid in targets:
            try:
                if signal_verified_member(pid, expected_rel, signal.SIGTERM):
                    signalled.append(pid)
            except (ProcessLookupError, FileNotFoundError):
                continue
            except (PermissionError, OSError) as exc:
                errors.append(f"pid {pid}: {exc}")
        time.sleep(0.025)
    if preserve:
        # Never cgroup.kill while preserving the receipt/bootstrap process.
        for _ in range(40):
            try:
                pids = [int(x) for x in read_text(cg / "cgroup.procs").splitlines() if x.strip()]
            except OSError:
                pids = []
            targets = [pid for pid in pids if pid not in preserve]
            if not targets:
                break
            for pid in targets:
                try:
                    if signal_verified_member(pid, expected_rel, signal.SIGKILL):
                        killed.append(pid)
                except (ProcessLookupError, FileNotFoundError):
                    continue
                except (PermissionError, OSError) as exc:
                    errors.append(f"pid {pid}: {exc}")
            time.sleep(0.025)
        return {
            "term_pids": signalled,
            "kill_pids": killed,
            "preserved_pids": sorted(preserve),
            "kill_fallback": False,
            "errors": errors,
        }
    try:
        kill_file = cg / "cgroup.kill"
        if kill_file.exists() and os.access(kill_file, os.W_OK):
            kill_file.write_text("1\n", encoding="ascii")
            return {
                "term_pids": signalled,
                "kill_pids": killed,
                "kill_fallback": True,
                "errors": errors,
            }
        errors.append("cgroup.kill unavailable/unwritable")
    except OSError as exc:
        errors.append(f"cgroup.kill: {exc}")
    return {
        "term_pids": signalled,
        "kill_pids": killed,
        "kill_fallback": False,
        "errors": errors,
    }


def cgroup_populated(cg: pathlib.Path) -> int:
    return parse_kv(read_text(cg / "cgroup.events")).get("populated", -1)


def service_properties(budget: dict[str, Any] | None, runtime_seconds: float) -> list[str]:
    props = [
        "Type=exec",
        "Delegate=yes",
        "ProtectControlGroups=yes",
        "MemoryAccounting=yes",
        "KillMode=control-group",
        "TimeoutStopSec=2s",
        f"RuntimeMaxSec={runtime_seconds + 5.0:.3f}s",
    ]
    if budget:
        if budget.get("pids_max") is not None:
            props.append(f"TasksMax={budget['pids_max']}")
        if budget.get("memory_max_bytes") is not None:
            props.append(f"MemoryMax={budget['memory_max_bytes']}")
        if budget.get("memory_swap_max_bytes") is not None:
            props.append(f"MemorySwapMax={budget['memory_swap_max_bytes']}")
        if budget.get("cpu_max") is not None:
            quota, period = budget["cpu_max"].split()
            if quota == "max":
                props.append("CPUQuota=infinity")
            else:
                # systemd-run rejects e.g. 200.000% on this host. Preserve exact
                # integer percentages; do not round or broaden the approved quota.
                percent, remainder = divmod(100 * int(quota), int(period))
                value = str(percent) if remainder == 0 else f"{100.0 * int(quota) / int(period):.3f}"
                props.append(f"CPUQuota={value}%")
    return props


def make_receipt_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(path, 0o700)


def validate_workspace_quota_attestation(
    attestation: dict[str, Any],
    expected: dict[str, Any],
    owner_uid: int,
    mode: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate root-owned evidence for the lock-microbench's hard Btrfs cap."""
    fields = ("filesystem_uuid", "subvolume_id", "qgroup_id", "max_referenced_bytes", "accounting_mode")
    if owner_uid != expected.get("attestation_owner_uid") or mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("workspace quota attestation is not protected by root ownership/mode")
    if (
        type(attestation.get("schema_version")) is not int
        or attestation["schema_version"] != 1
        or attestation.get("mountpoint") != "/"
    ):
        raise ValueError("invalid workspace quota attestation schema/mountpoint")
    for key in ("subvolume_id", "max_referenced_bytes"):
        if type(attestation.get(key)) is not int or attestation[key] <= 0:
            raise ValueError(f"invalid workspace quota attestation integer: {key}")
    for key in fields:
        if attestation.get(key) != expected.get(key):
            raise ValueError(f"workspace quota attestation mismatch: {key}")
    if attestation.get("workspace_subvolume") != expected.get("workspace_subvolume"):
        raise ValueError("workspace quota attestation path mismatch")
    timestamp = attestation.get("verified_at_utc")
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("workspace quota attestation lacks verification time")
    try:
        verified_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("workspace quota attestation timestamp is invalid") from exc
    if verified_at.tzinfo is None:
        raise ValueError("workspace quota attestation timestamp must include a timezone")
    reference = now if now is not None else datetime.now(UTC)
    age_seconds = (reference - verified_at.astimezone(UTC)).total_seconds()
    if age_seconds < -300 or age_seconds > expected.get("max_attestation_age_seconds", 0):
        raise ValueError("workspace quota attestation is stale or from the future")
    return {
        "status": "VERIFIED",
        "filesystem_uuid": attestation["filesystem_uuid"],
        "subvolume_id": attestation["subvolume_id"],
        "qgroup_id": attestation["qgroup_id"],
        "max_referenced_bytes": attestation["max_referenced_bytes"],
        "accounting_mode": attestation["accounting_mode"],
        "verified_at_utc": timestamp,
    }


def watchdog_phase(arm_name: str, started: bool) -> str:
    if arm_name not in ("aa-first", "aa-second", "pair-first", "pair-second", "microbench", "diagnostic"):
        raise ValueError(f"invalid contention arm name: {arm_name}")
    if not started:
        return arm_name
    return "runtime-second" if arm_name in ("aa-second", "pair-second") else "runtime-first"


def qgroup_diagnostic_next_deadline(origin_ns: int, previous_sample_ns: int, now_ns: int) -> int:
    """Next fixed one-second slot without catch-up bursts or subsecond samples."""
    period_ns = 1_000_000_000
    slot = max(1, (now_ns - origin_ns) // period_ns + 1)
    return max(origin_ns + slot * period_ns, previous_sample_ns + period_ns)


def watchdog_observer_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    sample_starts = [row.get("watchdog_sample_start_monotonic_ns") for row in samples]
    sample_starts = [value for value in sample_starts if isinstance(value, int)]
    gaps = [right - left for left, right in zip(sample_starts, sample_starts[1:], strict=False)]
    loop_cpu_ns = [row.get("watchdog_loop_cpu_ns") for row in samples]
    loop_cpu_ns = [value for value in loop_cpu_ns if isinstance(value, int) and value >= 0]
    loop_work_ns = [row.get("watchdog_loop_work_ns") for row in samples]
    loop_work_ns = [value for value in loop_work_ns if isinstance(value, int) and value >= 0]
    total_cpu_ns = sum(loop_cpu_ns)
    total_work_ns = sum(loop_work_ns)
    return {
        "telemetry_complete": bool(samples)
        and len(sample_starts) == len(samples)
        and len(loop_cpu_ns) == len(samples)
        and len(loop_work_ns) == len(samples),
        "sample_start_count": len(sample_starts),
        "loop_cpu_sample_count": len(loop_cpu_ns),
        "loop_work_sample_count": len(loop_work_ns),
        "sample_start_gap_max_ns": max(gaps, default=None),
        "watchdog_loop_cpu_ns_sum": total_cpu_ns,
        "watchdog_loop_work_ns_sum": total_work_ns,
        "watchdog_cpu_to_active_work_ratio": total_cpu_ns / total_work_ns if total_work_ns else None,
        "watchdog_loop_cpu_ns_max": max(loop_cpu_ns, default=None),
        "watchdog_loop_work_ns_max": max(loop_work_ns, default=None),
    }


def process_resource_sample(pid: int) -> dict[str, Any]:
    root = pathlib.Path('/proc') / str(pid)
    status = {}
    for line in (root / 'status').read_text().splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            if key in ('Name', 'VmRSS', 'VmSwap', 'Cpus_allowed_list'):
                status[key] = value.strip()
    smaps = {}
    try:
        for line in (root / 'smaps_rollup').read_text().splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                if key in ('Pss', 'Swap', 'SwapPss'):
                    smaps[key] = value.strip()
    except OSError as exc:
        smaps['unavailable'] = f'{type(exc).__name__}: {exc}'
    raw = (root / 'stat').read_text()
    tail = raw[raw.rfind(')') + 2:].split()
    if len(tail) < 20:
        raise ValueError('incomplete process stat')
    return {'pid': pid, 'status': status, 'smaps_rollup': smaps,
            'utime_ticks': int(tail[11]), 'stime_ticks': int(tail[12]), 'start_ticks': int(tail[19]),
            'clock_ticks_per_second': os.sysconf('SC_CLK_TCK')}


def host_process_snapshot() -> dict[str, Any]:
    result = subprocess.run(['ps', '-eo', 'pid,ppid,pcpu,rss,etimes,comm', '--sort=-pcpu'],
                            check=True, capture_output=True, text=True, timeout=10)
    top = []
    for line in result.stdout.splitlines()[1:16]:
        fields = line.split(None, 5)
        if len(fields) != 6:
            raise ValueError('incomplete top-process listing')
        pid = int(fields[0])
        item: dict[str, Any] = {'pid': pid, 'ppid': int(fields[1]), 'cpu_percent': float(fields[2]),
                                'rss_kib': int(fields[3]), 'elapsed_seconds': int(fields[4]), 'command': fields[5]}
        try:
            item['proc'] = process_resource_sample(pid)
        except (OSError, ValueError) as exc:
            item['proc_unavailable'] = f'{type(exc).__name__}: {exc}'
        top.append(item)
    return {'timestamp_utc': utc_now(), 'monotonic_ns': time.monotonic_ns(),
            'controller_affinity': sorted(os.sched_getaffinity(0)), 'top_processes': top}
