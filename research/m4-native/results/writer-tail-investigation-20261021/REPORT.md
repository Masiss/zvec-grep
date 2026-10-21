# M4 writer-tail investigation — native open and cached fingerprint candidate

**Status: PARTIAL — one recurring open cost is identified and one research-only optimization is measured; the 369.428 ms cycle-7 outlier is not fully explained.** No 500×20 or other sustained native campaign was launched. All experiments below are bounded synthetic/local microbenchmarks under the existing M4 hard caps (1 GiB memory, swap 0, CPU 200%, tasks 128); no production/native-library source or benchmark policy was changed.

## Evidence and call path

The canonical 500×20 single-arm receipt is `../pending-drain-native-20261021/REPORT.md`, with per-round rows in `../pending-drain-native-20261021/per-round.csv`. Its zero-based cycle 7 compacts and has writer commit 369.428 ms, native vector build 223.503 ms, native collection open 180.306 ms, M4 update 104.606 ms, and post-M4-to-round-end 37.658 ms. The open-only writer-thread CPU was not recorded for that cycle; the 166.893 ms writer-thread CPU is for the entire native build, not the open call. Do not infer the event's CPU/I/O split from wall time.

Source trace in `research/m4-native/tests/m4_bridge_integration.cc`:

1. `BuildDeltaVectorSnapshot` chooses the current delta or, at the shard threshold, all live rows for a compacted base; it creates a fresh vector collection via `OpenVector` and then upserts and flushes rows (around lines 1485–1607).
2. `OpenVector` assembles the vector schema and calls `zvec_collection_create_and_open` (around lines 145–190). Schema assembly measured <0.1ms across the probes.
3. The native build records open, row conversion, upsert and flush separately. Generation metadata is durable-written; then M4 snapshot clone/open/apply/flush runs; finally the existing finalize/publish/retire path completes. No change to compaction, flush ordering, publication, or snapshot lifecycle was made.

### Native collection-open microbench

`collection_open_microbench.cc` creates and closes fresh empty collections, isolating schema construction from `zvec_collection_create_and_open`. It ran 3 iterations per case in a private `/var/tmp` root, at the unchanged 1 GiB / swap 0 / CPU 200% / tasks 128 caps. Raw JSONL and before/after host context are retained beside this report (`collection-open-20261021T031913Z.jsonl`, `collection-open-schema-20261021T032148Z.jsonl`, matching `host-*.json` and stderr files).

The later schema-instrumented run measured:

| Schema | Open wall ms (3 samples) | Open calling-thread CPU ms | Schema assembly wall ms |
|---|---:|---:|---:|
| Exact six scalar fields + HNSW | 82.851, 59.199, 63.770 | 42.016, 33.830, 35.663 | 0.008–0.021 |
| Same six scalar fields + FLAT | 62.908, 65.812, 74.396 | 34.917, 38.397, 31.145 | 0.008–0.013 |
| No scalar fields + HNSW | 8.704, 8.158, 8.335 | 3.868, 3.595, 3.238 | 0.007–0.012 |

**Interpretation:** under this empty-collection fixture, six scalar-index fields account for most of the repeatable create/open cost (HNSW median63.770ms, range59.199–82.851ms; FLAT median65.812ms, range62.908–74.396ms) versus no-scalar median8.335ms, range8.158–8.704ms. HNSW versus FLAT with the same scalar schema has no measurable material difference in these three-sample runs. This rejects HNSW construction as the explanation for ordinary collection-open cost; it does not prove anything about populated-index build/search cost. Removing scalar indexes is not a valid feature-equivalent optimization without changing filter semantics, so no such change is recommended. The experiment does not explain why cycle 7 reached180.306ms—nearly2× the later untraced probe's maximum—and cannot distinguish an event-specific native path from I/O/scheduling/host effects. No open-only or event-specific profiler/syscall breakdown was collected.

A follow-up decomposed the scalar schema into six one-index-at-a-time cases (3 opens each; raw output `collection-open-field-20261021T033207Z.jsonl`). In that later run, the full HNSW schema median was79.600ms (77.681–80.253), full FLAT median78.572ms (72.931–90.799), and no-scalar HNSW median12.736ms (10.455–13.380). Each individual field alone had a median30–41ms (logical PK32.639, generation36.515, file ID34.941, ancestor directories40.130, file name41.313, symbol type36.376); one logical-PK sample was a 100.174ms outlier. No single scalar field is isolated as the cause, and one-field costs are not additive (the shared six-field schema is faster than their sum). This is a local three-sample result, not a stable per-field ranking. Host context was load1=4.29, MemAvailable about4.9–5.0GiB and I/O-full PSI avg10 about1.9–2.1%.

A bounded `strace -f -c -w` summary on three fresh exact-HNSW opens versus three no-scalar opens (`strace-exact-hnsw-20261021T033356Z.summary.txt`, `strace-no-scalar-hnsw-20261021T033400Z.summary.txt`) saw 1,501 versus385 futex calls, 96 versus12 fsync calls, and63 versus15 fdatasync calls. This is whole-process accounting (initialize, three create/close cycles, shutdown); sync calls may come from close/shutdown rather than the timed open. Summed syscall wall time is across threads and `strace` heavily perturbs actual open latencies. Native source confirms empty segment creation passes all indexed scalar fields to `InvertedIndexer::CreateAndOpen` (`segment.cc:570, 4006–4046`; `inverted_indexer.cc:40–83`), which creates/opens its RocksDB context and per-field indexers. The trace is consistent with more synchronization/synchronous-persistence work in the scalar-index lifecycle, alongside measurable calling-thread CPU; it does not map syscalls to a specific field or lifecycle phase, isolate device I/O, or explain the untraced cycle-7 outlier. No device-I/O attribution is available.

### Existing M4 cached-fingerprint candidate, 500 docs × 5 rounds

The existing explicit research modes were run once each, sequentially, baseline first and candidate second, using fresh roots and the same full-sync fixture and hard caps. Outputs and host-context snapshots are `incremental-fts-sustained-20261021T032013Z.jsonl` and `incremental-fts-sustained-cached-fingerprint-20261021T032015Z.jsonl` plus paired `host-*.json` files.

| Metric | Strict baseline (`--incremental-fts-sustained`) | Candidate (`--incremental-fts-sustained-cached-fingerprint`) |
|---|---:|---:|
| M4 open, rounds 0–4 (ms) | 80.22, 80.96, 79.28, 84.88, 78.71 | 37.93, 38.73, 38.44, 43.48, 39.11 |
| M4 update, rounds 0–4 (ms) | 91.74, 92.53, 91.53, 99.52, 90.06 | 49.12, 49.58, 57.83, 56.56, 49.55 |
| Commit p50 / p95 (ms, n=5) | 216.28 / 258.48 | 191.02 / 232.65 |
| Compaction round (round 3) native open (ms) | 59.96 | 59.70 |
| Compaction round M4 open (ms) | 84.88 | 43.48 |

The candidate keeps one analyzer fingerprint pass (~36–42ms) and skips the second cached-analyzer verification pass (~38–42ms); it does not alter native collection open. In this small sequential fixture, the compaction-round M4 open fell41.40ms and total M4 update fell42.96ms, while native open was effectively unchanged. Commit p50 moved down25.26ms; p95/max moved down25.83ms. Host snapshots around the two arms show similar load (4.18–4.20) and MemAvailable (4.72–4.92GiB), but this was not a stationary baseline/control or repeated/order-balanced comparison. Treat whole-commit changes as directional only: one 5-round arm per condition, candidate runs second, with notable native-open/build outliers (candidate round0 native open130.74ms). This is not a repeatability or qualification claim.

Both runs reopened generation 6 with 500 live FTS docs and zero vector/FTS coherence errors. Process peak RSS was 216,632 KiB baseline versus 211,092 KiB candidate; process-reported write bytes were 60,342,272 versus 60,981,248 (not device-write attribution); roots were 11,473,185 bytes. These small-run observations do not establish a memory or I/O regression/improvement.

The optimization switch already exists in `m4::Options` and is opt-in. Because it bypasses a separate post-open source-fingerprint verification on cache hits, keep strict behavior as the default. Existing `m4_analyzer_cache_lifecycle` regression covers cache lifecycle and mutation behavior; a newly built binary passed (1/1, 6.46 s). The existing `--incremental-fts-candidate` integration suite also passed: 582 checks, 0 failures; its compaction/reopen/recovery/coherence checks ran under the unchanged resource limits. No algorithm/default was changed. A future choice to promote this optimization requires explicit review of the dictionary-mutation/verification tradeoff and a fresh, separately authorized, durability-qualified native measurement.

## Failed/limited attempts, authority, and artifacts

One initial launch failed to execute because the probe binary was absent (exit127; empty output); a second probe aborted with SIGABRT under a locally imposed `TasksMax=32`, below the existing authorized128. Its journal contains worker-thread stacks, but there is no pids.events receipt proving task exhaustion, so the exact cause is unknown. Both are retained and excluded. These were not project runtime-guard rejections or M4 campaign arms. Subsequent probes used exactly the existing128 task cap; no cap was raised beyond policy. Neither failed attempt is erased or reclassified.

A first `ctest` invocation found that the lifecycle test binary was absent and reported `Not Run`; after building the current target, the test passed. No failed correctness assertion was hidden.

The current evidence exhausts the useful small local experiments: scalar-index initialization is the dominant ordinary empty-collection setup cost; HNSW is not; cycle-7's extra open tail remains unexplained; the safe-by-default M4 double fingerprint check is a separately measurable candidate optimization. No 500×20, additional native campaign, process-affinity/host-contention intervention, or benchmark-policy change is authorized/performed here. Existing live-workspace edits from other tasks were left untouched.

Artifacts: this report, `collection_open_microbench.cc`, JSONL outputs, strace summaries, stderr logs, and before/after host snapshots are retained in this directory. `systemd-run --wait --collect` retired the cgroups. Successful collection-open microbench runs removed their own synthetic roots on exit; the failed TasksMax32 attempt left an empty root directory, retained and not reused. The two 500×5 roots remain under `/var/tmp` (each `du -sb` 11,496,089 bytes; process logical root metric11,473,185); the candidate-recovery root remains at10,963,735 bytes. All were verified and left untouched after the runs.