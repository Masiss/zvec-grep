# Three-way benchmark results — 64-file pilot

Status: **EXPLORATORY / NON-QUALIFYING.** Three repeats per arm, 2-CPU affinity,
release builds, but **no memory cgroup** was applied. Results are useful for
direction, not a production qualification.

## Corpus

- Source: `~/itc/camera/AttendanceSafetyCamera/**/*.cs`
- First 64 lexicographic files, **429,535 bytes**
- Manifest SHA-256: `f5fc630ab75cf3381be1cfecec03ff1593e9f6d2883ad23a1600aae7ce08c4e1`
- 60 files indexed; 4 files were not selected by the text indexer.
- Same corpus copy, model `local/potion-code-16m-v2`, CPU, index concurrency 2.

## Arms

| Arm | Revision | Binary |
|---|---|---|
| Fork | `abaf7bf` | `zg-fork` |
| Upstream | `e1f93d2` | `zg-upstream` |
| Integration | `integration/upstream-rust` + FP16/parallel finalization fixes | `zg-integration` |

All release builds. Fork required its own `ZVEC_JIEBA_DICT_DIR` and
`--index-embedding-concurrency`; those are CLI-version differences, not model
differences.

## Method

Per repeat, fresh corpus and workspace:

1. Initial index.
2. No-op rescan.
3. Append a comment to the largest `.cs` file (24 entities re-indexed).
4. FTS query (`Camera`, limit 10).
5. Reopen query in a new process.
6. Status.

Commands run under `taskset -c 0,1`; `/usr/bin/time -v` recorded wall and max RSS.
Order counterbalanced: integration/upstream/fork, upstream/fork/integration,
fork/integration/upstream. Three repeats; means below.

## Results (mean of 3; seconds / max RSS MiB)

| Phase | Fork | Upstream | Integration |
|---|---:|---:|---:|
| Initial index | 1.83 s / 380 | 1.72 s / 435 | **1.71 s / 382** |
| No-op rescan | **0.50 s / 280** | 0.58 s / 279 | 0.68 s / 279 (median 0.55) |
| Mutation (24 entities) | 1.50 s / 450 | **1.21 s / 508** | 1.38 s / 447 |
| FTS query | **0.42 s / 280** | 0.56 s / 284 | 0.56 s / 284 |
| Reopen query | **0.41 s / 280** | 0.56 s / 284 | 0.56 s / 284 |
| Status | 0.00 s / 33 | 0.41 s / 278 | 0.42 s / 279 |

Raw data: `/home/masis/bench/results-cpu2-raw.json` and
`/home/masis/bench/results-cpu2-summary.json`; per-run logs under
`/home/masis/bench/run/<arm>-cpu2-r{1,2,3}/`.

## Reading

- **Correctness**: all phases exited 0; query results matched.
- **Initial index**: integration is effectively equal to upstream (1.71 vs
  1.72 s) and faster than fork (1.83 s).
- **Mutation**: integration is **13.5% slower than upstream** (1.38 vs 1.21 s)
  and faster than fork (1.50 s). M2 reuse is not producing a wall-time win on
  this corpus/model; manifest publish/read/fetch overhead can exceed the saved
  model2vec embedding work.
- **Memory**: integration peaks lower than upstream in initial and mutation
  (382 vs 435 MiB; 447 vs 508 MiB). This is consistent with fewer live vectors,
  but not proof of reuse.
- **Query/reopen**: integration equals upstream; fork is faster, likely because
  its CLI output path is simpler.

## Bug found during benchmark

The first integration build rejected all reuse because the manifest vector
fingerprint hashed the original `f32` vector while storage is FP16. Decoding
returns FP16 values, so the hash never matched. Fixed to hash the FP16
representation (`half::f16::from_f32(...).to_bits()`), with a regression test.
The final numbers above use the fixed binary.

## Decision

**INCONCLUSIVE / NOT A WIN.** Do not run the 421-file qualification yet.

Before a larger run:

1. Add end-to-end reuse telemetry (vectors reused / manifest hits) so we can
   distinguish "reuse not happening" from "reuse happening but overhead wins".
2. Re-run the 64-file mutation with telemetry and, if reuse is active, batch
   manifest I/O or make M2 opt-in.
3. Only then run 421 files under the approved memory/CPU envelope.

No benchmark authorization or safety gate was changed. The missing memory
cgroup is a measurement-quality limitation, not a pass.
