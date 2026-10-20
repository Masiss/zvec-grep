# Restore outer FTS/WAL durability — 2026-10-20

## 1. Exact removal, motivation and affected contract

The diagnostic was in `tests/m4_bridge_integration.cc`, not the M4 storage library or production native code:

- `SyncTree(root, m4_payload_already_durable, hardlinked_segments_durable)` skipped **every regular file under `fts/`** when its second argument was true. Directory sync was retained.
- `BuildDeltaVectorSnapshot` invoked `SyncTree(generation_root, true)` after M4 `Flush()` and before snapshot finalization and `PublishDeltaGeneration`.
- The motivation was to test whether redundant outer FTS file syncs caused writer tails: `Index::Flush` already syncs segments, stats, tombstones and manifest. That assumption was too broad: `src/index.cc::FlushInternal` writes the manifest and then calls `wal_.Reset()`; `Reset` truncates WAL without an internal file fsync. The removed outer file sync also covered this WAL size update.
- The contract affected was **durable component contents/size before bridge publish/ack**, not ranking, query freshness, or atomic pointer exchange. Directory fsync cannot substitute for fsync of file data/size. A stale pre-truncate WAL may be ignored by `manifest.wal_seq`, but that does not establish equivalent recovery under all storage crash states.
- Historical motivation and limitation are in canonical handoff §35. Earlier wording calling the elided sync redundant/durability-equivalent is superseded; this report does not rewrite historical observations or claim corruption was observed.

## 2. Which previous baseline/candidate comparisons differed?

| Historical experiment | Write ordering / fsync / WAL lifecycle assessment |
| --- | --- |
| Two filter-identity sustained 500×20 A/B pairs | Intended code difference was filter evaluation, not write protocol. Candidate-first arm receipts record the same bridge source hash `68ae22c1…97523`, and different executable hashes. No deliberate WAL/fsync difference is documented for these pairs; writer path is expected to be common. Complete original per-variant sources/compiler dependency provenance are not available, and reverse arms lack independent arm-local executable fingerprints. Therefore exact historical write-order equality cannot be newly certified from labels/commands alone. Their data remain historical observations, not current-source qualification. |
| Sync-reduction treatment/control pairs (`sync-reduction-pair-*`, confirmatory pair) | **Deliberately non-equivalent sync sequences.** Treatment skipped outer regular-file fsync across FTS, control retained it; both retained directory sync and called the same component Flush and bridge publication sequence. Inner M4 WAL append/manifest/truncate logic was not changed, but treatment removed the subsequent sync making the truncate durable. Do not treat these pairs as durability-equivalent latency comparisons. |
| Preserved `57f3a19e…ba342f` current-source diagnostic binary | Direct trace in `faults-corrected/old-elision.trace` confirms M4 WAL truncate with **no subsequent outer WAL fsync** before the after-stage process exit. It is diagnostic/historical only after this correction. |

Neither the original filter A/B observations nor these sync-reduction pairs identify a writer-tail cause. New full-sync latency is not inferred from them.

## 3. Minimal correction and limits

The bridge now calls **`SyncTree(generation_root)`** with full regular-file sync before publication. The broad FTS skip parameter/branch was removed entirely, so it cannot silently be enabled again by passing true at the publication call. The remaining bool is explicitly only for immutable hardlinked segment inodes in `CloneM4IndexSnapshot`; mutable copied WAL/state and directory entries are still synced. Its call changes from `(destination,false,true)` to `(destination,true)` with unchanged clone semantics.

No change to component WAL encoding/lifecycle, manifest format, reserve/publish records, snapshot selection, native API, ranking, or production code. `PublishDeltaGeneration` still runs only after durable component staging: bridge publish-WAL → bridge manifest/sequence → bridge WAL truncation. Error from `SyncTree` propagates before publish. Restoring the original stronger sync sequence avoids inventing a new durability design. This is structural equivalence to the pre-elision full-sync bridge, **not proof that a filesystem/device always honors fsync across real power loss**.

A small `--delta-recover-check` test-only CLI verifies recovered generation/highwater, live FTS count and vector/FTS result coherence. It adds no benchmark instrumentation.

## 4. Focused correctness and fault verification

Rebuilt Release binary, no performance workload:

- `--vector-delta-candidate`: **1,401 checks, 0 failures**. Includes staged/publish-WAL crashes, orphan generation/shard cleanup, compaction crash boundaries, native vector agreement and snapshot pinning.
- `--incremental-fts-candidate`: **582 checks, 0 failures**, including mutable-state clone/shared-run path.
- `tools/test_bridge_outer_sync.py`: **5 cases / 4 recovery oracles PASS**, with decoded-fd syscall traces. Uses copies of the closed generation-11 vector fixture; reserve generation 12 is the test transaction.

| Case | Observed | Recovery |
| --- | --- | --- |
| Old diagnostic, crash after stage | exit 86; WAL truncate but no following WAL file fsync | Historical negative control, not qualified |
| Restored binary, crash after stage | successful outer WAL fsync after truncate, then exit 86 before publish | generation 11, highwater 12; coherence PASS |
| EIO at restored outer WAL fsync | exact decoded generation-12 FTS WAL fd; fsync returns injected EIO; child exits 91 before publish | generation 11, highwater 12; coherence PASS |
| SIGKILL at same fsync | decoded target WAL syscall, process killed by signal 9 | generation 11, highwater 12; coherence PASS |
| Crash after durable publish-WAL | outer WAL fsync succeeds, then publish record and exit 87 before manifest checkpoint | generation 12, highwater 12; coherence PASS |

`strace` injection is main-thread syscall ordinal 65, determined from the successful trace and **verified against the actual WAL fd path in every injected trace**. A mismatched injection target fails the test. No timing/performance conclusion is drawn from traced runs. Fault cases exercise the full-rebuild branch through the same corrected stage helper; the incremental clone branch has focused normal correctness coverage, not every fault permutation.

Initial fault run failed because the test expected `(INJECTED)` on the SIGKILL syscall line. This strace version prints `fsync(target)=?` followed by `killed by SIGKILL`; return code was already -9 and target was correct. Evidence is preserved in `faults/`, `fault-tests.log`, and `fault-test-format-diagnosis.json`. Parser assertion was corrected and the suite passed on fresh roots under `faults-corrected/`. This was a concrete test-output-format fix, not retrying/bypassing a safety or correctness gate. The owned failed test service's metadata was retained and its cleanup verified before retirement (`failed-test-unit/`).

Process-crash tests are necessary here: a successful reopen alone would not test visibility across reserve/stage/publish boundaries or behavior after a failed sync. **They do not simulate kernel cache loss, torn device writes or physical power failure.** Such qualification would require an approved storage fault model/environment, not additional ordinary SIGKILL runs. No such large durability-design or qualification expansion was undertaken.

## 5. New binary provenance and build verification

New binary SHA-256:

`ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`

Old diagnostic SHA-256 remains `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`; it was not overwritten. Corrected binary is retained beside this report as `m4_bridge_integration.full-sync`, outside ignored `.work`, intentionally not committed as a binary.

`build-provenance.json` records exact configure/build commands, source-before-change revision plus exact current source SHA, actual compiler flags/config, ELF notes, runtime `ldd` and six shared-library hashes, and **61 actual in-repo compiler dependencies** parsed from generated `.o.d` files. Exact generated configuration bytes and focused source patch are in `configuration-and-change.tar.gz`. One pre-existing trailing space was removed for source packaging and the bridge rebuilt; the executable is byte-identical to the tested binary, so tests were not repeated. The before-whitespace receipt and its exact patch (`bridge-change.before-whitespace.patch.gz`) are preserved alongside the final source receipt. Against the prior receipt's 59 recorded inputs, only `tests/m4_bridge_integration.cc` changed; additional observed dependencies are retained, not silently omitted. Flags are Release GCC 16.2.1, `-O3 -DNDEBUG -std=gnu++17 -Wall -Wextra -Werror -pthread`. Build exit 0; one compiler job, transient owned cgroup, RuntimeMaxSec=600.

Before any future measurement: revalidate executable/retained-copy hash, all actual source dependencies, compiler/config/link command and loaded shared-library identities; require the full-sync binary's focused tests and unchanged readiness/containment/telemetry guards. Any change to a recorded compiler input invalidates this build receipt until rebuilt. Current-source correctness must not be inferred from the old diagnostic binary. No new A/A or performance campaign is authorized or launched by this correction.

## 6. Resource observations, decision and retention

Bounded build: 39.925 s runtime, 39.440 CPU s, reported peak memory 630.7M, swap 0B. Focused correctness job: 10.650 s, 9.677 CPU s, peak 933M, swap 0B. Corrected fault suite: 7.653 s, 7.071 CPU s, peak 394.7M, swap 0B. These are verification job accounting, **not index performance measurements**. Physical writes, peak PSS, maintenance debt and runtime cost of restored sync are NOT MEASURED. More file sync calls are intentional restoration of the stronger contract, not a claimed latency optimization.

Decision: small research-only correction implemented; observed full-sync ordering and focused process-crash recovery verified. No architecture/protocol redesign needed. No claim of physical power-loss qualification or writer-tail root cause. A future full-sync same-binary A/A requires fresh owner authorization; prior no-launch resource receipts are not readiness now.

Retain both binaries and all traces/logs/receipts. Checksum manifest covers the retained binary locally; Git retains source, tests and small evidence but not the executable. Existing unchanged core research sources/native inputs remain at their recorded source paths; do not delete them or assume this one commit is a standalone archive of every historical research dependency. Failed and corrected test evidence are both preserved. No unrelated Rust changes were modified or staged. The bridge source was previously untracked, so its first Git addition includes existing harness content; `configuration-and-change.tar.gz` contains `bridge-change.patch`, isolating this task's minimal delta (including the one pre-existing trailing-space cleanup). Reconstructing that patch's exact preimage yields SHA-256 `31e9e1db9dfacba98987c38799efcdc2be2bb805656e4ef89c27273d047a1cb5`, matching the old compiled bridge source receipt. This checks that no unrelated bridge source change was silently included.
