#!/usr/bin/env python3
"""Same-binary A/A with measured, adaptive inter-arm quiescence; research only."""
import argparse
import csv
import importlib.util
import json
import math
import os
import pathlib
import subprocess
import sys
import time
from itertools import pairwise

HERE = pathlib.Path(__file__).resolve().parent
BASELINE_MODULE = HERE / "capture_host_baseline.py"
ANALYZER = HERE / "analyze_host_pressure.py"
SUMMARIZER = HERE / "summarize_aa_diagnostic.py"
BINARY = pathlib.Path(os.environ["M4_AA_BINARY"]).resolve(strict=True)
os.environ["M4_SYNC_CONTROL_BINARY"] = str(BINARY)
os.environ["M4_SYNC_TREATMENT_BINARY"] = str(BINARY)
os.environ["M4_SYNC_PAIR_LABEL"] = "host-carryover-aa"
os.environ["M4_SYNC_PAIR_ORDER"] = "reduced-first"
spec = importlib.util.spec_from_file_location("host_baseline", BASELINE_MODULE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot import host sampler: {BASELINE_MODULE}")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
import run_sync_reduction_pair as runner  # noqa: E402

WINDOW_S = 10.0
BASELINE_S = 60.0
RECOVERY_WINDOWS = 3
RECOVERY_TIMEOUT_S = 1800.0


def disk_totals(row):
    try:
        devices = json.loads(row["diskstats_json"])
        return {key: sum(int(item[key]) for item in devices.values())
                for key in ("read_sectors", "write_sectors", "io_ms")}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid diskstats sample: {exc}") from exc


def features(rows):
    if len(rows) < 2:
        raise ValueError("host window has too few samples")
    start_ns, end_ns = int(rows[0]["monotonic_ns"]), int(rows[-1]["monotonic_ns"])
    duration_s = (end_ns - start_ns) / 1e9
    def optional_aggregate(key, cast, aggregate):
        values = []
        for row in rows:
            try:
                value = cast(row.get(key))
                if not math.isfinite(float(value)) or value < 0:
                    return None
                values.append(value)
            except (TypeError, ValueError):
                return None
        return aggregate(values) if values else None

    result = {
        "duration_s": duration_s,
        "load1_max": optional_aggregate("load1", float, max),
        "memavailable_min_kib": optional_aggregate("memavailable_kib", int, min),
        "swapfree_min_kib": min(int(r["swapfree_kib"]) for r in rows),
    }
    for key in ("io_some_avg10", "io_full_avg10", "memory_some_avg10", "memory_full_avg10"):
        result[f"{key}_max"] = optional_aggregate(key, float, max)
    for key in ("pswpin", "pswpout"):
        result[f"{key}_pages"] = int(rows[-1][key]) - int(rows[0][key])
    before, after = disk_totals(rows[0]), disk_totals(rows[-1])
    for key in before:
        result[f"disk_{key}_delta"] = after[key] - before[key]
    return result


# Host PSI, load and MemAvailable remain recorded in features/receipts but are
# not stability prerequisites or launch/abort gates. Only the explicit
# SwapFree capacity floor remains a host-side recovery criterion.
FEATURE_KEYS = ("swapfree_min_kib",)


def validate_baseline_samples(rows, require_sample_timing=True):
    """Classify missing/malformed host telemetry without changing the drift envelope."""
    required = ("monotonic_ns", "swapfree_kib", "pswpin", "pswpout", "diskstats_json")
    optional_host_context = ("load1", "memavailable_kib", "io_some_avg10", "io_full_avg10",
                             "memory_some_avg10", "memory_full_avg10")
    errors = []
    period_ns = int(host.SAMPLE_S * 1e9)
    if not rows:
        return {"status": "MEASUREMENT_INVALID", "errors": ["zero samples"], "sample_count": 0}
    timestamps = []
    timing_fields = ("sample_start_monotonic_ns", "sample_duration_ns")
    if require_sample_timing:
        required = (*required, *timing_fields)
    for index, row in enumerate(rows):
        for key in required:
            if key not in row or row[key] in (None, "", "NA"):
                errors.append({"code": "MISSING_VALUE", "row": index, "field": key})
        try:
            timestamps.append(int(row["monotonic_ns"]))
        except (KeyError, TypeError, ValueError):
            errors.append({"code": "INVALID_MONOTONIC_TIMESTAMP", "row": index})
        numeric_fields = tuple(key for key in required if key not in ("monotonic_ns", "diskstats_json"))
        for key in numeric_fields:
            try:
                value = float(row[key])
                if not math.isfinite(value):
                    raise ValueError("non-finite")
            except (KeyError, TypeError, ValueError):
                errors.append({"code": "INVALID_NUMERIC_VALUE", "row": index, "field": key})
        if all(key in row for key in timing_fields):
            try:
                sample_end = int(row["monotonic_ns"])
                sample_start = int(row["sample_start_monotonic_ns"])
                sample_duration = int(row["sample_duration_ns"])
                if sample_duration < 0 or sample_end - sample_start != sample_duration:
                    raise ValueError("inconsistent sample time bracket")
                if sample_duration > period_ns:
                    errors.append({"code": "SAMPLE_READ_EXCEEDS_PERIOD", "row": index,
                                   "duration_ns": sample_duration, "period_ns": period_ns})
            except (KeyError, TypeError, ValueError):
                errors.append({"code": "INVALID_SAMPLE_TIME_BRACKET", "row": index})
        try:
            disk = json.loads(row["diskstats_json"])
            if not isinstance(disk, dict):
                raise ValueError("diskstats must be an object")
        except (KeyError, TypeError, ValueError):
            errors.append({"code": "INVALID_DISKSTATS_JSON", "row": index})
    if len(timestamps) == len(rows):
        duplicate_count = len(timestamps) - len(set(timestamps))
        if duplicate_count:
            errors.append({"code": "DUPLICATE_MONOTONIC_TIMESTAMPS", "count": duplicate_count})
        if any(b <= a for a, b in pairwise(timestamps)):
            errors.append({"code": "NONINCREASING_MONOTONIC_TIMESTAMPS"})
        gaps = [b - a for a, b in pairwise(timestamps) if b - a >= 3 * period_ns // 2]
        if gaps:
            errors.append({"code": "SAMPLE_GAP_OVER_1_5_PERIODS", "count": len(gaps),
                           "max_gap_ns": max(gaps), "period_ns": period_ns,
                           "gap_limit_ns": 3 * period_ns // 2})
        duration_ns = timestamps[-1] - timestamps[0]
        window_count = (duration_ns + 10_000_000_000 - 1) // 10_000_000_000
        if duration_ns < int((BASELINE_S - 2 * host.SAMPLE_S) * 1e9):
            errors.append({"code": "CAPTURE_SHORTER_THAN_60S_COVERAGE", "duration_ns": duration_ns,
                           "minimum_duration_ns": int((BASELINE_S - 2 * host.SAMPLE_S) * 1e9)})
        for index in range(window_count):
            left = timestamps[0] + index * 10_000_000_000
            right = left + 10_000_000_000
            bucket = [t for t in timestamps if left <= t < right]
            coverage_ns = 0 if len(bucket) < 2 else bucket[-1] - bucket[0]
            minimum_coverage_ns = 10_000_000_000 - 2 * period_ns
            if coverage_ns < minimum_coverage_ns:
                errors.append({"code": "PARTIAL_10S_WINDOW", "window_index": index,
                               "coverage_ns": coverage_ns,
                               "minimum_coverage_ns": minimum_coverage_ns})
    else:
        duration_ns, window_count = 0, 0
    cadence = [b - a for a, b in pairwise(timestamps)]
    if window_count < 6:
        errors.append({"code": "INSUFFICIENT_CAPTURE_WINDOWS", "window_count": window_count})
    unavailable_host_context = set()
    for row in rows:
        for key in optional_host_context:
            try:
                value = float(row[key])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid optional context")
            except (KeyError, TypeError, ValueError):
                unavailable_host_context.add(key)
    return {"status": "MEASUREMENT_INVALID" if errors else "VALID",
            "host_context_telemetry_unavailable": sorted(unavailable_host_context),
            "host_context_telemetry_disposition": "observational only; missing or malformed host pressure/load/MemAvailable does not block measurement",
            "sample_count": len(rows), "duration_s": duration_ns / 1e9,
            "window_count": window_count, "expected_period_ns": period_ns,
            "cadence_ns": None if not cadence else {"min": min(cadence), "median": sorted(cadence)[len(cadence)//2], "max": max(cadence)},
            "sample_timing_available": all(all(key in row for key in timing_fields) for row in rows),
            "errors": errors}


def ten_second_windows(rows):
    start = int(rows[0]["monotonic_ns"])
    out = []
    span_ns = int(rows[-1]["monotonic_ns"]) - start
    window_count = (span_ns + 10_000_000_000 - 1) // 10_000_000_000
    for idx in range(window_count):
        left, right = start + idx * 10_000_000_000, start + (idx + 1) * 10_000_000_000
        window = [r for r in rows if left <= int(r["monotonic_ns"]) < right]
        if window:
            out.append(features(window))
    return out


def establish_profile(rows):
    windows = ten_second_windows(rows)
    if len(windows) < 6:
        return None, {"ok": False, "reason": "baseline has fewer than six full 10-second windows", "windows": windows}
    first, last = windows[:3], windows[-3:]
    failures = []
    for key in FEATURE_KEYS:
        lo = min(w[key] for w in first)
        hi = max(w[key] for w in first)
        for index, window in enumerate(last, start=len(windows) - 3):
            if not lo <= window[key] <= hi:
                failures.append({"feature": key, "window_index": index, "value": window[key],
                                 "first_three_min": lo, "first_three_max": hi,
                                 "direction": "below" if window[key] < lo else "above"})
    if failures:
        first_failure = failures[0]
        reason = f"baseline drift: last half outside first-half envelope for {first_failure['feature']}"
        return None, {"ok": False, "reason": reason, "windows": windows, "failure_details": failures}
    profile = {key: {"min": min(w[key] for w in windows), "max": max(w[key] for w in windows)} for key in FEATURE_KEYS}
    return profile, {"ok": True, "windows": windows, "method": "six 10s windows; require last three inside first-three envelope; recovery requires 3 consecutive windows no worse than baseline pressure maxima and memory/swap-free minima"}


def within_profile(window, profile):
    return all(window[key] >= profile[key]["min"] for key in FEATURE_KEYS)


def resource_guard_failures(state, thresholds):
    checks = (
        ("resource_root_available_bytes", thresholds["var_tmp_bytes_min"], lambda observed, bound: observed >= bound),
    )
    metadata = runner.BENCHMARK_POLICY["resource_guard_metadata"]
    by_key = {spec["threshold_key"]: (gate_id, spec) for gate_id, spec in metadata.items()}
    key_map = {"resource_root_available_bytes": "var_tmp_bytes_min"}
    failures = []
    for key, bound, predicate in checks:
        observed = state.get(key)
        if observed is None or not predicate(observed, bound):
            gate_id, spec = by_key[key_map[key]]
            failures.append({"metric": key, "observed": observed, "threshold": bound,
                             "gate_id": gate_id, "threshold_key": key_map[key],
                             "evidence_class": spec["evidence_class"]})
    return failures


def gate_snapshot(policy_context=None, phase='recovery'):
    if policy_context is not None:
        state = runner.read_host(policy_context)
        decision = runner.containment.contention_decision(state['contention_host_snapshot'], policy_context, phase)
        state.update({'pass': decision['continuation_allowed'], 'diagnostic_decision': decision,
                      'resource_guard_failures': decision['blocking_failures'],
                      'historical_guard_failures': decision['historical_guard_failures']})
        return state
    state = runner.read_host()
    state["thresholds"] = runner.THRESHOLDS
    state["resource_guard_failures"] = resource_guard_failures(state, runner.THRESHOLDS)
    state["hard_safety_failures"] = state["resource_guard_failures"]  # compatibility field
    state["policy_id"] = runner.BENCHMARK_POLICY["policy_id"]
    state["source_of_authority"] = runner.BENCHMARK_POLICY["authority"]["historical_envelope_source"]
    state["failure_scope"] = "current A/A operation only"
    state["mission_status"] = "NOT_EVALUATED_BY_RUNNER"
    state["pass"] = not state["resource_guard_failures"]
    return state


def baseline_pressure_limits(rows):
    """Derive only existing baseline-relative memory/swap bounds from first 3 windows."""
    windows = ten_second_windows(rows)
    if len(windows) < 3:
        return None
    reference = windows[:3]
    return {
        "swapfree_min_kib": min(w["swapfree_min_kib"] for w in reference),
        "memory_some_avg10_max": max((w["memory_some_avg10_max"] for w in reference
                                       if w["memory_some_avg10_max"] is not None), default=0.0),
        "memory_full_avg10_max": max((w["memory_full_avg10_max"] for w in reference
                                       if w["memory_full_avg10_max"] is not None), default=0.0),
    }


def resource_guard_baseline_failures(rows, thresholds):
    """Apply only the retained-disk reserve to baseline samples."""
    failures = []
    for index, row in enumerate(rows):
        metric, observed, bound = ("resource_root_available_bytes",
                                  int(row["resource_root_available_bytes"]),
                                  thresholds["var_tmp_bytes_min"])
        if observed < bound:
            metadata = runner.BENCHMARK_POLICY["resource_guard_metadata"]
            gate_id = next(gid for gid, spec in metadata.items()
                           if spec["threshold_key"] == "var_tmp_bytes_min")
            failures.append({"sample_index": index, "metric": metric, "observed": observed,
                             "threshold": bound, "gate_id": gate_id, "threshold_key": "var_tmp_bytes_min",
                             "evidence_class": metadata[gate_id]["evidence_class"]})
    return failures


def baseline_pressure_failures(windows, pressure_limits):
    failures = []
    for index, window in enumerate(windows[3:], start=3):
        if window["swapfree_min_kib"] < pressure_limits["swapfree_min_kib"]:
            failures.append({"window_index": index, "features": window, "pressure_limits": pressure_limits})
    return failures


def baseline_pressure_window_pass(window, pressure_limits):
    return (pressure_limits is not None and
            window["swapfree_min_kib"] >= pressure_limits["swapfree_min_kib"])


def diagnostic_window_safe(window, state, pressure_limits):
    return state["pass"] and baseline_pressure_window_pass(window, pressure_limits)


def write_rows(path, rows):
    if not rows:
        return
    with pathlib.Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def validate_recovery_samples(rows):
    required = ("monotonic_ns", "sample_start_monotonic_ns", "sample_duration_ns", "load1",
                "memavailable_kib", "swapfree_kib", "io_full_avg10", "memory_some_avg10",
                "memory_full_avg10", "pswpin", "pswpout", "diskstats_json")
    errors = []
    if len(rows) < int(WINDOW_S / host.SAMPLE_S * 0.9):
        errors.append(f"sample_count={len(rows)} below 90% of nominal window cadence")
    if rows:
        for key in required:
            if key not in rows[0]:
                errors.append(f"missing sample field {key}")
        times = [int(row["monotonic_ns"]) for row in rows]
        if any(b <= a for a, b in pairwise(times)):
            errors.append("monotonic timestamps are not strictly increasing")
        if len(times) > 1:
            max_gap_ns = int(host.SAMPLE_S * 1.5 * 1e9)
            if max(b - a for a, b in pairwise(times)) > max_gap_ns:
                errors.append("sample gap exceeds 1.5 nominal periods")
            if times[-1] - times[0] < int(WINDOW_S * 0.9 * 1e9):
                errors.append("sample coverage is shorter than 90% of recovery window")
        numeric_fields = ("load1", "memavailable_kib", "swapfree_kib", "io_full_avg10",
                          "memory_some_avg10", "memory_full_avg10", "pswpin", "pswpout")
        for index, row in enumerate(rows):
            try:
                start = int(row["sample_start_monotonic_ns"])
                end = int(row["monotonic_ns"])
                duration = int(row["sample_duration_ns"])
                if start > end or duration < 0 or end - start != duration:
                    errors.append(f"sample {index} has invalid timestamp bracket")
                if "epoch_ns" not in row or int(row["epoch_ns"]) <= 0:
                    errors.append(f"sample {index} has invalid wall-clock timestamp")
                for key in numeric_fields:
                    value = float(row[key])
                    if not math.isfinite(value):
                        errors.append(f"sample {index} has non-finite {key}")
                devices = json.loads(row["diskstats_json"])
                if not isinstance(devices, dict):
                    errors.append(f"sample {index} diskstats is not an object")
                for device, values in devices.items():
                    if not isinstance(values, dict) or any(k not in values for k in ("read_sectors", "write_sectors", "io_ms")):
                        errors.append(f"sample {index} has malformed diskstats for {device}")
                        break
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"sample {index} is malformed: {exc}")
                break
    else:
        errors.append("empty recovery window")
    return {"status": "VALID" if not errors else "MEASUREMENT_INVALID", "rows": len(rows), "errors": errors}


def run_contained_pair(pair, mode, pressure_limits, profile, *, arm_runner=None,
                       capture_window=None, snapshot=None, required_windows=RECOVERY_WINDOWS,
                       timeout_seconds=RECOVERY_TIMEOUT_S, policy_context=None):
    """Run the sequential A/A arms only through the containment-backed arm runner."""
    if (mode == runner.containment.CONTENTION_MODE) != (policy_context is not None):
        raise ValueError('pair mode/context mismatch')
    if policy_context is not None:
        runner.containment.validate_contention_context(policy_context)
        if pair.resolve() != pathlib.Path(policy_context['operation_root']) or required_windows != RECOVERY_WINDOWS:
            raise ValueError('contention operation/recovery count mismatch')
    arm_runner = arm_runner or runner.run_arm
    capture_window = capture_window or (lambda: capture_for_mode(WINDOW_S, policy_context, 'recovery'))
    snapshot = snapshot or (gate_snapshot if policy_context is None else lambda: gate_snapshot(policy_context))
    context_kwargs = {} if policy_context is None else {'policy_context': policy_context}
    interarm_rows = []
    window_records = []
    consecutive = 0
    deadline = time.monotonic() + timeout_seconds
    previous_expected_identity = runner.EXPECTED_AA_IDENTITY
    expected_identity = previous_expected_identity or runner.binary_runtime_identity(BINARY)
    runner.EXPECTED_AA_IDENTITY = expected_identity
    (pair / "expected-binary-identity.json").write_text(json.dumps(expected_identity, indent=2, sort_keys=True) + "\n")

    def persist_interarm(recovered):
        write_rows(pair / "interarm-host-samples.csv", interarm_rows)
        first_identity = read_arm_identity(pair / "aa-first")
        second_identity = read_arm_identity(pair / "aa-second")
        (pair / "interarm-quiescence.json").write_text(json.dumps({
            "windows": window_records, "recovered": recovered, "mode": mode,
            "policy_context": policy_context,
            "evidence_class": runner.containment.CONTENTION_EVIDENCE if policy_context is not None else None,
            "recovery_policy": ('contention capacity/integrity windows, no historical-envelope or idle-match prerequisite' if policy_context is not None else
                                "all operation resource and baseline-pressure guards; diagnostic evidence remains exploratory" if mode == "diagnostic" else "qualification stability envelope plus all operation resource and baseline-pressure guards"),
            "required_consecutive_windows": required_windows, "timeout_seconds": timeout_seconds,
            "first_arm_identity": first_identity,
            "second_arm_identity": second_identity,
            "same_binary_source_and_native_library_identity": (
                first_identity is not None and second_identity is not None and
                first_identity.get("runtime_identity") == second_identity.get("runtime_identity")
            ),
        }, indent=2) + "\n")

    try:
        print("AA_ARM_START name=aa-first", flush=True)
        if not arm_runner("aa-first", BINARY, mode, pressure_limits, **context_kwargs):
            persist_interarm(False)
            raise SystemExit("STOP: first arm operation-guard/containment gate failed; see aa-first artifacts")
        print("AA_ARM_COMPLETE name=aa-first", flush=True)
        print("AA_INTERARM_RECOVERY_START", flush=True)
        if policy_context is not None:
            deadline = time.monotonic() + timeout_seconds  # recovery timeout excludes first-arm runtime
        while consecutive < required_windows and time.monotonic() < deadline:
            try:
                rows, metadata = capture_window()
            except Exception as exc:
                window_records.append({"window": len(window_records), "status": "MEASUREMENT_INVALID",
                                       "error": f"{type(exc).__name__}: {exc}",
                                       "consecutive_recovered_windows": 0})
                consecutive = 0
                persist_interarm(False)
                raise SystemExit("NO_LAUNCH: MEASUREMENT_INVALID; inter-arm telemetry source failed") from exc
            interarm_rows.extend(rows)
            integrity = (validate_contention_samples(rows, policy_context, 'recovery', WINDOW_S)
                         if policy_context is not None else validate_recovery_samples(rows))
            if integrity["status"] != "VALID":
                window_records.append({"window": len(window_records), "status": "MEASUREMENT_INVALID",
                                       "measurement_integrity": integrity,
                                       "consecutive_recovered_windows": 0})
                consecutive = 0
                persist_interarm(False)
                raise SystemExit("NO_LAUNCH: MEASUREMENT_INVALID; inter-arm window integrity failed")
            try:
                state = snapshot()
                if not isinstance(state, dict) or "pass" not in state:
                    raise ValueError("resource-gate snapshot is incomplete")
            except Exception as exc:
                window_records.append({"window": len(window_records), "status": "MEASUREMENT_INVALID",
                                       "error": f"resource snapshot: {type(exc).__name__}: {exc}",
                                       "consecutive_recovered_windows": 0})
                consecutive = 0
                persist_interarm(False)
                raise SystemExit("NO_LAUNCH: MEASUREMENT_INVALID; inter-arm resource snapshot unavailable") from exc
            try:
                summary = features(rows)
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                window_records.append({"window": len(window_records), "status": "MEASUREMENT_INVALID",
                                       "measurement_integrity": integrity,
                                       "error": f"feature extraction: {type(exc).__name__}: {exc}",
                                       "consecutive_recovered_windows": 0})
                consecutive = 0
                persist_interarm(False)
                raise SystemExit("NO_LAUNCH: MEASUREMENT_INVALID; inter-arm feature extraction failed") from exc
            quality_profile_match = (within_profile(summary, profile)
                                     if mode == "qualification" and profile is not None else None)
            decisions = None
            resource_guard_pass = state["pass"]
            pressure_guard_pass = baseline_pressure_window_pass(summary, pressure_limits)
            if policy_context is not None:
                decisions = contention_rows_decisions(rows, policy_context, 'recovery')
                if not state['pass'] or any(not d['continuation_allowed'] for d in decisions):
                    window_records.append({'window': len(window_records), 'resource_gate': state,
                                           'contention_decisions': decisions, 'consecutive_recovered_windows': 0})
                    persist_interarm(False)
                    raise SystemExit('NO_LAUNCH: contention capacity failure; no idle-wait retry')
                recovered = True
            else:
                recovered = (diagnostic_window_safe(summary, state, pressure_limits) and
                             (quality_profile_match is not False))
            consecutive = consecutive + 1 if recovered else 0
            baseline_match = None if profile is None or summary is None else within_profile(summary, profile)
            record = {"window": len(window_records), "features": summary, "resource_gate": state,
                      "resource_guard_pass": resource_guard_pass,
                      "baseline_pressure_guard_pass": pressure_guard_pass,
                      "baseline_pressure_guard_enforced": policy_context is None,
                      "measurement_quality_profile_match": quality_profile_match,
                      "hard_safety_window_pass": (recovered if policy_context is not None else
                                                  False if summary is None else diagnostic_window_safe(summary, state, pressure_limits)),
                      "contention_decisions": decisions if policy_context is not None else None,
                      "within_no_worse_baseline_limits": baseline_match,
                      "consecutive_recovered_windows": consecutive,
                      "measurement_integrity": integrity, "capture_metadata": metadata}
            window_records.append(record)
            print(f"AA_RECOVERY_WINDOW n={len(window_records)} integrity={integrity['status']} recovered={recovered} consecutive={consecutive}", flush=True)
        recovered = consecutive >= required_windows
        persist_interarm(recovered)
        if not recovered:
            raise SystemExit("NO_LAUNCH: host did not pass integrity and recovery gates before timeout")
        current_identity = runner.binary_runtime_identity(BINARY)
        if current_identity != expected_identity:
            (pair / "identity-mismatch-before-second-arm.json").write_text(json.dumps({
                "expected": expected_identity, "observed": current_identity,
                "benchmark_second_arm_launched": False,
            }, indent=2) + "\n")
            raise SystemExit("NO_LAUNCH: binary/source/native-library identity changed before second arm")
        print("AA_ARM_START name=aa-second", flush=True)
        if not arm_runner("aa-second", BINARY, mode, pressure_limits, **context_kwargs):
            persist_interarm(False)
            raise SystemExit("STOP: second arm operation-guard/containment gate failed; see aa-second artifacts")
        print("AA_ARM_COMPLETE name=aa-second", flush=True)
        persist_interarm(True)
    except BaseException:
        persist_interarm(consecutive >= required_windows)
        raise
    finally:
        runner.EXPECTED_AA_IDENTITY = previous_expected_identity
    first_identity = read_arm_identity(pair / "aa-first")
    second_identity = read_arm_identity(pair / "aa-second")
    return {"recovered": consecutive >= required_windows, "windows": window_records,
            "same_binary_source_and_native_library_identity": (
                first_identity is not None and second_identity is not None and
                first_identity.get("runtime_identity") == second_identity.get("runtime_identity")
            ),
            "first_arm_identity": first_identity,
            "second_arm_identity": second_identity}


def read_arm_identity(arm_dir):
    result_path = pathlib.Path(arm_dir) / "arm-result.json"
    if not result_path.is_file():
        return None
    result = runner.containment.strict_json_file(result_path)
    hash_path = pathlib.Path(arm_dir) / "binary-sha256.txt"
    digest = hash_path.read_text().split()[0] if hash_path.is_file() else None
    identity_path = pathlib.Path(arm_dir) / "binary-identity.json"
    runtime_identity = runner.containment.strict_json_file(identity_path) if identity_path.is_file() else None
    return {"arm": pathlib.Path(arm_dir).name, "unit": result.get("unit"),
            "binary_sha256": digest, "runtime_identity": runtime_identity, "private_workdir": result.get("private_workdir"),
            "workload_root": result.get("workload_root"), "status": result.get("status"),
            "performance_eligible": result.get("performance_eligible")}


def archived_build_source_provenance():
    import hashlib
    current_receipt = os.environ.get("M4_AA_BUILD_RECEIPT")
    if current_receipt:
        path = pathlib.Path(current_receipt).resolve(strict=True)
        return {"manifest": str(path), "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "build_receipt": runner.containment.strict_json_file(path),
                "interpretation": "current build receipt; inputs and runtime checked before this authorized attempt"}
    provenance = HERE / "current-corrected-tests-20261019/provenance.sha256"
    entries = []
    if provenance.is_file():
        for line in provenance.read_text().splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2:
                continue
            path = pathlib.Path(fields[1])
            observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            entries.append({"archived_sha256": fields[0], "path": str(path),
                            "current_sha256": observed,
                            "matches_current": None if observed is None else observed == fields[0]})
    logs = {}
    build_dir = HERE / "current-corrected-tests-20261019"
    for name in ("configure.log", "build.log"):
        path = build_dir / name
        if path.is_file():
            logs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"manifest": str(provenance), "entries": entries, "archived_build_log_sha256": logs,
            "compiler_flags_recovered": False,
            "interpretation": "exact binary SHA is authoritative; archived source checksums are partial build provenance, and current source fingerprints are captured separately"}


def sample_contention(context, phase):
    start = time.monotonic_ns()
    state = runner.containment.host_snapshot(runner.RESOURCE_ROOT)
    decision = runner.containment.contention_decision(state, context, phase)
    if not decision['continuation_allowed']:
        raise RuntimeError('contention capacity failure: ' + json.dumps(decision))
    disk = {name: {'read_sectors': d['sectors_read'], 'write_sectors': d['sectors_written'], 'io_ms': d['io_ms']}
            for name, d in state['diskstats'].items()}
    mem = state.get('meminfo_kib', {})
    load = state.get('loadavg') or []
    row = {'load1': load[0] if load else None,
           'memavailable_kib': mem.get('MemAvailable_kib'),
           'swapfree_kib': mem.get('SwapFree_kib'),
           'pswpin': state['vmstat']['pswpin_pages'], 'pswpout': state['vmstat']['pswpout_pages'],
           'diskstats_json': json.dumps(disk), 'contention_host_json': json.dumps(state)}
    for family, prefix in (('io_pressure', 'io'), ('memory_pressure', 'memory'), ('cpu_pressure', 'cpu')):
        pressure = state.get(family) or {}
        for kind in ('some', 'full'):
            values = pressure.get(kind) or {}
            row[f'{prefix}_{kind}_avg10'] = values.get('avg10')
            row[f'{prefix}_{kind}_total_us'] = values.get('total')
    end = time.monotonic_ns()
    row.update({'monotonic_ns': end, 'epoch_ns': time.time_ns(), 'sample_start_monotonic_ns': start,
                'sample_duration_ns': end-start})
    return row


def capture_for_mode(duration, context, phase):
    if context is None:
        return host.capture(duration, None)
    return host.capture(duration, None, sample_function=lambda: sample_contention(context, phase))


def contention_rows_decisions(rows, context, phase):
    if not rows:
        raise ValueError('missing contention samples')
    decisions = []
    previous = None
    for row in rows:
        state = runner.containment.strict_json_text(row['contention_host_json'])
        decisions.append(runner.containment.contention_decision(state, context, phase))
        if previous is not None:
            for key in ('pswpin_pages', 'pswpout_pages', 'oom_kill', 'pgscan_direct'):
                if state['vmstat'][key] < previous['vmstat'][key]:
                    raise ValueError('nonmonotonic ambient/recovery swap/reclaim telemetry')
            if state['vmstat']['oom_kill'] > previous['vmstat']['oom_kill']:
                raise ValueError('host OOM kill during baseline/recovery; no attribution to M4')
        previous = state
    return decisions


def validate_contention_samples(rows, context, phase, duration):
    """Integrity at declared monitor resolution, not nominal idle-host cadence."""
    errors = []
    try:
        contention_rows_decisions(rows, context, phase)
        gap = context['max_monitor_gap_seconds']*1e9
        times = []
        for row in rows:
            if any(isinstance(row.get(k),bool) or not isinstance(row.get(k),int) for k in
                   ('monotonic_ns','epoch_ns','sample_start_monotonic_ns','sample_duration_ns')):
                raise ValueError('invalid contention timestamp types')
            end, start, read_duration = (int(row[k]) for k in ('monotonic_ns', 'sample_start_monotonic_ns', 'sample_duration_ns'))
            state = json.loads(row['contention_host_json'])
            if not start <= state['monotonic_ns'] <= end or read_duration != end-start or not 0 <= read_duration <= gap:
                raise ValueError('invalid/overlong contention sample bracket')
            if int(row['epoch_ns']) <= 0:
                raise ValueError('invalid contention wall-clock stamp')
            required_channels = (
                ('swapfree_kib', state['meminfo_kib']['SwapFree_kib']),
                ('pswpin', state['vmstat']['pswpin_pages']),
                ('pswpout', state['vmstat']['pswpout_pages']),
            )
            for key, value in required_channels:
                if isinstance(row[key], bool) or not math.isfinite(float(row[key])) or float(row[key]) != value:
                    raise ValueError('inconsistent contention sample channels: '+key)
            optional_channels = (
                ('load1', (state.get('loadavg') or [None])[0]),
                ('memavailable_kib', state.get('meminfo_kib', {}).get('MemAvailable_kib')),
                ('io_some_avg10', ((state.get('io_pressure') or {}).get('some') or {}).get('avg10')),
                ('io_full_avg10', ((state.get('io_pressure') or {}).get('full') or {}).get('avg10')),
                ('memory_some_avg10', ((state.get('memory_pressure') or {}).get('some') or {}).get('avg10')),
                ('memory_full_avg10', ((state.get('memory_pressure') or {}).get('full') or {}).get('avg10')),
            )
            for key, value in optional_channels:
                observed = row.get(key)
                if observed is None or value is None:
                    continue
                if isinstance(observed, bool) or not math.isfinite(float(observed)) or float(observed) != value:
                    raise ValueError('inconsistent optional host-context channel: '+key)
            expected_disk = {name: {'read_sectors':d['sectors_read'], 'write_sectors':d['sectors_written'], 'io_ms':d['io_ms']}
                             for name,d in state['diskstats'].items()}
            if json.loads(row['diskstats_json']) != expected_disk:
                raise ValueError('inconsistent contention diskstats channels')
            times.append(end)
        if len(times) < 2 or any(not 0 < b-a <= gap for a,b in pairwise(times)):
            raise ValueError('missing/nonmonotonic contention coverage')
        if times[-1]-times[0] < duration*1e9-gap:
            raise ValueError('short contention capture')
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        errors.append(str(exc))
    return {'status': 'MEASUREMENT_INVALID' if errors else 'VALID', 'errors': errors,
            'rows': len(rows), 'maximum_observation_gap_seconds': context['max_monitor_gap_seconds'],
            'nominal_sample_period_seconds': host.SAMPLE_S,
            'resolution_note': 'actual brackets retained; reduced resolution limits attribution, not a host-idleness test'}


def baseline_gate_decision(mode, integrity, pressure_limits, absolute_failures,
                           pressure_failures, quality_pass, quality_reason=None,
                           policy_context=None, contention_decisions=None):
    if (mode == runner.containment.CONTENTION_MODE) != (policy_context is not None):
        raise ValueError('baseline mode/context mismatch')
    if policy_context is not None:
        runner.containment.validate_contention_context(policy_context)
        decisions = contention_decisions or []
        valid = integrity.get('status') == 'VALID' and bool(decisions)
        allowed = valid and all(d['continuation_allowed'] for d in decisions)
        return {'policy_id': runner.BENCHMARK_POLICY['policy_id'],
                'operation_outcome': 'OPERATION_READY_EXPLORATORY' if allowed else 'OPERATION_BLOCKED',
                'gate_id': None if allowed else 'M4-CA-CAPACITY-OR-INTEGRITY',
                'gate_class': 'CONTENTION_AWARE_CAPACITY_AND_INTEGRITY',
                'reason': 'approved workload plan; historical comparisons retained, not idle prerequisites' if allowed else 'capacity/integrity did not pass',
                'evidence_class': runner.containment.CONTENTION_EVIDENCE,
                'historical_guard_failures': absolute_failures,
                'historical_baseline_pressure_failures': pressure_failures,
                'contention_decisions': contention_decisions, 'policy_context': policy_context,
                'recovery_conditions': 'capacity and integrity; no idle wait, no automatic retry',
                'mission_status': 'NOT_EVALUATED_BY_RUNNER'}
    # Preserve all raw historical comparisons in receipts, but only the disk
    # reserve remains a host-side admission guard in non-contention modes.
    absolute_failures = [failure for failure in absolute_failures
                         if failure.get("gate_id") == "M4-RG-DISK"]
    policy = runner.BENCHMARK_POLICY
    if integrity.get("status") != "VALID":
        gate_id, gate_class = "M4-MQ-INTEGRITY", "MEASUREMENT_INTEGRITY"
        outcome, reason = "OPERATION_BLOCKED", "baseline telemetry integrity did not pass"
        evidence = policy["measurement_quality"]["telemetry_integrity"]["requirements"]
    elif pressure_limits is None:
        gate_id, gate_class = "M4-MQ-INTEGRITY", "BASELINE_PRESSURE_EVIDENCE_INCOMPLETE"
        outcome, reason = "OPERATION_BLOCKED", "insufficient valid baseline windows to derive pressure bounds"
        evidence = policy["baseline_pressure_guard"]["evidence_class"]
    elif absolute_failures:
        gate_ids = sorted({item["gate_id"] for item in absolute_failures})
        gate_id, gate_class = gate_ids, "HISTORICAL_ENVELOPE_RESOURCE_GUARD"
        outcome, reason = "OPERATION_BLOCKED", "ambient baseline violated unchanged operation-level resource guard(s)"
        evidence = policy["authority"]["historical_envelope_source"]
    elif pressure_failures:
        gate_id, gate_class = policy["baseline_pressure_guard"]["gate_id"], "BASELINE_DERIVED_RESOURCE_GUARD"
        outcome, reason = "OPERATION_BLOCKED", "ambient baseline exceeded its derived memory/swap pressure envelope"
        evidence = policy["baseline_pressure_guard"]["evidence_class"]
    elif not quality_pass and mode == "qualification":
        gate_id, gate_class = "M4-MQ-STABILITY", "MEASUREMENT_QUALITY"
        outcome, reason = "OPERATION_BLOCKED", quality_reason or "baseline stability envelope failed"
        evidence = policy["measurement_quality"]["stability_envelope"]["diagnostic_evidence_class"]
    elif not quality_pass:
        gate_id, gate_class = "M4-MQ-STABILITY", "MEASUREMENT_QUALITY_ONLY"
        outcome, reason = "OPERATION_READY_EXPLORATORY", quality_reason or "baseline stability envelope failed"
        evidence = policy["measurement_quality"]["stability_envelope"]["diagnostic_evidence_class"]
    else:
        gate_id, gate_class = None, "MEASUREMENT_QUALITY_PASS"
        outcome, reason, evidence = "OPERATION_READY", None, "QUALIFICATION"
    if gate_class == "HISTORICAL_ENVELOPE_RESOURCE_GUARD":
        gate_ids = gate_id if isinstance(gate_id, list) else [gate_id]
        recovery = "; ".join(next(spec["recovery"] for spec in policy["resource_guard_metadata"].values()
                                   if spec["threshold_key"] == next(f["threshold_key"] for f in absolute_failures
                                                                    if f["gate_id"] == current))
                              for current in gate_ids)
    elif gate_class == "BASELINE_DERIVED_RESOURCE_GUARD":
        recovery = policy["baseline_pressure_guard"]["recovery"]
    elif gate_class == "MEASUREMENT_INTEGRITY" or gate_class == "BASELINE_PRESSURE_EVIDENCE_INCOMPLETE":
        recovery = "restore complete, finite, timestamped telemetry and enough valid windows to derive pressure bounds; no launch until valid"
    elif gate_class in {"MEASUREMENT_QUALITY", "MEASUREMENT_QUALITY_ONLY"}:
        recovery = "qualification requires a valid stable baseline; diagnostic mode may continue only with valid telemetry and all resource/pressure guards, and remains exploratory"
    else:
        recovery = None
    return {
        "policy_id": policy["policy_id"], "gate_id": gate_id, "gate_class": gate_class,
        "operation_outcome": outcome, "reason": reason, "evidence_class": evidence,
        "failure_scope": "this baseline/A/A operation only",
        "mission_status": "NOT_EVALUATED_BY_RUNNER", "mission_marked_done": False,
        "safe_independent_work_may_continue": True,
        "recovery_conditions": recovery,
        "owner_approval_required_to_change_thresholds": gate_class == "HISTORICAL_ENVELOPE_RESOURCE_GUARD",
    }


def write_operation_status(pair, status, phase, *, reason=None, gate_id=None,
                           gate_class=None, evidence_class=None, recovery=None,
                           safe_independent_work_may_continue=None):
    payload = {
        "schema_version": 1, "operation_id": pair.name,
        "operation": "same-binary A/A host-carryover measurement",
        "operation_status": status, "phase": phase, "reason": reason,
        "gate_id": gate_id, "gate_class": gate_class,
        "evidence_class": evidence_class, "recovery_conditions": recovery,
        "failure_scope": "this benchmark/A/A operation only",
        "mission_status": "NOT_EVALUATED_BY_RUNNER", "mission_marked_done": False,
        "safe_independent_work_may_continue": (status != "SAFETY_STOP" if safe_independent_work_may_continue is None
                                                else safe_independent_work_may_continue),
        "policy_id": runner.BENCHMARK_POLICY["policy_id"],
        "source_of_authority": runner.BENCHMARK_POLICY["authority"]["historical_envelope_source"],
    }
    temp = pair / ".operation-status.json.tmp"
    temp.write_text(json.dumps(payload, indent=2) + "\n")
    temp.replace(pair / "operation-status.json")
    return payload


def write_contention_analysis(pair, baseline_rows, context):
    """Temporal pressure association only; host/process scopes are intentionally separate."""
    def host_summary(states):
        return {family: {kind: {'min': min(s[family][kind]['avg10'] for s in states),
                               'max': max(s[family][kind]['avg10'] for s in states),
                               'mean': sum(s[family][kind]['avg10'] for s in states)/len(states)}
                         for kind in (('some',) if family == 'cpu_pressure' else ('some', 'full'))}
                for family in ('io_pressure', 'memory_pressure', 'cpu_pressure')}
    def swap_rates(states):
        elapsed = (states[-1]['monotonic_ns'] - states[0]['monotonic_ns'])/1e9
        if elapsed <= 0:
            raise ValueError('insufficient monotonic pressure evidence')
        return {key: (states[-1]['vmstat'][key]-states[0]['vmstat'][key])/elapsed
                for key in ('pswpin_pages', 'pswpout_pages')}
    baseline = [runner.containment.strict_json_text(r['contention_host_json']) for r in baseline_rows]
    result = {'evidence_class': runner.containment.CONTENTION_EVIDENCE,
              'operation_id': context['operation_id'], 'ambient_host_psi': host_summary(baseline),
              'ambient_host_swap_pages_per_second': swap_rates(baseline), 'arms': {},
              'interpretation': 'During-minus-ambient is temporal association, NOT identified M4 incremental causality. PSI avg10 is smoothed and host-wide. CPU usage is owned-cgroup time; process IO snapshots can lose exited workers. No I/O hard cap, no candidate win/regression/production conclusion.',
              'missing_for_causal_identification': ['matched counterfactual host workload', 'per-stage CPU/runqueue/wait/syscall attribution; no heavy tracing enabled']}
    for arm in ('aa-first', 'aa-second'):
        path = pair/arm/'containment/runtime.jsonl'
        if not path.is_file():
            continue
        rows = [runner.containment.strict_json_text(line) for line in path.read_text().splitlines()]
        active = [r for r in rows if r.get('elapsed_ns') is not None]
        if len(active) < 2:
            raise ValueError('insufficient active-arm contention telemetry')
        states = [r['host'] for r in active]
        for row in active:
            runner.containment.contention_decision(row['host'], context,
                'runtime-first' if arm == 'aa-first' else 'runtime-second',
                runner.containment.workspace_reservation_credit(row['workspace']))
        elapsed = (active[-1]['monotonic_ns'] - active[0]['monotonic_ns'])/1e9
        host_psi = host_summary(states)
        result['arms'][arm] = {'host_psi': host_psi, 'host_swap_pages_per_second': swap_rates(states),
            'host_psi_mean_difference_from_ambient': {f: {k: v['mean']-result['ambient_host_psi'][f][k]['mean']
                                                        for k,v in kinds.items()} for f,kinds in host_psi.items()},
            'owned_cgroup_cpu_seconds_observed': (active[-1]['cgroup']['cpu_stat']['usage_usec']-active[0]['cgroup']['cpu_stat']['usage_usec'])/1e6,
            'observed_wall_seconds': elapsed,
            'peak_owned_memory_bytes': max(r['cgroup']['memory_current_bytes'] for r in active),
            'peak_owned_swap_bytes': max(r['cgroup']['memory_swap_current_bytes'] for r in active),
            'peak_process_io_write_bytes_snapshot': max(r['cgroup']['process_tree_write_bytes'] for r in active),
            'historical_exceedance_samples': sum(bool(r['diagnostic_decision']['historical_guard_failures']) for r in active),
            'historical_gate_failure_counts': {gid: sum(any(g['gate_id']==gid for g in r['diagnostic_decision']['historical_guard_failures']) for r in active)
                                               for gid in runner.BENCHMARK_POLICY['resource_guard_metadata']}}
    (pair/'contention-analysis.json').write_text(json.dumps(result, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("diagnostic", "qualification", "contention-aware"), default="qualification")
    parser.add_argument('--contention-plan', type=pathlib.Path)
    args = parser.parse_args()
    mode = args.mode
    policy_context = None
    if mode == runner.containment.CONTENTION_MODE:
        if args.contention_plan is None:
            raise SystemExit('NO_LAUNCH: approved operation-bound resource plan and separate execution authorization required')
        policy_context = runner.containment.strict_json_file(args.contention_plan)
        runner.containment.validate_contention_context(policy_context)
        operation = HERE / policy_context['operation_id']
        if (str(operation.resolve()) != policy_context['operation_root'] or
                str(runner.RESOURCE_ROOT) != policy_context['resource_root']):
            raise SystemExit('NO_LAUNCH: contention plan root binding mismatch')
        filesystem = subprocess.check_output(['stat', '-f', '-c', '%T', str(runner.RESOURCE_ROOT)], text=True).strip()
        if filesystem != 'btrfs':
            raise SystemExit('NO_LAUNCH: contention workload requires fresh roots on Btrfs')
        runner.PAIR = operation
    elif args.contention_plan is not None:
        raise SystemExit('NO_LAUNCH: contention plan cannot leak into qualification/legacy diagnostic')
    if not BINARY.is_file():
        raise SystemExit(f"missing A/A binary: {BINARY}")
    pair = runner.PAIR
    pair.mkdir(parents=True, exist_ok=False)
    if policy_context is not None:
        runner.verify_full_sync(BINARY)
        (pair / 'approved-contention-plan.json').write_text(json.dumps(policy_context, indent=2) + '\n')
    binary_identity = runner.binary_runtime_identity(BINARY)
    runner.EXPECTED_AA_IDENTITY = binary_identity
    (pair / "binary-identity.json").write_text(json.dumps(binary_identity, indent=2, sort_keys=True) + "\n")
    binary_sha = subprocess.check_output(["sha256sum", str(BINARY)], text=True).split()[0]
    (pair / "pair.json").write_text(json.dumps({
        "experiment": "same-binary A/A host carry-over check",
        "mode": mode,
        "evidence_class": (runner.containment.CONTENTION_EVIDENCE if policy_context is not None else
                           "EXPLORATORY / NOT QUALIFICATION EVIDENCE" if mode == "diagnostic" else "QUALIFICATION"),
        "policy_context": policy_context,
        "quality_gate_policy": ('contention is observed, not eliminated; capacity and telemetry integrity always required' if policy_context is not None else
                                "stability envelope is non-blocking only in diagnostic mode; telemetry integrity is always required"),
        "binary": str(BINARY), "binary_sha256": binary_sha,
        "binary_runtime_identity": "binary hash + current source git revision/tree fingerprint + resolved shared-library path/hash; enforced before each arm",
        "source_git_revision": binary_identity["source_git_revision"],
        "current_source_tree_sha256": binary_identity["current_source_tree_sha256"],
        "loaded_shared_libraries": binary_identity["loaded_shared_libraries"],
        "binary_build_source_provenance": archived_build_source_provenance(),
        "workload": "500 docs x 20 rounds", "fresh_roots": True,
        "sampling_interval_ms": 50, "baseline_observation_seconds": BASELINE_S,
        "recovery_rule": ('3 consecutive valid 10-second capacity/integrity windows; no historical-envelope/idle matching' if policy_context is not None else
                          "3 consecutive 10-second windows within unchanged operation resource and baseline-pressure guards; stability similarity does not block exploratory recovery"
                          if mode == "diagnostic" else
                          "3 consecutive 10-second windows no worse than stable pre-arm pressure maxima and memory/swap-free minima, plus existing resource gate"),
        "resource_thresholds": runner.THRESHOLDS,
        "benchmark_policy_id": runner.BENCHMARK_POLICY["policy_id"],
        "resource_threshold_authority": runner.BENCHMARK_POLICY["authority"]["historical_envelope_source"],
        "whole_mission_status": "NOT_EVALUATED_BY_RUNNER",
        "durability_note": runner.DESCRIPTION,
    }, indent=2) + "\n")
    write_operation_status(pair, "IN_PROGRESS", "baseline-preflight")
    if not (runner.preflight('baseline', policy_context) if policy_context is not None else runner.preflight('baseline')):
        preflight = runner.containment.strict_json_file(pair / "baseline" / "preflight.json")
        failures = (preflight.get('diagnostic_decision', {}).get('blocking_failures', [])
                    if policy_context is not None and preflight.get('diagnostic_decision') is not None else
                    preflight.get("resource_guard_failures", preflight.get("hard_safety_failures", [])))
        telemetry_incomplete = preflight.get("status") == "TELEMETRY_INCOMPLETE"
        write_operation_status(pair, "OPERATION_BLOCKED", "baseline-preflight",
            reason=preflight.get("reason") or "one or more unchanged operation-level resource guards failed",
            gate_id=("M4-MQ-INTEGRITY" if telemetry_incomplete else
                     [failure.get("gate_id") for failure in failures]),
            gate_class=("MEASUREMENT_INTEGRITY" if telemetry_incomplete else
                        "CONTENTION_CAPACITY" if policy_context is not None else "HISTORICAL_ENVELOPE_RESOURCE_GUARD"),
            evidence_class=(runner.containment.CONTENTION_EVIDENCE if policy_context is not None else
                            "required preflight telemetry unavailable" if telemetry_incomplete else
                            "historical successful-run envelope; not universal host safety"),
            recovery=(preflight.get("recovery") if telemetry_incomplete else
                      "fresh preflight meets all unchanged guards; no automatic retry"))
        raise SystemExit("NO_LAUNCH: OPERATION_BLOCKED by telemetry integrity at baseline preflight" if telemetry_incomplete
                         else "NO_LAUNCH: OPERATION_BLOCKED by resource guard at baseline preflight")
    print(f"AA_BASELINE_START duration_s={BASELINE_S:g} sample_ms={host.SAMPLE_S * 1000:g}", flush=True)
    baseline_path = pair / "ambient-baseline.csv"
    baseline_rows = []
    try:
        baseline_rows, metadata = capture_for_mode(BASELINE_S, policy_context, 'baseline')
        integrity = (validate_contention_samples(baseline_rows, policy_context, 'baseline', BASELINE_S)
                     if policy_context is not None else validate_baseline_samples(baseline_rows))
        contention_decisions = contention_rows_decisions(baseline_rows, policy_context, 'baseline') if policy_context is not None else None
    except Exception as exc:
        contention_decisions = None
        metadata = {"status": "CAPTURE_FAILED", "error": f"{type(exc).__name__}: {exc}",
                    "whole_mission_status": "NOT_EVALUATED_BY_RUNNER"}
        integrity = {"status": "MEASUREMENT_INVALID", "rows": len(baseline_rows),
                     "errors": [f"host telemetry capture failed: {type(exc).__name__}: {exc}"]}
    if integrity["status"] != "VALID":
        profile = None
        profile_result = {"ok": False, "status": "MEASUREMENT_INVALID",
                          "reason": "host telemetry integrity validation failed",
                          "measurement_integrity": integrity, "windows": []}
    else:
        profile, profile_result = establish_profile(baseline_rows)
        profile_result["status"] = "STABILITY_GATE_PASS" if profile is not None else "STABILITY_GATE_FAIL"
        profile_result["measurement_integrity"] = integrity

    def persist_baseline():
        write_rows(baseline_path, baseline_rows)
        baseline_path.with_suffix(".host.json").write_text(json.dumps(metadata, indent=2) + "\n")
        (pair / "quiescence-profile.json").write_text(json.dumps({
            "ambient_capture": metadata, "baseline_result": profile_result, "profile": profile,
        }, indent=2) + "\n")

    baseline_quality_pass = profile is not None
    pressure_limits = baseline_pressure_limits(baseline_rows) if integrity["status"] == "VALID" else None
    if policy_context is not None and pressure_limits is not None:
        pressure_limits['contention_baseline_host_oom_kill'] = runner.containment.strict_json_text(baseline_rows[-1]['contention_host_json'])['vmstat']['oom_kill']
    resource_failures = resource_guard_baseline_failures(baseline_rows, runner.THRESHOLDS) if integrity["status"] == "VALID" else []
    pressure_failures = (baseline_pressure_failures(ten_second_windows(baseline_rows), pressure_limits)
                         if pressure_limits is not None else [])
    profile_result["resource_guard_failures"] = resource_failures
    profile_result["baseline_pressure_failures"] = pressure_failures
    decision = baseline_gate_decision(mode, integrity, pressure_limits, resource_failures,
                                      pressure_failures, baseline_quality_pass, profile_result.get("reason"),
                                      policy_context=policy_context, contention_decisions=contention_decisions)
    profile_result["baseline_decision"] = decision
    (pair / "baseline-gate-decision.json").write_text(json.dumps(decision, indent=2) + "\n")
    persist_baseline()
    if decision["operation_outcome"] == "OPERATION_BLOCKED":
        write_operation_status(pair, "OPERATION_BLOCKED", "baseline-validation",
            reason=decision["reason"], gate_id=decision["gate_id"],
            gate_class=decision["gate_class"], evidence_class=decision["evidence_class"],
            recovery=decision["recovery_conditions"])
        print(f"AA_BASELINE_BLOCKED class={decision['gate_class']} gate={decision['gate_id']} reason={decision['reason']}", flush=True)
        raise SystemExit(f"NO_LAUNCH: {decision['gate_class']} ({decision['gate_id']}); {decision['reason']}")
    if baseline_quality_pass:
        print(f"AA_BASELINE_STABLE windows={len(ten_second_windows(baseline_rows))} duration_s={metadata['duration_seconds']:.2f}", flush=True)
    else:
        print(f"AA_BASELINE_QUALITY_FAIL_NONBLOCKING mode=diagnostic reason={profile_result.get('reason')}; evidence=EXPLORATORY_NOT_QUALIFICATION", flush=True)
    write_operation_status(pair, "IN_PROGRESS", "contained-pair")
    try:
        run_contained_pair(pair, mode, pressure_limits, profile,
                           **({'policy_context': policy_context} if policy_context is not None else {}))
        persist_baseline()
        if policy_context is not None:
            write_contention_analysis(pair, baseline_rows, policy_context)
        else:
            subprocess.run([sys.executable, str(ANALYZER), str(pair)], check=True)
        if mode == "diagnostic":
            subprocess.run([sys.executable, str(SUMMARIZER), str(pair)], check=True)
    except BaseException:
        persist_baseline()
        raise
    write_operation_status(pair, "OPERATION_COMPLETE", "analysis-complete",
        evidence_class=(runner.containment.CONTENTION_EVIDENCE if policy_context is not None else
                        "EXPLORATORY / NOT QUALIFICATION EVIDENCE" if mode == "diagnostic" else "QUALIFICATION"),
        recovery="not applicable")
    print(f"AA_COMPLETE mode={mode} {pair}", flush=True)


def record_unhandled_operation_exit(pair, exc):
    if isinstance(exc, FileExistsError):
        return  # never overwrite a consumed operation's original receipt on a duplicate attempt
    if not pair.is_dir():
        return
    status_path = pair / "operation-status.json"
    current = runner.containment.strict_json_file(status_path) if status_path.is_file() else {}
    if current.get("operation_status") not in {None, "IN_PROGRESS"}:
        return
    receipts = []
    for path in pair.rglob("receipt.json"):
        try:
            receipts.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    unverified = any(item.get("safety_outcome") == "CONTAINMENT_UNVERIFIED" for item in receipts)
    message = str(exc)
    receipt_statuses = {item.get("status") for item in receipts}
    if unverified or "SAFETY_STOP" in message:
        status, gate_class = "SAFETY_STOP", "CONTAINMENT_OR_CLEANUP_UNVERIFIED"
    elif "MEASUREMENT_INVALID" in message:
        status, gate_class = "OPERATION_BLOCKED", "MEASUREMENT_INTEGRITY"
    elif isinstance(exc, SystemExit) and ("NO_LAUNCH" in message or "STOP:" in message):
        if receipt_statuses.intersection({"NONZERO_EXIT", "SIGNAL_EXIT", "LAUNCH_FAILED"}):
            status, gate_class = "OPERATION_FAILED", "WORKLOAD_OR_LAUNCH_FAILURE"
        else:
            status, gate_class = "OPERATION_BLOCKED", "OPERATION_LEVEL_GATE_REFUSAL"
    else:
        status, gate_class = "OPERATION_FAILED", "RUNNER_OR_ANALYSIS_FAILURE"
    decision_path = pair / "baseline-gate-decision.json"
    decision = runner.containment.strict_json_file(decision_path) if decision_path.is_file() else {}
    gate_id = decision.get("gate_id")
    evidence_class = decision.get("evidence_class")
    recovery = decision.get("recovery_conditions")
    for item in receipts:
        if item.get("gate_decisions"):
            failed = [gate for gate in item["gate_decisions"]
                      if gate.get("status") == "FAIL" and gate.get("enforced", False)]
            if failed:
                gate_id = [gate.get("gate_id") for gate in failed]
                evidence_class = "; ".join(sorted({gate.get("evidence_class", "") for gate in failed}))
                recovery = "; ".join(sorted({gate.get("recovery", "") for gate in failed}))
                break
    pair_path = pair / 'pair.json'
    pair_info = runner.containment.strict_json_file(pair_path) if pair_path.is_file() else {}
    if pair_info.get('mode') == runner.containment.CONTENTION_MODE:
        evidence_class = runner.containment.CONTENTION_EVIDENCE
        gate_id = [item.get('reason') for item in receipts if item.get('reason')] or gate_id
        recovery = 'preserve original receipt; fix actual capacity/integrity/containment blocker; new explicit authorization for another operation, no idle wait or automatic retry'
    write_operation_status(pair, status, "uncaught-operation-exit",
        reason=message, gate_id=gate_id, gate_class=gate_class,
        evidence_class=evidence_class, recovery=recovery,
        safe_independent_work_may_continue=not unverified)


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        record_unhandled_operation_exit(runner.PAIR, exc)
        raise
