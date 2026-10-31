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
  and faster than fork (1.50 s). The later paired diagnostic shows reuse is
  active; the remaining gap is dominated by finalization, not the embedding
  stage.
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

## Paired M2 ON/OFF diagnostic — 2026-10-31

Same integration binary, same corpus/workload, 2-CPU affinity, 3 repeats,
counterbalanced order (`off,on,upstream` / `on,off,upstream`). M2 was disabled
only with the diagnostic env `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1`; the default
remains ON. Upstream `e1f93d2` was the control. No memory cgroup.

Mutation changes one existing fragment and appends one new entity, so the
expected result is 22 reused + 2 computed fragments.

| Metric (mean) | M2 ON | M2 OFF | Upstream |
|---|---:|---:|---:|
| Mutation wall | 1.243 s | 1.340 s (median 1.250) | 1.113 s |
| Mutation CPU | 0.927 s | 0.923 s | 0.990 s |
| Peak RSS | 458 MiB | 458 MiB | 518 MiB |
| `index_embedding` | **0.88 ms** | **8.36 ms** | 8.20 ms |
| `index_prepare` | 0.83 ms | 0.45 ms | 0.37 ms |
| `index_commit` | 12.75 ms | 14.74 ms | 9.81 ms |
| `index_optimize` | 680.7 ms | 773.9 ms (median 694.1) | 561.7 ms |
| Manifest load | 0.030 ms | — | — |
| Vector fetch | 0.317 ms | — | — |
| Manifest publish | 2.99 ms | 4.79 ms | — |
| Vectors reused | **22** | 0 | — |
| Embeddings computed | **2** | 24 | 24 |

Raw rows: `/home/masis/bench/reuse-diag/diag-raw.json`; per-run outputs under
`/home/masis/bench/reuse-diag/<variant>-r{1,2,3}/`.

### Reading

- **Reuse is active.** Manifest hit file: 1/1; 22 of 24 fragments reused, and
  exactly the changed + new fragments (2) were embedded.
- **Manifest I/O is not the bottleneck.** Load + fetch + publish ≈ 3.3 ms,
  while embedding drops by ≈ 7.5 ms. The net is a small win (single-digit ms).
- **Total mutation is dominated by `index_optimize` (≈ 0.68 s).** M2 cannot
  move wall time much at this corpus size. The integration's optimize is also
  slower than upstream (0.68 s vs 0.56 s), which is an M3/finalization issue,
  not M2.
- **Correctness:** ON, OFF, and upstream query outputs have the same SHA-256;
  all runs exited 0. The changed and new fragments were not reused.
- **Batching is not justified yet.** Publish is ≈ 3 ms for one manifest; adding
  batching would add complexity without evidence that it dominates. Revisit
  only if a larger corpus shows manifest I/O growth.

## Decision

**M2 reuse is functional and net-positive, but small.** It saves ~7.5 ms of
embedding work per 64-file mutation and costs ~3.3 ms of manifest I/O; the
remaining mutation time is finalization. This is **not** a 421-file
qualification and does not justify changing defaults or batching.

Next: run the 421-file campaign only under the approved memory/CPU envelope,
and keep upstream as the control. Do not claim a wall-time win from M2 alone.

No benchmark authorization or safety gate was changed. The missing memory
cgroup is a measurement-quality limitation, not a pass.

## M3-B overlap candidate — 64-file mutation with concurrent query load

Status: **EXPLORATORY / NON-QUALIFYING. Candidate REJECTED and rolled back.**
Same 64-file corpus, `taskset -c 0,1`, release builds, **no memory cgroup**.
Three counterbalanced repeats per arm (`base`, `candidate`). The base arm is
`d2344ed`; the candidate adds fragment/dirty-collection overlap
(`5ac1b01`). Both arms were built with the identical diagnostic per-stage
telemetry (`5741e1d`) so stage walls are comparable.

### Protocol

- Fresh corpus copy per run; initial index, then one append mutation to the
  largest `.cs` file (24 entities) in the same process.
- A concurrent in-process FTS query loop (`Camera`, limit 10,
  `RefreshPolicy::Off`) issued a query every ~15 ms during the mutation, so
  every reader had to contend with the finalization write window.
- Per-run wall, process CPU (`/proc/self/stat`), `/proc/self/io` deltas,
  `VmHWM`, query p50/p95/max, per-stage `index_optimize_*` timings, and query
  parity (10 matches).
- 3 repeats; order alternated `base,candidate` / `candidate,base`.
- Harness: `/home/masis/bench/m3b_overlap_bench.rs`;
  runner: `/home/masis/bench/run_m3b_overlap.sh`; raw rows:
  `/home/masis/bench/m3b/out/<arm>-r{1,2,3}/result.json` (instrumented arms)
  and `/home/masis/bench/m3b/out-uninstrumented-base/` (first base build).

### Results (3 repeats)

| Metric | Base (`d2344ed`) | Candidate (overlap) |
|---|---:|---:|
| Mutation wall (mean / median) | 1,082.8 / 1,111.9 ms | **894.3 / 912.1 ms** |
| `index_optimize` (mean / median) | 815.9 / 846.7 ms | **638.2 / 658.1 ms** |
| Fragment optimize | 485.5–580.1 ms | 591.8–660.7 ms |
| Entities optimize (under lock) | 171.0–177.7 ms | 196.8–251.6 ms |
| Files optimize (under lock) | 259.9–281.6 ms | 323.9–387.3 ms |
| State-lock window = max(entities, files) | **259.9–281.6 ms** | 323.9–387.3 ms |
| Query p50 (mean) | 4.98 ms | 3.53 ms |
| Query p95 (mean / median) | **275.1 / 269.7 ms** | 360.1 / 375.8 ms |
| Query p95, each repeat | 297.1 / 258.5 / 269.7 ms | 325.0 / 375.8 / 379.5 ms |
| CPU user / system (mean) | 583.3 / 253.3 ms | 596.7 / 256.7 ms |
| Peak RSS (mean) | 665.2 MiB | 578.0 MiB |
| I/O write (mean) | 63.08 MB | 58.44 MB |
| Query errors / parity | 0 / 10 matches | 0 / 10 matches |

The first, uninstrumented-base run (base from `d2344ed`, candidate from
`5ac1b01`) reproduced the same direction: mutation wall 1,013.5 → 919.6 ms
and query p95 266.3 → 360.7 ms (mean).

### Reading

- **Correctness**: all runs exited 0, zero query errors, identical query parity
  (10 matches), and marker/retry tests passed.
- **Mutation wall improved**, by ~17% mean in the instrumented pair and ~9%
  in the uninstrumented pair (host noise between runs; treat as direction,
  not a qualified effect). `index_optimize` fell from ~816–847 ms to
  ~638–658 ms.
- **Query latency regressed.** Every candidate repeat exceeded every base
  repeat. The mechanism is visible in the stages: in the base arm the dirty
  collections run alone under the state lock for ~260–282 ms; under overlap
  they run concurrently with native fragment maintenance and contend for the
  same 2 CPUs, stretching the state-lock window to ~324–387 ms. Readers block
  for that window, so p95 rose by ~31% (mean) and crossed the provisional
  300 ms target.
- Fragment optimize itself was slower under the overlap (591–661 ms vs
  486–580 ms), so the overlap also failed to preserve the fragment stage's
  standalone cost.
- RSS and write I/O were lower in the candidate, but that does not offset the
  reader-latency regression.

### Decision

**REJECT / ROLLBACK.** The candidate is reverted in `0981a78`; sequential
finalization is restored. The per-stage telemetry from `5741e1d` is kept as
diagnostic-only. No optimizer-thread change, no 421-file campaign, no
production/default/resource change. A future attempt should avoid stretching
the state-lock window (e.g. port the fork's handle-based maintenance outside
the lock under a lease) rather than merely overlapping native work.
