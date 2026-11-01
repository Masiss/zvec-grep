#!/usr/bin/env python3
"""Measure one command's wall time, CPU, max PSS and I/O, with host state."""

from __future__ import annotations

import argparse
import json
import pathlib
import resource
import subprocess
import sys
import tempfile
import time


def read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0])
    return values


def host_state() -> dict[str, object]:
    load = pathlib.Path("/proc/loadavg").read_text().split()
    mem = read_meminfo()
    pressure = {}
    try:
        for line in pathlib.Path("/proc/pressure/memory").read_text().splitlines():
            key, _, rest = line.partition(" ")
            pressure[key] = rest.strip()
    except OSError:
        pass
    return {
        "load1": float(load[0]),
        "load5": float(load[1]),
        "load15": float(load[2]),
        "mem_available_kib": mem.get("MemAvailable"),
        "swap_free_kib": mem.get("SwapFree"),
        "memory_pressure": pressure,
    }


def read_pss_kib(pid: int) -> int | None:
    try:
        for line in pathlib.Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def read_io(pid: int) -> dict[str, int] | None:
    try:
        values = {}
        for line in pathlib.Path(f"/proc/{pid}/io").read_text().splitlines():
            key, _, value = line.partition(":")
            values[key.strip()] = int(value.strip())
        return values
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=pathlib.Path, required=True)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("command is required")

    before_host = host_state()
    before_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    # Use temporary files rather than pipes: large --json reports would fill a
    # pipe while the sampler waits for process exit.
    with tempfile.TemporaryFile(mode="w+") as out_file, tempfile.TemporaryFile(
        mode="w+"
    ) as err_file:
        proc = subprocess.Popen(
            command,
            cwd=args.cwd,
            stdout=out_file,
            stderr=err_file,
            text=True,
        )
        max_pss = 0
        last_io: dict[str, int] | None = None
        while proc.poll() is None:
            pss = read_pss_kib(proc.pid)
            if pss is not None:
                max_pss = max(max_pss, pss)
            io = read_io(proc.pid)
            if io is not None:
                last_io = io
            time.sleep(args.interval)
        proc.wait()
        out_file.seek(0)
        err_file.seek(0)
        stdout = out_file.read()
        stderr = err_file.read()
    wall = time.perf_counter() - started
    after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    after_host = host_state()

    payload = {
        "command": command,
        "exit_code": proc.returncode,
        "wall_seconds": round(wall, 6),
        "user_seconds": round(after_usage.ru_utime - before_usage.ru_utime, 6),
        "system_seconds": round(after_usage.ru_stime - before_usage.ru_stime, 6),
        "max_rss_kib": after_usage.ru_maxrss,
        "max_pss_kib": max_pss,
        "io": last_io,
        "host_before": before_host,
        "host_after": after_host,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
