#!/usr/bin/env python3
"""Check effective systemd cgroup and host reserve before execing the workload."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys


def cgroup_directory() -> pathlib.Path:
    for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines():
        hierarchy, _controllers, relative = line.split(":", 2)
        if hierarchy == "0":
            path = pathlib.Path("/sys/fs/cgroup") / relative.lstrip("/")
            if path.is_dir():
                return path
    raise RuntimeError("unified cgroup v2 path is not readable")


def memavailable_bytes() -> int:
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing")


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        raise SystemExit("usage: exec_guard.py -- COMMAND [ARGS...]")
    command = sys.argv[2:]
    if not command:
        raise SystemExit("empty workload command")
    try:
        cgroup = cgroup_directory()
        effective = {
            "memory.max": (cgroup / "memory.max").read_text().strip(),
            "memory.swap.max": (cgroup / "memory.swap.max").read_text().strip(),
            "cpu.max": (cgroup / "cpu.max").read_text().strip(),
            "pids.max": (cgroup / "pids.max").read_text().strip(),
        }
        memory_max = int(os.environ["P1_NATIVE_EXPECTED_MEMORY_MAX"])
        swap_max = int(os.environ["P1_NATIVE_EXPECTED_SWAP_MAX"])
        cpu_quota_percent = int(os.environ["P1_NATIVE_EXPECTED_CPU_QUOTA_PERCENT"])
        pids_max = int(os.environ["P1_NATIVE_EXPECTED_PIDS_MAX"])
        cpu_quota, cpu_period = effective["cpu.max"].split()
        cpu_ratio = None if cpu_quota == "max" else int(cpu_quota) * 100 / int(cpu_period)
        checks = {
            "memory_max": effective["memory.max"] == str(memory_max),
            "memory_swap_max": effective["memory.swap.max"] == str(swap_max),
            "cpu_quota_percent": cpu_ratio == cpu_quota_percent,
            "pids_max": effective["pids.max"] == str(pids_max),
        }
        root = pathlib.Path(os.environ["P1_NATIVE_RESOURCE_ROOT"])
        disk_available = shutil.disk_usage(root).free
        memory_available = memavailable_bytes()
        checks["disk_reserve"] = disk_available >= int(os.environ["P1_NATIVE_MIN_FREE_BYTES"])
        launch_cushion = int(
            os.environ.get("P1_NATIVE_MIN_FREE_BYTES_AT_LAUNCH", os.environ["P1_NATIVE_MIN_FREE_BYTES"])
        )
        checks["disk_launch_cushion"] = disk_available >= launch_cushion
        checks["host_memory_reserve"] = memory_available >= int(os.environ["P1_NATIVE_MIN_MEMAVAILABLE_BYTES"])
        result = {
            "guard": "CONTAINMENT_PREFLIGHT",
            "passed": all(checks.values()),
            "checks": checks,
            "effective_cgroup": effective,
            "cgroup_path": str(cgroup),
            "filesystem_available_bytes": disk_available,
            "host_memavailable_bytes": memory_available,
        }
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        result = {"guard": "CONTAINMENT_PREFLIGHT", "passed": False, "error": str(error)}
    print(json.dumps(result, sort_keys=True), flush=True)
    if not result["passed"]:
        return 125
    return subprocess.run(command, check=False, shell=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as error:
        print(json.dumps({"guard": "EXEC_FAILED", "error": str(error)}), file=sys.stderr, flush=True)
        raise SystemExit(127) from error
