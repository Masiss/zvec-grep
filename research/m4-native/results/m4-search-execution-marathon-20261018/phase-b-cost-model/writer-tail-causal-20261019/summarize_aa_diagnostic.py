#!/usr/bin/env python3
"""Summarize one same-binary diagnostic A/A without qualification claims."""
import csv
import json
import math
import os
import pathlib
import sys


def percentile(values, q):
    values = sorted(float(x) for x in values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    return values[low] if low == high else values[low] * (high - pos) + values[high] * (pos - low)


def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def summarize_arm(arm):
    summary = json.loads((arm / "stdout.jsonl").read_text().splitlines()[0])
    commits = [float(x) for x in summary["commit_samples_ms"]]
    rt = rows(arm / "runtime-samples.csv")
    pss = rows(arm / "pss-samples.csv")
    cgroup_cpu = [x.get("cgroup_cpu_usage_usec", "") for x in rt]
    if all(value not in ("", "NA", None) for value in cgroup_cpu):
        cpu_seconds = max(0, int(cgroup_cpu[-1]) - int(cgroup_cpu[0])) / 1_000_000
        cpu_source = "systemd cgroup cpu.stat usage_usec; includes bootstrap and benchmark processes"
    else:
        cpu_ticks = [int(x["process_cpu_ticks"]) for x in rt]
        tick_rate = os.sysconf("SC_CLK_TCK")
        cpu_seconds = max(0, cpu_ticks[-1] - cpu_ticks[0]) / tick_rate
        cpu_source = "per-process /proc stat ticks"
    disks0 = json.loads(rt[0]["diskstats_json"])
    disks1 = json.loads(rt[-1]["diskstats_json"])
    device_write_sectors = {
        dev: max(0, int(disks1.get(dev, {}).get("sectors_written", 0)) - int(d0.get("sectors_written", 0)))
        for dev, d0 in disks0.items() if dev in disks1
    }
    pss_peak_kib = max((int(x["pss_kb"]) for x in pss), default=None)
    process_write_delta = max(0, int(rt[-1]["write_bytes"]) - int(rt[0]["write_bytes"]))
    elapsed_s = (int(rt[-1]["monotonic_ns"]) - int(rt[0]["monotonic_ns"])) / 1e9
    return {
        "rounds": len(commits), "writer_commit_ms": {
            "p50": percentile(commits, .50), "p95": percentile(commits, .95),
            "p99": percentile(commits, .99), "max": max(commits),
        },
        "cpu_seconds": cpu_seconds,
        "cpu_measurement_scope": cpu_source,
        "cpu_percent_of_one_core": 100 * cpu_seconds / elapsed_s if elapsed_s > 0 else None,
        "process_write_bytes_delta_proc_io": process_write_delta,
        "device_write_sector_deltas_by_device_unaggregated": device_write_sectors,
        "peak_pss_kib": pss_peak_kib,
        "peak_rss_kib": max((int(x["rss_kb"]) for x in pss), default=None),
        "peak_process_swap_kib": max((int(x["swap_kb"]) for x in pss), default=None),
        "duration_seconds_from_sampler": elapsed_s,
        "benchmark_peak_cpu_percent_reported": summary.get("peak_cpu_percent"),
        "benchmark_commit_write_bytes_reported": summary.get("commit_write_bytes"),
        "benchmark_compaction_physical_bytes_reported": summary.get("compaction_physical_bytes"),
        "host_state": {
            "io_full_avg10_max": max(float(x["io_full_avg10"]) for x in rt),
            "memory_some_avg10_max": max(float(x["memory_some_avg10"]) for x in rt),
            "memory_full_avg10_max": max(float(x["memory_full_avg10"]) for x in rt),
            "load1_max": max(float(x["load1"]) for x in rt),
            "memavailable_min_kib": min(int(x["memavailable_kib"]) for x in rt),
            "swapfree_min_kib": min(int(x["swapfree_kib"]) for x in rt),
            "pswpin_pages_delta": int(rt[-1]["pswpin_pages"]) - int(rt[0]["pswpin_pages"]),
            "pswpout_pages_delta": int(rt[-1]["pswpout_pages"]) - int(rt[0]["pswpout_pages"]),
        },
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: summarize_aa_diagnostic.py <pair-directory>")
    pair = pathlib.Path(sys.argv[1]).resolve(strict=True)
    pair_meta = json.loads((pair / "pair.json").read_text())
    if pair_meta.get("mode") != "diagnostic":
        raise SystemExit("refusing to label non-diagnostic run as exploratory A/A")
    first, second = summarize_arm(pair / "aa-first"), summarize_arm(pair / "aa-second")
    report = {
        "status": "EXPLORATORY / NOT QUALIFICATION EVIDENCE",
        "causality": "one same-binary pair cannot establish causality",
        "binary_sha256": pair_meta["binary_sha256"],
        "arms": {"aa-first": first, "aa-second": second},
        "second_over_first": {
            "commit_p50_ratio": first["writer_commit_ms"]["p50"] and second["writer_commit_ms"]["p50"] / first["writer_commit_ms"]["p50"],
            "commit_p95_ratio": first["writer_commit_ms"]["p95"] and second["writer_commit_ms"]["p95"] / first["writer_commit_ms"]["p95"],
            "commit_p99_ratio": first["writer_commit_ms"]["p99"] and second["writer_commit_ms"]["p99"] / first["writer_commit_ms"]["p99"],
        },
        "order_effect_caveat": "arm order and host-state difference are inseparable in one pair; compare per-cycle latency with aligned telemetry, do not infer causality",
    }
    out = pair / "diagnostic-summary.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"DIAGNOSTIC_SUMMARY_WRITTEN {out}")


if __name__ == "__main__":
    main()
