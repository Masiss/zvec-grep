# Release candidate evidence — 0.3.0-rc.2

This directory holds the release-candidate package for `integration/upstream-rust`.
Operational continuation checkpoints remain at the repository root.

## Status

- **RC READY for merge/release review.**
- **Production rollout NOT READY**: no 421-file/production benchmark, the
  pinned zvec 0.8.0 blocks scalar outside-lock maintenance, and #10 deferred
  maintenance is not implemented.
- M3-B and #10 are **not RC blockers**; the shipped finalization is the safe
  sequential path.

## Baseline

| Field | Value |
|---|---|
| Package | zvec-grep `0.3.0-rc.2` |
| Upstream base | `e1f93d257800f8626eb877eaf45573518076ab65` |
| Release code commit | `289674bc127f664fea0993381c6d109302af95ae` |
| Release tag | `rust-v0.3.0-rc.2` (final docs HEAD; local, not pushed) |
| Release binary SHA-256 | `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a` |

The docs commit/tag can be ahead of the release code commit; docs-only changes do
not alter the binary. Use `git rev-parse HEAD` in the tagged checkout for the
exact documentation HEAD.

## Owner-accepted decisions

1. Model compute default cap 8, with `ZVEC_GREP_MODEL_COMPUTE_THREADS` override.
2. Watcher debounce 750 ms / max 5 s; manual priority.
3. M2 sidecar as auxiliary persisted state, with recovery/compatibility coverage.
4. Rebuild preflight with a one-active-copy hard floor and a warning band below
   the conservative same-size target.

## Document map

| Document | Purpose |
|---|---|
| `RELEASE-NOTES-0.3.0-rc.2.md` | User-facing release notes and limitations |
| `RELEASE-MANIFEST.md` | Exact baseline, artifact hashes, verification |
| `PR-DESCRIPTION.md` | Ready-to-use PR summary/test plan/rollback |
| `RELEASE-READINESS.md` | Gates, upgrade/downgrade, sidecar recovery, #10 audit, rollback |
| `MERGE-READINESS.md` | Net diff, fork dispositions, merge verdict |
| `FEATURE-MATRIX.md` | Per-item integration disposition |
| `BENCHMARK-PLAN.md`, `BENCHMARK-RESULTS.md` | Benchmark protocol/results, exploratory status |
| `M3B-HANDLE-MAINTENANCE-FEASIBILITY.md` | M3-B design and native blocker evidence |
| `../../HANDOFF-20261031-NEXT.md` | Current operational checkpoint |
| `../../HANDOFF.md` | Historical implementation/continuation detail |

## Verification summary

1,157 tests passed, 0 failed, 15 ignored; clippy `-D warnings`, fmt, release
build, and CLI/MCP smoke pass. See `RELEASE-MANIFEST.md` for the full table and
artifact paths.

## Rollback

Revert the branch/merge commit. Delete `reconciliation/` to disable reuse without
touching canonical data. Reopen after an interrupted rebuild to recover the old
generation; never delete the active index manually. Keep M3-B outside-lock code
disabled on zvec 0.8.0.
