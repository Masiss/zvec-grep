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

- Metadata fingerprint covers every scanner-discovered path, size, mtime and
  source fingerprint, plus the serialized scan rules.
- Content fingerprint reads the discovered files (bounded to 256 MiB) and
  hashes their bytes. The sidecar stores both fingerprints.
- If metadata matches but content changed with the same size/mtime, the fast
  path does not return no-op. It runs the normal index pipeline with a forced
  content recheck, then refreshes the sidecar.
- Fast path runs only when the workspace is indexed, storage exists, the
  generation/index version match, metadata and content fingerprints match, and
  there are no explicit changes, rebuild, scan/model options, or skipped files.
- Missing, corrupt, legacy-version or stale sidecars force normal indexing;
  when metadata is unchanged the normal pipeline is asked to recheck content.
- On a hit, it writes the workspace manifest update and returns a no-op
  `IndexResult` without acquiring a model or opening the zvec store.
- The sidecar is v2 and is written only after a successful index when the
  pre-index and post-index metadata/content fingerprints are equal. It is
  removed before any normal index attempt and refreshed after a successful
  incremental change or rebuild.
- The sidecar is a startup hint, not searchable index state. Legacy `ZGM2`
  sidecars and existing index layouts are untouched.

The content check closes the same-size/same-mtime false-no-op gap for a
workspace with a usable sidecar. It is deliberately bounded: above 256 MiB, or
when any file changes while being read, no sidecar is written and the normal
path is used.

## Before/after

Single host, one fixture, repeated direct runs:

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Warm no-op p50 | 554.67 ms | **60.57 ms** | ~9.2× faster |
| Warm no-op p95 | 577.63 ms | **71.80 ms** | ~8.0× faster |
| Peak PSS | 316,864 KiB | **33,628 KiB** | ~89.4% lower |
| Peak RSS / process | ~320 MB | ~36 MB | lower |
| Fast no-op CPU | — | ~0.05 s user+sys | no model/storage open; reads fixture content |

Incremental one-file change: 29.87 s, peak RSS 1.11 GB; next no-op 0.03 s.
Rebuild: 170.73 s, peak RSS 2.06 GB; post-rebuild no-op 0.04 s.

## Correctness and regression

- Fast-path test: initial index, warm no-op with a fresh model manager (zero
  model runtimes acquired), delete, missing-sidecar fallback, corrupt-sidecar
  fallback, and recovered fast no-op.
- Same-size/same-mtime content test: forced recheck updates FTS and vector;
  the old token is absent and the new token is present.
- Indexing service tests: **29 passed** (after freeing disk for rebuild tests).
- Compact manifest tests: **5 passed**; ZGM2 compatibility retained.
- Existing fixture: FTS and vector both return `file_0000.rs`; 2,000 `.bin`
  manifests, zero legacy `.json` manifests.
- Fast path checks storage existence and manifest generation/index version; it
  does not publish an index or run finalize.
- No scheduler, watcher, UX, policy, threshold, or index-format change.

## Decision

Keep the optimization: it removes the dominant 407 ms storage-open cost and
~283 MB peak PSS from warm direct no-ops, with content verification and no
observed correctness regression. The first no-op after a sidecar is absent
still takes the normal path (measured ~1.38 s in this run); subsequent warm
runs are fast.

Limits: single-run incremental/rebuild timings; no forced cold page-cache
drop; no 45-run qualification rerun. A deeply corrupted storage tree is not
fully verified by the fast path unless `IndexStore::exists` reports it missing;
deep integrity still requires opening storage. TOCTOU is reduced by read-time
stat checks and pre/post fingerprints, but a source that changes and reverts
entirely within one index attempt cannot be ruled out.

Evidence: `benchmarks/harness-v2/operations/startup-direct/results/`.
