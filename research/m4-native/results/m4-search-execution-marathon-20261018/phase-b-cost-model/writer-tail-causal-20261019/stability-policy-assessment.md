# Stability policy options — 2026-10-19

## Decision context

Three complete 60-second captures were rejected by the current envelope; all three had a failing first-half/last-half comparison on `load1_max`, and replay shows each also violates the envelope on multiple other features. The current gate is strict and uses the empirical envelope exactly as written. It is not proven to have an unacceptable false-rejection rate: there is no stable-host ground truth for those captures. The current limits and profile rule remain unchanged in code.

This assessment replays the current gate, a diagnostic use of existing absolute thresholds across available samples, robust per-window summaries, and the existing inter-arm recovery feature against the three raw captures and synthetic cases. It does **not** infer benchmark wins, launch arms, or declare any rejected capture a valid baseline. Full evidence and hashes are in `stability-gate-offline-replay.json`.

## Options

### A. Retain strict first-half envelope as-is (recommended for this run)

**Rule:** no changes to threshold, feature set, windowing, or decision. The first three 10-second windows set exact min/max bounds for each feature; all last-three windows must remain inside.

**Replay:** all three historical captures remain `STABILITY_GATE_FAIL`; the reported first failure is still `load1_max`. The complete feature list shows 23 failing feature/window pairs, 21, and 15, respectively. All six windows in every capture have 200 samples and 9.95 seconds of coverage. Each strict failure is reproducible from the raw CSV.

**Synthetic behavior:** stable `PASS`; rising and falling load `FAIL`; an I/O spike `FAIL`; high but constant PSI `PASS`; heavy but constant swap-in `PASS`; a later extreme outlier `FAIL`; a single early outlier widening the first-half bounds `PASS`. The latter two establish asymmetric sensitivity, not a measured error rate.

**Trade-off:** avoids post-hoc threshold tuning and maintains comparability with the established B3.1 protocol. It may reject benign small drift when the first-half range is narrow and may accept stable-but-bad absolute conditions unless the pre-arm safety snapshot also rejects them.

### B. Apply the existing absolute safety limits continuously during baseline/arms (owner approval required before implementation)

**Candidate rule:** apply the existing `MemAvailable ≥ 5,477,762 KiB`, load1 `≤3.14`, and I/O PSI full avg10 `≤4.46%` thresholds to every sample, and stop before any additional arm when violated. The disk-space threshold remains snapshot-only unless a safe, low-overhead periodic `statvfs`/`df` check is separately specified. This is an additional runtime safety gate, not a stability-envelope replacement.

**Offline replay using the available time-series fields:**

| Trace | Samples | MemAvailable violations | load1 violations | I/O PSI full violations | Combined sampled result |
|---|---:|---:|---:|---:|---|
| 16:32:49 capture | 1,200 | 0 | 0 | 1,128 (94.0%) | Fail |
| 16:36:27 capture | 1,200 | 0 | 0 | 1,058 (88.2%) | Fail |
| 16:38:24 capture | 1,200 | 0 | 0 | 1,158 (96.5%) | Fail |

All three preflight disk-space snapshots passed. The CSV does not contain per-sample disk free space, so a full four-limit continuous replay is not possible. Synthetic stable/ramp/I/O-spike traces remain below the sampled limits and pass; the constant 8% PSI synthetic trace fails the 4.46% limit even though the stability envelope alone accepts it; the outlier trace also crosses a limit. This confirms the safety and stability rules answer different questions.

One explicitly separate post-audit 60.019-second baseline was captured after the three-history replay/audit completed. Its own preflight passed (MemAvailable 6,020,276 KiB, load1 2.17, I/O PSI full 0.51%, `/var/tmp` 19,211,120,640 B), but its time series had 64/1,200 MemAvailable samples (5.33%) under the same 5,477,762-KiB threshold, minimum 5,304,712 KiB (173,050-KiB shortfall). The exact samples were indexes 599–615 and 618–664. Load and I/O PSI stayed under their ceilings. The unchanged stability envelope rejected this fourth capture for declining load and other feature drift; no arm ran. This is direct evidence that a one-shot preflight can pass while a later sample crosses an absolute limit. It strengthens the case to discuss option B but still does not retroactively redefine the established snapshot policy.

**Trade-off:** would prevent work while measured I/O pressure remains above the already-established preflight ceiling, closing the current gap between one-shot preflight and a minute-long baseline. However, that ceiling is documented as an empirical successful envelope, not as a continuously enforced health policy. It would change which runs may proceed and require an explicit owner choice, including sampling overhead, hysteresis/debounce, recovery, and whether `avg10` should be sampled at the same 50-ms cadence. Do not implement the candidate from these failure traces alone.

### C. Replace extrema-envelope comparison with robust rolling medians/trend summaries (not ready to adopt)

**Diagnostic replay:** the current six windows were summarized as first-three vs last-three medians, without selecting an acceptance tolerance. `io_full_avg10_max` median deltas were +3.04, +3.49, and −0.35 percentage points. `load1_max` deltas were −0.36, +0.05, and +0.15. Minimum `MemAvailable` deltas were −43,960, −2,948, and −170,440 KiB. Swap-in window-median deltas were −15, −103, and +199 pages. These scores show that the traces are not all moving in the same direction and that load alone does not summarize host drift. None supplies a validated cutoff.

**Synthetic replay:** the same medians/deltas are emitted for stable, ramp, transient, missing/duplicate, and outlier fixtures. No candidate tolerance is declared, so this alternative has no pass/fail decision in the replay. This is intentional: choosing a tolerance from three failures would be post-hoc tuning, not calibration.

**Trade-off:** medians/robust scale can reduce sensitivity to one noisy sample, but a rolling window can hide short pressure spikes, and any tolerance needs a separate calibration set with known stable/unstable periods. Missing/duplicate timestamps still need to produce `MEASUREMENT_INVALID`, regardless of statistic.

### D. Retain the current three-window inter-arm recovery requirement (keep as existing behavior)

**Rule:** after a completed arm, require three consecutive windows to be within the original baseline profile before launching the next arm; failed windows reset the consecutive count. Existing snapshot safety checks remain separate.

**Replay:** no historical arm/recovery time series exists in the rejected captures, so their real inter-arm behavior cannot be evaluated. A labeled proxy that treats each raw capture's last three baseline windows as hypothetical recovery after a profile built from its first three fails recovery on all three; the post-audit capture does too. These are not actual arm/recovery observations. Synthetic replay covers stable, rising/falling load, transient I/O, constant high PSI, swap storm, missing/duplicate samples, and outliers; a dedicated synthetic sequence (one bad window followed by three good windows) resets the count on the bad window and reaches recovery only on the third good sample. The replay exercises profile comparison/consecutive counting only; it does not exercise live resource snapshots or actual arm load.

**Trade-off:** retains a useful hold point without changing acceptance. It cannot repair a baseline that was already invalid, and it is not evidence that a future host will stay safe during an arm.

### E. Run both A/A arms despite baseline drift, then invalidate afterward (reject)

**Replay:** the existing gate does not launch arms after a baseline failure, and the captures contain no arm outcomes to replay. Counterfactual simulation on all three historical and the post-audit baseline shows that their valid measurement data plus passing start-snapshot receipts would allow an arm under this candidate, but the final current strict rule would mark each result invalid. Synthetic rising/falling load, transient I/O and outlier cases similarly waste a launch before invalidation; malformed/missing/short captures remain blocked by integrity checks. The constant high-PSI and synthetic early-outlier cases would still appear valid under the strict stability rule absent their separate continuous limit/integrity checks. This option burns resources and can expose an arm to drifting pressure without yielding a valid comparison; reject it.

### F. Matched host-state A/A comparison (future diagnostic, not an alternative baseline rule)

A paired A/A with identical binaries, mode/query mix, fresh roots and predeclared order can detect carryover/observer effects *after* the baseline passes. No pair exists for these rejected attempts, so there are no real arm results to replay. It cannot convert their baseline into a valid control. Keep the previously authorized pair bounded and run only after current preflight passes and the owner-approved gate remains unchanged.

## Recommendation and decision boundary

For this investigation, retain option A and the existing recovery behavior D. Do not loosen the stability range or choose a new tolerance. Option B is the only narrowly scoped policy extension that directly closes an observed gap, but it requires owner approval because it turns empirical snapshot limits into continuous stop conditions. Option C needs a prospective calibration corpus; options E and F cannot substitute for a valid baseline.

If option B is considered, the exact owner decision is whether to add continuous samplewise checks for the three measurable snapshot limits during the baseline and between arms (including stop/debounce behavior), leaving disk space at its existing snapshot check. Until that decision, run no A/A on a failing baseline; report the reason and wait for all four existing preflight gates to pass.
>>> END_OF_DELIVERABLE <<<
