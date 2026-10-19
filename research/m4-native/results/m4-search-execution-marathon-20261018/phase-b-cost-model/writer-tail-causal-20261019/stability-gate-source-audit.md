# Stability-gate source audit — 2026-10-19

## Scope and conclusion

Audited the three raw 60-second captures, their preflight receipts, and the live sampler/runner/analyzer sources. No benchmark ran during this audit. The historical preflight snapshot passed on all three attempts; the current baseline algorithm rejected validly sampled data as unstable. Replaying the exact current envelope function shows the printed `load1_max` reason was only the *first* failing feature. All three captures also have substantial I/O-PSI excursions and other feature-envelope misses. The evidence does **not** support calling these false rejections: the observed host state was changing enough that the captures are not credible matched-condition A/A baselines.

The gate is nevertheless brittle and diagnostically incomplete. It has zero tolerance outside an empirical first-half range, compares only the first and last halves, and reports only the first offending feature. Synthetic traces demonstrate both a later tiny excursion rejected by a narrow envelope and a much larger later shift accepted when an early outlier widened that envelope. That asymmetry is evidence to review the contract, not evidence to change it. No resource or stability threshold was changed.

## Source and threshold provenance

Relevant source files:

- `run_sync_reduction_pair.py`: `THRESHOLDS`, `read_host()`, `preflight()`, process sampler.
- `run_host_carryover_aa.py`: baseline capture, feature aggregation, stability comparison, recovery gate.
- `capture_host_baseline.py`: 50-ms ambient telemetry sampler.
- `analyze_host_pressure.py`: monotonic join of benchmark-cycle intervals to sampler data.

The four values in `run_sync_reduction_pair.py` are the established B3.1 empirical successful-run envelope recorded in project guidance (`AGENTS.md`) and the M4 handoff, not values derived from these three failures and not an idle-host definition:

| Resource | Current threshold | Source-level use |
|---|---:|---|
| `MemAvailable` | ≥ 5,477,762 KiB | Read once in each `preflight()` snapshot. |
| Free bytes on resource root (`/var/tmp`) | ≥ 18,219,000,000 B | `df -B1` once in each `preflight()` snapshot. |
| I/O PSI `full avg10` | ≤ 4.46% | Read once in each `preflight()` snapshot. |
| Linux load average, 1 minute | ≤ 3.14 | Read once in each `preflight()` snapshot. |

Project guidance describes the envelope as an empirical successful envelope from ten valid arm-before receipts; the code itself only hardcodes the numbers and does not retain the receipt IDs or an individual approver. Do not tune these values from the rejected captures.

`preflight()` runs before the ambient baseline and before each benchmark arm. Inter-arm recovery calls `gate_snapshot()` once after each 10-second capture and checks the same four resource limits. There is **no continuous safety abort** during the 60-second baseline or either arm. The 50-ms sampler collects host metrics during an arm but its readings do not trigger a safety stop. `MemAvailable`, load, I/O PSI, and free disk are therefore snapshot-gated at those points, not continuously gated.

## Stability-envelope semantics

`capture_host_baseline.py` uses `SAMPLE_S = 0.05` and stores in-memory rows throughout the capture; it writes CSV/JSON only after observation. `run_host_carryover_aa.py` applies:

1. Anchor windows at the first sample's `monotonic_ns`.
2. Compute `window_count = ceil((last_ts - first_ts) / 10 s)`.
3. Use half-open timestamp buckets `[start + k·10s, start + (k+1)·10s)`.
4. For each bucket, compute feature maxima/minima or first-to-last counter deltas.
5. Require at least six buckets.
6. For **each** feature, require every last-three-window feature to be within the inclusive range `[min(first three), max(first three)]`. There is no epsilon, relative tolerance, median/MAD rule, or per-feature weighting.
7. Return at the first feature in `FEATURE_KEYS` order that violates the condition.

Feature definitions:

- `load1_max`: maximum `/proc/loadavg` 1-minute value in that 10-second bucket. Linux already smooths load average; the gate does no additional smoothing and compares bucket maxima, not means.
- `memavailable_min_kib` and `swapfree_min_kib`: bucket minima.
- I/O and memory `some/full avg10`: maxima of the kernel PSI `avg10` samples. PSI `avg10` is already a rolling 10-second average; the code does not recompute PSI from total counters.
- `pswpin_pages` / `pswpout_pages`: first-to-last `/proc/vmstat` counter delta within the bucket. The gate compares per-window activity, not just whether swap occurred.
- `disk_*_delta`: first-to-last totals of `/proc/diskstats` fields. `disk_totals()` sums all listed devices, including stacked layers (physical disk, partition, device-mapper, and zram); those totals can double-count the same I/O and are not physical-I/O totals. This does not explain away the independent PSI/load/swap misses, but it makes the disk feature unsuitable for physical attribution and can amplify topology-dependent noise.

The code comment/reason says “six full 10-second windows”, but the old path only used `ceil(span/10s)` and `features()` accepted any bucket with two samples. A short capture around 50.2 seconds could produce six buckets with a tiny last bucket. The new diagnostic integrity check rejects short coverage/partial buckets before profile establishment; the stability formula itself remains unchanged.

## Timestamp, window, and race audit

The three CSVs each contain 1,200 rows and six buckets of 200 rows. Their span is 59.9506–59.9508 seconds. Observed cadence:

| Capture | min ms | median ms | p99 ms | max ms | Windows |
|---|---:|---:|---:|---:|---|
| 09:32:49Z | 48.979 | 49.998 | 50.599 | 51.146 | 6 × 200 |
| 09:36:27Z | 49.190 | 50.010 | 50.485 | 50.758 | 6 × 200 |
| 09:38:24Z | 48.722 | 49.997 | 50.549 | 51.170 | 6 × 200 |

There are no missing or duplicate timestamps in these historic captures under the added 1.5×-period gap check; all six buckets have ~9.95 seconds of timestamp coverage. The baseline uses `CLOCK_MONOTONIC` (`time.monotonic_ns`) for grouping. Epoch time is recorded but is not used for the stability comparison. Benchmark cycle joins also use monotonic anchors, avoiding wall-clock conversion.

The old sampler timestamp was read before the load/PSI/disk reads in the same row, so it was a leading timestamp for a sequential, non-atomic sample. No evidence shows that this small intra-row skew caused the 10-second envelope failures, but the skew was not bounded in the old artifacts. The sampler now records a start/end bracket, reports `monotonic_ns` at sample completion, and records `sample_duration_ns`; the active-run validator rejects an impossible bracket or a sample collection longer than the 50-ms cadence. Preflight values are also sequential reads rather than an atomic snapshot and the old receipt has only a wall-clock second, but no race is evidenced by these captures.

## Exact historical decisions

All three baseline preflight receipts pass the unchanged four limits:

| Start +07 | MemAvailable KiB | load1 | I/O PSI full avg10 | `/var/tmp` free B | Snapshot |
|---|---:|---:|---:|---:|---|
| 16:32:49 | 9,502,220 | 2.73 | 2.59% | 19,263,115,264 | PASS |
| 16:36:27 | 9,397,528 | 2.32 | 3.32% | 19,259,400,192 | PASS |
| 16:38:25 | 9,430,804 | 2.45 | 4.44% | 19,257,237,504 | PASS |

Direct replay of the raw samples through `ten_second_windows()`, `features()`, and `establish_profile()` reproduces the existing reason for all three: `baseline drift: last half outside first-half envelope for load1_max`. The source returns the first feature only because `load1_max` is first in `FEATURE_KEYS`. Full failing feature/window/value ranges are in `stability-gate-offline-replay.json`.

The load change itself was not uniformly large: attempt 1 declined from a first-three range 2.46–2.73 to 2.17–2.31; attempt 2's last windows were 2.44 vs a narrow first-three range 2.38–2.39; attempt 3's last two windows were 2.78 vs 2.47–2.63. However, it was not the only signal:

- I/O PSI full avg10 medians were 7.04%, 10.115%, and 10.01%; maxima reached 10.45%, 11.64%, and 11.11%. The first preflight snapshot in each case was below 4.46%, but the trace's per-sample PSI then rose markedly. In the third trace the sampled minimum was 4.36%, near the boundary; the other traces also spent much of the capture above it.
- Swap-in was 255, 924, and 2,655 pages over 60 seconds; these are distinct from the later live attribution captures.
- The full replay finds additional changes in disk counters, memory PSI, `SwapFree`, and (in two traces) `MemAvailable`.

Thus the terse `load1_max` string is incomplete, but these are not “load-only, tiny noise” rejects. The baseline gate classified the condition as stability failure because it did not reapply absolute resource limits during the capture. The PSI trace would be an excursion above the project envelope if that limit were made continuous; the current contract only checks it at preflight/arm-start/recovery snapshots.

## Analyzer and sampler findings

- Baseline rows are buffered in RAM and flushed to CSV only after the 60-second observation. There is no per-row file flush in `capture_host_baseline.py`.
- The arm-time external sampler reads `/proc/<pid>/smaps_rollup`, `/proc/<pid>/io`, PSI, meminfo/vmstat, and diskstats on each polling iteration. Its current source keeps PSS/runtime rows in memory, records a monotonic start/end bracket per iteration, and writes both CSVs only after the benchmark child exits. A regression smoke confirms neither output file opens while the child is live. This removes sampler output writeback from the measured interval; the repeated `/proc` reads still consume CPU and their observer effect was not quantified.
- `analyze_host_pressure.py` joins using the benchmark's `whole_start_monotonic_ns` and cycle event offsets; pre/during/post buckets are half-open and clipped at adjacent cycle boundaries. A current-source two-cycle synthetic join passed with exact 2/10/3 sample counts, no overlap, and zero post-sample lag at event boundaries. It preserves host PSI totals/deltas. Each sampler row brackets the sequential metric reads, but individual metrics do not have separate timestamps; the measured bracket duration bounds, rather than eliminates, that skew.
- Both the baseline sampler and runner preflight omit command-line arguments from persisted top-process summaries. A one-off post-audit artifact generated before the sampler source change was redacted in-place; its receipt records old/new hashes.
- Baseline capture omits CPU PSI and runnable-task count. Linux load average is not CPU utilization and can include uninterruptible I/O wait. This matters because the current host-only capture later showed low sampled CPU utilization and only two runnable tasks while the 1-minute load average remained elevated; load alone is a poor discriminator for CPU interference.

## Diagnostic changes made (no threshold change)

`run_host_carryover_aa.py` now separates `SAFETY_GATE_FAIL`, `STABILITY_GATE_FAIL`, and `MEASUREMENT_INVALID` in emitted outcomes. The safety label still means a failed existing snapshot gate; it does not assert a continuous policy. Baseline telemetry is checked for required numeric fields, valid diskstats JSON, strictly increasing monotonic timestamps, gaps ≥1.5× the nominal period, a full 60-second capture within two sample periods, six adequately covered 10-second windows, and a valid sample start/end bracket. Invalid capture data cannot produce an A/A launch. The 1.5× gap and coverage checks are explicit measurement-integrity criteria, not changes to either resource or stability thresholds.

The first failing feature/reason remains unchanged for historical decision parity; the runner now includes *all* failing feature/window/range details. `run_sync_reduction_pair.py` labels a failed existing preflight as `SAFETY_GATE_FAIL`. Synthetic tests and full raw replay are recorded in the adjacent `stability-gate-offline-replay.json` and `diagnostic-fixes-and-tests.md`.

## Gate result

The strict gate is doing what its code says for the three raw traces. The host was not stable by multiple independent host features, so there is no objective ground truth that these are false rejections. The design is sensitive to small changes when the first-half range is narrow and can be permissive when an early outlier widens it; its false-rejection/false-acceptance rates are unknown. Retain all approved thresholds and the strict envelope pending explicit owner approval of any alternate stability contract.

## Post-audit attempt (one gated baseline; no arms)

After the three raw baselines, source audit, synthetic replays, and host attribution were complete, one authorized post-audit attempt used the same binary. Both the standalone pre-launch receipt and the runner's own baseline preflight passed all four unchanged thresholds. The 60.019-second capture itself was valid (1,200 rows, 50-ms cadence, six full windows); the unchanged envelope rejected it for `load1_max` drift down. It is recorded separately from the three preregistered raw baselines in `stability-gate-offline-replay.json` as `post_audit_attempts`.

This new trace provides an important distinction: every load sample and I/O-PSI sample remained within their absolute snapshot ceilings, but **64/1,200 `MemAvailable` samples (5.33%) fell below the existing 5,477,762-KiB snapshot threshold**, reaching 5,304,712 KiB (shortfall 173,050 KiB). Those samples occur in index ranges 599–615 and 618–664. The runner only checks the absolute value at the preflight/arm-start points, so it did not abort the baseline on this interval; the independent all-feature stability rule rejected the capture and no arm launched. This is direct evidence that snapshot safety and whole-window stability are separate checks. It supports considering a continuous memory guard, but does not authorize changing policy.

The captured process summaries for this new attempt were redacted in-place after capture to remove command-line arguments; PID, comm, CPU and RSS summary fields were retained. Redaction receipt: `host-carryover-aa-pair-20261019T102259Z/process-argv-redaction.json`. Future baseline and runner source now omit argument columns at capture time.

Raw sources: the three pre-existing `host-carryover-aa-pair-*` directories and `aa-resource-gate-wait-20261019/README.md`. The fourth post-audit attempt is in `host-carryover-aa-pair-20261019T102259Z/`. Replay, checksums, host attribution, policy options, diagnostics, and A/A readiness are referenced in this investigation's sibling deliverables.
>>> END_OF_DELIVERABLE <<<
