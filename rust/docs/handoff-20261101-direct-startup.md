# Handoff — 2026-11-01 — Direct-path Startup Optimization

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`
- Branch: `work/zg-optimization`
- Baseline: `3be680b`
- No push/deploy, scheduler change, UX V2 change, index-format change, model or
  threshold change.

## Implemented

- `clean-scan.json` v2 sidecar and direct no-op fast path in
  `pipelines/indexing/service.rs`, with metadata + content fingerprints.
- Fast path skips model lease and zvec storage open when the indexed workspace,
  storage generation/version, scan rules, metadata and content fingerprints
  match.
- Sidecar refresh is gated on successful index and equal pre/post fingerprints;
  stale sidecars are removed before normal index attempts.
- Tests cover fresh no-op, same-size/same-mtime content changes with FTS/vector
  freshness, delete invalidation, missing/corrupt sidecar fallback, sidecar
  refresh, and no model acquisition.

## Measured

- Warm no-op: 554.67 ms p50 / 577.63 ms p95 → **60.57 ms / 71.80 ms**.
- Peak PSS: 316,864 KiB → **33,628 KiB**.
- Incremental one file: 29.87 s; next no-op 0.03 s.
- Rebuild: 170.73 s; post-rebuild no-op 0.04 s.
- Daemon steady client no-op: ~0.04 s, daemon PSS ~115 MB (separate scope).

## Decision

Keep. The dominant cost was opening the read/write zvec store, not model
loading. Remaining cold/no-sidecar startup still takes the normal path.

## Known limits

Deep storage corruption is not verified by the fast path beyond storage
existence. A source change that fully reverts during one index attempt is a
residual TOCTOU window. Cold page-cache behavior and multi-workspace startup
were not measured.

## Next safe action

If further work is authorized, add a cheap storage-generation integrity marker
or measure cold/multi-workspace behavior; do not change the searchable format
or reopen #10.
