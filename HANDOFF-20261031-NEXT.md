# Continuation handoff — 2026-10-31 (after M2 reuse diagnostic)

Read this first. It supersedes the “Next action” at the bottom of `HANDOFF.md`
for the current worktree. Historical detail remains in `HANDOFF.md`,
`FEATURE-MATRIX.md`, and `BENCHMARK-RESULTS.md`.

## Branch / worktree

- Worktree: `/home/masis/src/zvec-grep-integration`
- Branch: `integration/upstream-rust`
- Exact upstream base: `e1f93d2` (`upstream/main`)
- State: **clean**, **20 commits ahead** of `e1f93d2`
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

**Overlap fragment optimize with dirty directory/entity/file optimize**, without
changing the optimizer thread policy:

- Start `fragments.optimize()` on a scoped/worker thread.
- Concurrently take the state lock and run `optimize_dirty()` for the dirty
  scalar collections.
- Join both before clearing `optimizing`, the generation check, or the
  `optimize.pending` marker.
- Preserve the existing failure/retry semantics: a failed stage must keep the
  marker and debt; do not silently clear either.
- Do not increase optimizer threads; the 4-thread experiment is already rejected.

Tests to add/adjust before benchmarking:

- reader `list_files()` and concurrent write/query while fragment optimize is
  in flight;
- close/reopen during/after optimize;
- injected optimize failure keeps the marker and retries;
- generation check preserves debt from a concurrent write;
- no deadlock between state lock, fragment worker, and close.

Benchmark protocol:

- 64-file corpus, same integration binary, before/after overlap.
- `taskset -c 0,1`, release, no memory cgroup; record wall, CPU, RSS,
  `index_optimize`, and query output parity.
- Keep upstream `e1f93d2` as control.
- Only after the overlap is verified should the 421-file campaign be considered,
  under the approved memory/CPU envelope.

If overlap is not sufficient, the larger alternative is to port the fork’s
handle-based maintenance (all collections outside the state lock) with the
maintenance lease; do not cherry-pick `b4b8686` wholesale.

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

## Verification at handoff

- `cargo test -p zg-engine --lib`: **685 passed, 0 failed, 10 ignored**
- `cargo test -p zg-daemon`: **124 passed, 0 failed**
- `cargo clippy -p zg-engine -p zg-daemon --all-targets -- -D warnings`: **PASS**
- `cargo fmt --all -- --check`: **PASS**
- `git diff --check`: **PASS**
- Final telemetry binary sanity: **22 reused + 2 computed**
