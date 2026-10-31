# PR description — integration/upstream-rust → upstream

## Title

Release candidate: M2/M3-A integration, safe finalization, and release readiness (`0.3.0-rc.2`)

## Summary

This branch integrates the production-relevant work from `work/zg-optimization`
onto upstream `e1f93d2` and prepares a release candidate. It does **not** ship
the blocked M3-B outside-lock scalar maintenance or #10 deferred maintenance.

Release code commit: `289674bc127f664fea0993381c6d109302af95ae`
(upstream base `e1f93d257800f8626eb877eaf45573518076ab65`).

Status: **RC READY for merge/release review; production rollout NOT READY**
(no 421-file/production benchmark, zvec 0.8.0 native blocker for scalar
outside-lock maintenance, #10 not implemented).

## What changed

- **M2 selective embedding reuse** for modified files, with an additive,
  versioned `reconciliation/` sidecar bound to fragment identity, model,
  embedding-input fingerprint, and the exact FP16 stored vector.
- **M3-A dirty finalization**: per-collection debt, fragment optimize outside the
  store state lock, scalar optimize under the lock, durable `optimize.pending`
  marker, generation-aware retry.
- **Watcher coalescing**: 750 ms trailing debounce / 5 s cap; manual priority.
- **Streaming token-density input detection**.
- **Model compute cap 8** with explicit override.
- **Rebuild preflight** with a one-active-copy floor, `2× + 4 GiB` target, a
  warning band, and actionable error help.
- Diagnostics for optimize/reconcile stages.

## Owner-accepted behavior changes

1. Model compute default cap 8 on >8-core hosts.
2. Watcher-triggered jobs may start up to 750 ms later (5 s cap).
3. M2 sidecar reuse enabled by default; `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1`
   disables it.
4. Rebuild preflight can refuse below one active-index copy and warns below the
   conservative same-size margin.

## Compatibility

- Index version remains `2`; no in-place migration. Incompatible versions require
  explicit `zg --index --rebuild` and do not mutate storage before consent.
- Sidecar is additive; older readers ignore it, and this release ignores older/
  unknown sidecars. Deleting `reconciliation/` disables reuse only.
- No zvec schema/table/row-format change, no dependency-lock change.

## Verification

- **1,157 tests passed, 0 failed, 15 ignored** across engine (lib + integration),
  daemon, CLI, transport-MCP, daemon-protocol, FP16, and host-native.
- clippy `-D warnings`, `cargo fmt --check`, release `zg-cli` build: PASS.
- CLI/MCP release smoke: `0.3.0-rc.2`, MCP `2024-11-05`, agent 1 tool, full 6
  tools, no protocol error.
- Release binary SHA-256:
  `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a`.
- Evidence: `docs/release/RELEASE-READINESS.md`,
  `docs/release/RELEASE-MANIFEST.md`; artifacts under
  `/home/masis/bench/release-readiness-20261031/`.

## Test plan

- [x] Engine lib + integration suites
- [x] Daemon, CLI, transport-MCP, daemon-protocol, FP16, host-native
- [x] clippy all targets, `-D warnings`
- [x] `cargo fmt --check`
- [x] release build
- [x] MCP initialize + tools/list for agent and full toolsets
- [x] upgrade/downgrade refusal and explicit rebuild
- [x] sidecar torn/corrupt/oversized/v1/symlink recovery
- [x] rebuild interrupted-publication rollback
- [x] retrieval parity (sidecar-present, torn, rebuilt vs fresh)
- [x] pre/post-optimize query visibility
- [ ] 421-file/production benchmark (not authorized; production NOT READY)

## Non-goals / known limitations

- M3-B scalar outside-lock maintenance: **blocked** by pinned zvec 0.8.0
  (`optimize()` rejects readers/writers; native gate can SIGABRT). Not shipped.
- #10 deferred maintenance: **not implemented**; visibility evidence exists, but
  recall/latency, storage growth, close policy, and crash semantics are not
  qualified.
- No production qualification or performance win claimed; the 64-file
  confirmation is exploratory/INCONCLUSIVE.

## Rollback

Revert the branch/merge commit; no storage migration. Delete `reconciliation/`
to disable reuse. Reopen after an interrupted rebuild to recover the previous
generation; do not delete the active index manually. Keep M3-B outside-lock code
disabled on zvec 0.8.0.

Full gates and rollback: `docs/release/RELEASE-READINESS.md`.
