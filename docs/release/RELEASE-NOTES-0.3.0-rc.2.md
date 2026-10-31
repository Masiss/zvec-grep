# zvec-grep 0.3.0-rc.2 — release notes (release candidate)

Status: **RC READY for merge/release review.** Not a production qualification.

- Upstream base: `e1f93d257800f8626eb877eaf45573518076ab65`
- Release code commit: `289674bc127f664fea0993381c6d109302af95ae`
- Branch: `integration/upstream-rust`
- Package version: `0.3.0-rc.2`

## Highlights

- **M2 selective embedding reuse**: modified files reuse stored vectors when the
  fragment identity, model, embedding-input fingerprint, and exact FP16 stored
  vector all match. The optional `reconciliation/` sidecar is auxiliary; a
  missing, corrupt, torn, oversized, incompatible, symlinked, or non-regular
  sidecar is ignored and never blocks a query.
- **M3-A finalization**: per-collection dirty debt; fragment maintenance runs
  outside the store state lock; directory/entity/file maintenance remains under
  the lock. A durable `optimize.pending` marker and writer-generation check make
  retries crash-safe.
- **Watcher coalescing**: 750 ms trailing debounce with a 5 s cap; manual jobs
  bypass the trailing wait.
- **Streaming token-density input detection** for large text inputs.
- **Model compute cap**: default 8 threads when
  `ZVEC_GREP_MODEL_COMPUTE_THREADS` is unset; a positive override wins.
- **Rebuild preflight**: one-active-copy hard floor, `2 × active + 4 GiB`
  conservative target, and a warning band for a potentially smaller rebuild.
  A failed build retains the active index.
- **Diagnostics**: per-stage `index_optimize_*` and `index_reconcile_*` timings.

## Owner-accepted behavior changes

These are intentional and should be called out to users:

1. Compute default cap 8 on >8-core hosts.
2. Watcher-triggered indexing may start up to 750 ms later (5 s cap).
3. M2 sidecar reuse is enabled by default; `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1`
   disables it.
4. Rebuild preflight can refuse below one active-index copy and warns between
   that floor and the conservative same-size margin.

## Compatibility and upgrade/downgrade

- Index format remains version `2`; there is **no in-place migration**.
  Missing/older/newer/malformed index versions report `RebuildRequired` and do
  not mutate storage. Run `zg --index --rebuild`; the fixed `.zvec-grep/index`
  path is preserved and the previous generation is retained until commit.
- The M2 sidecar is additive and versioned. Older readers ignore it; older
  sidecars are ignored by this release. Deleting `reconciliation/` disables
  reuse without affecting canonical data or queries.
- No zvec table/schema/row-format change.

## Correctness fixes in this RC

- Sidecar reads no longer follow symlinks or read non-regular files; a symlinked
  sidecar directory is rejected in read-only and read-write opens.
- Publish telemetry counts only actually published manifests.
- Rebuild preflight no longer falsely rejects every shrinking rebuild below the
  same-size margin; it proceeds with a warning and still rolls back safely on
  failure.
- Retrieval parity, torn-sidecar recovery, pre/post-optimize visibility, and
  preflight decisions have dedicated regressions.

## Known limitations / not RC blockers

- **M3-B scalar outside-lock maintenance is blocked** by pinned zvec 0.8.0:
  readers/writers are rejected during `optimize()` and the native gate can
  SIGABRT. The outside-lock path is not shipped. This is a known limitation, not
  an RC blocker.
- **#10 deferred maintenance is not implemented.** Committed rows are visible
  before optimize, but deferred recall/latency, storage growth, close policy,
  and crash semantics are not qualified. This is not an RC blocker.
- **Production rollout is NOT READY.** The 64-file confirmation is exploratory/
  INCONCLUSIVE and no 421-file/production benchmark was run. Do not present this
  RC as production-qualified.
- Docs/evidence are maintained under `docs/release/`; handoffs remain at the
  repository root.

## Verification

- 1,157 tests passed, 0 failed, 15 ignored (5 handle/native-maintenance gates,
  10 local-model/benchmark/oracle tests).
- clippy `-D warnings`, `cargo fmt --check`, and release `zg-cli` build pass.
- CLI/MCP release smoke: `zg 0.3.0-rc.2`, MCP protocol `2024-11-05`, agent
  toolset 1 tool, full toolset 6 tools, no protocol error.
- Release binary SHA-256:
  `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a`.

See `RELEASE-READINESS.md` for gates, `RELEASE-MANIFEST.md` for artifacts, and
`PR-DESCRIPTION.md` for the review summary.

## Rollback

1. Revert the branch/merge commit; no dependency or storage migration is needed.
2. Delete the sidecar directory to disable reuse; canonical data/queries remain.
3. For an interrupted rebuild, reopen the workspace to recover the previous
   generation; do not delete the active index manually.
4. Keep M3-B outside-lock code disabled; do not enable it on zvec 0.8.0.
