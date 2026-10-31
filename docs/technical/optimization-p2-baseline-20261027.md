# P2 baseline disposition — 2026-10-27

**Status: PASS as a baseline-retained decision; no performance candidate is qualified.** This closes Phase 2 under the documented fallback. It is not native performance, statistical, or production qualification.

## Correctness gate

P2-A/B focused correctness coverage passed 17 cases across separate runs. It covers maintenance debt/reopen/retry, watcher coalescing semantics, PK-delete idempotency and durability, staged replacement, and partial-delete error propagation. The last partial-mutation regression exercised the production Rust→zvec C-ABI path with a test-only Linux interposer; the backend itself was not shown to naturally emit `InternalError`. Details and individual evidence paths are recorded in [the upgrade plan](optimization-upgrade-plan-20261027.md).

## Bounded P2-B scheduler diagnostic

One paired diagnostic ran under operation ID `p2-watch-coalescing-20261027T131148Z-cf2fa335`, using the frozen [protocol](../../benchmarks/native-lifecycle/p2-watch-coalescing-protocol.json) (SHA-256 `6e5a602eba70762d96b439f97d6f0b29333269bf54202920f2ffa2a5172be222`). The fixed scenario submitted 41 Watch changes to a single-concurrency scheduler with 25 ms inter-event spacing and an 80 ms *mock async executor*; control ran before coalesced mode in the same process.

| Arm | Writer/executor calls | Changes observed | Final-event-to-last-call-start lag |
| --- | ---: | ---: | ---: |
| Coalescing off | 14 | 41 | 32 ms |
| Coalescing on | 2 | 41 | 750 ms |

The coalesced arm had 85.7% fewer executor calls and retained every change. Its 750 ms value is a scheduler start-lag diagnostic, **not** event-to-visible freshness. The mock executor does not measure native indexing CPU, actual optimize cost, lifecycle wall time, or query latency. The printed `optimize_calls` field is an alias for these mock executor calls, not native optimize invocations. Fixed arm order, one pair, and concurrent host activity make this exploratory only. It is useful evidence that coalescing reduces submitted writer work in this synthetic burst, but it is not enough to claim the plan's proposed ≥20% native CPU/lifecycle improvement.

The run used HEAD `d3b2a95bb238bce47e2dcb87034e5600d9de56e0`, tracked-diff SHA-256 `6a122cafb3215314d3ac0ffb322f49964b102c99823a3eb9205ebe62ffa95239`, and scheduler source SHA-256 `b2a7e4e9534f4396ad36eb59689ea40a176efa2985deba56aaf4b5b1d5dc3f03`. `cargo test --locked -p zg-daemon --lib watch_burst_benchmark -- --ignored --nocapture` passed (1/1; test body 3.01 s; build 3m23s). The transient systemd scope was configured for 200% CPU, 1 GiB memory, no swap, 128 tasks, and a 900 s timeout; `systemd-run` returned 0 and scope result was `success`. The scope disappeared before cgroup peak/CPU accounting could be read, so those per-process/peak measurements are unavailable and no stronger resource-accounting claim is made.

Host snapshots are in `.tmp/p2-watch-coalescing/p2-watch-coalescing-20261027T131148Z-cf2fa335/`. Before: `MemAvailable` 8.54 GiB and `.tmp` filesystem free 28.15 GiB; after: 3.62 GiB and 28.09 GiB, respectively. Both remained above the 3 GiB/17 GiB launch floors. I/O PSI was already elevated before the run (some/full avg10 6.16%/5.92%); the post-run process snapshot showed a concurrent `qmd embed` process. No during-run host sampler was retained, so attribute no pressure change to this benchmark.

## Decision

At Phase 2 gate closure, the earlier P1 v7 native lifecycle result remained separate: exploratory query p95 181.592 ms and event-to-visible max 385.027 ms met the provisional absolute SLOs for that measured WIP revision, but did not attribute a bottleneck on the post-P2 tree. That historical disposition is followed by current-tree substage profiling and one exploratory 2-vs-4 optimizer-thread pair, documented in [the native profile report](optimization-native-profile-20261027.md). Finalization is the dominant stage; the thread pair showed only a 1.13% finalize-time improvement with 4 threads, while candidate query p95 failed the provisional 300 ms SLO. No performance candidate is qualified. Retain the 2-thread baseline; do not tune defaults, claim a performance win, deploy, or promote the result.

No defaults, production runtime, deployment, schema, or public contract changed in this Phase 2 work. Nothing was staged or committed.

## Gap audit — 2026-10-30

Re-read the P2 evidence after the Phase 3 lock-split work. No mandatory P2 gate was skipped; these are the deliberate limits of the baseline-retained disposition:

- **P2-C (model memory) and P2-D (build)** were not selected; they are not hidden pending tasks.
- The **Watch-coalescing** result is a mock-executor diagnostic (14→2 calls, 41/41 changes), not native CPU/lifecycle evidence.
- The **optimizer-thread** result is one exploratory pair; 4 threads was rejected because query p95 crossed the provisional 300 ms SLO, not because of a statistical proof.
- The watch-coalescing scope disappeared before cgroup peak/CPU accounting could be read; that resource-accounting limit is recorded.
- The **partial-mutation** regression used a test-only Linux interposer; the backend was not shown to naturally emit `InternalError`.
- P1 v7 remains a single exploratory run, not statistical/production qualification.

Reopening P2 requires new evidence, a new protocol, and separate authorization. The Phase 3 lock-split hardening is tracked separately and does not reopen P2.

## Follow-up: current-tree native profile

The first current-tree profile ran as `p2-native-profile-20261027t134637z-d0a1aa13`. Eight writer jobs accumulated 8.683 s of job wall time; `index_optimize` (the full `finalize_writes()` wrapper) accounted for 5.715 s / 65.8%, and for 89.4% of the four mutation jobs. Freshness max was 444.914 ms (PASS for this run); query p95 was 260.040 ms but validity was **INCONCLUSIVE** (one missed slot, max schedule lag 274.846 ms). The profile is exploratory and not an A/B comparison.

The first follow-up substage attempt (`p2-native-substage-profile-20261027t142307z-f5acfee1`) passed its lifecycle test but emitted zero finalization events because `cfg(test)` is compiled out in a dependency crate. A corrected single run (`p2-native-substage-v2-20261027t145503z-2a65b72b`) emitted eight events. Across four watcher mutation finalizations (3.734 s total), fragments optimize accounted for 2.180 s (58.4%), files 0.882 s (23.6%), entities 0.393 s (10.5%), checkpoint 0.274 s (7.3%). Freshness max was 340.236 ms (PASS for this run); query p95 was 151.674 ms but **INCONCLUSIVE** (one missed slot, max lag 318.036 ms). Incomplete inserts and fragment deletes require current optimization behavior for visibility; no threshold/defer candidate was selected. The v1 optimizer-thread pair stopped after its 2-thread control because its query schedule was invalid; its authorization was consumed without a candidate run. The owner then authorized a fresh v2 pair; the first preflight was blocked by memory, but after the owner re-requested launch the preflight passed and both arms completed. Each test passed 1/1 with eight finalization events; both query schedules were valid. Control (2 threads): mutation finalization 4.568694 s, query p95 263.321 ms (PASS), freshness max 629.776 ms (PASS). Candidate (4 threads): mutation finalization 4.516861 s (-1.13%), query p95 320.494 ms (FAIL vs 300 ms), freshness max 444.235 ms (PASS). Fragment optimize was 6.74% slower with 4 threads. This is one exploratory pair, not statistical qualification; reject 4-thread promotion and retain the 2-thread baseline. The v2 pair authorization is consumed; no default, maintenance-policy, or production change. See [the detailed report](optimization-native-profile-20261027.md), v1 control-only artifacts under `.tmp/native-lifecycle-profile/p2-native-optthreads-ab-20261027t151858z-d36a1394-`, and completed v2 pair/control/candidate artifacts under `.tmp/native-lifecycle-profile/p2-native-optthreads-ab2-20261027t153804z-f479eee4-`.

## Evidence artifacts

- Frozen protocol: `benchmarks/native-lifecycle/p2-watch-coalescing-protocol.json`
- Run log: `.tmp/p2-watch-coalescing/p2-watch-coalescing-20261027T131148Z-cf2fa335/runner.log` (SHA-256 `91c5db50562e184b3b355911449bc532a7342355316b579171afcd8eb646618e`)
- Input and artifact hashes: `.tmp/p2-watch-coalescing/p2-watch-coalescing-20261027T131148Z-cf2fa335/input-sha256.txt`, `evidence-sha256.txt`
- Host snapshots and cgroup-after status: `.tmp/p2-watch-coalescing/p2-watch-coalescing-20261027T131148Z-cf2fa335/`
- P1 report: [P1 baseline and acceptance](optimization-p1-baseline-20261027.md)
