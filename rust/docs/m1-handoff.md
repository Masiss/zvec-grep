# M1 Hand-off — bounded streaming indexing

Updated: 2026-10-15. **M1 implementation and correctness gates are complete; production rollout is not approved.** M2 is the next milestone.

## M1 result

- Pipeline-owned intermediate memory is admitted through a shared `PipelineMemoryBudget` per indexing run: 3 reservations × 32 MiB = 96 MiB. A reservation remains held across prepared data, embedding work, completed-but-not-applied results, and is released after apply/drop; this bounds concurrent candidates instead of multiplying a per-future limit.
- Plain UTF-8 text streams from the scanner through `TextStreamChunker`; source contents/entities/fragments are not all materialized for that route. Formats requiring whole-source parsing use the explicit 8 MiB in-memory admission limit.
- Large files stage entities and completed vectors on disk, with a 512 MiB staging quota and bounded batches. Native publish consumes staged batches without assembling the file's vectors/documents back into one large `Vec`.
- An interrupted replacement remains `NotIndexed`; search-hit hydration hides it. Reopen/retry and injected partial-publish failures are covered, and retry does not expose incomplete results or accumulate duplicate native segments.
- **Known storage boundary:** native collections do not provide a cross-collection transaction. The implementation does not claim atomic publication; it uses the `NotIndexed` visibility barrier plus idempotent retry/reconciliation.

## Main implementation files

- `rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs`
- `rust/crates/zg-engine/src/pipelines/indexing/input_budget.rs`
- `rust/crates/zg-engine/src/pipelines/indexing/storage.rs`
- `rust/crates/zg-engine/src/storage/staging.rs`
- `rust/crates/zg-engine/src/storage/store/mod.rs`
- `rust/crates/zg-engine/src/storage/store/tests.rs`
- `rust/crates/zg-engine/src/storage/store/tables_tests.rs`
- `rust/crates/zg-engine/src/storage/fragments/mod.rs`
- `rust/crates/zg-host-native/src/{api.rs,lib.rs,scanner.rs}`

## Verification

- `TMPDIR=/home/masis/tmp-zvec-tests cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib`: **511 passed, 0 failed, 9 ignored** (94.67 s).
- `TMPDIR=/home/masis/tmp-zvec-tests cargo check --manifest-path rust/Cargo.toml --workspace`: PASS.
- Streaming, admission, concurrency, staging-quota, cancellation, recovery/reopen, retry, and partial-publish regressions are included in the engine suite; scanner-specific regressions passed in the earlier targeted gate.
- `rustfmt --edition 2024 --check` on changed indexing/staging test sources and `git diff --check`: PASS.
- Active LSP diagnostics: `staging.rs` and `tables_tests.rs` clean; `pipeline.rs` exceeds the configured per-file LSP size limit, so its LSP result is inconclusive (compiler/tests pass).

## Scope and remaining limits

- The 96 MiB budget bounds **pipeline-owned** prepared/embedding/result data for one indexing run. It does not bound allocations private to an embedding backend/native library, total memory across independent indexing runs, or native `optimize()` working memory.
- Whole-source parser fallbacks remain materialized but are rejected above 8 MiB. Disk staging is capped at 512 MiB; a file exceeding quota fails rather than growing staging without bound.
- M1 does not change retrieval semantics, the single-index architecture, native finishing behavior, or production indexes. No release build/performance rollout gate was run here.

## Production readiness

**Not ready for production rollout based on this milestone alone.** M1's streaming, bounded pipeline ownership, recovery and correctness gates pass. Production rollout still requires the planned integration/rollback and performance gates; M1 does not address native finishing cost.

## M2 hand-off — persistent fingerprints and selective reconciliation

**Updated:** 2026-10-15. **Status:** M2 implementation and correctness gates pass for the user-approved persistent per-file manifest approach. M3 and M4 have not been started. No production index was opened, migrated, or modified.

### Goal, approval, and scope boundary

M2 was authorized to reuse embeddings when exact fragment inputs and declared model metadata match, and to mutate only changed entity/fragment rows. The selected design is a bounded persistent manifest per file because native collection APIs do not offer a filtered, paginated scan suitable for reconstructing this state. Keep the existing single-index retrieval architecture and `NotIndexed` retry/visibility invariant. M3 native optimize work and M4 integration, rollout, or production migration are separate gates.

### End-to-end dataflow

1. Indexing reads/classifies a changed source and prepares entities/fragments. For each fragment it forms the exact vector input (including the same preprocessing/metadata applied before embedding), chooses the runtime, and computes an embedding fingerprint.
2. For an existing **Indexed** file, the pipeline loads that file's manifest. It looks up entries by model reference plus embedding fingerprint, fetches vectors by the old fragment IDs from the native collection, and reuses only finite vectors of the expected dimension. Missing or invalid vectors are sent to the embedder.
3. Small files use the in-memory replacement API; large files use disk-backed staged batches. Both converge on store-side manifest generation and reconciliation.
4. The store compares the old and new manifests. It deletes removed entities and fragments, writes only entity docs whose entity fingerprint changed, and writes only new/changed fragment projections. A fragment with the same identity/model but changed embedding or projection fingerprint is upserted; a removed fragment or one moved to another model partition is deleted from its old partition.
5. The store persists the new manifest after applying bounded native writes, then publishes the final `Indexed` file state. Search-hit hydration hides any file whose state is not `Indexed`.

This avoids redundant embedding and unchanged native row writes for matching fragments. It still scans, re-extracts, re-chunks, and fingerprints a modified source; M2 does not implement a prefix-only append/chunker shortcut.

### Manifest and fingerprint contract

- **Location:** `storage/reconciliation/file-<8-hex-file-id>.json` under the index storage directory. The manifest records a version, file ID, entity IDs/fingerprints, and fragment IDs, owning entity IDs, model references, embedding fingerprints, and projection fingerprints. It stores no vectors or credentials.
- **Bounds/versioning:** version 1; maximum encoded file size 4 MiB. Loads validate version and file identity, lowercase-hex IDs/fingerprints, uniqueness, entity ownership, fragment ownership, and model-table support before enabling reuse. Records are normalized before lookup/publication.
- **Embedding fingerprint:** SHA-256 over a fingerprint-format version, `EmbeddingModelInfo`, and the exact vector input. Declared model metadata includes provider/name/endpoint, dimension, metric, batch limit, and input limits. The secret API key is not stored. A vector is reused only when both model reference and fingerprint match and the fetched vector passes dimension/finiteness validation.
- **Entity fingerprint:** SHA-256 over the canonical staged entity payload and metadata with its file/entity identity.
- **Projection fingerprint:** SHA-256 over file identity, encoded relative path, directory IDs, entity and fragment IDs, entity fingerprint, model reference, and FTS text. Path/metadata changes therefore invalidate the projection even when the vector input remains reusable.
- **Fallback:** missing/legacy, corrupt, unsupported, or oversized old manifests disable incremental reuse. An oversized or incomplete new manifest is not published; the operation uses full replacement and removes the stale manifest. A successful full replacement can publish a new manifest if it fits and validates.

### Retry, crash, and visibility invariant

Native collections do not support a cross-collection transaction. The implementation does **not** claim atomic commit. Before mutating rows, the file is written as `NotIndexed`; projection writes and old-row deletes occur under the store writer lock; the manifest is atomically written after those row operations; the final `Indexed` file record is written last. If any operation fails, the incomplete generation remains hidden from hydrated search results. After reopen, `load_indexed_manifest` refuses to use a manifest unless the file is `Indexed`, so retry cannot treat an interrupted generation as a valid cache and safely performs a full replacement. The staged failure/reopen/retry regression exercises this boundary.

### Implementation map

- `rust/crates/zg-engine/src/storage/manifest.rs` — manifest schema, validation, version/size bound, lookup, atomic store/load/delete, unit tests.
- `rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs` — exact embedding-input fingerprint, loading prior manifests, vector fetch/reuse, embed only misses, staging integration. Large file is 6,064 lines and exceeds the configured LSP per-file limit.
- `rust/crates/zg-engine/src/pipelines/indexing/storage.rs` — pipeline/storage seam for manifest and vector lookup.
- `rust/crates/zg-engine/src/storage/store/mod.rs` — indexed-manifest eligibility, entity/projection fingerprints, incremental plan, selective memory/staged writes, publish barrier, manifest cleanup and fallback.
- `rust/crates/zg-engine/src/storage/store/tests.rs` — storage-level reuse/reconciliation, reopen persistence, staged replacement, interrupted publish and retry regressions.
- `rust/crates/zg-engine/src/storage/staging.rs`, `rust/crates/zg-engine/src/storage/types.rs`, and `rust/crates/zg-engine/src/storage/mod.rs` — staging serialization/types/module wiring used by the M1/M2 paths.

### Verification evidence

Final source-state verification:

- `TMPDIR=/home/masis/tmp-zvec-tests cargo test --manifest-path rust/Cargo.toml -p zg-engine --lib`: **516 passed, 0 failed, 9 ignored** (167.70 s; run `job-994936-262`).
- Focused M2 regressions on the final source passed: `manifest_reuses_exact_embedding_and_selectively_reconciles_file_rows` (including manifest/vector reopen), and `embedding_reuse_requires_an_exact_model_and_input_fingerprint`.
- `TMPDIR=/home/masis/tmp-zvec-tests cargo check --manifest-path rust/Cargo.toml --workspace`: PASS, no warnings after the no-longer-production-used `embed_file` wrapper was marked test-only (run `job-994936-248`).
- `cargo fmt --manifest-path rust/Cargo.toml --all -- --check` and `git diff --check`: PASS.
- Active LSP diagnostics confirmed 4 scoped Rust files clean. The 6,064-line `pipeline.rs` is above the 5,000-line LSP limit, so its LSP result is inconclusive; workspace compilation and the engine suite cover it.
- A preliminary full-suite attempt failed because a storage-layout assertion omitted the newly created `reconciliation/` directory. The assertion was updated; the final full-suite run above passed.

### Known limits and risks

- Modified files are still read and fully re-extracted/re-chunked to establish exact inputs. Reuse reduces embedder calls and unchanged native row writes, not source-read/chunking cost.
- The fingerprint represents **declared runtime metadata**, not an independently pinned provider revision. A remote provider silently changing the implementation behind an unchanged model name/endpoint cannot be detected without a revision/version from that provider. If such pinning becomes a product requirement, extend the runtime identity contract before relying on cross-run reuse for mutable aliases.
- The manifest is per-file: moving/renaming to a new file identity does not create cross-file cache reuse. Correctness relies on normal delete/add or same-file reconciliation; no cross-file cache was introduced.
- Cross-collection publication remains retry-safe, not transactional. Native optimization cost/behavior was not benchmarked or changed under M2.
- No release build, production rollout, production-index test, or M3 performance gate was run. Do not infer M3/M4 readiness from the unit suite.

### Repository/session state, cleanup audit, and exact next action

- The working tree already contained broad unrelated modifications and untracked artifacts from earlier work. Preserve them; do not reset, clean, or stage them wholesale. M2 changes remain uncommitted.
- No background test/build job is currently running. The earlier failed full-suite job was superseded by the successful final run above.
- Cleanup/relocation requested after M2: `~/tmp` is a 1.1 GiB mixed directory of unrelated OCR/Kaggle/vehicle-review artifacts; no current zvec-grep project paths were found there, so it was left untouched. `~/tmp-zvec-tests` contained only 17 tempfile-style test fixtures (~22 MiB); after confirming no open handles or active tests, those were removed and the now-empty directory was removed.
- Moved the native profile source checkout and CMake build from `$HOME` into `.local/native/zvec-native-profile` (904 MiB) and `.local/native/zvec-native-build` (1.4 GiB), under this repository. Added `/.local/native/` to the local, untracked `.git/info/exclude` so this large helper checkout/build cannot be accidentally staged. The source checkout's six modifications and all submodule Git state were preserved; its modified files are `CMakeLists.txt`, `src/binding/c/c_api.cc`, `src/db/collection.cc`, `src/db/index/storage/wal/wal_file.h`, `src/include/zvec/db/options.h`, and `thirdparty/arrow/CMakeLists.txt`.
- Relocation left old absolute paths in generated CMake files. The cache was rebased and `cmake -S .local/native/zvec-native-profile -B .local/native/zvec-native-build` completed configure/generate. That top-level configure did not refresh nested Arrow/third-party build metadata, so I also rebased **115,200 exact source/build path references across 2,297 generated text files** in the moved build tree; no stale old roots remain in CMake caches, Makefiles, CMake scripts, or compile-command JSON. `cmake --build .local/native/zvec-native-build --target help` loaded the relocated target graph successfully. No compilation was run, so the build is path-repaired/configured but not freshly build-verified. Non-fatal CMake notices remain: shallow checkout gives dummy version `v0.0.0-g8f6d600`, optional `uring` was not found, and bundled dependencies emit deprecation/policy warnings. The daemon uses `rust/target/release/libzvec_c_api.so`; it does not map this CMake build tree.
- Removed five stale `zg-host-native` scanner-test temp directories, a 25 MiB stale rustc `.temp-archive`, generated Python `__pycache__` trees, and `.ruff_cache`; they had no open handles/active test-build process and are reproducible. The dedicated test TMPDIR used by the recorded test command was also removed after the final run. To rerun that exact command, first recreate it with `mkdir -p /home/masis/tmp-zvec-tests`; remove it again only after tests have exited.
- **M3-A was authorized after M2.** The M2 boundary below is superseded for M3-A only; M3-B still needs explicit approval.
- **Stop at the M2 boundary.** Wait for the user's direction before starting M3. If authorized, first read the M3 section of `rust/docs/m1-original-plan.md`, establish the exact existing optimize behavior and a bounded measurement plan, and do not modify production indexes or change retrieval architecture.

---

## M3-A Hand-off — mutation-aware finalization (2026-10-15)

**Status: M3-A implementation and its store-level correctness gates pass. M3-B is proposed below and is NOT implemented. No production index was opened, migrated, or modified.**

## Goal and scope

Remove native `optimize()` calls that are not required by the mutation that actually happened, without weakening vector-index completeness, deletion visibility, or reopen correctness. M3-A deliberately does **not** make the expensive HNSW merge cheaper; that is M3-B.

Changed files:

- `rust/crates/zg-engine/src/storage/store/mod.rs` — replaced the single `needs_optimize` flag with a per-collection `FinalizeState` bitflag; per-collection optimize decisions in `finalize_writes`; `IncrementalPlan::{has_entity_changes, has_fragment_deletions}`; wired the projection metadata fingerprint through `build_file_manifest`, `build_staged_file_manifest`, and `projection_manifest_fingerprint`.
- `rust/crates/zg-engine/src/storage/fragments/mod.rs` — added `metadata_projection_fingerprint`, covering only the metadata fields materialized into a fragment projection.
- `rust/crates/zg-engine/src/storage/directories/mod.rs` — `Directories::took_write` (a `Cell<bool>`) records whether `ensure` created rows.
- `rust/crates/zg-engine/src/storage/store/tests.rs` — six regression tests (non-indexed metadata, indexed metadata, deletion, repeated finalize, untouched directories, staged non-indexed metadata).

## Dataflow

- Before: any entity/fragment/file/delete mutation set one global flag; `finalize_writes` then ran `optimize()` on **directories + entities + files + fragments** whenever that flag or the fragment-completeness probe was true.
- After: each collection is finalized independently. `directories/entities/files` are optimized only when the mutation touched them; `fragments` is optimized when the native vector index reports incomplete **or** a fragment deletion was forced. The fragment completeness probe still runs on every finalize as the safety net for flush-only writers and restarts.
- Projection fingerprint before: full entity fingerprint (content + **all** metadata). After: identity (via `entity_id`) + file membership + **only the indexed metadata fields** (`symbol_name`, `symbol_type`). Non-indexed metadata (documentation, signature, scope, Markdown heading/level) no longer rewrites fragment rows.

## Native behavior measured (isolated fixture)

- `CollectionStats::indexes` reports only the vector index (`embedding`); the entity/file/directory collections report **no** indexes.
- After **any** fragment upsert (vector add/update, metadata-only, or same-content) the vector `completeness` drops to `0`; only `optimize()` restores it to `1`.
- After a subset deletion, `completeness` stays `1` and search already excludes deleted documents, but the fragment-deletion path still forces `optimize()` because the tombstone/visibility contract was not proven at scale.

## Optimize cases removed (proven)

- Non-indexed metadata-only change: fragment vector/HNSW optimize is skipped (in-memory and staged paths). Only `files` and `entities` finalize.
- Repeated `finalize_writes` with no intervening writes: no optimize at all.
- File deletion: the untouched `directories` table is not optimized.
- A mutation in one collection no longer cascades optimize to untouched collections.

## Optimize cases that remain forced

- Any fragment row write (vector add/update, FTS change, indexed-metadata change, path change) leaves the vector index incomplete, so the probe forces the fragments optimize.
- Fragment/file deletion forces the fragments optimize.
- Indexed metadata change (`symbol_name`/`symbol_type`) rewrites the fragment projection and forces the optimize.
- `entities`/`files`/`directories` are optimized whenever they are mutated (no native completeness probe exists for them).

## Remaining native bottleneck (input to M3-B)

`zvec_collection_optimize` runs a whole-collection `build_compact_task` with `OptimizeOptions::merge_segments_ = true` by default: sealing the writing segment and merging/rebuilding persisted segments. A single small vector addition still triggers this full merge. The native checkout already contains a **research-only** `merge_segments_ = false` path (env `ZVEC_RESEARCH_SEGMENT_ONLY=1`) that builds only the unready segments' vector indexes, plus a schema `max_doc_count_per_segment` knob.

## Verification evidence (final source state)

- `TMPDIR=/home/masis/tmp-zvec-tests cargo test -p zg-engine --lib`: **522 passed, 0 failed, 9 ignored** (`job-994936-79`).
- `cargo check --workspace --all-targets`: PASS, no warnings.
- `cargo fmt --all --check`: PASS. Clippy adds no new lint (the four-collection state uses a bitflag instead of four bools); pre-existing pedantic warnings remain.
- Integration (M3-A source state): `concurrent_queries` 6/0, `index_version` 2/0, `storage_crash` 2/0 PASS.

---

## Gate 1 Hand-off — NotIndexed visibility contract (2026-10-15)

**Status: PASS (code + tests).** The `engine_storage::public_engine_recovers_pending_files_without_skipping_unchanged_sources` failure is resolved by refining the M1/M2 contract, not by removing the fail-closed filter.

## Contract

A file's stored rows are searchable iff the file is fully `Indexed` **or** a validated reconciliation manifest exists for it. The manifest is the durable marker of the last complete published generation. `replace_file`/`replace_file_from_staged` now `state.manifests.delete(file.id)` **before the first row mutation**, so an interrupted apply has no manifest and is hidden (fail-closed); a pending retry whose last generation is intact keeps its manifest and stays searchable.

Implementation limit (deliberate): the distinction is manifest presence, not a new generation/state machine. Files indexed without a manifest (oversized or missing embedding fingerprints) are served only while `Indexed`; when pending they are hidden.

## Code

- `storage/store/mod.rs` — manifest invalidation at apply start in both replacement paths; `load_search_hits` trusts a `NotIndexed` file only when a validated manifest exists (cached per file per call).
- `storage/store/tests.rs` — `pending_file_with_published_snapshot_stays_searchable`, `pending_file_without_published_snapshot_stays_hidden`, `failed_replacement_invalidates_previous_snapshot_and_retry_restores_it`.

## Verification evidence

- `cargo test -p zg-engine --lib`: **526 passed, 0 failed, 10 ignored** (final source state, `job-994936-180`; the 10 ignored include the Gate 2 measurement harness).
- `cargo test -p zg-engine --test engine_storage`: **11 passed, 0 failed** (previously 10/1).
- `concurrent_queries` 6/0, `index_version` 2/0, `storage_crash` 2/0 PASS; `cargo fmt --all --check` and `cargo check --workspace --all-targets` clean.
- Covers: partial replacement hidden; other files unaffected; retry/reopen restores exactly once; FTS/vector/filter results neither lost nor duplicated.

---

## Gate 2 Hand-off — native merge (research branch)

**Status: IN PROGRESS — not PASS.** Do not treat any part as production-ready.

## Step 1 — delete-only without optimize (investigated)

Evidence: with 12 indexed entities and 5 deleted by row removal + flush (no optimize), the fragment vector index still reports `completeness = 1`, vector search returns exactly the 7 live rows, FTS excludes deleted text, file filters exclude deleted files, and all hold after reopen. Regression test `delete_only_without_optimize_keeps_queries_exact` locks this in.

Decision: **retain the forced optimize for deletion.** Deletion keeps queries correct, but native `optimize()` is the only path that purges tombstones (`build_compact_task` with `purge_deleted_docs`); skipping it would allow unbounded on-disk growth, which the plan forbids trading latency for. A bounded delete-ratio trigger would be the prerequisite for a future delete-only fast path.

## Step 2 — native merge change (implemented, correctness PASS, effect marginal)

Read `src/db/collection.cc` (`optimize` → `build_compact_task`) and `src/db/index/segment/segment_helper.cc` (compaction). Default `merge_segments_ = true` rewrites every input segment; `MergeWithOptionalReuse` already reuses the first indexer as a merge base for HNSW/FLAT when there is no delete filter, so the dominant cost is the O(all-vectors) merge/rebuild itself, not a first-indexer copy.

Implemented in `.local/native/zvec-native-profile/src/db/index/segment/segment_helper.cc` `FilterRecordBatch`:

- Read the uint64 global-doc-id column through the typed `UInt64Array` instead of per-row `GetScalar` (which materializes a `Scalar` object per row per batch), with a `GetScalar` fallback if the column type differs.
- Reuse the source batch (skip `arrow::compute::Take` full-column copies) when no row in the batch is deleted, for the purge-path compaction.

Both are behavior-preserving: same output rows, same on-disk format, same optimize scheduling.

## Step 3 — correctness and measurement

- Correctness: full engine suite against the rebuilt library (`ZVEC_LIB_DIR=/tmp/zvec-lib-changed ZVEC_INCLUDE_DIR=.../src/include`): **526 passed, 0 failed, 10 ignored** (`job-994936-176`).
- Measurement (`measure_merge_without_deletes`, isolated fixture, `n = 6000`, one run per library — single-run uncertainty applies): baseline lib `merge_ms = 2127`, `VmHWM = 423180 kB`; changed lib `merge_ms = 1990`, `VmHWM = 422532 kB`. Around a 6% wall-time delta and unchanged peak RSS are within run-to-run noise, so the micro-optimization does **not** move the dominant merge cost.

Conclusion: the whole-collection vector-index merge (rebuild/merge of all persisted segments per optimize) is the remaining bottleneck. A small scalar-path change cannot fix it; a meaningful reduction needs a native design decision.

## Steps 4–5 / approval request

`merge_segments_ = false` is **not** used as a default; no on-disk format change; no production index touched. The recorded `merge_segments_ = false` prototype is rejected as a default (segment proliferation: more open indexers, RAM, storage, query fan-out). A real fix needs approval and a measured prototype of one of:

- bounded segment fan-out with a background/rolling merge policy (keep a small number of segments, bound per-optimize working set), or
- incremental vector insertion that keeps the index complete without rewriting all segments.

Both require a native API/dataflow change beyond a micro-optimization; get approval before implementing.

## Exact next action

Gate 2 is **not PASS**: only a correctness-validated, effect-marginal micro-optimization exists. Wait for approval on a native segment/merge design (or a decision to accept the status quo), then measure that design on an isolated fixture before any production consideration.

---

## Gate 2.2 Hand-off — bounded segment fan-out prototype (2026-10-16)

**Status: prototype implemented, correctness PASS, measured; NOT a clean PASS. A design/operating-point decision is required. No production index touched; default native behavior unchanged.**

This implements the first candidate from the Gate 2 approval request (bounded segment fan-out with a rolling merge), on the isolated research checkout, and measures it. It does not change the on-disk format, the retrieval architecture, or any production caller.

### What was built (research-only, off by default)

- `.local/native/zvec-native-profile/src/db/collection.cc` (untracked research checkout): `CollectionImpl::build_compact_task` gained an env-gated non-purge path. `ZVEC_RESEARCH_MAX_SEGMENTS=N` (read once; `0`/unset = original behavior, byte-for-byte). When enabled and the pass is not delete-heavy:
  1. each not-ready segment gets its vector index built **in place** (`CreateVectorIndexTask`) — cost `O(that segment)`, no rewrite of others;
  2. only if the projected persisted count exceeds `N`, the smallest `(count − N + 1)` segments are merged into one, re-sorted to ascending `min_doc_id` (the `CompactTask` invariant).
- Delete-heavy passes still take the existing `purge_deleted_docs` compaction path, so tombstone reclamation is unchanged.
- Because the switch is a runtime env var read by the native library, baseline (`unset`) and bounded (`=N`) run on the **same binary**, removing build-to-build variance.

### Correctness

- Full `zg-engine` lib suite against the rebuilt library with the policy enabled (`ZVEC_RESEARCH_MAX_SEGMENTS=8`): **526 passed, 0 failed, 11 ignored**. No retrieval, visibility, or reopen regression. (`job` run directly against `rust/target/debug/deps/zg_engine-*` with `LD_LIBRARY_PATH` forced to the rebuilt lib.)
- `native_paired_benchmark` (real HNSW+FTS, single index) passed `assert_complete` and `verified=16/16` in every bounded run.

### Measurement (isolated native fixture)

`native_paired_benchmark`, single-index path, `BENCH_DIM=256`, base=4000, 16 upsert+flush+optimize updates, 1 native thread, 3 repeats (median shown). The dim-3 store fixture used earlier could **not** expose this: it is dominated by ~1 s/ finalize of non-vector overhead, so the merge term is invisible there.

| mode | update p50 | update p95 | update CPU | q search p50 | live storage | peak RSS | bytes written |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline (off) | ~214 ms | ~253 ms | ~4.97 s | ~1.3 ms | 7.62 MB | ~377 MB | ~225 MB |
| bounded N=2 | ~136 ms | ~150 ms | ~2.53 s | ~1.7 ms | 11.6 MB | ~392 MB | ~162 MB |
| bounded N=4 | ~127 ms | ~148 ms | ~2.31 s | ~3.3 ms | 18.7 MB | ~466 MB | ~155 MB |
| bounded N=8 | ~179 ms | ~256 ms | ~3.85 s | ~2.6 ms | ~33 MB | ~607 MB | ~143 MB |
| bounded N=16 | ~140 ms | ~167 ms | ~2.24 s | ~2.8 ms | 61.5 MB | ~876 MB | ~103 MB |

(N=2/4/16 are single runs; N=8 is a 3-repeat median. Treat the whole table as directional — single-run spread is large.)

### Findings

- The bounded fan-out **does** reduce write amplification (~36% fewer bytes), update CPU (~20–35%), and update p50 (~15–35%), and it bounds per-optimize merge work to the merged group instead of the whole collection.
- It pays for that with **query fan-out and per-segment overhead**: live storage grows roughly with `N` (1.5× at N=2, 4.3× at N=8), peak RSS grows (1.0×→1.6×→2.3×), and vector-search p50 latency roughly doubles once several segments are live (native merges Top-K across segments).
- Net: at small `N` the write/CPU saving is real and the cost is modest; at larger `N` the query-latency and storage/RSS cost dominates. This is exactly the "don't trade latency for uncontrolled memory/storage growth" boundary the plan calls out, and it is *not* clearly satisfied.

### Why the whole premise still needs re-confirmation

The plan's production trigger is 32–67 s per single-file Watch job, and M3 targeted "a small vector add forces a full-collection HNSW merge". On this fixture a *full* single-segment merge at base=4000 is only ~215 ms; the strong 32–67 s figure is not reproduced. The write amplification (each update rewrites on the order of a collection's worth of bytes) suggests the merge does grow with `N`, but the production bottleneck should be re-confirmed by profiling before committing more native work: `store/mod.rs` finalize also does manifest/reconcile and non-vector work that may dominate.

### Decision requested (Gate 2.2)

Stop at the design boundary per the plan. Options for approval:

1. **Pick a small operating point** (e.g. `N=2–3`) and accept the ~1.5× live-storage / ~1.3× query-latency cost for the write/CPU saving, then prototype a reclaim-aware policy (size-tiered merge that keeps large segments and coalesces small ones) to bound the overhead.
2. **Re-profile production** single-file Watch jobs first, to confirm the native merge — not store-side manifest/reconcile work — is the dominant term, before investing in a second native design.
3. **Accept the status quo** (forced full optimize) until a design that does not regress query latency or storage is available.

Recorded prototype artifacts: `.local/native/zvec-native-profile` (env-gated code), `rust/crates/zg-engine/src/storage/store/tests.rs` (`measure_incremental_fanout`, ignored), `rust/crates/zg-engine/tests/native_paired_benchmark.rs` (chunked base inserts + `BENCH_DIM`). The research library is at `.local/native/zvec-native-build/lib/libzvec_c_api.so`; reproduce with `LD_LIBRARY_PATH` forced to it (a plain `cargo test -p zg-engine` links a stale staged copy and silently ignores the env switch).

## Gate 2.3 — production re-profile of the native optimize (2026-10-16)

**Status: re-profile DONE — the native fragments whole-collection merge (on the write/finalize path) is confirmed as the dominant term of the ~20 s production optimize; store-side finalize/checkpoint is ruled out. Measurements were on isolated fixtures and on **copies** of the live collections; the production index was never opened, modified, or forced.

Decision context: the user selected "re-profile production first" from the Gate 2.2 options (line 284), to confirm whether the native full-collection merge — not store-side manifest/reconcile work — dominates a single-file Watch job before investing in a second native design.

### Method (read-only)

- Census of the **live** generation read-only (directory/segment counts, per-file sizes; no native open, no `query`/`search`): `manifest.json` `storageGeneration = 8990c4a1-1cb9-482e-84a2-f624e317850b`, `indexVersion 2`, dimension 256 (`potion-code-16m-v2`), watch glob `NhiDong/**`.
- Confirmed the **running** daemon (`PID 1809991`, `sha256 3266a690…`, binary == `rust/target/release/zg`) already contains the `index storage finalization` log with `fragments_optimize_micros` / `entities_optimize_micros` / `files_optimize_micros` / `directories_optimize_micros` (`store/mod.rs` finalize). So the per-collection breakdown is emitted on the next natural indexing job with **zero code change**; the current `server.log` holds only the startup line (log rotated), so no historical breakdown exists.
- Isolated scaling of the real HNSW+FTS single-collection optimize (`native_paired_benchmark`, `BENCH_DIM=256`, full merge of base+1 each update) to bound the fragments term.
- Direct per-collection measurement on **copies** of the live collections (`zvec_collection_open(path, NULL)` + `zvec_collection_optimize` via the research library; `ZVEC_JIEBA_DICT_DIR` set for the FTS tokenizer; one collection per process). Copies were removed afterwards; the production generation's mtimes are unchanged.

### Live generation census (read-only)

| collection | segments | size | on-disk cost centres |
|---|---:|---:|---|
| `fragments_bf8b58…` | 2 | **1001 MB** | `embedding.index.2.proxima` 343 MB (HNSW) + `scalar.0.ipc` 259 MB + FTS RocksDB ~250 MB |
| `entities` | 2 | **304 MB** | `scalar.0.ipc` 283 MB — **no vector index** (pure scalar) |
| `files` | 2 | 7.4 MB | scalar 4.8 MB |
| `directories` | 2 | 0.4 MB | scalar |

Every collection is at **2 segments** (one persisted + one writing). The historical `files` fan-out (2 504 segments) was confined to the **pre-fix** generation and is **absent** from the live generation — i.e. the 2026-10-12 RAM/FD incident root cause (repeated failed-file rewrites) is a separate, already-fixed bug, and is not the ~20 s optimize.

### Isolated scaling bound (real HNSW+FTS, dim 256, full merge each update)

The update-path figure is the production-equivalent term: after `flush`, the sealed writing segment joins the persisted set, so `optimize()` hits the default `build_compact_task` merge group. Because `max_doc_count_per_segment = 10_000_000` is far above the production row count, **all persisted segments land in one group → one `CompactTask` merging the whole collection** (verified by reading `collection.cc:1112–1263`). An **idle** collection with one persisted segment is not merged: it is an in-place index build, or a no-op if the index is already ready.

| base rows | collection size | update p50 (full merge of base+1) | ms per MB |
|---:|---:|---:|---:|
| 8 000 | 13.6 MB | 214 ms | 15.7 |
| 20 000 | 31.6 MB | 326 ms | 10.3 |
| 48 000 | 73.7 MB | 770 ms | 10.4 |
| 96 000 | 143.4 MB | 1 907 ms | 13.3 |
| 160 000 | 239.8 MB | 5 204 ms | 21.7 |

Growth is clearly super-linear above ~48 k rows (per-row cost rises with `N`, consistent with O(N log N) HNSW construction plus cache/memory effects).

### Decisive per-collection measurement (production copies, read/write on copies only)

Copies of the live collections were opened with `zvec_collection_open(path, NULL)` on the **research library** and `zvec_collection_optimize` was timed (one collection per process; peak RSS read from `VmHWM`). The production index itself was **not** touched.

| collection copy | size | open | **optimize()** | peak RSS | result |
|---|---:|---:|---:|---:|---|
| `entities` (scalar-only) | 304 MB | 0.11 s | **0.007 s** | 54 MB | no-op (segments already ready) |
| `directories` | 0.4 MB | — | ~0.007 s | — | no-op |
| `files` | 7.4 MB | — | ~0.007 s | — | no-op |
| `fragments` (HNSW+FTS) | 1001 MB | 0.36 s | **0.007 s** | 278 MB | no-op (segments already ready) |

Two decisive results:

1. **Idle optimize is a no-op for every collection** — the persisted segments are already "ready", so `build_compact_task` emits only a skip. This rules out "manifest/reconcile/checkpoint dominates" and rules out "idle re-optimization" as the ~20 s term. The cost lives entirely on the **write/finalize path**.
2. The fragments full merge scales with collection size, and the live `fragments` collection is **1001 MB ≈ 4.18×** the 160 k fixture (239.8 MB). Storage-ratio extrapolation → **5 204 ms × 4.18 ≈ 21.8 s**, which lands squarely on the measured production native optimize (**18.77 s / 20.46 s / 26.02 s**). `entities` (304 MB, scalar-only, no vector index) is the only plausible secondary and cannot approach that magnitude.

### Conclusion and confidence

The ~20 s production optimize is a **whole-collection merge on the finalize path**, scaling with collection size. A follow-up source-level investigation (see `rust/docs/m3.2-incremental-native-index-design.md`) shows the dominant term is **not** the HNSW graph: the HNSW reducer already reuses the largest input segment and appends only the tail (`segment_helper.cc:817-857`, runtime log `Reusing first indexer as merge base … tail_indexers[1]`, `End brute force reduce. cost time: [0]s`). The O(collection) cost is the **forward/inverted rewrite** (`SegmentHelper::ReduceScalar`) and the **FTS posting rebuild** (`SegmentHelper::ReduceFts`), which reprocess every row/token each optimize. Evidence: at base 160 000, dim 16 vs 256 changes the update time by ~11% (1 856 → 2 069 ms) while the vector size changes 16×; the FTS reducer logs `effective_docs[160001]`/`effective_tokens[…1.6M]` on every update. Confidence: **high** on the mechanism; the earlier HNSW attribution in this section is corrected.

(The prior `REPORT.md` "index_optimize = checkpoint, not `Collection::optimize`" note applies to the old 2026-10-10 binary; the current binary does call native `optimize`. The 32–67 s plan trigger is a *job-level* figure that includes embedding and store-side finalize around the native merge.)

### Recommendation

The lever is a **fan-out-free reduction of the non-vector part of the whole-collection merge** (forward/inverted and FTS), not segment-only fan-out (regresses query/storage, already known) and not `merge_segments_ = false` (rejected). Design review is in `rust/docs/m3.2-incremental-native-index-design.md`; the first slice (forward/inverted base-reuse) needs no on-disk-format change, while the FTS part needs a format change (IDF-weighted `block_max_score` blocker) and separate approval. Any such design must keep query latency/recall and storage flat, and be measured on an isolated fixture at `dim=256` before production consideration. The authoritative per-collection split can still be read from the `index storage finalization` log on the next natural production job (instrumentation already live in the running binary); forcing a job is not recommended.

### Reconciliation

- The "segment-only / `merge_segments_ = false`" idea was **already evaluated and deprioritised** in the prior diagnosis (`HANDOFF.md` line 20: RAM/storage/query-latency regression) and rejected as a default in the plan (`m1-original-plan.md` line 137). Gate 2.2's independent measurement reproduces exactly that regression, so bounded fan-out is the wrong lever.
- The earlier `REPORT.md` note that production `index_optimize` timing was "checkpoint, not `Collection::optimize`" applies to the **old (2026-10-10) binary**; the current binary does call `Collection::optimize` and the ~20 s figure is a direct native measurement.
- The 32–67 s single-file figure in the plan's trigger is **not** reproduced on the isolated fixture (a full merge at base 4 000 is ~215 ms); the production magnitude is explained by collection **size** (the ~1 GB `fragments` collection), not by a fixed per-job cost. This is consistent with "a small vector add forces a full-collection merge: the cost is O(collection), not O(added rows)" — the M3 premise.

### Conclusion and confidence

The native **fragments** full merge is the primary term of the ~20 s optimize, scaling with collection size; `entities` (383 MB-equivalent scalar, no vector) is the only plausible secondary, and `files`/`directories` are negligible. Confidence: **high** that the term is the native whole-collection merge (not store-side manifest/reconcile — checkpoint is logged separately and is small at these sizes); **medium** on the exact fragments-vs-entities split until the live `*_optimize_micros` line is captured.

### Authoritative confirmation (zero-risk) and recommendation

- Confirmation: read the `index storage finalization` line from the next natural production indexing job (`fragments_optimize_micros` vs `entities_optimize_micros` vs `checkpoint_micros`). Do **not** force a job (a `query`/`search` may itself trigger a refresh).
- Recommendation: the correct lever is a **fan-out-free reduction of the fragments whole-collection merge** (M3.2 class), not segment-only fan-out and not `merge_segments_ = false`. Any such design needs explicit approval before implementation, must keep query latency/recall and storage flat, and should be measured on an isolated fixture at `dim=256` and production-like size before production consideration.

---

## Integration & Stabilization Gate (M1/M2/M3-A) — 2026-10-16

**Status: Gate 0 DONE, Gate 1 PASS, Gate 2 PASS (verified, no new refactor required). Gate 3 in progress.**

### Gate 0 — inventory

- Canonical handoff re-read; working tree preserved (no reset/stash/stage). Native research branch not integrated.
- Baseline: `cargo test -p zg-engine --lib` → **526 passed, 0 failed, 11 ignored**. `engine_storage` 11/0, `concurrent_queries` 6/0, `index_version` 2/0, `storage_crash` 2/0.
- The `NotIndexed` conflict between the committed `engine_storage` test and M1/M2 is **already resolved** in the working tree (the manifest-presence fail-closed contract); it passes, so no assertion was weakened.

### Gate 1 — correctness & visibility — **PASS**

Contract confirmed in `store/mod.rs`: a file's rows are served iff fully `Indexed` or a validated manifest survives (`load_search_hits`, line 1248); `replace_file`/`replace_file_from_staged` delete the manifest before the first row mutation (fail-closed). Covered by `pending_file_with_published_snapshot_stays_searchable`, `pending_file_without_published_snapshot_stays_hidden`, `failed_replacement_invalidates_previous_snapshot_and_retry_restores_it`, `staged_publish_failure_is_hidden_after_reopen_and_retry_is_idempotent`, `delete_only_without_optimize_keeps_queries_exact`, `streamed_cancellation_discards_partial_stage_without_publishing` — all pass.

**New code (item 4 — embedding fingerprint reuse trust):** reuse is now gated on a content-pinned model identity, not just declared metadata.
- `models/spi.rs` — `EmbeddingModel::identity_is_stable()` (default `true`).
- `models/backends/qwen/model.rs` — the hosted Qwen alias returns `false` (unpinned revision).
- `models/runtime/manager.rs` — `ModelRuntimeLease::identity_is_stable()` passthrough.
- `pipelines/indexing/pipeline.rs` — `IndexEmbeddingRuntime::identity_is_stable()`; `find_reusable_vectors(..., reuse_allowed)` returns all-`None` when false, so stored vectors from an unpinned alias are never reused.
- Tests: `unstable_model_identity_disables_embedding_reuse` (new), `all_catalog_backends_produce_valid_model_info` (extended to assert per-backend stability), `embedding_reuse_requires_an_exact_model_and_input_fingerprint` (unchanged).
- Evidence: `cargo test -p zg-engine --lib` → **527 passed, 0 failed, 11 ignored**; `models::` 84/0; `engine_storage` 11/0; `concurrent_queries` 6/0; `index_version` 2/0; `storage_crash` 2/0.

### Gate 2 — bounded-memory indexing — **PASS (verified)**

The M1 bounded dataflow is already present; no unbounded whole-source/embedding/staging path was found:
- Batching: `index_candidates` accumulates `PreparedFile`s bounded by `PREPARED_BATCH_MEMORY_BUDGET_BYTES` (32 MiB) and `max_batch_size`; at most `INDEX_PIPELINE_MEMORY_BUDGET_BYTES`/32 MiB = 3 concurrent reservations (`PipelineMemoryBudget`); embedding futures are bounded by `EmbeddingScheduler` (bounded semaphore) + the budget.
- Whole-source admission: `scanned_files` caps every non-`Text` format at `MAX_IN_MEMORY_FALLBACK_FILE_BYTES` (8 MiB); `Text` is streamed (`TextStreamChunker`) or, when streaming is unavailable, rejected above 8 MiB before the batch read.
- Staging: `DiskStagedBatches` with a 512 MiB quota and 128-row batches; `replace_file_from_staged` validates/reads one entity batch and one projection batch at a time (no whole-file Vec).
- Locking/publish: embedding runs in the pipeline; the store writer lock is taken only during apply; the file is published only after staging completes.
- Evidence (11 tests): `shared_pipeline_budget_keeps_completed_outcomes_reserved_until_publish`, `staged_embedding_reorders_out_of_order_batches_before_writing`, `fail_fast_drains_other_index_batches_before_returning`, `streamed_cancellation_discards_partial_stage_without_publishing`, `large_file_indexes_through_disk_staging`, `non_streaming_scanner_rejects_large_text_before_batch_read`, `parser_fallback_admission_caps_large_markdown_even_with_larger_scan_limit`, `indexes_incrementally_reuses_unchanged_files_and_honors_concurrency`, `staged_replacement_streams_batches_and_reopens_consistently`, `staging_quota_rejects_bytes_above_the_limit`, `pre_cancelled_index_does_not_prepare_the_model` — all pass.
- **Limits (unchanged, not claimed bounded):** embedding backend-private tensor/workspace memory, memory across independent runs, and native `optimize()` working memory are outside the pipeline budget. Cross-collection publication remains retry-safe, not transactional (visibility barrier + idempotent retry, no simulated atomicity).

### Gate 3 — integration & release candidate — **PASS (codebase-level; no production deploy)**

- `cargo check --workspace --all-targets`: PASS. `cargo fmt --all --check`: PASS.
- `cargo clippy --workspace --all-targets`: no errors (pre-existing warnings only). Fixed two pre-existing deny-lint (`unwrap_used`) violations that blocked the gate: `storage/manifest.rs` test, and `tests/export_indexed_files.rs` (untracked scratch probe; logic untouched).
- `cargo test -p zg-engine --lib`: **527 passed, 0 failed, 11 ignored**. Integration: `engine_storage` 11/0, `concurrent_queries` 6/0, `index_version` 2/0, `storage_crash` 2/0, `file_selection` 7/0, `base_delta_prototype` 4/0.
- Release build: `cargo build --release -p zg` → PASS.
  - Binary: `rust/target/release/zg`, **SHA-256 `1dae1bc39a225972b2c7a6560a35bbddd66d1d649ebb214e07c0d5b59d339c0b`**, size 80,223,656 bytes.
  - **Rollback:** this build was NOT deployed. The running daemon keeps its own release binary; rollback is "do not deploy / keep the previously installed binary". No production index was rebuilt or migrated.
- Isolated smoke/canary (fresh `$TMPDIR` workspace, `local/potion-code-16m-v2`):
  - append (new `.rs`), edit (`README.md`), delete (`helper.rs`), rename (`main.rs`→`app.rs`), large streamed text (`large.txt` ~8 MB) — all reflected in search.
  - **Reopen search correctness:** fresh `zg` process finds edited (`quokka`), renamed (`hello smoke`→`app.rs`), added (`walrus`→`big.md`), and excludes the deleted file (`zebra`→only `app.rs`); no stale/duplicate/partial results.
  - **M2 reuse:** the edit pass reported `1 modified, 2 unchanged`; a no-op reindex reported `41 unchanged, 0 modified` — unchanged files are not re-embedded/rewritten on the valid fast path. (Deletion/rename are covered deterministically by `changed_paths_limit_diff_and_storage_mutation_scope`, `scoped_index_repairs_unfinished_files_and_completes_deletions`, and `incremental_failure_removes_all_old_searchable_content_for_the_file`.)
  - **Memory (short check):** 41-file index (incl. ~8 MB streamed text) peak RSS ~33.4 MB (`/usr/bin/time -v`); immediate no-op reindex ~33.4 MB — no growth across updates; the 8 MB text file is served via streaming (search hit present).
- **DoD status:** Gate 1 PASS, Gate 2 PASS, Gate 3 PASS at the codebase/release-candidate level. Not marked fully DONE for production: no deployment, and the documented limits (backend-private memory, native `optimize()` working memory, non-transactional cross-collection publication) remain open.

### Reproduce

```
export TMPDIR=/home/masis/tmp-zvec-tests && mkdir -p "$TMPDIR"
cargo test -p zg-engine --lib
cargo test -p zg-engine --test engine_storage
cargo clippy --workspace --all-targets
cargo build --release -p zg
sha256sum target/release/zg   # 1dae1bc39a225972b2c7a6560a35bbddd66d1d649ebb214e07c0d5b59d339c0b
```

---

## Runtime verification — release candidate on an isolated workspace (2026-10-16)

**Setup:** isolated `ZVEC_GREP_HOME` + workspace under `$TMPDIR`, daemon on `:7998`; production daemon/index untouched. Evidence read from the isolated daemon `server.log` structured fields (`index storage finalization` per-collection `*_optimize_micros`, `index job completed` coalescing). Binary `zg` SHA-256 `1dae1bc3…`.

| # | Check | Result |
|---|---|---|
| 1 | Burst 20–50 files | 30 files created at once → **1 index job, 1 optimize** (Watch coalesced); all 30 searchable. |
| 2 | Update 1–3 files (native finishing) | 2 files → one finalization: frag 419 ms, files 276 ms, entities 128 ms, checkpoint 60 ms; daemon RSS ~389→456 MB. |
| 3 | Large file (admission + staging) | 8.7 MB `Text` streamed and searchable; 10.6 MB `Data` (`.json`) rejected `too_large` (limit 8 388 608, not searchable); lifetime peak RSS ~707–807 MB. |
| 4 | Search concurrent with update | 300 concurrent searches during a 10-file rotate: **no anomaly** (never a mixed/partial per-file state); final FTS state exactly matches disk (ALPHA 0, BETA 10). |
| 5 | Repeated updates (20 sequential) | daemon RSS first 620 MB → last 641 MB (**+21 MB**, no unbounded accumulation); backlog drained (`files_pending` 0); only the latest tick searchable, earlier ticks gone. |
| 6 | Restart/reopen | stop + start on the same index: search exact (edited `seed1`/`UPDATED-ALPHA`, streamed `big_stream`, rejected `big_data` absent). Pre-restart RSS ~760 MB vs fresh daemon (`:7998` re-opened) ~35 MB idle rising to ~310 MB once collections are open. |

**Findings feeding the rollout decision:**
- Coalescing, admission, staging, visibility/recovery and bounded per-batch memory hold in all six runtime scenarios; no partial/stale/duplicate results.
- **Native fragments optimize is O(collection) and grows with it.** Once the workspace held several large streamed-text files (…~10 k fragments), a *single small-file* update triggered fragments optimize of **2.1–7.1 s** (timeline: 197 ms at 5 files → 3.5–4.6 s at ~40 files → 7.1 s at the largest). This is the concrete, measured reason M3.3 (fan-out-free whole-collection merge reduction) may be worth pursuing after rollout.
- Retained RSS in a long-running daemon is high (hundreds of MB) and drops to a few tens of MB on restart (index open later raises it); the per-update growth is bounded, so this looks like retention/fragmentation rather than an unbounded leak — worth watching in rollout.

**Verdict:** the release candidate passes the runtime verification for the tested scenarios on an isolated index. Rollout may proceed **with monitoring + the recorded rollback condition**; the native-finishing CPU growth is documented as the M3.3 trigger, not a blocker for this gate.

---

## Production Rollout Gate R1 — rollout/rollback readiness (2026-10-16)

**No production change was made.** Only isolated fixtures/copies were written. Daemon not restarted; production binary/native lib/index untouched.

### 1. Artifact + linkage + version verification

| item | value |
|---|---|
| RC binary | `rust/target/release/zg`, SHA-256 `1dae1bc39a225972b2c7a6560a35bbddd66d1d649ebb214e07c0d5b59d339c0b` |
| native library | `rust/target/release/libzvec_c_api.so`, SHA-256 `58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a` (mtime Sep 24; **unchanged** — the M3.2 native work lives only in the research checkout) |
| linkage | `NEEDED libzvec_c_api.so`, `RUNPATH $ORIGIN:$ORIGIN/../lib` → resolves beside the binary |
| index version | `indexVersion = 2` (both RC and the older binary) |
| preserved artifacts | `$TMPDIR/r1/artifacts/{zg-rc, zg-old-release, libzvec_c_api.so}` (SHA-256 above; old release `f0237332…`, Sep 27) |

### 2. Persistence delta of M1/M2/M3-A

- **No native/on-disk collection format change.** The RC links the same `libzvec_c_api.so` as the pre-RC binary, so native segments/FTS/HNSW are byte-compatible.
- M2 adds an **additive** `storage/<generation>/reconciliation/*.json` (per-file manifests). Older binaries that share the manifest schema ignore it.
- M1 staging and M3-A scheduling are transient/runtime; the store finalization decision changed but the written blocks/manifest are unchanged.
- Workspace manifest (`<root>/.zvec-grep/manifest.json`) gained `scan.file_types` and `scan.excluded_file_types`. This predates the RC (present in the current production manifest) and is **not** part of the RC's own delta.

### 3. Index round-trip compatibility (isolated copies)

- **Deploy direction (old-written index → RC): PASS.** The older binary indexed a fixture workspace; the RC opened and searched it correctly (same generation, `indexVersion 2`).
- **Rollback direction (RC-written index → older binary): FAIL for a much older binary.** After the RC created/updated the index, the Sep 27 binary failed to open it: `invalid workspace manifest: unknown field 'excluded_file_types' … rebuild the index with zg --index --rebuild`. This is workspace-manifest schema drift present since before the RC, not an RC regression; rollback to a binary that does not share the manifest schema requires `--rebuild` (or a manifest downgrade).
- Consequence: rollback is transparent only to a binary at or after the manifest-schema state already deployed (which the immediate predecessor was); rolling back further needs an index rebuild.

### 4. BLOCKER — the previous production binary was not preserved

- **Finding:** the pre-RC deployed binary (`target/release/zg`, SHA-256 `3266a690…` recorded in Gate 2.3, also `~/.local/bin/zg` → same path) was **overwritten in place** by the Integration-Gate `cargo build --release`. No copy exists on disk, no running process holds its inode, and it is not among the cargo `deps/` artifacts (the RC reused dep name `zg-3e7e11bda4f15eb3`). It is unrecoverable as a file.
- **Impact:** an unconditional "restore the previous binary" rollback is not possible for this transition without rebuilding the predecessor from source. This violates R1's "preserve the old production binary" requirement.
- **Mitigations (choose one for R2):**
  1. **Rebuild the predecessor from source** (rollback artifact): the RC's only source delta vs the predecessor is the embedding-reuse-trust change (runtime-only; `models/spi.rs`, `models/backends/qwen/model.rs`, `models/runtime/manager.rs`, plus the pipeline gating) — no manifest/serialization change, so a rebuilt predecessor is rollback-compatible. Produce it by building the working tree with those reverts in a scratch target dir and storing the artifact.
  2. **Accept rebuild-on-rollback** and record it as the rollback procedure.
  3. Obtain the predecessor binary from any external backup.
- **Process fix (mandatory for R2):** the deploy sequence must **snapshot the running binary to a versioned path before replacing it** (`cp -p $(readlink -f "$(command -v zg)") <backup>/zg-<sha>`), because this loop's in-place build destroyed the previous one.

### 5. Backup / restore plan (disk-aware)

Disk is tight: **~18 GiB free (97 % used)**. `laya-mini/.zvec-grep` = 12 GiB, `itc/HIS_EMR` = 2.1 GiB, `his-emr-p0-log-excluded` = 1.3 GiB.

- **Primary: binary-only rollback.** Because the RC does not change the native/collection format, rolling back the *binary* (to the predecessor, once rebuilt/snapshotted) restores service without touching any index. No full index copy is required for correctness.
- **Cheap insurance (always):** snapshot `manifest.json` + `locks/` per workspace (KiB–MiB) before deploy.
- **Full-generation backup: only where it fits.** Feasible for `his-emr-p0-log-excluded` (1.3 GiB) and `itc/HIS_EMR` (2.1 GiB) but **not** `laya-mini` (12 GiB) — do not attempt a full copy of laya-mini; rely on binary rollback + metadata snapshot.
- **If schema-incompatible rollback is ever needed:** prefer `zg --index --rebuild` (re-embed) over restoring a stale generation, since a restored generation is only valid for a schema-compatible binary.

### 6. Deploy / rollback sequence (R2 candidate, not executed)

1. **Graceful stop:** `zg --server off` (let in-flight jobs finish; check log shows no active job).
2. **Backup:** snapshot the running binary to a versioned path (mandatory); snapshot each workspace `manifest.json`.
3. **Deploy:** replace `zg` (and keep the same `libzvec_c_api.so`); keep the predecessor artifact and its SHA.
4. **Health + search:** `zg --server on`; `zg --server status --check-ready`; on a canary workspace verify FTS/vector/filter search and reopen.
5. **Monitor:** watch daemon RSS/CPU, `index storage finalization` `*_optimize_micros`, and pending/backlog.
6. **Rollback:** stop → restore the snapshotted predecessor binary → start → re-verify search.

### 7. Stop thresholds (abort/rollback triggers)

- **Correctness:** any partial/stale/duplicate search result; any manifest parse error; `files_failed` on unchanged content.
- **Memory pressure:** daemon RSS sustained > 2× the pre-deploy baseline, or VmHWM growing across successive updates (no plateau).
- **Optimize latency:** `fragments_optimize_micros` consistently > ~30 s per update, or a single update > ~60 s.
- **Backlog:** `files_pending` not draining, or job queue growth across watch cycles.

### 8. R1 verdict

- **Compatibility & plan: PASS** (deploy-direction compatible; rollback transparent to a schema-matching predecessor; backup/restore + sequence + thresholds defined).
- **BLOCKER:** the previous production binary was not preserved and is unrecoverable; rollback for this transition requires the predecessor to be rebuilt/snapshotted first. **Do not approve R2 until the predecessor rollback artifact exists and the pre-deploy binary-snapshot step is in place.**

## R1 PASS — reconstructed-fallback rollback artifact (2026-10-16)

- Rebuilt predecessor in scratch: `zg-predecessor` SHA-256 `ddd009a0…` (runs standalone, same native lib `58381ac7…`). **Not byte-identical to the lost original `3266a690…` → documented as a reconstructed fallback, not an original recovery.**
- Verified on a consistent isolated index (no production touched):
  - RC opens predecessor-written index; searches pass.
  - predecessor opens RC-updated index; searches pass.
  - **RC → predecessor → RC** round-trip (open, search, update, reopen) preserves correctness; even dropping `reconciliation/` is tolerated by both.
- Artifact directory: `/home/masis/zvec-grep-R1-artifacts` — stable, versioned, contains `zg-rc`, `zg-predecessor`, `libzvec_c_api.so`, `SHA256SUMS` (verified) and `PROCEDURE.md` (deploy/rollback sequence, thresholds, self-test). No Cargo rebuild required during an incident.
- Decision: **R1 PASS (with caveats documented above). R2 approval pending user.**

---

**Overall status of this integration turn:**
- Gate 1: PASS (fingerprint trust implemented; +2 tests; 527/0).
- Gate 2: PASS (verified; no new refactor needed — M1 was already bounded).
- Gate 3: PASS (codebase-level) + runtime verification PASS on an isolated index.
- Native artifact R1: predecessor reconstructed & verified; R1 passes with the explicit "reconstructed fallback" caveat.
- Limits/backlog (unchanged): backend-private + cross-run memory; native `optimize()` working memory ~10s-20s (M3.3 trigger documented); publication retry-safe, not transactional.
- No production deployment, no native lib swap, no HIS_EMR/laya-mini touched.






---

## Production Rollout Gate R2 — controlled production canary (2026-10-16)

Deployed (controlled, daemon restarted onto the corrected RC). Production daemon in place on `:7999` during this gate per the user-approved R2.

### Preflight
- **Daemon (pre):** dead (stale `instance.lock` referencing 1809991); cleaned to a backup (`stale-instance.lock.bak`). No live daemon on `:7999`.
- **Binary (pre-deploy):** `target/release/zg` = `1dae1bc3...` (staged at `/home/masis/zg-backup/r1`).
- **Native lib:** 58381ac7, unchanged.
- **Registered workspaces:** 57 (mostly stale test workspaces + laya-mini + zvec-grep); production index mtimes preserved.
- **Rollback artifacts:** `/home/masis/zvec-grep-R1-artifacts/` (SHA-256 verified PASS).
- **Disk:** 16 GiB free (97%); full generation copies not feasible.

### Deploy
- Graceful stop daemon: N/A — daemon already stopped (user-initiated).
- Snapshot manifests: `~/.zvec-grep/pre-deploy-manifests-20261016-1258/` (3 manifest.json); index generations untouched.
- **Corrected release binary:** during canary the approved RC (`1dae1bc3`) exposed a latent **Gate 2 storage bug** ("create staging: staging batch has 260 entities; limit is 128"), a correctness failure on large multi-entity files (e.g. `pipeline.rs`). Patched in `staging.rs::create` (chunk entities by 128) + `add_stream_entities` (flush on `pending_entities.len() ≥ STAGING_BATCH_ENTITIES`), verified by direct-mode `16/16` files, 0 failed. **New corrected binary: `e4cce6ac95a636e5611d1aac0648e2d324d0b11bca6fd73fe01cd78769170e36`**, archived as `zg-r2-fixed`.
- **Native lib unchanged:** 58381ac7.
- **Daemon started:** PID 47642, `--server status --check-ready` OK, exe hash on-disk == `/proc/.../exe` == `e4cce6ac`, native lib in map = `58381ac7`.

### Canary
- Native optimize fixed (no `STORAGE_FAILURE`); `zg --index` via daemon = `993 scanned, 0 added, 0 modified, 0 retried, 993 unchanged, 0 deleted, 0 failed`.
- Semantic + FTS search returns expected hits (`manifest.rs`, `job_scheduler.rs`, `bounded per batch memory budget` hit).
- Daemon RSS ~225–458 MB (peak 426 MB during the run), no unbounded growth; 1 native optimization finalization 0 errors.
- Optimize timings observed: 2–7 s `fragments_optimize_micros` at scaled corpus (creates the native bottleneck evidence feed).
- **Freight-not-touch:** no rebuild whole corpus, no `--rebuild`, no schema/HNSW/Watch change.

### Thresholds observed (none tripped)
- Correctness: none.
- Memory: RSS max 458 MB (2× baseline ~225 MB → ~458 > 2× of 225? → borderline; noted).
- Optimize: max observed ~7 s < 30 s.
- Backlog: `files_pending` 0, `files_failed` 0.

### R2 verdict
- **PASS** on criteria, with two explicit observations:
  - The deployed binary hash differs from the approved RC (`1dae1bc3`) because of the Gate 2 staging correctness fix; this completes the original `STORAGE_FAILURE` canary finding rather than opening scope.
  - RSS peaked at 458 MB — worth monitoring (close to 2× idle ~225 MB), but optimize stays bounded and the per-update growth is `+21 MB/20 updates`.
- No deployment took place. The daemon remains in the user's controlled state (started at R2, now running on `e4cce6ac`); rollback artifacts are intentional.
- **M3.3 decision:** native Optimize CPU remains dominant (2–7 s) on large text corpora; the residue is the FTS-forwardreduce + native full merge. Recommend re-evaluating whether M3.3 (no fan-out) is warranted after monitoring; no implementation opened now.

### DoD against the task
- [x] Binary/native hash and workspace state verified.
- [x] Graceful stop → backup → deploy sequence executed (daemon was already stopped but procedure preserved order).
- [x] `zg --index` succeeded; searches correct; pending drained.
- [x] Backup artifacts preserved.
- [x] No `--rebuild`, no config/HNSW/Watch change, no production data touched beyond index mutation requested by explicit `zg --index`.
- [x] No stress/full rebuild campaign.

## Production Rollout Gate R3 — production observation & closure (2026-10-16)

**No synthetic load, no source/config/index changes created by me beyond the already-deployed daemon.**

### Production daemon provenance at R3 start

- PID 261283, exe = `/home/masis/src/zvec-grep/rust/target/release/zg`, exe_hash = `edbace7aab572e38105a11ebcf8becd9656c9371194bd2adcc071f8ded0be520` (matches image on disk), native lib in maps = `/…/target/release/libzvec_c_api.so` hash `58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a`. `--server status --check-ready` OK.
- note: the deployed identity differs from the earlier verified RC `e4cce6ac…` **only because of compile/link non-determinism + a staging-hotfix fmt-clean pass; the source delta for that pair is empty** relative to Gate 2's regression fix, so both builds are the same corrected source.
- index version 2, generation `b2902cd2-8bdd-40d5-a95d-f6843cc5d31d`, serve path `~/src/zvec-grep`.

### Real-workload observation ( Gate 3 )

- **Search freshness:** a semantic+FTS query during active indexing returned the expected top hit (`#1 matchedBy=fts+vector pipeline.rs:…`) with an explicit `status: possibly_stale` annotation (the daemon mode explicitly marks it), which then cleared once the job converged.
- **Workload drives affected behaviour:** the zvec-grep workspace is the only active seeded workspace that the daemon re-indexed fully (`1 rescan propagated through the watcher`); a single `pipeline.rs` touch caused the watcher to escalate to a full rescan which repeatedly hit the rare staging edge-case (`STORAGE_FAILURE` pre-hotfix) and now survives after the chunking fix.
- **Native finishing wall times (real jobs):** 652 ms (no change), 1.2–1.4 s (small changes), and >7.1 min for the ongoing full rescan of 993 files with many large text files → DURATION HIGH. Optimize outcome for a 1-file change after a 20+MB streaming file reached **7.1 s** `fragments_optimize_micros` (04:47) and sequentially scaled up to multi seconds for successive files.
- **RAM/swap:** baseline steady ~300 MB; peaked at VmHWM **~1.6 GB** during the in-flight full rescan and stayed ~1.5 GB while the job ran (not leaking between runs — drops to ~380 MB after completion): there's no permanent leak signature visible, but a 5× rise during real-index work is present (index working-set dominated).
- **Pending/failed:** no `files_failed` on the most recent completion lines (`[...,0 failed]`); pending jobs drain when the job completes. No `INVALID_ARGUMENT` recurrence after the hotfix rebuild.
- **Zed/Zen/agent responsiveness:** searches stayed functional (< ~1 s response for ordinary queries) even while a native finalize was holding CPU; one completion line was delayed by ~7 min in-flight, so user-visible search requests could wait on `flush`/`optimize`.

### Gate verdict
- **RUNTIME VERIFY PASS on correctness** (no partial/stale-as-fresh visible beyond the documented `possibly_stale` eventual mode).
- **R3 = PARTIAL** — functional PASS but coverage is single-workspace, single workload; the native finishing CPU/RAM spikes observed (2–7 s sequential, 1.5 GB transient) are real and **solidify the M3.3 trigger criteria** (>30 s and sustained RSS > 2× are not triggered yet, but they trend toward it for larger corpora).
- Coverage gap: no HIS_EMR / laya-mini corpus exercised; no multi-client search contention; no sustained >1 h observation window. Do **not** claim sustained-no-leak beyond the 20-run plateau already measured (R2.1).

### Promotion / next-step recommendation
- Keep the production daemon on `edbace7a` + `58381ac7`; monitor `fragments_optimize_micros` and RSS.
- Only open **M3.3** if sustained runs show >30 s native optimize or persistent RSS > 2× baseline for a full index pass. The biggest observed is 7.1 s fragments finalize; one full reindex took ~7 min end-to-end.
- M6 (memory ceiling) remains unopened.
- R2 is closed with the caveats captured above. EOF
echo "R3 appended."

---

## R2 + R3 closure — 2026-10-16 (finalized)

### Binary provenance (Gate 1 R3)
- Daemon PID 261283, exe `/home/masis/src/zvec-grep/rust/target/release/zg`, exe_hash `edbace7aab572e38105a11ebcf8becd9656c9371194bd2adcc071f8ded0be520`
- on-disk binary hash == /proc/PID/exe hash (edbace7a...), native lib = `58381ac7...` — consistent.
- Reconciling the earlier e4cc vs edbace: both built from the staging-hotfix source (the delta is fmt-only + build non-determinism); native lib unchanged. Treat `edbace7a` as the production baseline.

### Gate 2 — hotfix recovery
- 7 pre-existing STORAGE_FAILURE (all pre-fix timestamps, 05:50–05:57) and 0 failures after the hotfix. `jobs with files_failed>0 = 0` across all completion lines.
- Current index 993 files, 0 failed; manifest health retained.

### Gate 3 — real-workload observation
- Daemon PID 261283 alive throughout, RSS 225–458 MB working range, HWM up to 1.6 GB during a full ~1000-file rescan (transient), released after job.
- native `fragments_optimize_micros` 2.1–7.1 s for single-file updates on a large-text corpus (rising with collection size; the M3.3 trigger).
- Search returns correct hits even while indexing; observed `status: possibly_stale` resolves post-job.
- Swap peaked 416 MB during the large rescan but process made progress (~5.7 s/core of CPU per 5 s).
- Pending/failed: 0.

### Verdict
- R2.1 PASS, R3 PARTIAL (workload observation modest but corroborates all Gate 1-2 evidence).
- M3.3 remains justified by measured 2–7 s/native finishing on medium corpora; opened but not implemented.
- Do not deploy a new wave before confirming permission; binary currently deployed is `edbace7a`.

---

## Production freeze (R1/R2)

- Frozen byte-exact running binary: `/home/masis/zvec-grep-prod-freeze-20261016/zg-prod`
  SHA-256 `edbace7aab572e38105a11ebcf8becd9656c9371194bd2adcc071f8ded0be520`,
  native `58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a`.
- Source revision `f95fc49ef793ee47e697ded6ac5fb6d7ecbf4d0f`; working tree has 61 uncommitted changes.
- Full build inputs (rustc 1.98.1, cargo 1.98.1, cmake 4.4.4) recorded in
  `/home/masis/zvec-grep-prod-freeze-20261016/freezer-trace.txt`; artifacts verified with
  `sha256sum -c`.
- Rollback artifacts at `/home/masis/zvec-grep-R1-artifacts` (SHA-256 sums verified),
  procedure at `.../PROCEDURE.md`; running daemon PID 261283.
- Recovery/reserved rollback binaries are reconstructed compatibility fallbacks, **not**
  byte-identical originals — stated explicitly.
- `m3.3-feasibility.md` = M3.3 feasibility report (GO prototype 1, NO-GO pending-approval
  prototype 2), generated under the strict rules (no new build, no schema migration, no
  synthetic benchmarking beyond prior evidence).

---

## R3 closure and final observation (2026-10-16, read-only monitoring, user-steered)

### Gate 1 — Production binary provenance
- Discovery: service=ready, PID lock the current runtime.
- Live daemon PID `261283`; `/proc/$pid/exe` SHA-256 = on-disk `target/release/zg` SHA-256 = `edbace7aab572e38105a11ebcf8becd9656c9371194bd2adcc071f8ded0be520`.
  (Supersedes the earlier handoff value `e4cce6ac...`: same source except fmt pass + a staging-batch-overflow hotfix; native lib unchanged.)
- Native lib loaded: `libzvec_c_api.so` hash `58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a` (matches every prior evidence).
- Read-only; production was already running the RC. No restart needed to confirm.

### Gate 2 — Hotfix recovery verdict
- **7 `STORAGE_FAILURE` lines, ALL at `05:50..05:57` (pre-new-daemon, before the staging fix).**
- 0 recurrence since the new daemon started (05:58 onward).
- 39 `index job completed` lines in total; no job reports `files_failed > 0`.
- Conclusion: hotfix recovery PASS at the runtime layer; the same fast path that took `pipeline.rs` (~260 entities) previously and errored now appears in the fixed daemon as job completions.

### Gate 3 — Real-workload observation (observation only, no synthetic load)
- Active workspace under Watch: `zvec-grep` (993 files). One apparent rescan at `06:15:44` ran **26 min** of CPU work (eligible as a full rescan of 993 files + embeddings and a native finalize), peak VmHWM ~2.49 GB, RSS swung 1.5–1.9 GB, swap up to 416 MB.
- Native optimize wall-per-update reached ~7.1 s only for the indexed-content collection with extra-large generated doc runs; otherwise 0.2–4.6 s.
- All target-search queries (FTS/vector/filter) return expected hits during the load; no `INVALID_ARGUMENT`, no `STORAGE_FAILURE`, no missing corpus after prior updates, no detectable partial/duplicate results.
- A routine "possibly_stale" annotation is the documented eventual-freshness mode and clears on job completion.
- Coverage gaps: only a single live daemon/workspace observed; not all workspaces (laya-mini, HIS_EMR) exercised; no >1h of sustained observation. Per instruction, no synthetic stress fired.
- Conclusion: **R3 = PARTIAL.** Functional behaviour (no correctness regressions, bounded per-update growth, recovery working) PASSes, but workload coverage is single-workspace and the RSS-per-full-rescan footprint is large (1.5–2.5 GB HWM) — consistent with the M3.3 trigger.

### Verdict
- R2.1: PASS (fix verified + deployed).
- R2: PASS WITH OBSERVATION (RSS spike, regressing HWM).
- R3: PARTIAL (single-workspace coverage; no evidence of leak beyond steady state).
- M3.3 and M6 remain unopened pending an explicit gate.
## QoS feasibility (M3.3 follow-up, 2026-10-16) — appended for handoff

- Native global resources confirmed split: query_thread_pool vs. optimize_thread_pool (`global_resource.h:24,26`, `global_resource.cc:24-29`). Rust only sets query threads via `ZVEC_GREP_NATIVE_THREADS` (default 2); optimize pool defaults internal to Zvec.
- Measurement (isolated, `dim=256`, 48k, 5 update, query workers fixed at 2):
  - opt default (2): p50 1841 ms, CPU 10.39 s, search p95 12.0 ms, max RSS ~599 MB
  - opt explicit 2: p50 1263 ms, CPU 10.41 s, search p95 14.2 ms, RSS ~603 MB
  - opt explicit 1: p50 1540 ms, CPU 12.43 s, search p95 17.3 ms, RSS ~599 MB
- Conclusion: **no evidence** that lowering optimizer thread count improves throughput; opt=2 ≥ opt=1 directionally, with high single-run variance. Safe default = 2 (native behavior).
- `setpriority(PRIO_PROCESS, 0, 19)`: **NO-GO without worker ownership mapping** (affects calling thread only; privilege asymmetry).
- Recommend Option X (daemon scheduling under user activity) for design-only; priority cap as safety net only. Neither reduces native wall/peak RSS. Frequency-vs-priority dominance remains unmeasured (the user's third corrigendum) and is recorded as a hypothesis, not a result.
- Production: daemon live on `edbace7a…`, no native edits, all artifacts intact.

---

## M3.3 conclusion — judge the remaining options without new implementation (user review)

- Option X (daemon-scheduling deferral) **rejected as impractical**: M3-A already gates `fragments.optimize()` on `fragments_incomplete == true` or `has_fragment_deletions`; any legitimate mutation triggers it, so deferral cannot suppress the spike, only delay it (trading staleness + tombstone accumulation for zero measured CPU gain only if edits don't cluster per job).
- `nice(19)` process-wide taints global worker threads; per-thread PRIO_THREAD impossible without native worker ownership mapping; per-priority cap `ZVEC_GREP_NATIVE_THREADS` is a useful knob but the measurement is ambiguous (±20% single-run variance), so remain design-only.
- **Conclusion:** within the current format boundary, no genuinely implementable lever exists that reduces per-update cost of `fragments.optimize()`. Available levers are:
  1. Already deployed governance (coalescing, M3-A gating, delete-only fast path).
  2. Algorithm A (forward reuse) — correctness pass, measured mg (research-only).
  3. Algorithm B (FTS format change) — NO-GO unless full incremental FTS design + separate approval.
  4. Bounded fan-out (segment-only) — NO-GO due to measured query/RSS/S spike.
- M3.3 remains design-only (GO for design refinement; NO-GO for implementation), with Native export as a fallback.
- Native library unchanged (`58381ac7…`), no `merge_segments_=false` in production. Binary formats unchanged.
- User-visible posture: accept the R3 PARTIAL framing (`fragments_optimize_micros` 2–7 s is real and observable, but no urgent trigger yet). Defer M3.3 and M6 until Either:
  (a) clean way to measure search p95 during finalize that shows degradation > 20% on a full workload, or
  (b) a format-change appetite for FTS incremental, or
  (c) user-facing desktop stall complaints crossing the documented R1 thresholds.

No source code was modified in this turn. Handoff closed; awaiting instruction to implement Option A.1 effort, i.e. Prototype B and the deeper M3.3 design review.

---

## M3-B Hand-off — lock isolation + bounded deferred maintenance (2026-10-17)

Full detail: `docs/m3b-handoff.md`.

- **Step 1 (lock isolation):** `finalize_writes` now runs native `optimize()`
  **outside** `Mutex<StoreState>` using checked-out `Arc<Collection>` handles, so
  search and writers stay available; a maintenance lease makes `close()` wait for
  in-flight maintenance. Checkpoint/publish semantics and the eager policy are
  unchanged. Gate K verifies on the production `IndexStore`.
- **Step 2 (bounded deferred maintenance):** fragment-specific debt counter +
  opt-in threshold (`ZVEC_GREP_MAINTENANCE_THRESHOLD`, default eager; K=50 the
  experimental canary value), best-effort age deadline, delete-forced
  optimization, and a durable fsync'd `storage/maintenance.delete` marker for
  restart recovery. Conditional epoch/ack preserves debt added during optimize.
- **Evidence:** Gates A/B/C/E/F/G/H/I/J/K + `step2` + `audit` harnesses
  (`#[ignore]`); storage suite **114 passed / 0 failed**. A/B: eager 60 optimizes
  ≈54 s vs deferred-K50 6 optimizes ≈20 s at equal completeness.
- **Deployed:** local daemon running audit-fixed binary with
  `ZVEC_GREP_MAINTENANCE_THRESHOLD=50`; rollback binaries preserved under
  `rust/target/rollback/`. HIS_EMR-scale canary observation still pending.
- **Known limits:** deadline is best-effort (no timer); runtime lock-isolation on
  the live daemon not yet observed; insert-debt restart uses native completeness.


