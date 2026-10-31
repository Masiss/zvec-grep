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

## PORT / COVERED / REWORK / SKIP

| Item | Source | Decision | Rationale |
|---|---|---|---|
| State-lock wait telemetry | `f62a7ee` | **PORTED** | Generic observability; applies directly to upstream `IndexStore`; test-only. |
| Streaming token-density detector | `b4b8686` | **PORTED** | Self-contained memory reduction; semantics verified against old algorithm. |
| Watcher burst coalescing | `b4b8686` | **PORTED** | Upstream scheduler merged options but had no trailing debounce; bounded debounce reduces writer churn without delaying manual jobs. |
| M2 per-file manifests / selective reconciliation | `b4b8686` | **REWORK** | Upstream now has its own workspace manifest, graph storage, and `IndexStore`; fork design must be re-expressed, not cherry-picked. |
| M3-A mutation-aware finalization | `b4b8686` | **REWORK** | Upstream finalization/optimization pipeline changed substantially; needs mapping to current stages. |
| M3-B lock isolation / maintenance | `b4b8686` | **COVERED** | Upstream has read-session and parallel optimize architecture; no direct port needed. |
| M4 lock-split / reader concurrency | `f9af33e`, `d2658b6`, `72af408`, `3809b29`, `5c60ad0`, `6c2d487`, `430ef82`, `aaafdb4` | **SKIP** | M4 experimental; upstream architecture differs; must not enter production. |
| M4 base/delta hybrid scoring | `3dc1e72`, `5b49769` | **SKIP** | M4 prototype only; no upstream base/delta surface. |
| M4 outer FTS WAL sync | `7c88272` | **SKIP** | M4 native test evidence, not a production Rust change. |
| Containment signal/unit retirement | `bfaf443` | **SKIP** | Research containment harness; unrelated to upstream Rust runtime. |
| M4 integration/bridge tests | `7eb949d`, `5198234`, `a614097`, `34d75aa`, `da01946`, `cf12f83` | **SKIP** | Depend on M4 sidecar/native surfaces; upstream has its own watcher/search tests. |
| M4 recovery diagnostics | `f62a7ee` (containment part) | **SKIP** | Only the generic state-lock telemetry was portable; M4 runner changes are research-only. |
| Fork scheduler logging and watch counters | `b4b8686` | **REWORK** | Useful observability, but upstream logging/tracing conventions differ; defer until needed. |
| Fork `zg-memory` crate | `b4b8686` | **SKIP** | Not required for these priorities; avoid adding a parallel memory abstraction. |

## Verification

- `cargo test -p zg-engine storage::store::tests --lib` — **31 passed**.
- `cargo test -p zg-engine --lib pipelines::indexing::input_budget` — **3 passed**.
- `cargo test -p zg-daemon` — **120 passed**.
- `cargo clippy -p zg-daemon -p zg-engine --all-targets -- -D warnings` — **PASS**.
- `cargo fmt --all -- --check` — **PASS**.
- `cargo test -p zg-engine --lib` — **669 passed, 2 config tests failed in parallel** with `ZG.ENGINE.RESOURCE_BUSY` on global-config locks. Re-running `config::tests::` serially — **13 passed**. This is a pre-existing parallel-lock flake, not caused by the ported changes; record it as a verification caveat.

## Current state

- Integration worktree is clean; branch is **3 commits ahead** of `upstream/main`.
- Old `work/zg-optimization` and its dirty working tree are untouched.
- No M4 experimental code was ported.
- No production defaults, storage format, migration, or release configuration changed.

## Next action

Review the three ports and run the full integration suite in CI-like conditions (single-thread config tests or isolated config dirs). If accepted, the next safe porting candidate is M2/M3 maintenance behavior, but only after mapping it onto upstream's current `IndexStore`/read-session architecture; do not cherry-pick `b4b8686` wholesale.
