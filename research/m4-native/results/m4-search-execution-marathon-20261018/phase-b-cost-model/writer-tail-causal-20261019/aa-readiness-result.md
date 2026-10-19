# Same-binary A/A readiness result — 2026-10-19

## Outcome

**No A/A arm launched.** The unchanged runner passed its independent four-resource snapshot and completed one permitted 60-second buffered baseline, then correctly stopped on `STABILITY_GATE_FAIL`. The reason remained `baseline drift: last half outside first-half envelope for load1_max`. There is no A/A result, writer-tail attribution, candidate comparison, or basis to infer an arm-order effect from this attempt.

## Exact gate receipts

Two sanitized, read-only snapshots passed before the baseline:

| Receipt | Local time (+07) | MemAvailable KiB (minimum 5,477,762) | load-1 (maximum 3.14) | I/O PSI full avg10 (maximum 4.46%) | `/var/tmp` available B (minimum 18,219,000,000) |
|---|---:|---:|---:|---:|---:|
| `aa-resource-gate-readiness-20261019Tcurrent-after-redaction.json` | 17:22:37 | 5,585,468 | 2.18 | 1.23% | 19,211,120,640 |
| Runner `baseline/host-before.json` | 17:22:59 | 6,020,276 | 2.17 | 0.51% | 19,211,120,640 |

Both passed all four gates; there were no package-manager processes in the runner receipt. Binary SHA-256 was `70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`, matching all three pre-existing pair artifacts.

Pair artifacts: `host-carryover-aa-pair-20261019T102259Z/`. `aa-readiness-run-20261019.log` contains `AA_BASELINE_START`, then `AA_BASELINE_REJECTED status=STABILITY_GATE_FAIL`, followed by `NO_LAUNCH`. There is no `AA_ARM_START`, `aa-first`, `aa-second`, or arm output. The runner did not execute or build the benchmark binary.

## New baseline observations

The buffered baseline contains 1,200 samples at a 50-ms nominal cadence over 60.019 seconds. Six 10-second windows each contain 200 samples and ~9.95 seconds of coverage; monotonic timestamps are strictly increasing with no ≥1.5-period gaps. The quality validator reports `VALID`; the gate failure is not a malformed/short capture.

The current first-three vs last-three replay reports **23 feature/window envelope misses**. `load1_max` declined from the first-half envelope 2.03–2.31 to 1.87, 1.73 and 1.47. Other features also moved: minimum `MemAvailable` in the last three windows fell as low as 5,304,712 KiB (first-three lower bound 5,472,460), and disk/PSI/swap features added to the miss list. Per-window data and exact failure values are in `stability-gate-offline-replay.json` under `post_audit_attempts`.

This capture had no sample with load1 >3.14 and no sample with I/O PSI full avg10 >4.46%. However, 64/1,200 `MemAvailable` samples were below the existing 5,477,762-KiB snapshot limit: sample indexes 599–615 and 618–664, minimum 5,304,712 KiB (shortfall 173,050 KiB). The preflight snapshot had 6,020,276 KiB. This is a retrospective diagnostic; current code checks that absolute resource limit at preflight/arm start, **not continuously during the baseline**. Since the strict baseline failed, no arm ran while this condition was present.

## Decision

Keep the existing four preflight limits, six-window timing, first-three/last-three envelope, and three-window inter-arm recovery unchanged. Do not try another capture immediately, loosen the envelope, or launch arms from this rejected baseline. The resource snapshot passed, but the baseline did not satisfy the stability gate and its time series includes a below-limit memory interval. A continuous-absolute-resource policy would be a behavioral change and remains for owner decision; evidence and precise scope are in `stability-policy-assessment.md`.

A sanitized read-only post-baseline snapshot at 17:32:29 +07 is in `aa-post-baseline-host-20261019.json`. MemAvailable was 5,472,864 KiB, **4,898 KiB below** the 5,477,762-KiB threshold; load1 0.78, I/O PSI full 0.08%, and `/var/tmp` 19,204,743,168 B passed. Do not use that post-run snapshot as a preflight receipt; it records the host after the observation.

This completes the single authorized post-audit baseline attempt. No retry or arm was launched. If an owner later authorizes/requests another try, first require all four existing preflight limits to pass, retain the new telemetry-integrity checks, and allow the current unchanged all-feature baseline envelope to decide whether two arms can run. The final no-launch raw sample is not evidence for or against the sync-elision treatment.
>>> END_OF_DELIVERABLE <<<
