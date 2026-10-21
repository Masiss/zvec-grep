# Bounded 0–6 scalar-open probe: footprint and resource plan

## Current interpretation after contract amendment — 2026-10-21

**Current status: PARTIAL / MEASUREMENT_INCOMPLETE.** Under the amended resource-only exploratory contract, load-1 is observational rather than a hard gate, and positive host swap/reclaim deltas alone are not distress. The 84-open run's load 5.19 and positive swap/direct-scan deltas therefore are not independent blockers; attribution remains unknown. The workspace observer's `MUTATION_COVERAGE_LOST` still leaves accounting `UNKNOWN`/`INCOMPLETE`, so this is not a complete resource measurement, verified peak, PASS, or qualification result. The workload completed and cleanup was verified; no retry or new run is authorized. See [contract amendment](CONTRACT-AMENDMENT-20261021.md). The detail below preserves the report's original contemporaneous evaluation and raw facts; the amendment supersedes its prior load/delta gate interpretation without rewriting receipts.

**Original contemporaneous status: PARTIAL / OPERATION_BLOCKED.** One owner-authorized 84-open resource rehearsal completed its workload, but the initial interpretation treated workspace coverage loss, load above the historical gate, and host swap/direct-reclaim movement as independent reasons the resource measurement was incomplete. The workspace coverage loss remains a current limitation; the load/delta classification is superseded by the amendment above. No retry was made. No latency benchmark or qualification run occurred.

## Findings

1. The intended diagnostic is small in *workload shape* relative to the 500×20 writer campaign: seven scalar counts (0–6) × two vector configurations (HNSW/FLAT) × three repetitions; each configuration does one empty fresh create and one existing-collection reopen. That is **84 serial API opens**, with one collection directory at a time and no inserted documents or queries. The existing authorization recorded in §64 was 1 GiB RAM, swap 0, CPU 200%, 128 tasks, and a 20-second operation timeout; it was exercised only for a read-only preflight, not for a launch.
2. The single rehearsal retained 1 GiB RAM, swap 0, CPU 200%, 128 tasks, 20-second workload watchdog, and owner-approved 3 GiB MemAvailable / 8 GiB free-disk floors. The 84 calls completed in 3.4919 s (workload-reported 3.4706 s); this is a resource-run duration, **not latency benchmark evidence**. Sampled cgroup memory peak was 49,082,368 B, swap peak 0, and task count peaked at 66. These are single-run observations, not grounds to lower caps. The independent workspace observer did not produce complete coverage.
3. Native source explains the bounded work: fresh create initializes IDMap, an empty DeleteStore snapshot, VersionManager, and one writing Segment. With scalar indexes, that Segment opens one shared inverted RocksDB context and creates per-field column indexers. Six chosen fields produce 12 RocksDB column families (six terms, plus the enabled array-length, range, and reversed-term families, and one shared CDF); zero scalar fields skip the scalar DB. There are no input rows, so this does not build an HNSW graph, populate FLAT vectors, or flush user postings. Reopen restores metadata, IDMap/DeleteStore and the writing Segment, opens existing scalar indexes, and performs the normal empty-WAL recovery path.
4. Close is outside the prior open timer. `CollectionImpl::close_unsafe()` calls flush, but `SegmentImpl::flush()` returns immediately when the WAL has no record. The resource harness now measures final per-root size after close and before removal: maximum 126,779 apparent B / 163,840 allocated B across 42 roots. This is not a transient peak; do not infer zero I/O from the empty-WAL fast path.
5. Current storage/host inventory is in the attached receipts. A read-only inventory found **209 `/var/tmp/m4-*` directories, 1,415,430,144 B allocated by `du`, all owned by `masis:masis`**. The current filesystem is shared Btrfs; `btrfs` accounting tools are unavailable, so these values are not exclusive physical-byte attribution. No broad cleanup is justified from names or age alone.

## Source and workload trace

- Probe schema/matrix and lifecycle: `research/m4-native/results/writer-tail-investigation-20261021/collection_open_microbench.cc:38-130`. It constructs six fields (`logical_pk`, `generation`, `file_id`, `ancestor_directory_ids`, `file_name`, `symbol_type`), an empty 4-D FP32 vector, and HNSW or FLAT; its existing runs time only fresh `create_and_open`, close, then delete the path. It does not measure reopen or directory footprint.
- M4 fingerprint/cache path: `research/m4-native/src/index.cc:68-92,135-207,1341-1445,3750+`. Fingerprinting precedes cache lookup; the opt-in shortcut applies only to an initial warm hit. The weak cache is process-local. The new regression launches a separate executable process, which must take the cold-cache, two-fingerprint path.
- Native API dispatch: `.local/native/zvec-native-profile/src/db/collection.cc:342-359,388-410,451-476,2166-2218,2345-2403` (`CreateAndOpen`/`Open`, dispatch, close/flush, recovery, create).
- Segment path: `.local/native/zvec-native-profile/src/db/index/segment/segment.cc:491-598,2164-2250,4006-4050`. Create initializes scalar index blocks; open loads persisted forward/scalar/FTS/vector blocks and recovers the writing segment; empty WAL short-circuits flush.
- RocksDB family construction: `.local/native/zvec-native-profile/src/db/index/column/inverted_column/inverted_indexer.cc:24-80`.

Prior open-only samples are planning evidence, not a bound: the 2026-10-21 schema probe (3 samples/case) had fresh-create medians 63.77 ms HNSW-six, 65.81 ms FLAT-six, and 8.34 ms no-scalar; the field probe had medians 79.60 ms HNSW-six, 78.57 ms FLAT-six, 12.74 ms no-scalar, and 32.64–41.31 ms for the six one-field controls. These runs had variable host conditions and did not time reopen or capture per-case storage. Do not multiply these medians into a promised duration.

## Actual retained footprint (read-only; no cleanup)

| Owned path | `du -sx -B1` allocated | Disposition |
|---|---:|---|
| `research/m4-native/.work/build` | 10,788,864 B | Keep: current CMake/test build. |
| `research/m4-native/.work/tmp` | 112,660,480 B | Keep except the narrowly identified snapshot cleanup candidate below; other scratch was not reviewed for deletion. |
| `research/m4-native/results` | 598,994,944 B | Keep: raw reports/receipts are evidence. |
| `.local/native/zvec-native-profile` | 947,531,776 B | Keep: native source checkout, with local research changes. |
| `.local/native/zvec-native-build` | 1,636,302,848 B | Keep: required native build/dependency for focused tests and probes. |
| `/var/tmp/m4-*` directories (209) | 1,415,430,144 B | Preserve: individual roots are not all mapped to lifecycle receipts; no bulk cleanup. |

Within `.work/tmp`, **20 `m4-analyzer-snapshot-*` directories total 111,902,720 B by `du` (5,595,136 B each)**, two files per directory. Their two file hashes match the pinned Jieba dictionary and HMM model (`analyzer-snapshot-hashes.txt`); the source `Analyzer::Impl` owns and removes its snapshot directory on destruction (`research/m4-native/src/index.cc:60-64`). They are older than the current test and the test created no new snapshots. No matching descriptor was found among readable host `/proc` fd tables; 358 PIDs were unreadable/raced, `lsof` reported unrelated container-overlay warnings, and the three running containers mount unrelated data paths. Therefore reproducibility is verified, but complete non-use is not. This is only a candidate to recheck—not an approved cleanup. **Nothing was deleted, and no cleanup is currently proposed for execution.** Btrfs-exclusive space reclaim is unknown.

The detailed, read-only file inventories are `var-tmp-m4-footprints.tsv`, `var-tmp-m4-path-metadata.tsv`, and `worktmp-snapshot-inventory.tsv`; the `/var/tmp` `du` scan completed at approximately 2026-10-21T07:13:06Z. `host-observation.json` is an observational snapshot, **not** an admission/preflight receipt. At 2026-10-21T07:21:35Z it recorded MemAvailable 5,089,764 KiB, `/var/tmp` free 10,809,724,928 B, load-1 3.61, I/O PSI full avg10 1.47%, and memory PSI full avg10 0.17%. The old §64 gates (5,477,762 KiB, 18,219,000,000 B, load-1 ≤3.14) were not met in that snapshot. Inventory sizes predate this report and the later hash receipt; the `results` directory is now slightly larger. Do not interpret this as a new preflight or authorize launch from it.

## Owner-approved resource rehearsal and outcome (2026-10-21)

The owner approved **3 GiB MemAvailable and 8 GiB free disk for this rehearsal only**, retaining MemoryMax 1 GiB, swap 0, CPUQuota 200%, TasksMax 128, and the 20-second workload timeout. The proposal was corrected from 28 to the full **84 opens** before launch: 0–6 scalar counts × HNSW/FLAT × three repetitions, each create/close/reopen/close, one empty root at a time, no documents or queries.

Fresh readiness at `2026-10-21T07:57:50Z`: MemAvailable 3,626,200 KiB and `/var/tmp` free 10,738,757,632 B. Both owner-approved floors passed. **However**, host load-1 was 5.19 (unchanged historical limit 3.14); this failure was recorded as nonblocking by the initial operation-context implementation. That was incorrect: only MemAvailable and disk thresholds were authorized to differ. The run therefore violated an unchanged admission guard. The guard implementation has since been corrected and regression-tested to enforce every historical gate except the two explicitly replaced floors. No retry is authorized or performed.

The workload returned 0 after 84 API opens; its internal total was 3,470.62 ms. `command-start` to `command-exit` was 3.4919 s; the watchdog sample window was 3.5551 s. Systemd `RuntimeMaxSec` was 25 s, watchdog runtime limit 20 s from workload start, watchdog wait 32 s, and controller wait 28 s; these layer deadlines bound the operation but do **not** mean the whole start-to-cleanup interval is capped at 20 s. Peak observed cgroup memory was 49,082,368 B (46.8 MiB), swap 0, task count 66/128, CPU usage 2,845,805 µs, sampled process-tree PSS/RSS maxima 54,908/69,136 KiB, with no cgroup memory/swap/pids max or OOM events and no CPU throttling. The cgroup I/O controller is unavailable (`io.stat`/`io.max` absent); process-tree write snapshot max was 117,190,656 B, not physical-device attribution. The 1 GiB apparent/allocated workspace thresholds are monitored soft budgets, not filesystem quotas or hard capacity proof.

Across complete samples, MemAvailable minimum was 3,438,784 KiB and free disk minimum 10,730,123,264 B, both above the approved floors. But host load remained 5.02–5.19 and host vmstat changed during the operation: swap-in +177 pages, swap-out +1,576 pages, direct-scan +270 pages; host OOM count did not change. Attribution is unknown. The M4-specific distress check has since been strengthened to stop on host swap/direct-reclaim/OOM movement; this condition would have blocked/aborted the observed run.

**Formal measurement result: INCOMPLETE / OPERATION_BLOCKED.** Workspace traversal was incomplete in 6/34 samples (4 `PARTIAL_KNOWN_RACE`, 2 `UNVERIFIABLE`): it observed a RocksDB log-file disappearance and directory mutations, then `MUTATION_COVERAGE_LOST` (“events exceed one bounded drain”). Sampled workspace accounting is not a verified peak. Native harness output measured maximum final per-root size as 126,779 apparent B / 163,840 allocated B before per-root cleanup, but does not establish transient peak. No retry after this telemetry/guard failure. No latency, qualification, or production claim.

Raw receipts and command output: `m4-scalar-open-rehearsal-20261021/` (fresh readiness, watchdog runtime JSONL/summary, host before/after, cgroup/unit cleanup, stdout/stderr, command start/exit, and operation result). The transient unit is `not-found`/`inactive`; cgroup and owned empty `/var/tmp` workspace were verified absent. No other `/var/tmp/m4-*` or project artifact was removed.

No diagnostic or benchmark may follow from this rehearsal. Any future attempt requires a separate owner decision after fixing the observer/guard design; this operation is not automatically retried.

## Focused correctness verification

`research/m4-native/tests/analyzer_cache_lifecycle.cc` now adds a fresh-`exec` reopen check against a persisted M4 generation. The child asserts a cold cache miss, one initialization, two fingerprint passes, and no skipped verification even when the candidate option is enabled. Build and focused CTest:

```text
cmake --build research/m4-native/.work/build/durability-restore-20261020 --target m4_analyzer_cache_lifecycle -j2  PASS
ctest --test-dir research/m4-native/.work/build/durability-restore-20261020 \
  -R '^m4_analyzer_cache_lifecycle$' --output-on-failure --timeout 45             PASS 1/1, 3.95 s
```

CTest log: `research/m4-native/.work/build/durability-restore-20261020/Testing/Temporary/LastTest.log` reports 38 checks, 0 failures. Test cleanup left the prior 20 snapshot directories unchanged. Active LSP for the changed C++ file timed out at 20 s and is inconclusive. The new exact-workload harness builds; focused containment-policy tests pass 36/36. One resource rehearsal ran as detailed above; it is incomplete and not a benchmark.

## Original disposition (superseded by the contract amendment above)

**At report time: OPERATION_BLOCKED.** The contemporaneous evaluation treated the load and positive swap/reclaim observations as gate failures, alongside the incomplete workspace telemetry. The contract amendment above supersedes only the load/delta classification; workspace accounting remains UNKNOWN/INCOMPLETE and the resource measurement remains incomplete. The one rehearsal is spent; no retry, diagnostic, latency benchmark, or qualification run is authorized. The runner now enforces the scoped amended contract and the focused policy tests pass.

*Research only. No production, schema, durability, or qualification claim.*
