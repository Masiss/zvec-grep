# Fork / upstream / integration feature matrix

- Fork: `origin/work/zg-optimization` at `abaf7bf` (M2/M3 work at `b4b8686`).
- Upstream: `upstream/main` at `e1f93d2`.
- Integration: `integration/upstream-rust` in this worktree.
- Overlap audit: 44 Rust files changed on both sides from merge base `f95fc49`.
- This matrix is about production-relevant behavior, not line-by-line porting.

## Decision vocabulary

- **INTEGRATED** — behavior is implemented on the upstream architecture and has focused tests.
- **PARTIAL** — a safe, useful part is integrated; a named part remains.
- **COVERED** — upstream already implements the invariant; no fork port is needed.
- **REWORK** — fork design is not directly portable; a concrete next step exists.
- **SKIP** — research/M4 or unrelated to production Rust.
- **BENCHMARK** — correctness is present, but a performance/resource claim needs a paired measurement.

## Matrix

| # | Feature / invariant | Fork evidence | Upstream state | Integration decision and evidence |
|---|---|---|---|---|
| 1 | State-lock wait telemetry | `storage/store/mod.rs` test hook; `f62a7ee` | absent | **INTEGRATED** `b3424a0`; test-only counters, reset, regression test. |
| 2 | Streaming token-density detection | `pipelines/indexing/input_budget.rs`; `b4b8686` | full UTF-16 allocation | **INTEGRATED** `317915b`; bounded streaming window, parity test against old algorithm. |
| 3 | Watcher burst coalescing | `zg-daemon/src/job_scheduler.rs`; `b4b8686` | merged options, no trailing debounce | **INTEGRATED** `d8eba99` + `948233f`; 750 ms debounce, 5 s cap, manual priority, cancellation/shutdown/freshness tests. |
| 4 | M2 selective reconciliation / embedding reuse | `storage/manifest.rs`, `pipelines/indexing/pipeline.rs`, `staging.rs`; `b4b8686` | unchanged files skipped; modified files re-embed every fragment | **INTEGRATED (core)** additive `reconciliation/file-*.json` sidecar; exact `(fragment_id, model, embedding fingerprint)` match; stored vector fetched from zvec; manifest publish/delete best-effort; tests in `storage::manifest`, `storage::fragments`, `pipelines::indexing::pipeline`. No existing zvec schema/table change. |
| 5 | M2 streaming/staged indexing | `storage/staging.rs`, stream text path, `read_stream` scanner API; `b4b8686` | no `read_stream`/disk staging surface | **REWORK / NOT INTEGRATED** — requires a scanner streaming API and staging lifecycle on upstream; current manifest reuse works without it. Next: port `read_stream` behind a scanner capability test, then staged batches; benchmark memory before enabling. |
| 6 | M2 entity/fragment fingerprint invalidation | `FileManifest` entity + fragment entries; `b4b8686` | no per-file embedding manifest | **INTEGRATED (simplified)** fragment fingerprint includes model metadata + exact embedding input; entity entry is unnecessary because `fragment_id` already encodes entity content/range + ordinal. |
| 7 | M2 unstable hosted-model reuse guard | `QwenEmbeddingModel::identity_is_stable = false`; `b4b8686` | no identity contract | **INTEGRATED** `EmbeddingModel::identity_is_stable` default true; Qwen false; empty fingerprint is never published or reused. |
| 8 | M3-A mutation-aware finalization | per-collection mutation debt; `b4b8686` | one `needs_optimize` bool; all collections optimized | **INTEGRATED** dirty mask (`directories/entities/files/fragments`); only dirty collections are finalized; marker remains crash barrier; store tests pass. |
| 9 | M3-B native fragment optimize outside `Mutex<StoreState>` | native optimize outside store lock; `b4b8686` | `finalize_writes_with` holds state mutex through all four optimizers | **PARTIAL / BLOCKED** `Fragments` is `Arc`; fragment optimize runs outside the state lock. Scalar collections still optimize under the lock. The fragment/dirty overlap candidate (`5ac1b01`) was rejected after query p95 rose 275→360 ms and reverted (`0981a78`). Handle-based outside-lock scalar maintenance was implemented and then **BLOCKED by zvec 0.8.0**: `optimize()` rejects iterators/writes (`FailedPrecondition`, WAL failure) and can SIGABRT. 0.7.2 Gate H/I is not evidence for 0.8.0. See `M3B-HANDLE-MAINTENANCE-FEASIBILITY.md` and `BENCHMARK-RESULTS.md`. |
| 10 | M3-B deferred maintenance (threshold, age deadline, durable marker, epoch/ack) | `b4b8686`; `rust/docs/m3b-handoff.md` | durable `optimize.pending` marker only | **NOT INTEGRATED** — needs a policy/config decision and benchmark; current marker + generation is the safe subset. Next: add opt-in threshold/age under benchmark policy, then paired idle/loaded test. |
| 11 | M3-B read-session separation | `read_session.rs` changes + `zg_memory::reclaim` | read sessions + write eviction already exist | **COVERED** upstream; fork's `zg_memory` reclamation is **SKIP** (would add a parallel memory abstraction). |
| 12 | Rebuild disk-space preflight | `workspace/build.rs`; 2x active + 4 GiB | no preflight | **INTEGRATED** in `workspace/index.rs`; called before `prepare_build` when an active index exists; conservative refusal, no cap change. |
| 13 | Model compute thread cap / override | `models/runtime/compute.rs`; `ZVEC_GREP_MODEL_COMPUTE_THREADS` | all available cores | **INTEGRATED (BEHAVIOR CHANGE)** default cap 8 when the env var is unset; a positive override wins. On >8-core hosts this changes upstream's all-cores default. Bounds are pinned by tests; owner acceptance required before production. |
| 14 | Model cache idle/capacity overrides | `models/runtime/manager.rs`; `ZVEC_GREP_MODEL_IDLE_TIMEOUT_SECONDS`, `ZVEC_GREP_MODEL_CACHE_CAPACITY` | fixed 15 min / 1 | **INTEGRATED** bounded overrides (idle <= 86400 s, capacity <= 16); defaults unchanged. |
| 15 | Graph persistence / IndexStore coordination | fork changes around `storage/store` and graph | newer upstream SQLite graph + `IndexStore` graph coordination | **COVERED** upstream; do not regress with fork layout. |
| 16 | FP16 embedding storage | fork changed fragments/zvec | upstream has FP16 projection | **COVERED** upstream; integration reuses `zg_zvec_fp16::decode_embedding` for M2 vectors. |
| 17 | Host-native scanner/watcher surface | `scanner.rs`, `watcher.rs`, `api.rs`; `b4b8686` | different host API; watcher tests exist | **PARTIAL** watcher coalescing integrated; scanner streaming (`read_stream`) not. Next: capability-gate the scanner API before staging. |
| 18 | Workspace manifest/build recovery | fork `workspace/build.rs` (new architecture) | upstream `workspace/index.rs` + manifest recovery is newer | **COVERED** upstream, except preflight (#12); fork build record design is not portable. |
| 19 | Extraction/text changes | `extraction/*` | upstream extraction evolved | **DISPOSITIONED** token-density detector integrated (#2). `TextStreamChunker` = REWORK (needs #17 scanner stream). Table-cell embedding projection = REWORK/behavior decision (changes table embedding input, no upstream evaluation). Python decorator / Rust attribute signatures = SKIP (upstream intentionally excludes them; tests assert the contract). `extract_doc` simplification and `ExtractionOutput`/graph removal = SKIP (fork-M4 architecture; upstream keeps richer docs/graph). |
| 20 | CLI/install/render/product surface | `zg-cli`, `zg/src/main.rs`, README | upstream CLI/product features are newer | **COVERED / SKIP** — upstream behavior is the product baseline; no fork replacement without a separate product decision. |
| 21 | M4 lock-split, base/delta, WAL sync, containment, M4 bridge tests | `indexed_search/*`, `tests/native_*`, research artifacts | no production M4 surface | **SKIP** — experimental; preserved in research artifacts, not integrated. |
| 22 | `zg-memory` crate | `rust/crates/zg-memory` | absent | **SKIP** — avoid a parallel memory abstraction; upstream reclamation behavior remains. |

## Competing implementations that still need a decision or measurement

| Area | Fork choice | Upstream choice | Current integration | Needed evidence |
|---|---|---|---|---|
| Modified-file embedding | persistent per-file manifest + streaming staging | re-embed all fragments of a modified file | manifest reuse without streaming | **64-file pilot: reuse active, no wall win**; confirmation mutation at parity (981 vs 941 ms median), query p95/freshness favor integration but formal result INCONCLUSIVE (upstream sample/outlier limits). See `BENCHMARK-RESULTS.md`. |
| Finalization | per-collection debt + deferred age/threshold | all collections under one store lock | dirty mask + fragment outside lock; scalar under lock | Overlap rejected; handle-based blocked by zvec 0.8.0 native maintenance exclusion. Needs a native version that allows query/write during optimize. |
| Read path | M4 lock-split/base-delta | read sessions + parallel optimize | upstream | M4 remains research-only |
| Scanner | streaming `read_stream` + disk staging | whole-file read | whole-file read | memory peak vs corpus size before staging |

## Benchmark result — 2026-10-31 (64-file pilot)

Three repeats per arm, 2-CPU affinity, release builds, no memory cgroup:
**exploratory, not qualification-grade**.

| Phase | Fork | Upstream | Integration |
|---|---:|---:|---:|
| Initial index | 1.83 s / 380 MiB | 1.72 s / 435 MiB | 1.71 s / 382 MiB |
| Mutation (24 entities) | 1.50 s / 450 MiB | **1.21 s / 508 MiB** | 1.38 s / 447 MiB |
| FTS query | **0.42 s / 280 MiB** | 0.56 s / 284 MiB | 0.56 s / 284 MiB |
| Reopen query | **0.41 s / 280 MiB** | 0.56 s / 284 MiB | 0.56 s / 284 MiB |

- Initial index: integration equals upstream, faster than fork.
- Mutation: integration is **13.5% slower than upstream**; M2 reuse is not a
  demonstrated wall-time win at this corpus/model size.
- Integration peak RSS is lower in initial/mutation, consistent with fewer live
  vectors but not proof of reuse.
- Bug found: the manifest vector fingerprint hashed `f32`, while storage is
  FP16, so reuse never matched. Fixed to hash the FP16 representation; final
  numbers use the fixed binary.
- Paired ON/OFF diagnostic (same binary): **22/24 fragments reused**, 2 embedded;
  embedding stage 0.88 ms vs 8.36 ms; manifest load+fetch+publish ≈3.3 ms;
  mutation wall 1.24 s vs 1.34 s. Reuse is active and net-positive but small;
  finalization dominates. No batching justified at this size.
- Full evidence: [`BENCHMARK-RESULTS.md`](BENCHMARK-RESULTS.md).

### Authorized 64-file confirmation (integration vs upstream)

3 counterbalanced repeats, 2 CPU, 512 MiB user cgroup, concurrent query load.
Mutation wall 981 vs 941 ms (integration +4.2%, inside bar); query p95 during
mutation 244 vs 873 ms; freshness lag 401 vs 1,601 ms. Upstream had 9–11 query
samples (pre-registered gate was 20) and one 2.8 s outlier, so the formal result
is **INCONCLUSIVE / exploratory**. Artifacts:
`/home/masis/bench/m3b-confirm/results-20261031T100004Z/`.

## Coverage rule

A fork item is not marked COVERED merely because a similarly named upstream feature exists. Each COVERED row above names the upstream invariant that subsumes it. Items #5, #10, and #17 remain open with a concrete next step. #9 is blocked by the pinned native library; #19 is fully dispositioned above. None are silently dropped.

## Correctness audit — 2026-10-31 (post-integration)

### M2 sidecar and reuse

- **Fingerprint mismatch / model mismatch / unstable model**: reuse is rejected.
  Regression: `m2_reuse_rejects_fingerprint_and_model_mismatch`.
- **Stale/rolled-back vector**: the manifest stores a hash of the stored vector;
  a vector from another generation is rejected even if its fragment ID and input
  fingerprint match. Same regression includes the stale-vector case.
- **FP16**: stored vectors decode through `zg_zvec_fp16`; regression checks the
  f16 round-trip bits in `reusable_vectors_are_loaded_by_fragment_identity_and_model`
  and the real-store lifecycle test.
- **Delete**: `IndexStore::delete_file` removes the sidecar; regression
  `reconciliation_manifest_survives_reopen_and_is_removed_with_the_file`.
- **Rename**: upstream rename is delete + new file identity. The old sidecar is
  removed and the new identity has none, so no old vector is reused. Regression:
  `reconciliation_manifest_is_removed_when_rename_replaces_file_identity`.
- **Failed replacement**: a storage failure does not publish a manifest;
  regression `failed_replacement_does_not_publish_a_reconciliation_manifest`.
- **Crash/reopen**: manifest survives a clean reopen; corrupt or missing sidecar
  is ignored and queries still work:
  `missing_or_invalid_reconciliation_sidecar_never_blocks_reopen_or_queries`.
- **Rollback**: deleting the sidecar disables reuse but never blocks reads. The
  data path is unchanged; only optimization is lost.

### M3 finalization / lock isolation

- **Bug found and fixed**: `close()` held the storage lease mutex while waiting
  for `optimizing`; `finalize_writes()` needs the same lease to reacquire the
  state lock and clear the flag → deadlock. `close()` now waits without the lease,
  then re-takes it for the final flush.
  Regression: `close_waits_for_in_flight_fragment_optimization`.
- **Reader isolation**: while fragment optimize is in flight, `list_files()`
  returns without waiting for the state lock:
  `fragment_optimize_releases_the_store_state_lock_for_readers`.
- **Concurrent write/query/optimize**: a write and FTS query succeed during
  native optimize; the concurrent write preserves debt and the marker:
  `fragment_optimize_allows_concurrent_writes_and_queries`.
- **Generation/recovery**: injected optimize failure keeps `optimize.pending`;
  reopen retries and clears it only after success:
  `fragment_optimize_failure_preserves_marker_and_retries_after_reopen`.
- **Lock ordering**: `finalize_writes` takes state → releases → native optimize
  → re-takes state; `close` takes state → releases → waits → re-takes lease and
  state. No path holds the lease while waiting for the state lock.

### Sidecar on-disk compatibility / migration

- The sidecar is additive: no existing zvec table, schema, or row format changed.
- `version: 2`, `deny_unknown_fields`, bounded at 4 MiB. Unknown/corrupt/oversized
  manifests are ignored; absence is normal for older indexes. Version-1 sidecars
  are ignored (no unsafe reuse) and are rewritten on the next modified file.
- **No migration required** for read or write: an old index opens, and the first
  modified file publishes a manifest. A downgrade ignores the extra directory.
- **Rollback**: remove `reconciliation/` (or restore the previous index
  generation). Reuse is disabled; canonical data and queries are unaffected.
- **Residual risk (reduced)**: the sidecar is still not transactional with the
  zvec generation, but the vector fingerprint binds it to the exact stored
  bytes. A rollback that restores a different vector is rejected; a rollback
  that restores an identical vector is harmless. The remaining release hardening
  is to bind the sidecar directory to the workspace storage generation so a
  stale sidecar is ignored wholesale, rather than entry-by-entry.
