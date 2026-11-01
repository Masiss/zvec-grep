# Direct-path Startup Optimization

Status: **implemented and verified; keep the clean-scan fast path.**
Baseline commit: `3be680b`. Searchable index format and reconciliation `ZGM2`
manifests are unchanged.

## Scope

Reduce direct `zg --index` startup latency and memory for a warm no-op without
changing correctness, retrieval parity, recovery, scheduler, UX V2, policy,
thresholds, or the searchable index format. No Deferred Maintenance #10 or
concurrent native optimize.

## Profile

Fixture: one workspace, 2,000 files × 8 KiB, already indexed.

| Component | Before | Notes |
|---|---:|---|
| CLI/process startup | ~7 ms / 25 MB PSS | `--version` |
| Engine storage open | **407 ms** | `IndexStore::open`; loads zvec state |
| Scan + no-op pipeline | 29 ms | 2,000 unchanged files |
| Storage retire/close | 36 ms | read/write handles |
| **Warm direct no-op total** | **~555 ms / 317 MB PSS** | p50 554.67 ms, p95 577.63 ms |

`strace` showed no model artifact or tokenizer opens during the no-op. The
bottleneck was opening the full read/write index store and loading zvec state,
not model construction or CLI parsing. A daemon warm no-op is ~0.04 s client
latency with ~115 MB resident daemon PSS; that is a different process scope and
is not compared directly with the direct CLI's 32 MB.

## Change

Added a small, optional `clean-scan.json` workspace sidecar plus a direct
startup fast path:

- Fingerprint covers every scanner-discovered path, size, mtime and source
  fingerprint, plus the serialized scan rules.
- Fast path runs only when the workspace is indexed, storage exists, the
  generation/index version match, the sidecar fingerprint matches, and there
  are no explicit changes, rebuild, scan/model options, or skipped files.
- On a hit, it writes the workspace manifest update and returns a no-op
  `IndexResult` without acquiring a model or opening the zvec store.
- The sidecar is written only after a successful index when the pre-index and
  post-index fingerprints are equal. It is removed before any normal index
  attempt and refreshed after a successful incremental change or rebuild.
- The sidecar is a startup hint, not searchable index state. Legacy `ZGM2`
  sidecars and existing index layouts are untouched.

This uses the same metadata quick-check already used by the indexer
(size + mtime + content-hash presence). A changed mtime/size falls back to the
normal path. Same-size/same-mtime content changes remain subject to the
existing indexer behavior, not a new semantic.

## Before/after

Single host, one fixture, repeated direct runs:

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Warm no-op p50 | 554.67 ms | **33.21 ms** | ~16.7× faster |
| Warm no-op p95 | 577.63 ms | **37.30 ms** | ~15.5× faster |
| Peak PSS | 316,864 KiB | **32,588 KiB** | ~89.7% lower |
| Peak RSS / process | ~320 MB | ~36 MB | lower |
| Fast no-op CPU | — | ~0.04 s user+sys | no model/storage open |

Incremental one-file change: 29.87 s, peak RSS 1.11 GB; next no-op 0.03 s.
Rebuild: 170.73 s, peak RSS 2.06 GB; post-rebuild no-op 0.04 s.

## Correctness and regression

- Fast-path test: initial index, warm no-op with a fresh model manager (zero
  model runtimes acquired), then delete and another warm no-op.
- Indexing service tests: **28 passed**.
- Compact manifest tests: **5 passed**; ZGM2 compatibility retained.
- Existing fixture: FTS and vector both return `file_0000.rs`; 2,000 `.bin`
  manifests, zero legacy `.json` manifests.
- Fast path checks storage existence and manifest generation/index version; it
  does not publish an index or run finalize.
- No scheduler, watcher, UX, policy, threshold, or index-format change.

## Decision

Keep the optimization: it removes the dominant 407 ms storage-open cost and
~284 MB peak PSS from warm direct no-ops, with no observed correctness
regression. The first no-op after a sidecar is absent still takes the normal
path (measured ~1.38 s in this run); subsequent warm runs are fast.

Limits: single-run incremental/rebuild timings; no forced cold page-cache
drop; no 45-run qualification rerun.

Evidence: `benchmarks/harness-v2/operations/startup-direct/results/`.
