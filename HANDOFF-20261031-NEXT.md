# Continuation handoff — 2026-10-31 (after M2 diagnostic and rejected M3-B overlap)

Read this first. It supersedes the “Next action” at the bottom of `HANDOFF.md`
for the current worktree. Historical detail remains in `HANDOFF.md`,
`FEATURE-MATRIX.md`, and `BENCHMARK-RESULTS.md`.

## Branch / worktree

- Worktree: `/home/masis/src/zvec-grep-integration`
- Branch: `integration/upstream-rust`
- Exact upstream base: `e1f93d2` (`upstream/main`)
- State: **clean**, **25 commits ahead** of `e1f93d2`
- Old worktree `work/zg-optimization` at `/home/masis/src/zvec-grep` is
  **dirty and untouched**. Do not reset, clean, or rebase it.
- No push, PR, merge, production-default change, resource-limit change, or
  storage-format change was made.

## What is already done

### M2 reuse correctness and telemetry

- Fixed the real M2 bug: the manifest vector fingerprint hashed the original
  `f32`, while native storage is FP16. Reuse therefore never matched. It now
  hashes `half::f16::from_f32(value).to_bits()`.
- Added a regression proving FP16-bucket equivalence and different-bucket
  inequality, plus reuse hit/reuse counters.
- Added bounded telemetry to `IndexResult.timings`, visible with
  `zg --index --debug`:
  - `index_reconcile_manifest_load`
  - `index_reconcile_manifest_hit`
  - `index_reconcile_manifest_miss`
  - `index_reconcile_vector_fetch`
  - `index_reconcile_embeddings_computed`
  - `index_reconcile_manifest_publish`
- Added diagnostic-only env toggle:
  `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1`. Default remains reuse **ON**.

### Paired 64-file diagnostic

Same integration binary, 3 counterbalanced repeats, `taskset -c 0,1`, release,
**no memory cgroup** (exploratory, not qualification-grade). Mutation changed
one existing fragment and appended one new entity.

Result:

- **22/24 fragments reused**, exactly **2** changed/new fragments embedded.
- Query output SHA-256 identical for ON, OFF, and upstream.
- Embedding stage: **0.88 ms ON vs 8.36 ms OFF**.
- Manifest load + vector fetch + publish: **≈3.3 ms**.
- Mutation wall: **1.243 s ON vs 1.340 s OFF** (OFF median 1.250 s).
- Upstream control: **1.113 s**.
- `index_optimize`: **≈680 ms**, dominating the mutation.
- Peak RSS: **458 MiB ON/OFF vs 518 MiB upstream**.

Conclusion: M2 reuse is functional and net-positive, but only a few ms; it is
**not** the wall-time bottleneck. Batching manifest I/O is **not justified** by
this evidence.

Evidence:

- `BENCHMARK-RESULTS.md`
- `/home/masis/bench/reuse-diag/diag-raw.json`
- `/home/masis/bench/reuse-diag/<variant>-r{1,2,3}/`
- `/home/masis/bench/reuse-final-check-diag.txt`

## M3-B overlap candidate — measured and REJECTED

The recommended overlap was implemented as `5ac1b01` and measured against
`d2344ed`; it was reverted in `0981a78`. Per-stage telemetry (`5741e1d`) was
kept as diagnostic-only.

64-file mutation, concurrent in-process FTS load, 3 counterbalanced repeats,
`taskset -c 0,1`, release, no memory cgroup:

| Metric (mean) | Base | Overlap candidate |
|---|---:|---:|
| Mutation wall | 1,082.8 ms | **894.3 ms** |
| `index_optimize` | 815.9 ms | **638.2 ms** |
| State-lock window (max scalar stage) | **271.1 ms** | 362.4 ms |
| Query p95 | **275.1 ms** | 360.1 ms |
| Query errors / parity | 0 / 10 | 0 / 10 |
| Peak RSS | 665.2 MiB | 578.0 MiB |

All three candidate repeats exceeded all three base repeats on query p95. The
cause is in the stage breakdown: overlapping fragment maintenance with the
scalar collections makes the scalar stage contend for the same 2 CPUs, which
stretches the state-lock window from ~260–282 ms to ~324–387 ms; readers block
for that window. Fragment optimize itself also slowed (486–580 ms → 592–661 ms).

Decision: **REJECT / ROLLBACK**; the provisional 300 ms query target is not
negotiable in this comparison. Details and raw evidence are in
`BENCHMARK-RESULTS.md`.

## Handle-based maintenance feasibility — assessed

Full assessment: [`M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`](M3B-HANDLE-MAINTENANCE-FEASIBILITY.md).

Verdict: **GO for the Rust-side design**, with mandatory native gates before
implementation is accepted. Stable handles cannot be raw pointers: `Collection`
owns a raw FFI handle, closes on `Drop`, and is not `Clone` (`zvec-rust-0.8.0
collection.rs:117,680`), though it is `Send + Sync`. Each scalar table must hold
`Arc<Collection>` and expose `handle()`, as the fork already does. Fragments are
already behind `Arc<Fragments>`.

Minimal lifecycle: Phase A snapshots debt/generation and checks out handles under
the state lock; Phase B runs native optimize under a new `maintenance: Mutex<()>`
lease with no state lock; Phase C re-locks and acknowledges only if the
generation is unchanged and all stages succeeded. `close()` must take the
maintenance lease before setting `closed`/flushing. The current close wait loop
is insufficient: it releases the state lock before taking the store lease, so a
finalize can start in that window.

Two gates are unresolved: the pinned **zvec 0.8.0** concurrency guarantee
(Gate H/I evidence is 0.7.2), and the native seal window identified by the
Phase 3 research (research-only; the prebuilt library may still block readers
inside native optimize). Do not claim the 300 ms query target until both are
verified on this branch.

## Current bottleneck: finalization / optimize

The integration is slower than upstream mainly because of `index_optimize`
(≈0.68 s vs ≈0.56 s), not M2.

### Existing research (do not redo)

In the old branch `work/zg-optimization`:

- `docs/technical/optimization-native-profile-20261027.md`
- Profile of finalization:
  - fragment optimize: **58.4%**
  - file optimize: **23.6%**
  - entity optimize: **10.5%**
  - checkpoint: **7.3%**
- A 4-optimizer-thread A/B was already run:
  - finalization only **1.13% faster**
  - query p95 **320.494 ms**, failed the 300 ms provisional SLO
  - control p95 263.321 ms
  - conclusion: **keep 2 threads; do not repeat the 4-thread experiment**

### Why current integration regressed

`IndexStore::finalize_writes()` currently:

1. runs `fragments.optimize()` alone, outside the state lock;
2. then takes the state lock and runs `optimize_dirty()` for
   `directories/entities/files` in parallel.

Upstream runs all four collections in parallel under the state lock.

Because fragment optimize is the largest stage, the current M3-B partial design
serializes the other ~42% after it. The ~0.12 s gap is consistent with that
serialization, not with M2.

Relevant code:

- `rust/crates/zg-engine/src/storage/store/mod.rs` — `finalize_writes`,
  `optimize_dirty`.
- Upstream baseline: `git -C /home/masis/src/zvec-grep-upstream-baseline` or
  `git show e1f93d2:rust/crates/zg-engine/src/storage/store/mod.rs`.
- Fork M3-B reference:
  `git -C /home/masis/src/zvec-grep show abaf7bf:rust/crates/zg-engine/src/storage/store/mod.rs`
  (handle-based optimize outside the state lock, sequential).

## Recommended next action (not implemented)

The overlap approach is **closed**. The remaining ~0.12 s gap to upstream is
still finalization, but it must be closed without extending the state-lock
window that readers block on.

Implement the handle-based maintenance described in
[`M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`](M3B-HANDLE-MAINTENANCE-FEASIBILITY.md):
`Arc<Collection>` handles, a `maintenance` lease, and Phase A/B/C
snapshot–optimize–ack. Before accepting it, run the pinned-zvec-0.8.0
concurrency gate and the native seal-latency check; if either fails, stop and
treat it as **REWORK** (native dependency decision), not as a reason to retry
the rejected overlap. Do not:
- increase optimizer threads (the 4-thread experiment is already rejected);
- overlap native maintenance with the scalar stage while holding the lock;
- cherry-pick `b4b8686` wholesale.

A candidate must be measured with the same protocol used for the overlap:
64-file mutation, concurrent in-process query load, paired repeats, recording
mutation wall, per-stage optimize, query p50/p95, CPU, RSS, and I/O. Roll it
back if query latency regresses.

Tests to keep/extend before benchmarking:
- reader `list_files()` and concurrent write/query while maintenance is in flight;
- close/reopen during/after maintenance;
- injected optimize failure keeps the marker and retries after reopen;
- generation check preserves debt from a concurrent write;
- no deadlock between state lock, maintenance lease, and close.

## Hard constraints

- Do not run the 421-file campaign yet.
- Do not raise memory/qgroup/cgroup limits or change benchmark authorization.
- Do not change production defaults, storage format, schema, migration, or
  cleanup policy.
- Do not push, merge, or deploy.
- Do not touch M4 experimental code.
- Do not weaken tests or relabel evidence.
- Keep `/tmp` free for build/test temp; use `TMPDIR="$PWD/.tmp"`.

## Commits in this session

- `35ba34e` — fix FP16 vector fingerprint
- `00b19fa` — parallel dirty finalization
- `d0cfb23` — three-way pilot benchmark docs
- `baf55d3` — bounded M2 reuse telemetry
- `ba0cc38` — paired M2 reuse diagnostic docs
- `5ac1b01` — M3-B fragment/dirty overlap candidate (rejected)
- `5741e1d` — per-stage optimize telemetry (kept)
- `0981a78` — revert the overlap candidate

## Verification at handoff

- `cargo test -p zg-engine --lib`: **687 passed, 0 failed, 10 ignored**
- `cargo test -p zg-daemon`: **124 passed, 0 failed**
- `cargo clippy -p zg-engine -p zg-daemon --all-targets -- -D warnings`: **PASS**
- `cargo fmt --all -- --check`: **PASS**
- `git diff --check`: **PASS**
- Final telemetry binary sanity: **22 reused + 2 computed**
