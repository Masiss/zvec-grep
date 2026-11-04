#!/usr/bin/env python3
"""Record ambient host telemetry to establish an empirical quiescence envelope; no workload."""
import csv
import json
import os
import pathlib
import subprocess
import threading
import time

OUT = pathlib.Path(os.environ.get(
    "M4_HOST_BASELINE_OUT",
    pathlib.Path(__file__).resolve().parent / "host-quiescence-baseline-20261019.csv",
))
DURATION_S = 60.0  # observation duration only; never treated as a recovery wait
SAMPLE_S = 0.05
WAKE = threading.Event()


def parse_numeric(value, cast):
    try:
        return cast(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid proc numeric value {value!r}") from exc


def read_meminfo():
    return {line.split(":", 1)[0]: parse_numeric(line.split()[1], int)
            for line in pathlib.Path("/proc/meminfo").read_text().splitlines() if ":" in line}


def read_vmstat():
    wanted = {"pswpin", "pswpout"}
    return {key: parse_numeric(value, int) for key, value in
            (line.split() for line in pathlib.Path("/proc/vmstat").read_text().splitlines()) if key in wanted}


def read_pressure():
    """Collect optional host context; absent/malformed PSI remains unknown, not a workload gate."""
    result = {f"{kind}_{level}_{field}": None
              for kind in ("io", "memory") for level in ("some", "full")
              for field in ("avg10", "total_us")}
    for kind in ("io", "memory"):
        try:
            lines = pathlib.Path(f"/proc/pressure/{kind}").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if not parts or parts[0] not in ("some", "full"):
                continue
            try:
                values = dict(token.split("=", 1) for token in parts[1:] if "=" in token)
                result[f"{kind}_{parts[0]}_avg10"] = parse_numeric(values["avg10"], float)
                result[f"{kind}_{parts[0]}_total_us"] = parse_numeric(values["total"], int)
            except (KeyError, RuntimeError):
                continue
    return result


def read_diskstats():
    devices = {}
    for line in pathlib.Path("/proc/diskstats").read_text().splitlines():
        f = line.split()
        if len(f) >= 14:
            devices[f[2]] = {"read_sectors": parse_numeric(f[5], int), "write_sectors": parse_numeric(f[9], int), "io_ms": parse_numeric(f[12], int)}
    return devices


def sample():
    sample_start_ns = time.monotonic_ns()
    mem = read_meminfo()
    try:
        load1 = parse_numeric(pathlib.Path("/proc/loadavg").read_text().split()[0], float)
    except (OSError, IndexError, RuntimeError):
        load1 = None
    row = {
        "load1": load1,
        "memavailable_kib": mem.get("MemAvailable"),
        "swapfree_kib": mem.get("SwapFree", 0),
        "swaptotal_kib": mem.get("SwapTotal", 0),
        **read_vmstat(), **read_pressure(),
        "diskstats_json": json.dumps(read_diskstats(), separators=(",", ":")),
    }
    sample_end_ns = time.monotonic_ns()
    row.update({"monotonic_ns": sample_end_ns, "epoch_ns": time.time_ns(),
                "sample_start_monotonic_ns": sample_start_ns,
                "sample_duration_ns": sample_end_ns - sample_start_ns})
    return row


FIELDS = ["monotonic_ns", "epoch_ns", "sample_start_monotonic_ns", "sample_duration_ns",
          "load1", "memavailable_kib", "swapfree_kib", "swaptotal_kib",
          "pswpin", "pswpout", "io_some_avg10", "io_some_total_us", "io_full_avg10", "io_full_total_us",
          "memory_some_avg10", "memory_some_total_us", "memory_full_avg10", "memory_full_total_us", "diskstats_json"]


def capture(duration_s, output_path=None, *, sample_function=None):
    output_path = None if output_path is None else pathlib.Path(output_path)
    start = subprocess.check_output(["ps", "-eo", "pid,comm,%cpu,rss", "--sort=-%cpu"], text=True).splitlines()[:20]
    rows = []
    began = time.monotonic()
    deadline = began + duration_s
    next_sample = began
    while time.monotonic() < deadline:
        rows.append((sample_function or sample)())
        next_sample += SAMPLE_S
        remaining = next_sample - time.monotonic()
        if remaining > 0:
            WAKE.wait(remaining)
    end = subprocess.check_output(["ps", "-eo", "pid,comm,%cpu,rss", "--sort=-%cpu"], text=True).splitlines()[:20]
    # Persist once after observation so the observer doesn't generate a disk
    # write for every sample or contaminate the quiescence interval.
    metadata = {
        "start_top_processes": start, "end_top_processes": end,
        "duration_seconds": time.monotonic() - began, "sample_interval_seconds": SAMPLE_S,
        "rows": len(rows), "purpose": "empirical ambient baseline; no workload and no recovery claim",
    }
    if output_path is not None:
        with output_path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if sample_function and rows else FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        output_path.with_suffix(".host.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return rows, metadata


def main():
    rows, _ = capture(DURATION_S, OUT)
    print(f"HOST_BASELINE_COMPLETE rows={len(rows)} csv={OUT}")


if __name__ == "__main__":
    main()
