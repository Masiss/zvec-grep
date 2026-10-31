# P0 baseline and WIP checklist — 2026-10-27

**Status: DONE for P0 only.** The named WIP slices were inventoried, focused checks were run, and current documentation was reconciled with the latest M4 checkpoint. P1 baselining, performance experiments, production changes, staging, and commits were not part of this execution.

## Baseline identity

- Captured: 2026-10-27 13:31 +07:00, branch `work/zg-optimization`.
- Current `HEAD`: `d3b2a95bb238bce47e2dcb87034e5600d9de56e0`.
- The plan's original drafting snapshot was `eac1f5672b4f36e26584124f26a6ad5c27954a95`; it is historical, not the execution baseline.
- Tracked working-tree patch: **49 paths**, 2,572 insertions / 622 deletions. SHA-256 of `git diff --binary HEAD`: `deeb20155e5bdcc357333a5b3d7691d35dadef7bee30d710b28ad53ae10ae0b9`.
- At capture, `git ls-files --others --exclude-standard` reported **7,982 untracked paths** across the workspace (many existing M4 results/build/data files). They were not staged, deleted, or included in the tracked-patch digest. This report itself was added afterward and is therefore outside that captured untracked count too.
- No live/production index or data was touched; no performance benchmark operation was launched during this P0 pass. Test scratch/temp was pointed at repository-root `.tmp`, with this pass's output under `.tmp/m4-phase0`; pre-existing scratch was not swept.

The digest covers tracked changes only. The source manifest below identifies all tracked modified paths and the relevant untracked source trees; it intentionally does not hash or enumerate the large user-owned result/data trees.

### Tracked path manifest

```text
AGENTS.md
README.md
docs/README.md
research/m4-native/AGENTS.md
research/m4-native/README.md
research/m4-native/docs/benchmark-policy.json
research/m4-native/docs/benchmark-policy.md
research/m4-native/include/m4/index.h
research/m4-native/include/m4/rust_file_adapter.h
research/m4-native/results/ACTIVE-EXECUTION.md
research/m4-native/results/CANDIDATE-EVIDENCE-AUDIT-20261025.md
research/m4-native/results/M4-GATE-B-PREFLIGHT-20261026.md
research/m4-native/results/M4-RECOVERY-EXECUTION-PLAN-20261027.md
research/m4-native/results/M4-RECOVERY-GATE-1-2-20261027.md
research/m4-native/results/RESULTS.md
research/m4-native/results/immutable-write-amplification/ANALYSIS-20261024-quota-followup.md
research/m4-native/results/immutable-write-amplification/ANALYSIS-20261025-wal-replay-memory.md
research/m4-native/results/lock-contention-microbench/latest-operation.json
research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/capture_host_baseline.py
research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/run_host_carryover_aa.py
research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/run_sync_reduction_pair.py
research/m4-native/rust-bridge/src/lib.rs
research/m4-native/rust-bridge/tests/file_publication.rs
research/m4-native/src/index.cc
research/m4-native/src/rust_file_adapter.cc
research/m4-native/tests/m4_bridge_integration.cc
research/m4-native/tests/m4_compaction_writer_contention.cc
research/m4-native/tests/m4_correctness.cc
research/m4-native/tests/native_m4_paired_benchmark.cc
research/m4-native/tests/test_profile_native_m4_paired.py
research/m4-native/tools/m4_containment_runner.py
research/m4-native/tools/run_rust_lifecycle_pair.py
research/m4-native/tools/run_shared_collection_pair.py
research/m4-native/tools/test_m4_containment_runner.py
research/m4-native/tools/test_m4_contention_policy.py
research/m4-native/tools/test_run_shared_collection_pair.py
rust/crates/zg-daemon/src/job_scheduler.rs
rust/crates/zg-engine/src/pipelines/indexed_search/m4_integration_tests.rs
rust/crates/zg-engine/src/pipelines/indexed_search/pipeline.rs
rust/crates/zg-engine/src/pipelines/indexed_search/storage.rs
rust/crates/zg-engine/src/storage/entities/mod.rs
rust/crates/zg-engine/src/storage/files/mod.rs
rust/crates/zg-engine/src/storage/fragments/mod.rs
rust/crates/zg-engine/src/storage/fragments/tests.rs
rust/crates/zg-engine/src/storage/store/mod.rs
rust/crates/zg-engine/src/storage/store/tests.rs
rust/crates/zg-engine/src/storage/zvec.rs
rust/docs/audit-20261025.md
rust/docs/m4-incremental-native-index.md
```

Relevant untracked source additions present at capture:

- `docs/technical/*.md` (technical guide, system maps, and proposal); this manifest is the newly added Phase-0 entry.
- `research/m4-native/src/index_test_access.h`, `research/m4-native/tests/{m4_benchmark.cc,native_*}`, `research/m4-native/tools/{run_scalar_open_latency_diagnostic.py,test_run_rust_lifecycle_pair.py}`.
- `rust/crates/zg-engine/src/pipelines/indexed_search/m4_integration_tests/gate_b_lifecycle.rs`.
- `rust/docs/audit-20261025-scripts/*.py`.

All other untracked data, receipts, binaries, local scratch, and private files remain untouched and outside this manifest.

## WIP inventory and verification

| Slice | Status at this working revision | Evidence / disposition |
| --- | --- | --- |
| Scheduler/watch coalescing | **implemented; focused regression verified** | `job_scheduler.rs`; `watch_coalescing_is_bounded_and_trailing`: 1 passed. Existing coalescing bounds remain; the added submission telemetry was not separately asserted by that test. |
| Primary-key fragment deletion | **implemented; focused regression verified** | `storage/fragments/{mod.rs,tests.rs}`; `deleting_fragment_ids_by_primary_key_is_idempotent`: 1 passed with `m4-research-integration` enabled. No throughput claim. |
| Native maintenance state and write telemetry | **implemented; focused correctness verified** | Eight explicitly ignored maintenance/deletion/reopen/failure tests plus `wa_counts_new_then_unchanged`: 9 passed when individually selected with `-- --ignored`; the benchmark-named `step2_ab_benchmark` was not run. `DEFAULT_MAINTENANCE_THRESHOLD=50` remains unused (compiler warning); current runtime default is still eager (`threshold=0`). |
| Optional M4 cross-store adapter regression | **feature-isolated; default engine test target compiles** | Gated the adapter import/helpers/crash-reopen test in `storage/store/tests.rs` on `m4-research-integration`. `cargo test --no-default-features --lib --no-run`: PASS. The cross-store crash/reopen test itself remains unverified in this pass. |
| M4 selective visibility / short commit fence | **narrowly verified in research feature only** | `publication_masks_changed_files_without_blocking_unaffected_queries`: 1 passed. Gate-B cause-accounting tests: 5 passed, 1 intentionally ignored (the full lifecycle measurement arm). The whole-pass fence remains REWORK; no production qualification is implied. |
| M4 containment and runner policy | **synthetic checks verified; one prerequisite-only test unverified** | Containment runner: 26 passed. Contention policy: 70 passed, excluding only `test_memory_optimized_cache_profile_binds_exact_patch_binary_and_caps` because its pinned binary `research/m4-native/.work/build/dict-cache-eviction-memory-opt-final` is absent. Paired-profile tests: 16 passed; shared-collection runner: 8 passed; lifecycle refusal classification: 2 passed. |
| Write-amplification qgroup binding | **fixed and focused regression verified** | `registered_workspace_quota_contract` now resolves the already-approved lock-contention qgroup contract for the write-amplification purpose as well. This enables the existing live qgroup check; it does not change any threshold or add a new policy. Policy/profile regression passed. `benchmark-policy.md` now documents the shared contract. |
| Documentation/status disposition | **updated** | The proposal now records the latest authorized Gate-B diagnostic, not the superseded `dc8e1120` 50/64 stop as current state. Navigation links to this manifest. Historical receipts were not rewritten. |

### Verification record

- Default product build: `cd rust && cargo check --locked -p zg --no-default-features` — **PASS**, no M4 feature enabled. It reports the unused `DEFAULT_MAINTENANCE_THRESHOLD` warning above.
- Rust focused regressions: watcher 1/1; PK deletion 1/1; maintenance/deletion/reopen/failure 8/8; write-accounting 1/1; selective-visibility/fence 1/1; Gate-B cause-accounting 5 passed / 1 intentionally ignored.
- M4 Python synthetic suites: containment 26/26; applicable contention-policy 70/70; paired-profile 16/16; shared-collection runner 8/8; lifecycle classification 2/2.
- `git diff --check` — **PASS**.
- Fresh LSP diagnostics were **inconclusive**, not clean: the Python paths had a timeout and a push-only/silent server, and `storage/store/tests.rs` exceeds the 5,000-line LSP limit. Rust test-target compilation and focused regressions are the verification evidence instead.
- The initial default engine test-target compile exposed an unconditional test-only import of the optional M4 bridge. The import, helpers, and M4 crash/reopen test are now feature-gated; `cargo test --locked -p zg-engine --no-default-features --lib --no-run` — **PASS**. M4-specific tests remain opt-in, and the production default build stays feature-off.

## Remaining unverified / blocked items

- The pinned-binary profile test above needs its exact candidate artifact; no binary was built and no benchmark/profile operation was run.
- `research/m4-native/rust-bridge/tests/file_publication.rs`, the M4 cross-store crash/reopen test, and the broader C++ native correctness additions were not run separately. The bridge `build.rs` writes CMake build scratch under `research/m4-native/.work`, while the latest stored scratch decision specifies repository-root `.tmp`; reconcile that build path before starting another standalone native build. The selected engine publication/fence regression did pass.
- Additional untracked differential/native tests are inventoried but not executed in this bounded P0 pass.
- Current M4 checkpoint: the single authorized 64-file diagnostic `m4-rust-lifecycle-pair-20261027t052952z-332a873b` completed and its authorization is consumed. Queue saturation was distinguished from disconnected receivers, but workspace inventory/soft-budget peaks remain unknown and p95 is non-comparable. Gate B remains PARTIAL with no performance verdict. **No retry or further operation was started.** See `research/m4-native/results/ACTIVE-EXECUTION.md` and `M4-GATE-B-QUERY-DROP-CAUSE-20261027.md`.

No files were staged or committed. This closes P0's inventory/baseline/focused-checklist deliverable only; it does not authorize P1 measurement, a new M4 operation, production migration, or any default/policy change.
