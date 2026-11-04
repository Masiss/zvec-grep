# Handoff — 2026-11-01 — Storage Footprint Optimization

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`
- Branch: `work/zg-optimization`
- Baseline: `4b3bded`
- No push, deploy, RC change, scheduler change, policy/threshold change, or
  index-format change.

## Implemented

- Compact binary reconciliation manifests (`ZGM2`, `.bin`) with legacy JSON
  read compatibility.
- Legacy sidecar removal on publish; both formats removed on delete.
- Manifest size estimation aligned with the compact encoding.
- Focused tests for round-trip, legacy read, truncation/trailing corruption,
  oversized manifests, and publish/load/delete.

## Measured result

- Disk: 980.3 MB → 899.5 MB (**8.24%** saved).
- Reconciliation: 134.5 MB → 50.2 MB (**62.7%** saved).
- Index + finalize: 203.13 s → 138.19 s (single run; not a controlled speedup).
- Peak RSS: 2.087 GiB → 2.068 GiB.
- Search after: FTS 633 ms mean, vector 717 ms mean; no before latency retained.
- Vector parity: 4/4 pass (initial/insert/delete/restart).
- Insert 8 KiB: +240,796 bytes; delete/reindex: +245,924 bytes residual churn.
- Full `zg-engine` suite: 549 passed, 3 unrelated authorization failures,
  39 ignored.

## Decision

Keep the optimization. Remaining footprint is zvec vector/entity storage
(88%); reducing it needs an engine/index-format change and is not authorized
here. Deferred Maintenance #10 remains closed/not implemented.

## Next safe action

If further storage work is authorized, profile a 4/8-workspace fixture for
per-workspace amplification; do not reopen #10 or change the searchable format
without a new preregistration.
