# Diagnostic fixes and verification — 2026-10-19

## Source changes

All changes are confined to the M4 research diagnostics/runner/artifact directory; no M4 production library, Rust production code, index format, or benchmark binary was changed.

1. `capture_host_baseline.py`
   - Records both `sample_start_monotonic_ns` and end `monotonic_ns` plus `sample_duration_ns`; row values are read between these timestamps. Keeps epoch time for metadata, but all windows use monotonic time.
   - Persists baseline CSV and metadata once after observation, avoiding per-row flush I/O.
   - Removes command-line arguments from start/end process summaries; retains PID, comm, CPU and RSS.
2. `run_host_carryover_aa.py`
   - Adds `validate_baseline_samples()` and explicitly classifies `MEASUREMENT_INVALID`, `STABILITY_GATE_FAIL` and `STABILITY_GATE_PASS` without altering any threshold or the first-three/last-three feature envelope.
   - Validates required fields, finite numeric values, diskstats JSON, strictly increasing monotonic timestamps, no gap ≥1.5 nominal periods, capture coverage ≥59.9 seconds, six windows each with ≥9.9 seconds coverage, and consistent sample brackets no longer than the 50-ms nominal period.
   - Rejects invalid telemetry before profile establishment or arm launch. Historic receipts use the same check except sample timing is unavailable in those old CSVs.
3. `run_sync_reduction_pair.py`
   - Removes command-line arguments from persisted top-process snapshots; preserves PID/comm/CPU/memory/RSS. Resource limits and launch decisions are unchanged.
   - Buffers per-process PSS and host/I/O/PSI samples in memory and writes them only after the benchmark child exits, instead of issuing Python CSV writes throughout the measured workload. Adds sample-start, completion-monotonic and duration fields to bound per-row collection skew.
4. `capture_host_pressure_attribution.py`
   - Adds read-only process `/proc/<pid>/io` and CPU deltas, memory/VM counters, CPU/I/O/memory PSI, diskstats and selected Btrfs sysfs counters; buffers and writes the observation after capture. Process command arguments are not collected.
5. `stability_gate_replay.py`
   - Adds deterministic replay of the three originally authorized raw baselines, the single post-audit baseline separately, and 11 synthetic cases. It records all feature/window misses, full window boundaries/sample counts, integrity status, the diagnostic samplewise safety view of the three available time-series limits, and robust first/last median summaries without turning those summaries into an unapproved acceptance threshold.
   - Replays counterfactual “run then invalidate,” first-half-profile recovery proxies, and synthetic inter-arm recovery. Clearly labels that no historical arm timeline exists for matched-host-state A/A.

## Current-source tests

Commands/evidence were run after the source edits described above:

| Check | Result |
|---|---|
| `python3 -m py_compile` on `stability_gate_replay.py`, `run_host_carryover_aa.py`, `capture_host_baseline.py`, `analyze_host_pressure.py`, `run_sync_reduction_pair.py`, `capture_host_pressure_attribution.py` | PASS (no syntax errors) |
| `stability_gate_replay.py --self-test` | `SELF_TEST_PASS cases=11 recovery=PASS boundaries=PASS` |
| Offline raw + synthetic replay | `raw=3 post_audit=1 synthetic=11`; all three originals and the new attempt reproduce `STABILITY_GATE_FAIL`; no workload launched by replay |
| Synthetic gate cases | stable PASS; rise/fall load, transient PSI, later outlier FAIL; constant high PSI and heavy steady swap-in pass the stability envelope but the former trips the diagnostic absolute PSI limit; missing/duplicate timestamps and short capture are `MEASUREMENT_INVALID` in the runner even though the old envelope alone could accept them |
| Synthetic inter-arm recovery sequence | One outside-profile window resets recovery; three following inside-profile windows are required and then pass |
| Disposable buffered process-sampler regression | `buffered_sampler_smoke rows=8 monotonic=PASS bracket=PASS proc_io=PASS PSI=PASS no_hotpath_file_open=PASS`; an instrumented `Path.open` guard verifies neither output file is opened while the child lives |
| Disposable current ambient sampler smoke (0.20 s) | `AMBIENT_SAMPLER_SMOKE_PASS rows=4 sample_brackets=PASS argv_redacted=PASS` |
| Synthetic `analyze_host_pressure.py` join (`analyze_host_pressure_smoke.py`) | `CYCLE_JOIN_SYNTHETIC_PASS cycles=2 samples_per_cycle=2/10/3 nonoverlap=PASS monotonic_anchor=PASS` |
| Syntax checks for current diagnostic Python sources and smoke harness | `py_compile` PASS |
| LSP/AST diagnostics for 7 changed Python files | No errors or unavailable checks; 3 clean files, 4 files with 46 warnings, all `unchecked-numeric-parse-python` on conversion sites that consume validated/raw telemetry; retained as warnings, not hidden by broad refactor |

The checked-in-artifact smoke from before the final redaction-only change remains at `sampler-smoke-20261019/` (8 rows, monotonic timestamps, process I/O and PSI columns). The buffered process-sampler regression uses a separate `sampler-smoke-buffered-20261019/` directory with SHA-256 receipts; a separate current ambient-sampler smoke used temporary files. These are sampler plumbing tests, not performance/observer-overhead measurement.

## Replay conclusions

- The exact original three CSVs have six 200-sample windows each, ~9.95 seconds coverage each, and valid monotonic cadence; they are not short/malformed artifacts. Every original replay returns the historical first reason `load1_max`, with 23/21/15 total feature-window misses.
- For those original traces, the absolute limits were checked diagnostically against each available sample. MemAvailable and load never violated their limits, but I/O PSI full avg10 exceeded 4.46% in 1,128/1,200, 1,058/1,200 and 1,158/1,200 samples, respectively. Disk free was only recorded at preflight and cannot be replayed samplewise.
- In the separately labeled post-audit attempt, the runner preflight passed; the valid 60-second capture later had 64/1,200 MemAvailable samples below its existing threshold. The stability profile rejected it and the runner launched no arms. This is evidence for the distinction between snapshot and drift gates; it is not a changed enforcement policy.
- The counterfactual `run then invalidate` would have launched from each of the three valid baseline captures (their preflight snapshots passed) and then discarded the pair under the current strict rule. This was simulated only; no arms were run from a rejected baseline.
- Robust medians report changes, not acceptance decisions. No tolerance was calibrated or added. A matched-host-state comparison cannot be evaluated without two actual arm timelines.

## DIAGNOSTIC / QUALIFICATION modes — 2026-10-19

### Source-level classification

`run_sync_reduction_pair.py::preflight()` classifies four existing limits as launch safety gates and the canonical handoff records them as validated preflight limits. They remain HARD SAFETY, unchanged: MemAvailable >= 5,477,762 KiB; `/var/tmp` free >= 18,219,000,000 B; I/O PSI full avg10 <= 4.46%; load1 <= 3.14. Available baseline samples for RAM/load/I/O PSI are now checked samplewise, disk reserve remains a point-in-time check where available, and the same limits are checked during each measured arm; a violation terminates the child and records a hard-safety receipt. No gate is weakened.

Telemetry integrity/cadence and the six-window first-three/last-three envelope are measurement-quality checks. QUALIFICATION retains them. DIAGNOSTIC allows a valid but stability-envelope-failing baseline to continue only if memory/swap safety remains within its pre-existing first-three-window envelope (SwapFree floor and memory PSI ceilings); the four absolute preflight limits remain mandatory. Memory PSI and swap-in/out are recorded at 50-ms sampling; no new absolute thresholds were invented.

### Implementation and tests

`run_host_carryover_aa.py --mode diagnostic|qualification` labels mode/evidence in `pair.json` and arm `mode.json`. Diagnostic uses same binary and separate fresh roots, sequential arms, bounded recovery, continuous telemetry, and a hard-safety runtime stop. Qualification remains the default and still refuses a stability-envelope failure. `run_sync_reduction_pair.py` now records per-arm monotonic start/end, CPU ticks, PSS, process I/O, host PSI/memory/swap/load and diskstats. `analyze_host_pressure.py` joins per-cycle writer latency to host-state samples; `summarize_aa_diagnostic.py` reports writer p50/p95/p99, CPU, process write bytes, unaggregated per-device sector deltas, peak PSS/RSS/swap, arm host state and second/first ratios, explicitly exploratory.

Verification after changes: `py_compile` PASS for five scripts; `test_aa_gate_policy.py -v` PASS (4 cases covering all four hard limits, samplewise baseline hard limits, measurement-quality-only drift, and RAM/swap pressure safety). Summary percentile self-check PASS (3 cases). The reserved-word syntax bug in the new test (`pass=True`) was caught by compile and corrected before execution. The later samplewise absolute-gate check was added after the one diagnostic attempt and then verified by the focused policy test; no new benchmark attempt was made. No production/native runtime files were changed.

### One authorized diagnostic attempt

Preflight at 19:13:11 +07 passed: MemAvailable 6,154,988 KiB, load1 1.78, I/O PSI full avg10 3.45%, `/var/tmp` free 19,176,132,608 B. Runner preflight at 19:13:16 also passed. The valid 60.03-second/1,200-sample ambient trace showed hard memory/swap pressure worsening versus the existing first-three-window envelope: SwapFree floor 16,970,032 KiB; later windows reached 16,899,232 KiB (70,800 KiB below that floor); memory PSI some/full avg10 reached 0.70% versus 0.00% baseline ceiling. I/O PSI full avg10 was 6.05–10.77% in windows 0–5, above the hard 4.46% ceiling in the ambient sample series. Absolute MemAvailable remained above its floor. The runner version used for this attempt stopped on memory/swap pressure before either arm; a subsequent source audit added samplewise baseline enforcement of existing absolute limits, and replaying this raw trace against the updated check also yields a hard I/O PSI violation. No A/A result or benchmark latency evidence exists. This is not a project block or qualification result; the safety gate correctly refused this single run.

Exact receipt:
`host-carryover-aa-pair-20261019T121316Z/` (no `aa-first`/`aa-second` output); log `/tmp/pi-bg/job-2966907-65.log`.

## Resource and experiment status

A same-binary DIAGNOSTIC/QUALIFICATION implementation and policy tests now exist. The one diagnostic attempt did not launch arms because the memory/swap hard-safety envelope failed, despite the point-in-time resource preflight passing. No retry is authorized in this task. Next distinguishing action: after a fresh hard-safety preflight, collect a telemetry window and run one diagnostic only if memory PSI and SwapFree remain within the existing baseline-relative safety envelope; do not require the entire load/IO stability envelope to be idle, and do not relax the I/O hard threshold. The A/A is **not complete** and must not be reported as PASS, regression, or win.
>>> END_OF_DELIVERABLE <<<
