# M4 benchmark containment and safety calibration — 2026-10-19

## Goal, scope and outcome

Implement and validate a research-only per-arm containment runner before any further 500×20 diagnostic. No production Rust/Zvec, daemon, ABI, schema, or index code changed. No privileged command, `sudo`, system configuration change, application stop, or host-pressure/OOM/disk-full test was used. No 500×20 workload or A/A arm ran in this slice.

Implementation: `research/m4-native/tools/m4_containment_runner.py`; executable tests: `research/m4-native/tools/test_m4_containment_runner.py`. Successful raw test receipts and checksums are in `containment-tests-20261019T131427Z/`.

## Phase A — observed cgroup/systemd capability

Read-only probe (`m4_containment_runner.py probe`, systemd 262, cgroup v2):

- Root controllers include `cpu`, `memory`, `io`, and `pids`. The user service has `cpu`, `memory`, and `pids` in `cgroup.controllers` and `cgroup.subtree_control`; **I/O is not delegated**. The current shell scope is `Delegate=no`, `KillMode=process`, `IOAccounting=no`, and is not treated as containment.
- A short-lived user service with `Delegate=yes` was proven able to create the per-arm cgroup. Per-arm samples exposed CPU/memory/pids controllers; memory/swap accounting, PSI files, and `cgroup.kill` were available. The benchmark process and its descendant were verified in the exact generated cgroup before interpreting completion.
- `io.pressure`, `/proc/diskstats`, and `/proc/vmstat` are observable. `io.stat` and `io.max` are absent in the delegated leaf. Therefore there is no enforceable cgroup I/O ceiling or per-arm device-byte counter here. Process `/proc/<pid>/io` read/write byte counters are logical process accounting, **not physical device writes**. Host diskstats remain host-wide and cannot attribute device traffic to an arm.
- `/var/tmp` is on Btrfs (`/dev/mapper/cryptroot`). Workspace `st_blocks*512` is sampled and hardlink-deduplicated but can count shared/reflink extents more than once. `filefrag -v` FIEMAP shared flags inspect only the final workspace tree; this is neither historical peak attribution nor a shared-byte de-duplication measurement.
- The runner creates a transient user service (`Delegate=yes`, `ProtectControlGroups=yes`, `KillMode=control-group`) and sets `cgroup.max.depth=0` before releasing a bootstrap gate to spawn the workload. The delegated subtree cannot grow. It verifies both bootstrap and benchmark child membership. Watchdog/controller liveness uses a pidfd; per-PID termination opens a pidfd and rechecks `/proc/<pid>/cgroup` before signaling, avoiding PID-reuse redirection. No process-group-only assumption is used.
- An independent watchdog runs outside the arm cgroup. It records runtime samples and writes the summary/abort receipt before `cgroup.kill`; if the controller disappears, it also removes its abandoned private socket directory. The transient service has a bounded systemd runtime fallback. Normal completion is finalized only after watchdog telemetry is durable. OOM events, missing required telemetry, absolute host-guard violations, the supplied memory/swap baseline, runtime timeout, and approved sampled-workspace ceilings cannot yield PASS.

## Executable validation

Command:

```sh
python3 -m py_compile research/m4-native/tools/m4_containment_runner.py \
  research/m4-native/tools/test_m4_containment_runner.py
python3 research/m4-native/tools/test_m4_containment_runner.py \
  --output-dir research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/containment-tests-20261019T131427Z
```

Result: **8 tests, 0 failures/errors**. Cases cover: hard-guard refusal without launching the command; synthetic memory/swap baseline enforcement; missing cgroup telemetry rejection; normal completion with a descendant observed in the owned cgroup; nonzero exit; SIGTERM and SIGKILL (neither can be PASS); runtime watchdog abort with descendant termination and exact reason; and controller SIGKILL with independent `supervisor_lost` receipt, `cgroup.kill`, socket cleanup, stopped/removed owned cgroup, and a still-live unrelated test process. The tests use only tiny synthetic processes; host-gate bypass is explicit in fixtures and does not authorize/represent a benchmark.

The first crash-test attempt exposed a `NameError` in the watchdog's supervisor-loss branch. The exact synthetic transient unit was stopped and its tree verified gone; the branch was fixed and crash recovery then passed repeatedly. A subsequent test assertion was corrected to accept systemd's expected `failed` state label after the controller itself is SIGKILLed, while still requiring empty `ControlGroup`, absent cgroup path, and no remaining owned processes. These are retained as failed development receipts in the earlier timestamped containment test directories; final receipts are the later passing run above.

Final fixture cgroup peaks were about 17–23 MB, with zero swap; these are **runner/tiny-fixture overhead only**, not M4 workload limits. `io.stat` remained unavailable, as expected. Final receipts distinguish `safety_outcome` from `measurement_quality`: e.g. a watchdog-aborted run can be `ABORTED_CONTAINED` while its resource telemetry is `COMPLETE`; nonzero/signalled workloads are not PASS even when measurement is complete.

## Resource evidence and limits of calibration

Historical successful 500×20 process receipts (one paired cohort; not cgroup-accounted): baseline/candidate sampled peak PSS 304,227/286,915 KiB; sampled peak RSS 308,788/294,412 KiB; sampled process swap 0/0 KiB. The benchmark-only `/proc/self/io` `write_bytes` counters were 247,373,824/251,011,072 B; including close/reopen recovery the external process sampler recorded 248,094,720/251,863,040 B. These are **process write-byte counters, not physical device writes or allocated-space peaks**. Retained root logical bytes were 7,177,536 B in both arms. The old run had no cgroup `memory.peak`, no cgroup swap peak, and no peak active workspace allocation/staging sample. It cannot select a safe per-arm memory cap or workspace quota.

No numeric cgroup memory/swap cap or workspace limit is proposed here. The observed process PSS is only a lower bound on the memory a cgroup limit must accommodate; cgroup page-cache and other charged memory were not measured for the actual workload. Selecting a cap from the tiny-fixture peaks or from process `write_bytes` would be unsupported. The current runner accepts `memory_max_bytes`, `memory_swap_max_bytes`, `cpu_max`, and sampled workspace limits only from a JSON budget explicitly marked `approval_status: approved` with a nonempty `approval_reference`. It rejects `io_max` because that controller is unavailable. Workspace thresholds are sampled aborts, not a kernel/filesystem quota; short-lived peaks can be missed.

### Proposed safety contract (no guard changes)

1. Keep the established absolute host limits hard, before and throughout each arm: MemAvailable ≥5,477,762 KiB; `/var/tmp` free ≥18,219,000,000 B; I/O PSI full avg10 ≤4.46%; load-1 ≤3.14. Keep the accepted A/A orchestrator's separate telemetry-integrity, 60-second baseline, diagnostic/qualification distinction, and three-window inter-arm recovery rules unchanged.
2. Pass a validated 60-second baseline-derived JSON with `swapfree_min_kib`, `memory_some_avg10_max`, and `memory_full_avg10_max` to every host-safe runner arm. The independent watchdog checks those same existing memory/swap safety bounds samplewise during the arm; it does not infer new numeric thresholds. `--pressure-limits-json` is required by the CLI. Diagnostic mode may bypass only an existing measurement-quality stability failure, never these safety checks.
3. Keep runtime timeout as an explicit per-command limit. Abort on cgroup OOM event, telemetry integrity failure, any existing hard host guard, supplied baseline memory/swap pressure limit, or an explicitly owner-approved sampled workspace limit. Persist an exact reason and timestamp before final cgroup kill.
4. Do not run OOM/disk-full tests on the host. If an owner-approved memory/swap/workspace budget is later supplied, enforce it before releasing the bootstrap gate. A real filesystem quota remains unproved/unavailable in this delegation.
5. A diagnostic A/A requires proven containment, a valid current 60-second baseline and recovery protocol, all existing hard limits passing at launch and samplewise, enough disk reserve for both fresh roots, and a complete measurement-quality receipt. Qualification A/B additionally requires the unchanged stability envelope. Any `WATCHDOG_ABORT`, signal, OOM, missing telemetry, or hard-guard refusal is not a PASS and not latency evidence.

## Phase G — current gate; no A/A launched

Final post-test read-only snapshot (`2026-10-19T13:14:49.926954Z`): MemAvailable 5,771,328 KiB (passes); SwapFree 13,282,320 KiB; `/var/tmp` free 15,469,010,944 B (fails by **2,749,989,056 B**, about 2.56 GiB); I/O PSI full avg10 4.32% (passes, 0.14 percentage points below the limit); load-1 1.62 (passes); memory PSI some/full avg10 0.04%/0.04%. The only failed current absolute guard is disk reserve. No benchmark/A/A command was launched. Do not reclaim space by deleting unreviewed data and do not weaken this gate. The executable next action is to wait for an owner-approved cleanup or resource recovery until `/var/tmp` is at least 18,219,000,000 B free, then take a fresh baseline/gate run. If eligible, first wire the existing A/A orchestrator to invoke this runner separately for each arm with the accepted pressure-limits JSON and identical fresh-root settings; then run the authorized pair. This is not an unconditional A/A retry.

Other deliverables: `m4_containment_runner.py probe` output was captured at `/tmp/m4-containment-capability-probe.json`; current gates are rechecked above. Final test source and receipts are checksummed in `containment-tests-20261019T131427Z/SHA256SUMS.txt`.

## Source of truth and caveats

This is a research-only containment/control path, not production integration. The new runner has not yet measured an actual 500×20 workload, and the existing same-binary A/A orchestrator has not yet been wired to invoke it per arm; do not launch that old orchestration as if it were contained. The ongoing B3.1 empirical resource envelope remains historical rather than a universal host requirement. No memory/swap cap, workspace quota, memory PSI threshold beyond the accepted baseline-relative rule, or I/O attribution claim is newly established by these tiny fixtures. Canonical handoff: `rust/docs/m4-incremental-native-index.md` §39.

# Tags: M4, research-only, cgroup-v2, watchdog, safety-gates
# End

---
Artifacts: `containment-tests-20261019T131427Z/`.

---

<!-- calibration append-only --></document>