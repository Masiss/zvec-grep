# Upstream Rust integration handoff — 2026-10-31

## Scope and branch

- Upstream base: `upstream/main` at `e1f93d2` (`fix(cli): stabilize index progress and simplify status output (#347)`).
- Local branch: `integration/upstream-rust`, created in an isolated worktree at `/home/masis/src/zvec-grep-integration`.
- Source comparison: `origin/work/zg-optimization` (`abaf7bf`) vs `upstream/main`.
- The old worktree/branch `work/zg-optimization` was not modified.
- No push, PR, merge, production-default change, or storage-format change was made.

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
| M2 per-file manifests / selective reconciliation | `b4b8686` | **REWORK** | Upstream now has its own workspace manifest, graph storage, and `IndexStore`; fork design must be re-expressed, not cherry-picked. |
| M3-A mutation-aware finalization | `b4b8686` | **REWORK** | Upstream finalization/optimization pipeline changed substantially; needs mapping to current stages. |
| M3-B lock isolation / maintenance | `b4b8686` | **PARTIAL** | Upstream separates read sessions and parallelizes collection optimize, but `finalize_writes_with` still holds `IndexStore` state mutex across `optimize(state)`; parallel optimize is not lock isolation. See audit evidence below. |
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

## Verification

- `cargo test -p zg-engine storage::store::tests --lib` — **31 passed**.
- `cargo test -p zg-engine --lib pipelines::indexing::input_budget` — **3 passed**.
- `cargo test --manifest-path rust/Cargo.toml -p zg-daemon` with `TMPDIR="$PWD/.tmp"` — **124 passed**.
- `cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib` with `TMPDIR="$PWD/.tmp"` — **671 passed, 0 failed**.
- Baseline `cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib` with `TMPDIR="$PWD/.tmp"` — **669 passed, 0 failed**.
- `cargo clippy -p zg-daemon -p zg-engine --all-targets -- -D warnings` — **PASS**.
- `cargo fmt --all -- --check` — **PASS**.

## Current state

- Integration worktree is clean; branch is **5 commits ahead** of `upstream/main`.
- Old `work/zg-optimization` and its dirty working tree are untouched.
- No M4 experimental code was ported.
- No production defaults, storage format, migration, or release configuration changed.

## Next action

Review the three ports and run the full integration suite in CI-like conditions (single-thread config tests or isolated config dirs). If accepted, the next safe porting candidate is M2/M3 maintenance behavior, but only after mapping it onto upstream's current `IndexStore`/read-session architecture; do not cherry-pick `b4b8686` wholesale.
