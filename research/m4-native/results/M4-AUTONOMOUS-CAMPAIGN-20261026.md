# M4 autonomous campaign checkpoint — 2026-10-26

## Executive status

**Provisional architecture decision: REWORK before integration.** M4 remains a credible research candidate for replacing FTS/INVERT while retaining native vector storage, but there is not yet evidence to approve a production integration or to reject M4 outright. Correctness evidence is materially stronger than lifecycle/performance or real-watcher integration evidence. No production Rust path was changed by this checkpoint.

The highest-value next work is (1) finish a research-only cross-store publication contract and its failure tests, including watcher-shaped update/delete/large-file batches; (2) run one valid, contained real-code WAL characterization and then a matched full-lifecycle native-optimize vs M4 flush/compaction comparison when the existing benchmark attestation is valid; (3) make the architecture decision from those results. Codec/framing exploration remains closed.

## Restored execution state

- Workspace: `/home/masis/src/zvec-grep`, branch `work/zg-optimization`; base HEAD before this checkpoint was `1b217a1` (`Record M4 WAL recovery budget decision`). The branch and worktree already contain numerous research, instrumentation, Rust, and result changes. They were not reset or overwritten.
- The current `m4_bridge_integration.cc` worktree had pre-existing performance instrumentation and reader-mode edits. The coordinator WAL recovery changes and their test invocation were committed locally; all other hunks remain unstaged.
- Owner decision remains: no absolute M4 WAL-file cap. The 2 MiB per-mutation and 8 MiB frame/batch limits protect accepted writes; default delta thresholds provide only a conditional growth envelope. Replay of externally supplied or non-default oversized WALs remains whole-file/unbounded and is not qualified.
- The required benchmark attestation was stale at the prior preflight. No real-code WAL characterization or matched lifecycle benchmark was run in this checkpoint, and no policy/harness gate was bypassed.

## Phase-gate assessment

| Phase | Current status | Evidence and remaining gap |
|---|---|---|
| 1. Correctness & durability | **PASS for the bounded standalone M4 API; not an integrated-system pass** | The committed WAL record-bound work has deterministic boundary, malformed-length, atomic-batch, and recovery coverage; `m4_correctness` previously passed 21,094 checks. The bridge recovery fix below adds fail-closed validation for invalid coordinator publish ordering. External/non-default oversized WAL replay still lacks an absolute memory bound by owner decision. |
| 2. Algorithms & resource efficiency | **INCONCLUSIVE** | No valid real-code workload or full-lifecycle resource comparison. Existing memory/compaction/WAL measurements are directional or fixture-bound; they do not establish production CPU, RAM, write, or latency envelopes. |
| 3. Retrieval & concurrency | **PARTIAL** | Existing M4 query/concurrency regressions and the bridge suite cover internal ranking, filters, leases, and publication behavior. Native-ranking parity and production reader-tail behavior are not established by matched workload evidence. |
| 4. Full-lifecycle performance | **BLOCKED pending valid benchmark authorization/attestation** | Existing paired write-amplification reports do not compare native `optimize` with M4 flush/compaction on equivalent traces. No new performance operation was launched. |
| 5. Integration readiness | **PARTIAL — research coordinator only** | The bridge correctness suite passed 4,679 checks, including new invalid-WAL-state cases. It exercises a C++ research coordinator, not the Rust filesystem watcher or a deployed adapter. Cross-store publication, file-level batching, migration, and rollback still need end-to-end contract tests. |
| 6. Architecture decision | **REWORK (provisional)** | Keep M4 isolated and continue bounded research. Do not integrate into production until phases 4–5 close and the owner-facing deployment/rollback contract is demonstrated. |

“No P0 identified” here is deliberately scoped to the exercised M4 core and bridge paths; this is not a claim of exhaustive system proof or production qualification.

## Work executed in this checkpoint

### Coordinator WAL publication invariant

Source review of `research/m4-native/tests/m4_bridge_integration.cc` found that recovery validated each coordinator WAL record's checksum and syntax, but previously did not enforce that a `P` (publish) record was preceded by a matching latest `R` (reservation). A syntactically valid `P 5` without `R 5` could advance the manifest. The normal writer's ordered, synced `R` → durable writes to both stores → synced `P` path does not produce that sequence through an ordinary crash, so this was a research-coordinator invariant gap rather than evidence of a current production P0.

Recovery now:

- treats records at or below the already-published manifest as historical;
- requires new reservations to increase monotonically;
- requires each new publication to match the latest reservation and advance publication order; and
- throws before rewriting the manifest on an invalid state.

Deterministic regressions cover an unreserved publish and a publish of an older reservation after a newer reservation; both assert failure, preserved WAL bytes, and unchanged manifest. The existing valid-final-record-at-EOF and torn-suffix tests remain covered.

**Verification:** rebuilt `m4_bridge_integration` in `research/m4-native/.work/build/dict-cache-concurrent-open` and ran it with a fresh operation-owned root under `research/m4-native/.work/tmp`. Result: `m4_bridge checks=4679 failures=0 generation=9 live_rows=24 architecture=full-snapshot-generation-publish`. The operation-owned test root was removed after the run and absence was verified. Binary SHA-256: `b99d36a292925df03510d38bf8fee586900e41a565eea8a07af2d7afceeac42a`. This was a correctness test, not a performance/qualification run.

**Current Rust staged-file contract verified:** two focused `zg-engine` store tests passed, each 1/1. `staged_replacement_streams_batches_and_reopens_consistently` replaces a file with 129 fragments across staging batches, verifies old FTS content disappears, all new FTS/vector entries are present, then reopens and verifies persistence. `staged_publish_failure_is_hidden_after_reopen_and_retry_is_idempotent` injects failure after 128 projection writes; partial candidates remain physically present but hydration hides them because the file is `NotIndexed` and has no published manifest; reopen remains fail-closed and retry publishes all 129 fragments. Cargo emitted the existing `DEFAULT_MAINTENANCE_THRESHOLD` dead-code warning from the dirty store worktree. Build artifacts reused `rust/target`; both test temps were under repo `.tmp` and cleaned.

## Three architecture paths and trade-offs

1. **Recommended research path — M4 FTS/INVERT sidecar coordinated with native vectors.** Preserve native vector storage and put an explicit generation/publish protocol around M4 FTS updates plus native vector writes. This best matches the existing bridge evidence and is reversible, but it adds a second durable store, cross-store recovery coordination, query merging, generation cleanup, and migration disk overhead. It must handle files exceeding one M4 batch without exposing partial file generations.
2. **Move M4 into the native index transaction boundary.** This could provide stronger shared snapshot/durability semantics and avoid a sidecar coordinator, but requires deeper native storage changes and migration compatibility work. There is no lifecycle evidence yet to justify that coupling.
3. **Keep the native implementation and stop M4 integration (NO-GO).** This avoids the extra durability and operational surface. It is the fallback if a representative, properly contained lifecycle comparison fails to demonstrate a worthwhile advantage or if the watcher/rollback contract cannot be met.

Path 1 is selected for **research only**, not production. The project does not yet have an evidence-based performance reason to choose it over path 3.

## Integration contract still required

- **Freshness granularity is a real architecture boundary.** `pipeline.rs` commits each successful candidate through `replace_file` / `replace_file_from_staged`, reports it indexed after that call, and invokes `finalize_writes` after the indexing passes. `store/mod.rs` performs replacement under its writer lock, removes the file manifest before row mutation, then publishes a manifest and indexed file status on success. On failure, partial projections may remain physically present, but `load_search_hits` hides them for a `NotIndexed` file without a trusted manifest. This is fail-closed per-file visibility, not rollback to the old file version. The current C++ bridge instead rebuilds and publishes a complete live-workspace snapshot at one generation boundary. These are not equivalent freshness semantics. Preserve the existing per-file boundary and fail-closed behavior by default; adopting pass-level snapshot visibility is a user-visible change requiring explicit owner acceptance.
- The existing C++ bridge already covers update/delete/add across generations, crosses the 256-row native batch boundary, pins readers during unpublished staging, and exercises cross-store crash points. Do not duplicate these tests. The remaining gap is to map the actual Rust watcher/file-replacement contract to stable M4 keys and preserve its fail-closed hydration rule for partial files, including when one file exceeds a 256-mutation or 8 MiB batch. A sidecar will need a per-file active revision (or equivalent) and candidate refill so staged chunks are not returned early and stale revisions do not consume final top-K. A whole-workspace epoch alone changes freshness semantics.
- **There is no current Rust/C++ M4 build or API seam.** `rust/crates/zg-engine/Cargo.toml` has no M4 dependency, `cc`/`cxx`/CMake build integration, or `build.rs`; M4 is built as a research C++ static library. The write contract is `IndexStorage`, while lexical reads are a separate `SearchStorage` trait. A real adapter therefore needs a deliberate C ABI/FFI (or service boundary) and must compose both read and write paths; the C++ bridge test does not establish packaging, ABI, or Rust watcher integration.
- Make watcher acknowledgement/freshness semantics explicit: do not report a file update complete until both the native vector state and M4 state are durable and the generation decision is recoverable.
- Exercise crashes after reservation, partial M4 batches, M4 flush, partial native batches, native flush, publish-WAL sync, and manifest rename. Recovery must expose only the old or complete new generation, never a mixed generation.
- Integrate with the actual Rust watcher/indexing pipeline through an isolated adapter test; the present C++ bridge does not prove this boundary.
- Demonstrate reader snapshot pinning across both stores, update/delete/rename semantics, stale-generation cleanup, migration, and rollback while retaining the previous index.
- Enforce `fsync_wal` and the accepted default M4 delta/frame limits at the adapter boundary. Continue to fail closed for malformed/oversized legacy WALs; do not imply an absolute recovery-memory bound for non-default external WALs.

## Next executable sequence

1. Reconcile the freshness contract from the Rust caller/store path, then evaluate the required ABI boundary. Prototype a research-only per-file revision adapter (or, if it cannot preserve the current contract within a bounded/simple design, document the explicit owner choice needed). Add an isolated test for a staged file replacement larger than one M4 batch and verify stale-revision refill. Reuse existing bridge update/delete, reader-pin, and crash-matrix coverage rather than duplicating it. Keep changes in `research/m4-native`; no production Rust changes.
2. Review and bound the coordinator journal lifecycle before treating the prototype as an integration contract. Current `NextGeneration` rescans the journal, and coordinator WAL checkpoint/growth behavior is not a production-ready lifecycle design.
3. Recheck the current attestation and resource gates without modifying them. If valid, preregister and run exactly one bounded, cgroup-contained real-code WAL characterization.
4. Then run the matched full-lifecycle native-optimize vs M4 flush/compaction comparison on one equivalent mutation trace, with host/process state and rollback/disk costs recorded per repository policy.
5. Reconcile correctness, resource, ranking, watcher, migration, and rollback evidence and issue the final GO / REWORK / NO-GO decision. Until then, retain **REWORK** and do not push or deploy.

## Artifacts and commits

- New checkpoint: this report.
- Existing WAL bound and budget decisions: `WAL-RECOVERY-BOUNDS-20261026.md` and commits `f4daf91`, `1b217a1`.
- The bridge recovery fix and focused regressions are committed locally, separate from pre-existing bridge instrumentation. Nothing has been pushed.
- No codec experiments, benchmark operations, production edits, or policy changes were performed.

**Status: PARTIAL / REWORK.** The bridge correctness milestone is verified; lifecycle performance and actual watcher integration remain open.