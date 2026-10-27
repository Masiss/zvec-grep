# M4 Recovery — Gate 3 diagnostic attempt (2026-10-27)

## Status

**Gate 3 did not complete.** One newly authorized 64-file diagnostic pair (`m4-rust-lifecycle-pair-20261027-dc8e1120`) launched native first. The native arm stopped making visible indexing progress at 50/64; its test threads were waiting on futexes for more than three minutes. The watchdog was still active. I stopped only its owned transient systemd unit to avoid spending the remaining runtime allowance on a stalled arm. The M4 arm never launched. There is **no Native–M4 comparison**, query-latency result, or qualification result. This authorization is consumed; no retry is authorized.

## Preflight and scope

- Explicit owner authorization: one fresh 64-file diagnostic pair only; no full-corpus qualification and no retries.
- Corpus: first 64 lexicographic Rust engine `.rs` files, **694,421 bytes**.
- Fresh no-launch preflight: `benchmark_started=false`; randomized order was `native`, then `m4`.
- Root filesystem: **20,411,490,304 bytes free** against **18,286,108,864 bytes required** (2,125,381,440 bytes margin).
- Attested Btrfs qgroup `0/259`: **117,755,904 bytes available**, **16,461,824 referenced**, 128 MiB maximum; required reserve **67,108,864 bytes**. These values matched the registered qgroup and the filesystem UUID/subvolume identity.
- Cgroup policy remained unchanged: 512 MiB memory, zero swap, 2 CPUs, 128 tasks. No guard was relaxed.

## Observations and integrity limits

The native arm ran for about **5m07s**. The last visible indexing progress was 50/64. The test process accumulated only about eight CPU seconds over roughly four minutes and its threads were in `futex_do_wait`; the bounded query worker/scheduler were active in the same test. This is evidence of a liveness/lock-wait problem under the offered-load diagnostic, but it does not identify the exact lock or prove a selective-fence defect. No query-completion report was written, so latency/fence-wait distributions cannot be recovered.

The independent watchdog recorded **297/297 successful qgroup samples**, all with inventory status `NOT_SAMPLED_KERNEL_QUOTA_BOUND`. Minimum observed qgroup headroom was **94,289,920 bytes**. Cgroup memory peak was **364,802,048 bytes** (about 348 MiB); `memory.events.max=0`, OOM counters=0. Thus neither the memory cap nor the qgroup reserve triggered before I stopped the arm.

Observed sample-start spacing was median **1,013 ms**, p95 **1,269 ms**, maximum **1,573 ms**, against a 500 ms configured interval and 2 s maximum gap. The maximum stayed below the configured 2 s limit, but the cadence drift is material and must be explained before query timing is treated as qualified. Recorded sample-read duration was mean 8.62 ms, p95 13.44 ms, maximum 18.41 ms. A live `ps` snapshot showed the watchdog process at about 50% CPU over its lifetime; that is an observational snapshot, not a per-arm CPU attribution. Instrument observer CPU and full-loop time separately before another operation.

The operator stop removed the transient unit before the watchdog captured a workload exit status. The arm result is therefore `WATCHDOG_ABORT / CONTAINMENT_UNVERIFIED / INCOMPLETE` (`contained_workload_exit_status_missing`; watchdog reason `cgroup_telemetry_disappeared`). The separate unit cleanup receipt says `unit_collected=true`, `verified=true`; this does not repair the missing cgroup exit telemetry, so preserve the conservative containment classification. No benchmark-related process remains. The partial workspace and receipts are intentionally retained; do not delete them during this checkpoint.

## Evidence

- Pair result: `../.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261027-dc8e1120/pair-result.json`
- Raw watchdog samples: `../.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261027-dc8e1120/pair-first/containment/runtime.jsonl`
- Watchdog abort and cleanup: same `pair-first/containment/watchdog-abort.json` and `unit-cleanup.json`
- Preflight and host snapshot: `M4-GATE-B-DIAGNOSTIC-20261027-dc8e1120/preflight.json`, `host-before.txt`
- SHA-256: pair result `fb6b77ed48afb362b4a03844499e44653397fec67ba6a656c34cf55bfc5b8312`; runtime `f4d9e39ed51adb19ce494d76c57f1ac5223d2171b3253b2d2561a30236398971`; abort `6087d4b6d18224ba0ae87e37a93bc4860ea67b7bc70f37854a4d9fcd35cb6d6d`; cleanup `809fc41c216915f0aa4e30fa7eb2faa9293e3439dfe519ef8c827c3695bd8c09`.

## Independent work and next decision

The current source and feature-enabled Rust suite were rechecked: **9 passed, 0 failed, 1 intentionally ignored**; Python containment tests **24 passed**; benchmark-policy JSON parses. The reported line/field diagnostics were stale against the checked source and are not current blockers.

Continue without a new operation by tracing native `IndexStore` read/write lock behavior and the lifecycle query worker, then add a bounded deterministic regression for the observed no-progress/concurrent-query case if source evidence supports it. Separately instrument watchdog CPU and complete-loop cadence; the 500 ms value currently describes a sleep interval, not a guaranteed sample-start period. Improve operator cancellation so a requested stop is recorded by the watchdog before the unit disappears. These are Gate 1/3 measurement and liveness defects, not permission to alter the workload or hard limits.

### Follow-up after the aborted arm

Source tracing confirms a plausible, but unproven, contention path: `IndexStore::read` and `IndexStore::write` both acquire the same `StoreState` mutex (`rust/crates/zg-engine/src/storage/store/mod.rs:464-479,2051-2055`). FTS/vector search and hit hydration execute under `read`; per-file index mutations and finalization use `write`. The lifecycle test's one query worker can offer at 40 requests/second while indexing. Thus slow native search or mutex contention/starvation can explain no indexing progress, but the source trace does not establish which occurred or show a lock cycle. The native arm is not waiting on the M4 selective fence; its query service time would include storage lock acquisition and native search work. Gate 3 provides no completed query trace to distinguish those components.

The external watchdog now records sample-start gaps, per-sample process CPU time and loop work, and a telemetry-completeness flag in `watchdog-summary.json`. This distinguishes cadence drift from observer CPU cost without changing workload, hard limits, or abort decisions. Tests cover the calculations and a contained normal-exit run's persisted fields. Verification: containment suite **25 passed**; Python syntax and `git diff --check` passed. This implementation has not been used in another pair.

Still unresolved: (1) the stalled query's native search-vs-mutex cause; (2) persisting offered/started/completed query telemetry before the test exits; (3) operator cancellation that the watchdog records before the unit disappears. The original stop remains `CONTAINMENT_UNVERIFIED` despite separate unit collection evidence. Continue with non-benchmark source/test work on these items; do not change production runtime or the offered workload. A fresh owner authorization and no-launch preflight are required before any further arm/pair. Numeric performance/resource qualification thresholds remain unapproved; full-corpus Gate B remains open. Production integration remains out of scope.

**Disposition:** `PARTIAL / OPERATION_BLOCKED`; preserve Gate 2's bounded research correctness evidence, but keep the overall architecture decision **REWORK**. No GO or performance winner is supported.

---

Status recorded: 2026-10-27. This is an aborted diagnostic report, not a comparison.