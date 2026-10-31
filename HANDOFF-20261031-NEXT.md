# Continuation handoff — 2026-10-31 (after M2 diagnostic and rejected M3-B overlap)

Read this first. It supersedes the “Next action” at the bottom of `HANDOFF.md`
for the current worktree. Historical detail remains in `HANDOFF.md`,
`FEATURE-MATRIX.md`, and `BENCHMARK-RESULTS.md`.

## Branch / worktree

- Worktree: `/home/masis/src/zvec-grep-integration`
- Branch: `integration/upstream-rust`
- Exact upstream base: `e1f93d2` (`upstream/main`)
- State: **clean**, **30 commits ahead** of `e1f93d2`
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

## Handle-based maintenance — implemented, native gate FAILED, rolled back

Full assessment and evidence: [`M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`](M3B-HANDLE-MAINTENANCE-FEASIBILITY.md).

The `Arc<Collection>` + maintenance-lease Phase A/B/C candidate was implemented
and exercised. The pinned **zvec 0.8.0** native gate failed: `optimize()` rejects
iterators (`create_iterator is not allowed while a maintenance operation ... is
running`) and writes (`Failed to open wal file`), and the process can abort
(`pthread lock: Invalid argument`, SIGABRT). The 0.7.2 Gate H/I evidence does
not cover 0.8.0. Evidence: `/home/masis/bench/m3b-native-gate/run.log`.

Final state: outside-lock scalar maintenance is **NOT retained**; finalization
is sequential under the state lock as before. The safe `Arc<Collection>` handle
refactor and the lifecycle regressions remain. Five outside-lock tests are
`#[ignore]`d with the blocker; handle lifetime, crash-marker recovery, scalar
failure/retry, close-wait and deadlock stress remain active.

No 64-file A/B was run and no candidate was promoted.

## Consolidation and merge-readiness — complete

Full report: [`MERGE-READINESS.md`](MERGE-READINESS.md). Verdict:
**merge-readiness READY for review; production-readiness NOT READY** pending
owner decisions (compute cap 8, watcher debounce, rebuild preflight, sidecar
storage-format acceptance) and the blocked native finalization optimization.

Verification: 1,150 tests passed / 0 failed / 15 ignored across engine (lib +
integration), daemon, CLI, transport-MCP, daemon-protocol, FP16, and host-native;
clippy/fmt PASS; release CLI build PASS. Fork disposition is complete; #19 was
semantically diffed (streaming chunker and table projection are REWORK, signature
decorator/attribute inclusion and doc simplification are SKIP, #5/#10/#17 remain
open).

Authorized 64-file confirmation (3 repeats/arm, 2 CPU, 512 MiB cgroup, concurrent
query load): mutation wall 981 vs 941 ms (integration +4.2%, inside the 5% bar),
query p95 during mutation 244 vs 873 ms, freshness lag 401 vs 1,601 ms. Upstream
had 9–11 query samples (pre-registered gate 20) and one 2.8 s outlier, so the
formal result is **INCONCLUSIVE / exploratory**, not a win. No 421-file run.

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

## Recommended next action

Do not start another optimization track. The remaining work is owner review of
the merge package:

1. Accept or reject the three behavior changes and the additive sidecar in
   `MERGE-READINESS.md` §4/§9.
2. Decide whether the branch evidence docs stay at repo root or move to an
   internal location.
3. Re-run CI with an absolute TMPDIR without `..`.
4. Merge only after those decisions; the blocked handle-based path stays out.

Reopen finalization only if a zvec version/API explicitly allows query/write
during `optimize()` and passes the ignored native gate plus the five ignored
lifecycle regressions. Do not retry the overlap or optimizer-thread experiments.

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
- `9fac03c` — Arc<Collection> scalar handles (kept)
- `df5618a` — handle-based regressions and safe hooks (outside-lock tests ignored)
- `4929ffc` — model runtime override bound tests
- `MERGE-READINESS.md` + this consolidation/benchmark update

## Verification at handoff

- `cargo test -p zg-engine --lib`: **687 passed, 0 failed, 10 ignored**
- `cargo test -p zg-daemon`: **124 passed, 0 failed**
- `cargo clippy -p zg-engine -p zg-daemon --all-targets -- -D warnings`: **PASS**
- `cargo fmt --all -- --check`: **PASS**
- `git diff --check`: **PASS**
- Final telemetry binary sanity: **22 reused + 2 computed**
