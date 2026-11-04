# Handoff — 2026-11-01 — Direct-path Startup Optimization

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`
- Branch: `work/zg-optimization`
- Baseline: `3be680b`
- No push/deploy, scheduler change, UX V2 change, index-format change, model or
  threshold change.

## Implemented

- `clean-scan.json` v6 sidecar and direct no-op fast path in
  `pipelines/indexing/service.rs`, with metadata + optional content
  fingerprints. V6 trusts ctime/inode metadata strictly and skips the content
  read; large workspaces can publish a null content fingerprint.
- Fast path skips model lease and zvec storage open when the indexed workspace,
  storage generation/version, scan rules, metadata and content fingerprints
  match.
- Sidecar refresh is gated on successful index and equal pre/post fingerprints;
  stale sidecars are removed before normal index attempts.
- Tests cover fresh no-op, same-size/same-mtime content changes with FTS/vector
  freshness, delete invalidation, missing/corrupt sidecar fallback, sidecar
  refresh, and no model acquisition.

## Measured

- V4 warm no-op: 554.67 ms p50 / 577.63 ms p95 → 69.48 ms / 72.49 ms.
- V6 warm no-op: **49.98 ms p50 / 53.91 ms p95**, peak RSS **36,860 KiB**
  (15 runs, 2,000 files).
- Incremental one file: 29.87 s; next no-op 0.03 s.
- Rebuild: 170.73 s; post-rebuild no-op 0.04 s.
- Daemon steady client no-op: ~0.04 s, daemon PSS ~115 MB (separate scope).

## Decision

Keep. The dominant cost was opening the read/write zvec store, not model
loading. Remaining cold/no-sidecar startup still takes the normal path.

## Known limits

The storage metadata seal detects missing/truncated/replaced generation files,
but not every deep data-page corruption. A source change that fully reverts
during one index attempt is a residual TOCTOU window. Content above 256 MiB is
supported only when ctime/inode metadata is trusted; otherwise it falls back.
Cold page-cache and multi-workspace startup were not measured.

## Verification opt-in

`ZVEC_GREP_VERIFY_STORAGE=1` disables the fast path for one normal storage-open
run. It is not a full data-page scrub.

## Next safe action

If further work is authorized, measure cold/multi-workspace behavior or add a
deeper opt-in scrub; do not change the searchable format or reopen #10.
