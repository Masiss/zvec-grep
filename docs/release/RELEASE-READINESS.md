# Release-readiness report — integration/upstream-rust

Date: 2026-10-31. Base: `e1f93d2`. Head: the commit containing this report.
No push, merge, deploy, default change beyond the owner decisions below, or new
benchmark was performed.

## 1. Verdict

- **Release candidate / merge readiness: READY.** All release gates in this
  report pass on the audited branch, including upgrade/downgrade refusal,
  sidecar crash recovery, rebuild rollback, retrieval parity, clean CI, and
  CLI/MCP release smoke.
- **Production rollout readiness: NOT READY.** The 64-file confirmation remains
  exploratory/INCONCLUSIVE, the 421-file corpus was not authorized, M3-B scalar
  outside-lock maintenance is blocked by zvec 0.8.0, and #10 deferred maintenance
  is deliberately not implemented.
- There is **no BLOCKED release gate** for this branch. The M3-B native blocker
  is a known limitation of the shipped safe path, not a release blocker.

## 2. Owner decisions applied

| Decision | Implementation / evidence |
|---|---|
| Accept model compute cap 8 | Default cap remains 8 when `ZVEC_GREP_MODEL_COMPUTE_THREADS` is unset; positive override wins. Bounds pinned by tests. Documented as a behavior change. |
| Accept watcher debounce 750 ms / max 5 s | Trailing debounce and hard cap retained; manual jobs bypass the trailing wait. Daemon tests pass. |
| Accept M2 sidecar as auxiliary persisted state | Versioned (v2), ≤4 MiB, atomic publish after native rows, additive under the index generation. Missing/corrupt/torn/oversized/non-regular/symlink sidecars are ignored. Vector fingerprint binds the exact FP16 stored vector. |
| Accept rebuild preflight conditionally | False rejection was tested and addressed: below the conservative same-size margin but at/above one active generation, the rebuild proceeds with a warning; below one generation it fails early with actionable help. A failed build keeps the active index. |

## 3. Release gates

| Gate | Result | Evidence |
|---|---|---|
| G1 Upgrade/downgrade and index compatibility | **PASS** | Incompatible/missing/older/newer index versions are `RebuildRequired`; no mutation before explicit rebuild. Rebuild keeps the fixed path and changes generation. |
| G2 Sidecar crash recovery and compatibility | **PASS** | Torn, corrupt, oversized, v1/unknown, symlinked, and non-regular sidecars are ignored; successful publish replaces them. |
| G3 Rebuild/rollback and preflight | **PASS** | Every interrupted publication step restores the old generation; unexpected manifest changes prevent destructive rollback; preflight false rejection has a warning band + help. |
| G4 Retrieval parity | **PASS** | Sidecar-present/torn and rebuilt indexes return the same FTS/vector paths as a fresh index of the final content; stale content is absent. |
| G5 Write → finalize → optimize → query audit | **PASS (audit only)** | Committed rows are searchable before optimize; marker/generation/debt behavior inspected. #10 remains unimplemented by instruction. |
| G6 Clean CI | **PASS** | 1,157 tests passed, 0 failed, 15 ignored; clippy `-D warnings`, fmt, release build. |
| G7 CLI/MCP release smoke | **PASS** | Release `zg --version`/`--help`; MCP initialize + `tools/list` for agent (1 tool) and full (6 tools), no protocol error. |
| G8 Merge package, release gates, rollback | **PASS** | `docs/release/` package (notes, manifest, PR description, gates, evidence, rollback); handoffs at repo root. |
| G9 Production qualification | **NOT READY** | 64-file confirmation INCONCLUSIVE; no 421-file/production benchmark; M3-B native blocker; #10 not implemented. |

## 4. Upgrade / downgrade compatibility

The on-disk index version is `2`. This branch does **not** perform in-place
up/downgrade migrations: an index with a missing, older (`0`, `1`), newer (`3`,
`5`), or malformed `indexVersion` is reported as `RebuildRequired` and all public
operations refuse to use or refresh it. `zg --index --rebuild` creates a new
generation, publishes atomically, and retains the previous generation until
commit. The fixed `.zvec-grep/index` path is preserved.

The sidecar is version `2` and is optional. A version-1/unknown sidecar is
ignored, so downgrading the sidecar reader never fails an index open and
downgrading the index never consumes a future sidecar schema. Deleting
`reconciliation/` disables reuse without touching canonical rows.

Evidence: `tests/index_version.rs` (all incompatible versions + explicit rebuild),
`storage/manifest.rs` tests (torn/v1/oversized/symlink sidecars).

## 5. Sidecar crash recovery

Publication order is: native file rows commit → sidecar atomic write. The sidecar
is a hint, never a source of truth.

| Crash window | Result |
|---|---|
| Before native commit | Old rows + old sidecar remain consistent. |
| After native commit, before sidecar publish | Old sidecar remains. Reuse is accepted only if fragment identity, model, embedding-input fingerprint, and the FP16 vector fingerprint all match; otherwise the file is re-embedded. |
| During sidecar atomic write | Readers see either the old complete file or the new complete file; temp files are not read. |
| After file deletion, before sidecar deletion | Stale sidecar is ignored for a missing file; file IDs are not reused after reopen. |
| During/after rebuild | Sidecars live inside the generation directory. Staged/backup movement carries or discards them with that generation; rollback restores the old generation's sidecars. |

`ManifestStore::load` now rejects symlinks and non-regular files before reading,
so a damaged/malicious workspace cannot turn a cache lookup into a blocking read.
`ManifestStore::open` rejects a symlinked `reconciliation/` directory. Oversized
sidecars are ignored; publish telemetry counts only actually published manifests.

## 6. Rebuild, rollback, and preflight

- `prepare_build` writes a durable build record and ownership generation;
  `publish_build` moves active → backup, staged → active, then atomically replaces
  the manifest. `recover_build`/`recover_build_for_rebuild` restore the previous
  generation at every interrupted step, and are idempotent.
- Tests cover: every interrupted publication step, failed cleanup, corrupt build
  metadata, unowned backup preservation, unexpected manifest change, symlinked
  temp directory, recovery interrupted during rollback, and workspace relocation.
- Preflight (`preflight_rebuild`) uses:
  - **hard floor:** one active-index copy; below it, fail early;
  - **conservative target:** `2 × active + 4 GiB` (test: headroom 0);
  - **warning band:** `[active, target)` proceeds with a warning because a
    shrink/model/selection change may fit; if the build hits ENOSPC, the staged
    generation is discarded and the active index is retained.
  - The hard-failure error includes exact active/available/minimum/target bytes
    and remediation (free space, remove stale temp/backup, or move the workspace).
- False rejection: a large active index rebuilt to a much smaller one is no
  longer rejected merely because it is below the same-size margin. Extreme cases
  below one active copy still reject; remediation is in the error help.

## 7. Retrieval parity

New integration test
`sidecar_torn_recovery_and_rebuild_preserve_retrieval_parity`:

1. Index an initial Rust workspace in root A and the final content in fresh root B.
2. Update root A to the final content with the sidecar present.
3. Compare FTS and vector paths for retained and changed tokens against root B.
4. Assert the removed token is absent.
5. Corrupt the sidecar, reopen, and repeat the query comparison.
6. Rebuild root A and repeat again.

The mock Qwen runtime declares an unstable identity, so reuse is intentionally
disabled there; this test proves that a present or torn sidecar never changes
retrieval. Stable-identity reuse itself is covered by the pipeline fingerprint
unit tests (`m2_reuse_requires_matching_fragment_identity_and_model`,
`m2_reuse_rejects_fingerprint_and_model_mismatch`, stale-vector and unstable-
identity cases). The local-model benchmark observed actual reuse (22/24
fragments) but remains exploratory.

## 8. Write → finalize → optimize → query visibility (#10 audit, no implementation)

### Path

1. **Write** (`IndexStore::write`, state mutex): native file/entity/fragment
   writes and optional graph writes commit under the state lock; `write_generation`
   advances and `dirty`/`needs_optimize` are set.
2. **Phase A** (`finalize_writes_detailed`, state mutex): reject `closed`; set
   `optimizing`; persist `optimize.pending`; snapshot `dirty` and
   `write_generation`; clone the fragment `Arc`.
3. **Phase B** (no state lock): native **fragment** optimize.
4. **Phase C** (state mutex): optimize directories/entities/files; clear
   `optimizing`; if the generation is unchanged, remove the marker and clear
   debt. A concurrent write preserves debt for the next finalization.
5. **Query** (`IndexStore::read`, same state mutex): FTS/vector search over the
   committed collections; native optimize is not required to make committed rows
   visible.

### Evidence

- `committed_rows_are_searchable_before_optimize_and_optimize_preserves_results`:
  both FTS and vector search return the newly written entity before
  `finalize_writes`, and the same result after optimize.
- `fragment_optimize_releases_the_store_state_lock_for_readers` and
  `fragment_optimize_allows_concurrent_writes_and_queries`: fragment maintenance
  does not block readers/writers.
- `fragment_optimize_failure_preserves_marker_and_retries_after_reopen`,
  `dirty_optimize_failure_preserves_marker_and_retries_after_reopen`: marker and
  debt survive failure/reopen.
- `close_waits_for_fragment_optimize` / lifecycle tests: close cannot race a
  successful write.

### Classification

| Work | Class | Reason |
|---|---|---|
| Native write + graph commit, generation/debt, marker persistence | **Mandatory synchronous** | Durability, recovery, and retry ownership. |
| `close` flush / wait for in-flight fragment optimize | **Mandatory synchronous** | A successful close must commit accepted writes. |
| Fragment/directory/entity/file `optimize()` | **Deferrable candidate** | Committed rows are visible without it; it is compaction/index maintenance. |
| Age/threshold policy, background scheduler | **Not implemented** | Needs owner policy, storage-growth limits, and a benchmark. |

### Risks / evidence gaps for #10

- The visibility test uses a small fixture and one native version; it is not
  proof of recall or latency at scale, especially for HNSW/FTS segments.
- `optimize` currently establishes full ANN completeness in the indexing path;
  deferring it may leave reduced recall, stale-delete reclamation, or larger
  on-disk/WAL state until maintenance runs.
- A deferred design must preserve the marker across crash/reopen, define a
  close/shutdown policy, bound storage growth, handle `drop`/rebuild/relocation,
  and prevent unbounded latency from a future maintenance burst.
- No benchmark or production implementation is authorized. #10 remains a
  documented candidate, not a shipped behavior.

## 9. Verification evidence

All commands from `rust/` with
`TMPDIR=/home/masis/src/zvec-grep-integration/.tmp` (absolute; a `..` component
breaks MCP path fixtures for environmental reasons).

| Suite | Result |
|---|---|
| `cargo test -p zg-engine --lib` | **702 passed**, 0 failed, 15 ignored |
| `cargo test -p zg-engine` integration | **40 passed**: concurrent_queries 8, engine_storage 17, file_selection 8, index_version 2, scan_failures 3, storage_crash 2 |
| `cargo test -p zg-daemon` | **124 passed** |
| `cargo test -p zg-cli` | **180 passed** (175 + 5) |
| `cargo test -p zg-transport-mcp` | **60 passed** |
| `cargo test -p zg-daemon-protocol` | **5 passed** |
| `cargo test -p zg-zvec-fp16` | **2 passed** |
| `cargo test -p zg-host-native` | **44 passed** (25 + 10 + 9) |
| clippy (`zg-engine`, `zg-daemon`, `zg-cli`, `zg-transport-mcp`, all targets, `-D warnings`) | **PASS** |
| `cargo fmt --all -- --check` | **PASS** |
| `cargo build -p zg-cli --release` | **PASS** |

**Total: 1,157 passed, 0 failed, 15 ignored.** The ignored tests are five
handle-based/native-maintenance gates (retaining the zvec 0.8.0 blocker reason)
plus ten local-model/benchmark/oracle tests that require external models or are
intentionally manual.

### CLI/MCP release smoke

Isolated `ZVEC_GREP_HOME`; release binary `0.3.0-rc.2`:

- `zg --version` → `0.3.0-rc.2`; `zg --help` → exit 0.
- MCP stdio: `initialize` (protocol `2024-11-05`) → server
  `zvec-grep 0.3.0-rc.2`; `tools/list` → agent toolset **1** tool
  (`zvec_grep_search`), full toolset **6** tools; no JSON-RPC error; stderr empty.
- Daemon stopped after each smoke; temp home removed/ignored.

Artifacts: `/home/masis/bench/release-readiness-20261031/` (`summary.json`,
`mcp-agent.out`, `mcp-full.out`, `version.txt`, `help.txt`, `zg.sha256`).
Release binary SHA-256: `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a`.

## 10. Rollback procedure

1. **Code:** merge-revert the branch (or revert its commits). There is no
   dependency lock, native schema, or index-format migration to undo.
2. **Sidecar:** delete `.zvec-grep/index/.../reconciliation/`. Reuse is disabled;
   canonical rows and queries are unaffected. A downgraded reader ignores it.
3. **Rebuild/rollback:** do not delete the active index manually. Reopen the
   workspace; `recover_build` restores the previous generation from the backup
   and discards the stage. If the build record/backup is inconsistent, preserve
   both directories for inspection; an unowned backup is never silently deleted.
4. **Preflight failure:** free disk space (at least one active copy; the
   conservative same-size margin is two copies + 4 GiB), remove stale
   `temp/`/`backup/` after recovery, or move the workspace to a larger filesystem,
   then retry `--rebuild`.
5. **M3-B:** keep the outside-lock path disabled and the ignored regressions in
   place. Do not enable handle-based scalar maintenance on zvec 0.8.0.

## 11. Unresolved risks / owner-visible caveats

- **M3-B native blocker:** zvec 0.8.0 rejects readers/writers during scalar
  `optimize()` and can SIGABRT. Published behavior keeps scalar optimize under
  the state lock; query p95 during mutation is exploratory (~244 ms in the
  authorized 64-file run), not a production guarantee.
- **#10 not implemented:** deferred maintenance needs a policy and benchmark;
  storage growth/recall risks are documented above.
- **Performance evidence:** 64-file confirmation is INCONCLUSIVE/exploratory;
  no 421-file or production corpus was run. Do not claim a qualified win.
- **Sidecar:** adds an auxiliary directory and disk I/O; stale hints are safe but
  may cause unnecessary re-embedding. It is not a zvec storage-format change.
- **Preflight:** remains conservative; extreme shrink below one active copy is
  still rejected. The error help and warning band are the remediation path.
- **Release notes:** the compute-cap, watcher-debounce, rebuild-warning-band, and
  sidecar behaviors must be called out for users.
