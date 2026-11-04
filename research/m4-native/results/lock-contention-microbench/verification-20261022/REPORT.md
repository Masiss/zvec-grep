# Suspend-aware watchdog follow-up — verification

## Contained rehearsal

User authorized one isolated `synthetic-containment-rehearsal`; it used only the runner's fixed 32 KiB/1 s Python workload, with 64 MiB memory, zero swap, 0.5 CPU, 128 tasks, 60 s maximum runtime, and the existing 2 s monitor-gap limit. It completed `PASS`: workload return code 0, 11 watchdog samples, no watchdog abort, hard safety telemetry complete, and owned-unit cleanup verified. Maximum inter-sample gaps were 0.111149375 s (`CLOCK_MONOTONIC`) and 0.111148095 s (`CLOCK_BOOTTIME`). The earlier 15.323005 s gap did **not** recur; this short run does not identify whether the old event was suspend or a realtime clock step, and is not native M4 performance evidence. Workspace accounting remains sampled, not proof; cgroup I/O accounting was unavailable and was not required for this synthetic rehearsal.

Raw operation plan, receipts, runtime samples, controller log, post-run wrapper note, and checksums: [`m4-contention-rehearsal-20261022T113503Z-clock-gap-probe-01`](../../containment-probes/20261022/m4-contention-rehearsal-20261022T113503Z-clock-gap-probe-01/). The generic outer controller printed the successful result and then exited nonzero on an accidental trailing `PY` marker. The contained operation itself passed; the error is preserved in that operation's log/note. The temporary controller was corrected and `py_compile` passed. The contained operation was not rerun.

## Regression verification

- Fresh RelWithDebInfo configure and full native build completed in `research/m4-native/.work/build/m4-clockgap-regression-20261022`.
- Native CTest: **8/8 passed** (16.06 s). This includes `m4_correctness` and the new concurrent-compaction timing assertion. Benchmark executables were built but not run.
- ThreadSanitizer build: **2/2 passed** (`m4_writer_preferred_mutex_test`, `m4_correctness`; 17.87 s).
- Python policy suites: **48/48** contention, **11/11** benchmark policy, **4/4** harness scenarios passed.
- `python3 -m py_compile` passed for the temporary controller. `git diff --check` passed for the changed source/test files.
- LSP sweep was inconclusive for two files and emitted existing broad AST-grep numeric-parse warnings in the runner; compiler, CTest, TSan, and Python tests passed. No new LSP clean claim is made.

Full command output is retained in `full-native-regression.log`, `snapshot-tsan-regression.log`, and `python-policy-regression.log`. This verifies source/test behavior and containment integration only; it does not qualify performance, reconstruct the prior clock anomaly, or authorize another native measurement. The 2 s policy threshold was not changed or bypassed.
