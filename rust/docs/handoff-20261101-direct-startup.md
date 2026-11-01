# Handoff — 2026-11-01 — Direct-path Startup Optimization

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`
- Branch: `work/zg-optimization`
- Baseline: `3be680b`
- No push/deploy, scheduler change, UX V2 change, index-format change, model or
  threshold change.

## Implemented

- `clean-scan.json` sidecar and direct no-op fast path in
  `pipelines/indexing/service.rs`.
- Fast path skips model lease and zvec storage open when the indexed workspace,
  storage generation/version, scan rules and discovered-file metadata match.
- Sidecar refresh is gated on successful index and equal pre/post fingerprints;
  stale sidecars are removed before normal index attempts.
- Tests cover fresh no-op, delete invalidation, sidecar refresh, and no model
  acquisition.

## Measured

- Warm no-op: 554.67 ms p50 / 577.63 ms p95 → **33.21 ms / 37.30 ms**.
- Peak PSS: 316,864 KiB → **32,588 KiB**.
- Incremental one file: 29.87 s; next no-op 0.03 s.
- Rebuild: 170.73 s; post-rebuild no-op 0.04 s.
- Daemon steady client no-op: ~0.04 s, daemon PSS ~115 MB (separate scope).

## Decision

Keep. The dominant cost was opening the read/write zvec store, not model
loading. Remaining cold/no-sidecar startup still takes the normal path.

## Next safe action

If further work is authorized, measure cold page-cache behavior and a
multi-workspace warm no-op; do not change the searchable format or reopen #10.
