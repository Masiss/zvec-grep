# Storage Footprint Optimization

Status: **implemented and verified; keep the compact reconciliation sidecar.**
Baseline commit: `4b3bded`. Frozen searchable index format unchanged.

## Scope

Reduce on-disk index size without changing retrieval correctness, quality,
stability, the zvec index format, scheduler, policy, thresholds, or UX V2.

Non-goals: Deferred Maintenance #10, concurrent native optimize, changing the
embedding model, re-running the 45-run qualification matrix, or changing the
frozen qualification protocol.

## Profile

Fixture: 2,000 files, 8 KiB each, deterministic manifest SHA-256
`45bc27b0...e501b7`, one workspace.

| Component | Before | After | Notes |
|---|---:|---:|---|
| Vector fragment store | 638.9 MB | 638.9 MB | zvec-owned; unchanged |
| Entity/scalar store | 208.5 MB | 208.5 MB | zvec-owned; unchanged |
| Reconciliation sidecars | 134.5 MB | 50.2 MB | 62.7% smaller |
| Files/directories/metadata | ~2.2 MB | ~2.2 MB | unchanged |
| **Total** | **980.3 MB** | **899.5 MB** | **80.7 MB saved (8.24%)** |

The ~0.94 GiB/workspace growth is dominated by the searchable vector store
(65%) and entity/scalar data (21%), not duplicate generations or WAL. There was
one active generation and no WAL/temporary-file accumulation. The only large,
safe-to-change component was the per-file reconciliation manifest: JSON stored
two 32/64-character hex fields per entity and a repeated model string per
fragment. It is required metadata, not stale duplication, but its encoding was
unnecessarily large.

## Change

`rust/crates/zg-engine/src/storage/manifest.rs` now writes a versioned compact
binary manifest (`ZGM2`, `file-XXXXXXXX.bin`):

- entity id: 16 bytes; fingerprint: 32 bytes;
- fragment id: 20 bytes; entity id: 16 bytes; model length + UTF-8 bytes;
- embedding/projection fingerprints: 32 bytes each.

Legacy `file-XXXXXXXX.json` manifests remain readable. Publishing removes the
legacy file atomically after the new one is durable. Deletion removes both.
Manifest size estimation now matches the compact encoding, so large-file
manifests are not rejected more aggressively than before.

This is a sidecar serialization change only. The searchable zvec fragment
layout and the `FileManifest` domain data are unchanged.

## Before/after

Single run per condition, same fixture and host:

| Metric | Before | After |
|---|---:|---:|
| Total disk | 980.3 MB | 899.5 MB |
| Index + finalize | 203.13 s | 138.19 s |
| Peak RSS | 2.087 GiB | 2.068 GiB |
| Reconciliation | 134.5 MB | 50.2 MB |

Post-change search latency (direct, 5 measured samples after warmup):
FTS mean 633 ms, vector mean 717 ms. The query path was not changed. Pre-change
search latency was not retained because the old index was replaced during the
rebuild; no before/after latency improvement is claimed.

Incremental insert/delete on the compact index:

- insert 8 KiB: +240,796 bytes, 18.70 s;
- delete and reindex: +245,924 bytes versus baseline, 1.69 s.

The residual delete delta is tombstone/metadata churn, not manifest growth.
Peak PSS was not sampled in this optimization run; peak RSS is reported.

## Correctness and regression

- Compact manifest round-trip, legacy JSON read, truncation/trailing rejection,
  oversized rejection, publish/load/delete: **5 tests pass**.
- Manifest reuse/reconciliation store test: **1 test pass**.
- Targeted vector parity after the change: initial, 8 KiB insert, delete, and
  restart/reopen **4/4 pass**.
- Old JSON index opened by the new binary and returned FTS and vector results
  before the rebuild.
- Full `zg-engine` suite: **549 passed, 3 failed, 39 ignored**. The three
  failures are in `authorization` tests and are unrelated to the storage diff;
  the worktree already contains unrelated authorization changes.
- No watcher/manual/scheduler code changed. No single-writer or index-format
  change was introduced.

## Decision

Keep the compact manifest. It saves 8.24% of total footprint and 62.7% of the
reconciliation sidecar, with no observed correctness or latency regression.
The remaining 88% is zvec-owned vector/entity storage; reducing it would require
an index-format or engine change and is outside this track. #10 is not reopened
by this result.

Evidence:
`benchmarks/harness-v2/operations/storage-footprint/results/summary.json`,
`search-latency-after.json`, `growth-after.json`, `vector-parity-after.json`.
