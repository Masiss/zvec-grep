# Upstream Rust integration handoff — 2026-10-31

> **Current continuation checkpoint:** [`HANDOFF-20261031-NEXT.md`](HANDOFF-20261031-NEXT.md).
> Read that first for the active branch state, M2 diagnostic result, optimize
> bottleneck, and exact next action. This file is historical detail.

## Scope and branch

- Upstream base: `upstream/main` at `e1f93d2` (`fix(cli): stabilize index progress and simplify status output (#347)`).
- Local branch: `integration/upstream-rust`, created in an isolated worktree at `/home/masis/src/zvec-grep-integration`.
- Source comparison: `origin/work/zg-optimization` (`abaf7bf`) vs `upstream/main`.
- The old worktree/branch `work/zg-optimization` was not modified.
- No push, PR, merge, production-default change, or existing zvec table/schema change was made. M2 adds an additive `reconciliation/file-*.json` sidecar; if that sidecar is treated as a storage-format change, stop for an owner decision before shipping.

## Ported commits

| Commit | Change |
|---|---|
| `b3424a0` | `test(zg-engine): add state-lock wait telemetry` — test-only per-thread wait count/total/max for `IndexStore` state-lock acquisition, plus reset/regression test. |
| `317915b` | `perf(zg-engine): stream token-density detection` — replace full UTF-16 `Vec` allocation in input-budget detection with a bounded streaming window; regression test compares against the previous full-scan algorithm. |
| `d8eba99` | `fix(zg-daemon): bound watcher burst coalescing` — 750 ms trailing debounce, 5 s maximum wait, wake-up on new watch/manual submissions; regression test verifies merged follow-up starts only after the trailing window. |
| `948233f` | `fix(zg-daemon): harden watcher coalescing edges` — existing follow-up now receives Manual priority, cancellation/shutdown cannot double-finish a debounced job; tests cover manual priority, trailing bounds, cancellation, shutdown, and freshness flags. |

## PORT / COVERED / REWORK / SKIP

| Item | Source | Decision | Rationale |
|---|---|---|---|
| State-lock wait telemetry | `f62a7ee` | **PORTED** | Generic observability; applies directly to upstream `IndexStore`; test-only. |
| Streaming token-density detector | `b4b8686` | **PORTED** | Self-contained memory reduction; semantics verified against old algorithm. |
| Watcher burst coalescing | `b4b8686` | **PORTED** | Upstream scheduler merged options but had no trailing debounce; bounded debounce reduces writer churn without delaying manual jobs. |
| M2 per-file manifests / selective reconciliation | `b4b8686` | **INTEGRATED (core) / PARTIAL** | Upstream architecture re-expressed: additive manifest sidecar, exact fingerprint match, stored-vector fetch, unstable-model guard. Streaming/staged indexing remains REWORK. |
| M3-A mutation-aware finalization | `b4b8686` | **INTEGRATED** | Per-collection dirty mask; only mutated collections are finalized. |
| M3-B lock isolation / maintenance | `b4b8686` | **PARTIAL / INTEGRATED** | Fragment native optimize now runs outside `Mutex<StoreState>`; directories/entities/files remain under the lock. The fragment/dirty overlap candidate was measured and **rejected** (query p95 regression) and rolled back; deferred maintenance and handle-based maintenance remain REWORK. |
| M4 lock-split / reader concurrency | `f9af33e`, `d2658b6`, `72af408`, `3809b29`, `5c60ad0`, `6c2d487`, `430ef82`, `aaafdb4` | **SKIP** | M4 experimental; upstream architecture differs; must not enter production. |
| M4 base/delta hybrid scoring | `3dc1e72`, `5b49769` | **SKIP** | M4 prototype only; no upstream base/delta surface. |
| M4 outer FTS WAL sync | `7c88272` | **SKIP** | M4 native test evidence, not a production Rust change. |
| Containment signal/unit retirement | `bfaf443` | **SKIP** | Research containment harness; unrelated to upstream Rust runtime. |
| M4 integration/bridge tests | `7eb949d`, `5198234`, `a614097`, `34d75aa`, `da01946`, `cf12f83` | **SKIP** | Depend on M4 sidecar/native surfaces; upstream has its own watcher/search tests. |
| M4 recovery diagnostics | `f62a7ee` (containment part) | **SKIP** | Only the generic state-lock telemetry was portable; M4 runner changes are research-only. |
| Fork scheduler logging and watch counters | `b4b8686` | **REWORK** | Useful observability, but upstream logging/tracing conventions differ; defer until needed. |
| Fork `zg-memory` crate | `b4b8686` | **SKIP** | Not required for these priorities; avoid adding a parallel memory abstraction. |

## Audit evidence — 2026-10-31

### Watcher coalescing

- Manual priority: `submit` now treats an existing follow-up as a reason to merge even for a Manual request; a Manual submission promotes the pending Watch job and wakes the debounce waiter immediately.
- Trailing updates: `WatchCoalescing::delay_until_ready` uses `last_event_at + 750 ms`, capped by `started_at + 5 s`; unit test covers both bounds.
- Cancellation: a cancelled debounced job checks `job.finished` before `finish_cancelled`/`finish_job`, preventing a second outstanding decrement.
- Shutdown: shutdown cancels the pending follow-up; the debounce waiter observes cancellation and does not double-finish.
- Freshness: the merged Watch follow-up retains `require_existing_index=true`; the Manual promotion path sets it false; tests assert both.
- Focused scheduler tests: **34 passed** (including 5 new coalescing/priority/cancellation/shutdown/bounds tests).

### M3-B classification: PARTIAL

Source evidence in `integration/upstream-rust`:

- `rust/crates/zg-engine/src/storage/store/mod.rs:470-503` — `finalize_writes` calls `finalize_writes_with`; `finalize_writes_with` enters `self.write(...)` and keeps the `IndexStore` state mutex held while `optimize(state)` runs.
- `rust/crates/zg-engine/src/storage/store/mod.rs:532-545` — `optimize_collections` runs four collection optimizers in parallel, but joins all workers before the write closure returns; this is **parallel optimize, not lock isolation**.
- `rust/crates/zg-engine/src/storage/fragments/mod.rs:177-181` — native `index.optimize()` is called from that write-locked path.
- `rust/crates/zg-engine/src/storage/read_session.rs:105-116` — read sessions open a separate read-only `IndexStore`; `release_for_write` at `:313-327` evicts read sessions before writes. This is read/write handle separation, not a commit-scoped fence.
- Native path: `CollectionImpl::optimize` takes `schema_handle_mtx_` exclusively for the seal phase; `CollectionImpl::query` takes it shared (`research/m4-native/.local/native/zvec-native-profile/src/db/collection.cc:966-986`, `:1951-1958`). Upstream Rust does not release its writer state lock around that native call.

Conclusion: upstream has **partial** isolation (read-session separation + parallel optimize), but not M3-B's native-optimize-outside-`Mutex<StoreState>` design. Do not mark M3-B COVERED; rework only if that isolation is required.

### Config-test reproduction

Default `/tmp` was 80% full (6.2 GB used, 1.6 GB available) and caused unrelated native storage failures (`Disk quota exceeded`, then SIGSEGV) in full-suite runs. Session build/test artifacts were removed from `/tmp`; remaining large entries (`v40log`, `v41log`, `v42log`, `ce-smoke-*`) were left untouched because they are not owned by this integration.

Controlled reproduction from the repo root with `TMPDIR="$PWD/.tmp"` and `cargo test --manifest-path rust/Cargo.toml` (build/test temp files stay under the worktree, not `/tmp`):

| Suite | Baseline `e1f93d2` | Integration |
|---|---:|---:|
| `config::tests::` parallel | **13 passed** | **13 passed** |
| Full `zg-engine --lib` | **669 passed, 0 failed** | **671 passed, 0 failed** |
| Full `zg-daemon` | n/a | **124 passed, 0 failed** |

The two config failures are **not reproducible on clean upstream** under equivalent controlled settings; they were a `/tmp` resource-exhaustion artifact, not a pre-existing config-lock defect and not caused by the ports.

## 2026-10-31 integration pass 2 — M2/M3

Full feature matrix: [`FEATURE-MATRIX.md`](FEATURE-MATRIX.md). Benchmark plan:
[`BENCHMARK-PLAN.md`](BENCHMARK-PLAN.md).

### M2 — selective reconciliation / embedding reuse

- Added additive `reconciliation/file-*.json` manifests. They are published only
  after native rows commit; a missing, invalid, oversized, or stale manifest
  disables reuse instead of failing indexing.
- Reuse requires exact `(fragment_id, model, embedding fingerprint)` equality.
  The fingerprint covers the version tag, full model metadata, and exact
  embedding input, so chunk/config changes and model changes cannot reuse an old
  vector.
- Stored vectors are fetched by fragment key from the model collection and
  decoded through `zg_zvec_fp16`.
- `EmbeddingModel::identity_is_stable` is false for Qwen hosted aliases; those
  fragments never publish a fingerprint and never reuse.
- Delete/failed-replace paths remove stale manifests best-effort.
- Tests: manifest round-trip/bounded/delete, vector fetch by identity/model,
  exact-fingerprint reuse guard, and full engine suite.

### M3 — mutation-aware finalization and lock isolation

- `StoreState` now carries a per-collection dirty mask. `finalize_writes` only
  optimizes collections with mutation debt; a pending marker still forces a safe
  full retry after reopening.
- `Fragments` is held in `Arc`; native fragment optimize runs after the store
  state lock is released. `write_generation` preserves debt from concurrent
  writes, and `close` waits for in-flight optimize before flushing/releasing.
- `directories`, `entities`, and `files` still optimize under the store lock
  because they expose mutable handles. This is explicitly **PARTIAL**, not a
  full M3-B port.
- Deferred maintenance (opt-in threshold, age deadline, epoch/ack) is not
  integrated; it needs a policy decision and a paired benchmark.

### Additional production-relevant ports

- Rebuild disk preflight: 2x active index + 4 GiB headroom before staging a
  rebuild; refuses instead of risking rollback loss.
- Model compute thread default capped at 8, with positive
  `ZVEC_GREP_MODEL_COMPUTE_THREADS` override.
- Model cache idle timeout/capacity overrides:
  `ZVEC_GREP_MODEL_IDLE_TIMEOUT_SECONDS` (<= 86400) and
  `ZVEC_GREP_MODEL_CACHE_CAPACITY` (<= 16); defaults unchanged.

### Correctness pass 3 — 2026-10-31

- **M2 manifest now version 2** and binds each entry to the stored vector via a
  vector fingerprint. A rolled-back/stale vector is rejected even when fragment
  ID, model, and input fingerprint match. Version-1 sidecars are ignored, not
  reused.
- Added regressions for fingerprint/model/stale-vector mismatch, failed
  replacement, real-store reopen, missing/corrupt sidecar, delete, and
  rename-as-delete-plus-new-identity.
- **Found and fixed a close deadlock**: `close()` held the storage lease while
  waiting for `optimizing`; `finalize_writes()` needs the same lease to clear it.
  `close()` now waits without the lease and re-takes it for the final flush.
- **Found and fixed read-only close**: the earlier early return skipped lease
  removal, leaving a read-only `IndexStore` registered and causing false
  `RESOURCE_BUSY` on reopen.
- Added M3 tests for reader isolation, concurrent write/query during optimize,
  close waiting, injected optimize failure, marker retention and reopen retry.
- Test-only optimize hooks are thread-local, so parallel tests cannot inject
  delay/failure into unrelated finalizers.
- Rebuild preflight keeps the production `2x + 4 GiB` rule; tests use a
  `cfg(test)` zero headroom so small temporary disks can exercise the rebuild
  path. Production constant is unchanged.

### Integration commits (pass 2)

- `ff4f4ac` — `feat(engine): integrate selective embedding reuse and maintenance isolation`
- `ae7bfe6` — `perf(engine): bound model runtime and preflight rebuild space`
- `0098a6f` — `fix(engine): harden M2 sidecar and M3 finalization correctness`
- `79eba5c` — `docs: record M2/M3 correctness audit and benchmark gates`
- `f5e7208` — `test(engine): cover reconciliation sidecar rename removal`
- `acdf8d1` — `docs: record reconciliation rename and final correctness counts`

### Benchmark pass — 2026-10-31

- Corpus: 64 camera `.cs` files, 429,535 bytes, manifest `f5fc630a…`.
- Arms: fork `abaf7bf`, upstream `e1f93d2`, integration branch.
- Three repeats each, `taskset -c 0,1`, release builds. No memory cgroup, so
  results are exploratory, not qualification-grade.
- **Bug found and fixed**: manifest vector fingerprint hashed the original f32
  vector, but storage is FP16; decoded vectors never matched, so M2 reuse was
  rejected. Fingerprint now hashes `half::f16::from_f32(...).to_bits()`; added
  `vector_fingerprint_matches_the_fp16_representation`.
- Also restored parallel finalization for the dirty
  `directories/entities/files` collections (`optimize_dirty`), preserving M3-A
  dirty selection while avoiding a serial regression.
- Result: initial 1.71 s vs upstream 1.72 s; mutation 1.38 s vs upstream 1.21 s;
  peak RSS 382/447 MiB vs upstream 435/508 MiB. Decision: **INCONCLUSIVE / NOT
  A WIN**; no 421-file run.
- Full evidence: [`BENCHMARK-RESULTS.md`](BENCHMARK-RESULTS.md).

### M2 reuse diagnostic — 2026-10-31

- Added bounded telemetry to `IndexResult.timings` (visible with `--debug`):
  manifest load/hit/miss, vector fetch/reuse, embeddings computed, manifest
  publish, and their durations.
- Added `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1` as a diagnostic-only toggle;
  default remains ON.
- Paired 64-file diagnostic, same binary, 3 counterbalanced repeats, 2-CPU
  affinity, no memory cgroup:
  - 22 of 24 fragments reused; exactly the changed and new fragments embedded.
  - Embedding stage **0.88 ms vs 8.36 ms** (ON vs OFF).
  - Manifest load + fetch + publish ≈ **3.3 ms**; net small win.
  - Mutation wall **1.24 s vs 1.34 s** (ON vs OFF, OFF median 1.25 s);
    upstream **1.11 s**. `index_optimize` ≈ 0.68 s dominates.
  - Query output SHA-256 identical across ON/OFF/upstream.
- Conclusion: **M2 reuse works and is net-positive at this size, but is not the
  wall-time bottleneck.** No batching change is justified by this evidence.
- Evidence: `BENCHMARK-RESULTS.md`, `/home/masis/bench/reuse-diag/diag-raw.json`.

### Verification (pass 2)

- `cargo test -p zg-engine --lib` — **685 passed, 0 failed, 10 ignored** (after FP16 fix).
- `cargo test -p zg-daemon` — **124 passed, 0 failed** (after final code).
- `cargo clippy -p zg-engine -p zg-daemon --all-targets -- -D warnings` — **PASS**.
- `cargo fmt --all -- --check` — **PASS**.
- `git diff --check` — **PASS**.
- All commands use `TMPDIR="$PWD/.tmp"`; `/tmp` is not used for build/test temp.

### Remaining blockers / next steps

1. **M2 streaming/staged indexing**: upstream scanner has no `read_stream` and
   no disk-staging lifecycle. Next: capability-gate the scanner API, then port
   staged batches and measure peak RSS on the 421-file corpus.
2. **M3-B deferred maintenance**: needs an opt-in policy (threshold/age) and a
   paired idle/loaded benchmark; do not silently change production defaults.
3. **M3-B full lock isolation**: directories/entities/files still require the
   state lock. Next: make their handles cloneable or move their native
   maintenance behind a read-session boundary.
4. **Three-way benchmark**: 64-file pilot executed (3 repeats, 2-CPU affinity,
   no memory cgroup). Result: **INCONCLUSIVE / NOT A WIN** — initial equals
   upstream, mutation 13.5% slower, RSS lower. See `BENCHMARK-RESULTS.md`.
   Do not run 421 files until reuse telemetry and manifest I/O overhead are
   understood.
5. **Storage sidecar decision**: the reconciliation directory is additive and
   does not alter existing zvec tables. If the owner considers any new on-disk
   sidecar a storage-format change, decide before release.

## Verification (pass 1)

- `cargo test -p zg-engine storage::store::tests --lib` — **31 passed**.
- `cargo test -p zg-engine --lib pipelines::indexing::input_budget` — **3 passed**.
- `cargo test --manifest-path rust/Cargo.toml -p zg-daemon` with `TMPDIR="$PWD/.tmp"` — **124 passed**.
- `cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib` with `TMPDIR="$PWD/.tmp"` — **671 passed, 0 failed**.
- Baseline `cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib` with `TMPDIR="$PWD/.tmp"` — **669 passed, 0 failed**.
- `cargo clippy -p zg-daemon -p zg-engine --all-targets -- -D warnings` — **PASS**.
- `cargo fmt --all -- --check` — **PASS**.

## Current state

- Integration worktree is clean; branch is **24 commits ahead** of `upstream/main`.
- Old `work/zg-optimization` and its dirty working tree are untouched.
- No M4 experimental code was ported.
- No existing zvec table/schema/format change; M2 adds an additive reconciliation sidecar. No production default, migration, or release configuration changed.
- M2 core and M3-A/M3-B fragment isolation are integrated; streaming staging and deferred maintenance remain open.
- M3-B fragment/dirty overlap candidate (`5ac1b01`) was rejected after a concurrent-query p95 regression and reverted in `0981a78`. Per-stage optimize telemetry (`5741e1d`) is kept as diagnostic-only. See `BENCHMARK-RESULTS.md`.

## Next action

See [`HANDOFF-20261031-NEXT.md`](HANDOFF-20261031-NEXT.md). Current priority is
the finalization bottleneck; the overlap approach is closed. Do not run 421
files until a replacement is measured.
