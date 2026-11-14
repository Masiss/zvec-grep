# E8-P2 — Integration Consolidation & Promotion Audit

Date: 2026-11-15  
Integration branch: `work/e8p2-integration-20261115`  
Base: E8-P1 `7b0aab8`; merged current main docs `6d5b523`  
Main worktree: `work/zg-recovered` @ `6d5b523` (untouched)

## 1. Verdict

**`QUALIFIED_WITH_LIMITATIONS` — review-ready, not merged or promoted.**

The integrated candidate contains the qualified Index v3, no-op fast path, E2
embedding reuse and E8/P1 explicit Vulkan work. CPU remains the default. The
implementation and independent verifier pass, including a conservative closure
for legacy `auto` indexes and accelerator runtime compatibility.

The limitation is deliberate: **Vulkan/CUDA/Metal indexes are rebuild-only for
writes** because no index-level proof exists that a changed driver/runtime
produces compatible vectors. Vulkan query-only access remains supported.
Legacy `auto` indexes are query-only. This is an explicit unsupported contract,
not a hidden fallback.

## 2. Dependency / patch provenance

The branch is based on E8-P1, which already contained Index v3, No-op, E2 and
E6. The integration adds a merge of current main docs and a selective E7-code
removal; it does not merge a research branch wholesale.

| Patch group | Commits | Classification | Integration decision |
|---|---|---|---|
| Index v3 C#/FTS/coverage | `986ab51`, `e9a9824`, `928f49f`, `894b71e`, docs `3bf6c18` | REQUIRED | Included |
| No-op fast path / publication | `63e6b2d`, `6536b00`, `6aa7385`, `ca04bb0`, `24af3e6` | REQUIRED | Included |
| E2 dedup/reuse/integrity | `0b1c667`, `e93b5db`, `d1e98b7`, `5744704`, docs `8ee9790`, `96d543c` | REQUIRED | Included; CPU cross-pass reuse qualified |
| E3/E6 benchmark and route contract | `2f0776b`, `8c555c3`, `5999001`, `ae3aab2`, `9579cae`, `95fd0f3`, `7013a47`, `9c1306d` | REQUIRED benchmark/dependency | Harness/tools retained; E6 contract used for gates |
| E8/P1 Vulkan + device identity | `b565472`, `b8a33b5`, `7b0aab8` | REQUIRED | Included |
| Main documentation | merge `ee07e66` of `6d5b523` | DEPENDENCY | Included |
| E4 model selection | `4742427`, `ca63ebb` | RESEARCH_ONLY | Docs retained; catalog entries remain behind non-default `e4-experiment` |
| R0 vector policy | `e909ff5` | RESEARCH_ONLY | Docs only; no production policy code |
| E7 scalability experiments | `0e21001` | DEFERRED / REVERTED PRODUCTION CODE | E7 env hooks removed from `llama_cpp/model.rs` and `runtime/concurrency.rs`; docs/harness timing output retained as provenance |

`ZVEC_GREP_E7_*` is absent from production source after the selective removal.
`e4-experiment` remains non-default and disabled in `zg`/`zg-engine` default
builds. E2-C, E1 deferred maintenance, R0 selective policy, E7 H1/H2 and new
E9 research were not introduced.

## 3. E8-P2 changes

1. **Conservative write safety** (`pipelines/indexing/service.rs`):
   - runs only after the read-only no-op preflight, so no-op remains a no-op;
   - legacy saved `auto` => every incremental write and rebuild-without-concrete
     device is rejected;
   - Vulkan/CUDA/Metal incremental writes are rejected; full `--rebuild` is
     required;
   - CPU incremental writes remain supported.
2. **E7 selective removal:** default E7 experiments were env-gated, but the code
   is research-only. Restoring the pre-E7 paths preserves default behavior.
3. **Documentation:** E8-P1 erratum and retrieval harness execution-device
   contract updated to the rebuild-only accelerator contract.
4. **Verifier:** `benchmarks/retrieval-evidence/tools/verify_e8p2.py` checks
   provenance, source leakage, device manifests, E2 counters, route contracts,
   guards, quality and negative mutations without importing the engine.

No ranking weights, gold data, model policy, index schema, production default,
installed binary or daemon were changed.

## 4. Cross-feature regression matrix

| Interaction | Evidence | Result |
|---|---|---|
| Index v3 ↔ FTS/structural/source | `csharp_symbols` 4, `csharp_coverage` 1, `fts_query` 1 | PASS |
| Index v3 ↔ no-op | `noop_fast_path` 11; real symbol cursor and query preservation | PASS |
| Index v3 ↔ E2 reuse | CPU cold unique 643/653 requests; incremental computed 1, fetched 9; line shift fetched 43 | PASS |
| E2 ↔ device identity | CPU/Vulkan manifests; Vulkan identity suffix; cross-device write rejection | PASS (CPU reuse; accelerator rebuild-only) |
| No-op ↔ device mismatch | CPU no-op 0 work; Vulkan-on-CPU no-op rejected before mutation | PASS |
| No-op ↔ publication/recovery | repeated no-op manifest hashes unchanged; kill during staged build then successful recovery | PASS |
| E6 ↔ FTS/Vector/Hybrid | route contracts PASS for `fts`, `vector`, `hybrid`, `hybrid-symbol`; negative contract/provenance tests | PASS |
| Vulkan ↔ indexing/query/incremental | 40-file cold 77.41 s; query-only CPU/Vulkan; incremental rejected; rebuild 75.63 s | PASS with unsupported incremental contract |
| Workspace relocation | CPU device survives, cache path cleared, CPU increment works, cross-device increment rejected | PASS |
| Feature-off build | `cargo check -p zg --release`, `cargo check -p zg-engine --release --no-default-features` | PASS |
| Existing-index reopen | version-3 CPU/Vulkan indexes queried after creation; no schema migration | PASS |
| Fresh vs incremental result | 3-file fresh and 1-file incremental: 32 query result shapes equal | PASS |

## 5. Retrieval and quality

### E6 corrected contract

- FTS-only, pure Vector, Hybrid and Hybrid-symbol route receipts all report
  `measurement_contract.status=PASS`, declared route equals normalized and
  observed execution, group count 1, zero duplicate routes.
- `verify_e6.py` PASS on P2 route receipts and on the frozen E8 full reports.
- `test_e6_negative.py` rejects hidden provenance and missing/FAIL contracts.
- No gold, query, ranking weight or model policy was modified.

### Frozen E6 quality (reused E8 receipts, not a new full P2 run)

| Arm | R@10 | R@50 | nDCG@10 | Solved@10 |
|---|---:|---:|---:|---:|
| CPU index, CPU query, hybrid | 0.6142 | 0.8025 | 0.5387 | 0.4444 |
| Vulkan index, Vulkan query, hybrid | 0.6142 | 0.8025 | 0.5387 | 0.4444 |
| CPU index, CPU query, pure vector | 0.5679 | 0.8673 | 0.5244 | 0.3704 |
| Vulkan index, Vulkan query, pure vector | 0.6049 | 0.8302 | 0.5481 | 0.4074 |
| Vulkan index, CPU query, pure vector | 0.6049 | 0.8673 | 0.5481 | 0.4074 |

Hybrid is identical between CPU and Vulkan. The pure-vector Vulkan-query K=50
anchor loss remains; this is why Vulkan is explicit opt-in and not a default.
The P2 40-file subset is a mechanism test, not the frozen quality estimate.

## 6. Performance and E2 resource evidence

Single serialized cold runs on the external 40-file workload; `/usr/bin/time`
maximum RSS. These are bounded measurements, not a variance study.

| Workload | CPU | Vulkan | Observation |
|---|---:|---:|---|
| 40 C# cold wall | 282.76 s | 77.41 s | 3.65× faster |
| 40 C# cold user CPU | 1,087.70 s | 20.62 s | 52.8× less |
| 40 C# peak RSS | 2,067,020 KB | 699,668 KB | 2.95× less |
| Repeated no-op | 0.05–0.06 s, 0 work | 0.06–0.11 s, 0 work | manifest unchanged |
| CPU one-file incremental | 1.54 s | n/a (rebuild-only) | 1 modified, 1 computed, 9 reused |
| CPU line-shift/reuse | 1.74 s | n/a | 43 vectors fetched, 1 computed |
| Vulkan rebuild | n/a | 75.63 s | full 40-file regeneration |

P1 historical 40-file numbers were 241.38 s CPU / 59.92 s Vulkan and E8 full
concurrent runs were 89:32 CPU / 54:43 Vulkan (user 20,908 s / 752 s). They are
not direct A/B baselines for this session; the P2 single-run numbers are
recorded separately. No repeated full-392 P2 A/B was run.

Query-only latency on the small matched subset (not quality decision): CPU
index/CPU query p50 165.4 ms; CPU index/Vulkan query 233.3 ms; Vulkan
index/CPU query 262.5 ms; Vulkan index/Vulkan query 268.6 ms.

## 7. Device / E2 safety qualification

- **Requested/resolved/actual/persisted:** explicit accelerator requests are
  fail-closed if unavailable; index descriptors persist the concrete device;
  query device is explicit and independent. No silent CPU fallback for an
  explicit accelerator.
- **Legacy `auto`:** cannot prove the actual backend. Query-only is allowed and
  was verified read-only. Every write is rejected; an explicit concrete rebuild
  creates a clean identity.
- **Accelerator runtime:** no claim of bitwise compatibility across drivers or
  runtimes. Therefore incremental writes and cross-pass reuse are disabled for
  accelerator indexes; rebuild is the only migration path.
- **Relocation:** saved device survives; cache paths clear; cross-device writes
  are rejected. No GPU UUID requirement was introduced.
- **CPU:** incremental reuse remains supported through E2 per-fragment identity
  and sidecars. A CPU backend upgrade is still a per-chunk reuse boundary, not a
  full-index token; this remains an explicit limitation.

## 8. Independent verification

`verify_e8p2.py` PASS covers:

- required Git ancestry and absence of E7/experimental hooks in production;
- workload cardinality, no-op zero-work, E2 counters, line shift and recovery;
- CPU/Vulkan/relocated/legacy device manifests;
- accelerator/legacy/cross-device negative guards;
- route contracts and E6 verifier/negative results;
- frozen hybrid metric equality;
- cold-performance arithmetic;
- adversarial contract/route/device mutations rejected.

Also run: default engine **765 passed, 0 failed, 28 ignored**; Vulkan engine
**765 passed, 0 failed, 28 ignored**; harness **4 passed**; focused integration
tests **17 passed**. Kill-9 during a staged CPU build recovered and republished
all three files.

## 9. Migration and rollback

- No index-version/schema change. Existing v3 CPU indexes reopen and can still
  be queried/incrementally updated.
- Existing explicit accelerator indexes are query-only for writes; rebuild with
  `--rebuild=true` to obtain a clean new write contract.
- Existing legacy `auto` indexes are query-only for writes; rebuild with an
  explicit `--device cpu|vulkan`.
- Rollback is deleting the P2 safety commit (or the integration branch) and
  returning to E8-P1. No data migration or storage rewrite is required. P2-built
  indexes remain readable by P1, though P1 lacks the P2 write guards.
- Reverting the E7 removal restores only the env-gated experimental paths; no
  index or manifest field changes.

## 10. Remaining risks / owner decisions

1. **OWNER_DECISION_REQUIRED:** merge/cherry-pick the integration branch and
   accept the rebuild-only accelerator write contract.
2. No full-392 P2 A/B; if promotion requires a new performance point rather than
   E8 full-quality reuse, authorize a bounded full run.
3. CUDA `Auto` and CUDA runtime compatibility remain outside this scope.
4. CPU backend-build change is not represented by an index-level token; E2
   per-fragment sidecars remain the reuse boundary.
5. Vulkan accelerator query on a Vulkan index still has the known pure-vector
   K=50 anchor caveat.
6. No hardware UUID is used; device identity is the declared execution backend.

## 11. Artifacts

- Integration receipts: `/home/masis/.cache/zvec-grep/e8p2-20261115/receipts/`
  (`SHA256SUMS`, provenance, verifier, workload logs, selected manifests).
- E8 full-quality receipts:
  `/home/masis/.cache/zvec-grep/e8-20261114/receipts/` (historical, preserved).
- E8-P1 receipts: `/home/masis/.cache/zvec-grep/e8p1-20261114/receipts/`
  (historical, preserved).
- External target/work/dependency prefixes are temporary and are removed after
  receipts are checksummed.

## 12. Promotion checklist (not executed)

- [ ] Review the E8-P2 diff and the E8-P1 erratum.
- [ ] Confirm CPU remains the default and no production default/schema change
      is included.
- [ ] Accept the explicit accelerator rebuild-only contract and legacy-`auto`
      query-only contract.
- [ ] Run the final default/Vulkan tests, focused integration tests, E6 verifier
      and `verify_e8p2.py` from the exact merge candidate.
- [ ] Merge without force-push; do not merge research-only E4/R0/E7 docs into a
      different production line unless the owner wants that provenance.
- [ ] If rejected, remove the branch/worktree; no index migration is needed.

Rollback: remove the P2 commit or integration branch and return to E8-P1. No
index/storage migration is introduced by E8-P2. P2-built indexes remain readable
by P1, but P1 does not enforce the P2 write guards.
