#!/usr/bin/env python3
"""Join 50-ms host telemetry to writer commits using the benchmark monotonic anchor."""
import csv
import json
import pathlib
import sys

NUMERIC = (
    "io_some_avg10", "io_full_avg10", "memory_some_avg10", "memory_full_avg10",
    "io_some_total_us", "io_full_total_us", "memory_some_total_us", "memory_full_total_us",
    "pswpin_pages", "pswpout_pages", "read_bytes", "write_bytes", "memavailable_kib",
    "swapfree_kib",
)


def numeric(row, key):
    value = row.get(key, "NA")
    if value in ("", "NA", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {key} value {value!r}") from exc


def window(rows, start_ns, end_ns):
    return [r for r in rows if start_ns <= int(r["monotonic_ns"]) < end_ns]


def delta(rows, key):
    if len(rows) < 2:
        return None
    a, b = numeric(rows[0], key), numeric(rows[-1], key)
    return None if a is None or b is None else b - a


def analyze_arm(arm_dir):
    summary_path = arm_dir / "stdout.jsonl"
    runtime_path = arm_dir / "runtime-samples.csv"
    if not summary_path.is_file() or not runtime_path.is_file():
        raise FileNotFoundError(f"need benchmark stdout and runtime sampler: {arm_dir}")
    try:
        summary_lines = summary_path.read_text().splitlines()
        if not summary_lines:
            raise ValueError("benchmark stdout is empty")
        summary = json.loads(summary_lines[0])
    except (OSError, ValueError, IndexError) as exc:
        raise RuntimeError(f"cannot parse benchmark summary {summary_path}: {exc}") from exc
    with runtime_path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"empty runtime telemetry: {runtime_path}")
    anchor = int(summary["whole_start_monotonic_ns"])
    output = []
    cycles = sorted(summary["writer_stage_timeline_ms"], key=lambda c: int(c["round"]))
    for index, cycle in enumerate(cycles):
        round_id = int(cycle["round"])
        start_ns = anchor + int(float(cycle["build_start_ms"]) * 1_000_000)
        end_ns = anchor + int(float(cycle["round_end_ms"]) * 1_000_000)
        previous_end = (anchor + int(float(cycles[index - 1]["round_end_ms"]) * 1_000_000)
                        if index else start_ns - 250_000_000)
        next_start = (anchor + int(float(cycles[index + 1]["build_start_ms"]) * 1_000_000)
                      if index + 1 < len(cycles) else end_ns + 250_000_000)
        pre_start = max(start_ns - 250_000_000, previous_end)
        post_end = min(end_ns + 250_000_000, next_start)
        pre = window(rows, pre_start, start_ns)
        during = window(rows, start_ns, end_ns)
        post = window(rows, end_ns, post_end)
        before = pre[-1] if pre else None
        after = post[0] if post else None
        record = {
            "round": round_id,
            "commit_ms": summary["commit_samples_ms"][round_id],
            "build_start_monotonic_ns": start_ns,
            "round_end_monotonic_ns": end_ns,
            "pre_sample_ns": None if before is None else before["monotonic_ns"],
            "post_sample_ns": None if after is None else after["monotonic_ns"],
            "pre_sample_age_ms": None if before is None else (start_ns - int(before["monotonic_ns"])) / 1e6,
            "post_sample_lag_ms": None if after is None else (int(after["monotonic_ns"]) - end_ns) / 1e6,
            "pre_window_ms": (start_ns - pre_start) / 1e6,
            "commit_window_ms": (end_ns - start_ns) / 1e6,
            "post_window_ms": (post_end - end_ns) / 1e6,
            "samples_pre_during_post": f"{len(pre)}/{len(during)}/{len(post)}",
        }
        for key in NUMERIC:
            record[f"{key}_pre_delta"] = delta(pre, key)
            record[f"{key}_during_delta"] = delta(during, key)
            record[f"{key}_post_delta"] = delta(post, key)
            if key.endswith("avg10"):
                record[f"{key}_pre_last"] = None if not pre else numeric(pre[-1], key)
                record[f"{key}_during_max"] = None if not during else max((numeric(r, key) or 0) for r in during)
                record[f"{key}_post_first"] = None if not post else numeric(post[0], key)
        output.append(record)
    return output


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: analyze_host_pressure.py <pair-directory>")
    pair = pathlib.Path(sys.argv[1]).resolve(strict=True)
    arms = [p for p in pair.iterdir() if p.is_dir() and (p / "stdout.jsonl").exists()]
    if not arms:
        raise SystemExit(f"no completed arms under {pair}")
    out_path = pair / "cycle-host-pressure.csv"
    rows = []
    for arm in sorted(arms):
        for record in analyze_arm(arm):
            rows.append({"arm": arm.name, **record})
    with out_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"HOST_PRESSURE_ANALYSIS_COMPLETE arms={len(arms)} cycles={len(rows)} output={out_path}")


if __name__ == "__main__":
    main()
