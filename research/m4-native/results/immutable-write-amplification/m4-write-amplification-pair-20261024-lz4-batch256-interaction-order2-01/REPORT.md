# m4-batch vs m4-batch paired workload profile

M4 stats-event coalescing: control=False, candidate=False.
M4 LZ4 WAL compression: control=False, candidate=True; acceleration=1 vs 1.
Control max_delta_docs: 1536; candidate max_delta_docs: 1536; stats_log_threshold: 4194304 vs 4194304 bytes; mutation batches: 256 vs 256.
Runs: 2; stop reason: none.
Evidence class: **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; measurement quality: **COMPLETE**; performance-eligible: **false**.
Raw logs, profiles, host snapshots, and query-level comparisons are retained alongside this report.

## Resource profile

| Pair | Role | Arm | Process wall (s) | Process CPU (s) | Peak RSS (MiB) | Peak PSS (MiB) | Peak swap (MiB) | Cgroup peak memory (MiB) | Process-tree write bytes | Mean CPU (% one core) | Peak CPU (% one core) | Physical writes (B) | Text-byte WA |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 4.89 | 3.92 | 322.1 | 315.3 | 0.0 | 374.3 | 33513472 | 97.3 | 108.8 | 33509400 | 1.81x |
| 1 | control | m4-batch | 6.01 | 4.96 | 323.1 | 316.4 | 0.0 | 345.8 | 47898624 | 96.6 | 111.2 | 47894500 | 2.59x |

## CPU attribution (process CPU milliseconds, per run)

| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 803.8 | 34.7 | 102.0 | 57.3 | 2918.7 |
| 1 | control | m4-batch | 1186.2 | 57.6 | 156.5 | 83.9 | 3477.5 |

## M4 snapshot write attribution

These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.

| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | initial | 0 | 2743649 | 2941712 | 17 | 1762800 | 4704979 | 2941712 | 10 | 2 | 0 | 0 | 0 |
| 1 | candidate | m4-batch | burst1 | 0 | 3165554 | 3386892 | 122 | 2094333 | 5482106 | 3386892 | 13 | 3 | 0 | 0 | 96 |
| 1 | candidate | m4-batch | burst2 | 0 | 3587533 | 3832024 | 205 | 2418897 | 6252349 | 3832024 | 16 | 4 | 0 | 0 | 184 |
| 1 | candidate | m4-batch | burst3 | 0 | 4009759 | 4268148 | 291 | 2751286 | 7912156 | 5158460 | 19 | 5 | 1 | 1 | 272 |
| 1 | candidate | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |
| 1 | control | m4-batch | initial | 0 | 8033628 | 2941712 | 17 | 1762800 | 4704979 | 2941712 | 10 | 2 | 0 | 0 | 0 |
| 1 | control | m4-batch | burst1 | 0 | 9260462 | 3386892 | 122 | 2094333 | 5482106 | 3386892 | 13 | 3 | 0 | 0 | 96 |
| 1 | control | m4-batch | burst2 | 0 | 10488359 | 3832024 | 205 | 2418897 | 6252349 | 3832024 | 16 | 4 | 0 | 0 | 184 |
| 1 | control | m4-batch | burst3 | 0 | 11717693 | 4268148 | 291 | 2751286 | 7912156 | 5158460 | 19 | 5 | 1 | 1 | 272 |
| 1 | control | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |

## LZ4 WAL codec attribution

Cumulative per-index diagnostics count only eligible frames passed to LZ4; accepted-frame byte savings compare original payload bytes with compressed payload bytes. Compression CPU uses the calling writer thread clock.

| Pair | Role | Arm | Stage | Attempts | Attempted input B | Compressed frames | Accepted input B | Output B | Saved B | Codec CPU ms |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | initial | 10 | 8033430 | 10 | 8033430 | 2743410 | 5290020 | 9.537 |
| 1 | candidate | m4-batch | burst1 | 13 | 9260200 | 13 | 9260200 | 3165240 | 6094960 | 10.856 |
| 1 | candidate | m4-batch | burst2 | 16 | 10488000 | 16 | 10488000 | 3587150 | 6900850 | 12.099 |
| 1 | candidate | m4-batch | burst3 | 19 | 11717300 | 19 | 11717300 | 4009300 | 7708000 | 13.344 |

## Durable WAL replay probe

The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.

| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 2743650 | 547.8 | 66.355 | 2743930 | 0/673 | 602.652 | 600.1 | 2400 |
| 1 | control | m4-batch | 8033630 | 643.5 | 96.149 | 8033910 | 0/1965 | 721.095 | 716.6 | 2400 |

## Latency percentiles (ms; pooled by role, arm and stage)

| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |
|---|---|---|---|---:|---:|---:|---:|
| candidate | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.531 | 1.531 | 1.531 |
| candidate | m4-batch | burst1 | freshness_lag_ms | 1 | 0.014 | 0.014 | 0.014 |
| candidate | m4-batch | burst1 | query_ms | 45 | 0.358 | 0.654 | 0.705 |
| candidate | m4-batch | burst2 | file_delete_batch_ms | 1 | 2.655 | 2.655 | 2.655 |
| candidate | m4-batch | burst2 | freshness_lag_ms | 1 | 0.015 | 0.015 | 0.015 |
| candidate | m4-batch | burst2 | query_ms | 45 | 0.458 | 0.793 | 0.946 |
| candidate | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.507 | 1.507 | 1.507 |
| candidate | m4-batch | burst3 | freshness_lag_ms | 1 | 0.015 | 0.015 | 0.015 |
| candidate | m4-batch | burst3 | query_ms | 45 | 0.525 | 0.966 | 0.990 |
| candidate | m4-batch | initial | query_ms | 45 | 0.306 | 0.541 | 0.564 |
| candidate | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.491 | 2.424 | 2.507 |
| candidate | m4-batch | mutation_calls | batch_update_ms | 3 | 26.324 | 26.365 | 26.369 |
| candidate | m4-batch | mutation_calls | batch_upsert_ms | 13 | 55.491 | 78.727 | 92.690 |
| candidate | m4-batch | reopen | query_ms | 45 | 0.503 | 0.869 | 0.872 |
| candidate | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 10 | 59.203 | 66.355 | 67.114 |
| control | m4-batch | burst1 | file_delete_batch_ms | 1 | 0.999 | 0.999 | 0.999 |
| control | m4-batch | burst1 | freshness_lag_ms | 1 | 0.021 | 0.021 | 0.021 |
| control | m4-batch | burst1 | query_ms | 45 | 0.592 | 1.067 | 1.175 |
| control | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.656 | 1.656 | 1.656 |
| control | m4-batch | burst2 | freshness_lag_ms | 1 | 0.023 | 0.023 | 0.023 |
| control | m4-batch | burst2 | query_ms | 45 | 0.831 | 1.506 | 1.681 |
| control | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.878 | 1.878 | 1.878 |
| control | m4-batch | burst3 | freshness_lag_ms | 1 | 0.020 | 0.020 | 0.020 |
| control | m4-batch | burst3 | query_ms | 45 | 0.742 | 1.325 | 1.378 |
| control | m4-batch | initial | query_ms | 45 | 0.475 | 0.823 | 0.836 |
| control | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.388 | 1.752 | 1.784 |
| control | m4-batch | mutation_calls | batch_update_ms | 3 | 39.882 | 44.753 | 45.186 |
| control | m4-batch | mutation_calls | batch_upsert_ms | 13 | 83.321 | 106.593 | 121.125 |
| control | m4-batch | reopen | query_ms | 45 | 0.715 | 1.231 | 1.319 |
| control | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 10 | 69.356 | 96.149 | 101.419 |

## WAL/system sync counts

Counts below come from `strace -D` on the target process; the syscall trace adds wall-time overhead and is not used as an untraced baseline.
| Pair | Role | Arm | fsync | fdatasync | path contains WAL | WAL sync succeeded |
|---:|---|---|---:|---:|---:|---:|
| 1 | candidate | m4-batch | not traced | not traced | not traced | not traced |
| 1 | control | m4-batch | not traced | not traced | not traced | not traced |

## Correctness and logical parity

All live-doc/token/file snapshots equal: **True**.
Workspace accounting complete: **True**. Incomplete live-write traversal is retained as UNKNOWN/INCOMPLETE, never converted to a capacity PASS.
Independent logical-oracle mismatches: m4-batch **0**, m4-batch **0**; oracle outputs equal between arms: **True**.
Control-vs-candidate retrieval-mismatch stage/query/pair cases: **0**.
Top-10 order mismatch cases: **0**; mean top-10 Jaccard: **1.000**.
Per-query PK set differences, ranking differences, and BM25 score deltas are in `comparison.json`; none are suppressed.

Known `json parse` diagnostic, pair 1, m4-batch, burst1: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst2: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst3: actual=79 oracle=79 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, initial: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, reopen: actual=79 oracle=79 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst1: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst2: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst3: actual=79 oracle=79 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, initial: actual=60 oracle=60 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, reopen: actual=79 oracle=79 missing=0 extra=0; equality=True.

## Guardrails and caveats

- This operation runs one pair in m4-batch→m4-batch order; before each timed P4 stage, every query is issued once untimed to reduce cold-page/arm-order contamination. P4 stages do not drop OS page cache. When enabled, the separate WAL replay probe advises only that fresh index's files with `POSIX_FADV_DONTNEED` and records `mincore` residency; zero resident pages are required to call those target files cold, while system/library caches remain warm. Counterbalanced repeats require fresh operation roots.
- RSS/PSS and CPU are sampled every ~100 ms; CPU utilization is percent of one logical core.
- `/proc/self/io` `write_bytes` is process-attributed physical I/O; cgroup process-tree `write_bytes` is recorded separately. Text-byte WA = process-attributed physical writes / UTF-8 source-text bytes ingested or updated. Neither is a device-wide physical-media attribution; host diskstats deltas are context only.
- The selected arms, mutation APIs, explicit flushes, sync tracing, and optional debt-policy settings are in `config.json`; native collection-batch DML and M4 ApplyBatch are not cross-engine atomic transactions.
- When explicitly enabled, the S5 debt policy is applied only to the M4 candidate after the base flush/reopen; default remains OFF.
- Durability boundaries differ: native appends batched writes and explicitly flushes at stages; M4 ApplyBatch fsyncs each bounded transaction when configured. WAL/system sync counts are reported separately; do not interpret this as equal durability latency.
- FTS/INVERT only: vectors are excluded from both arms; non-indexed `document_id` is omitted as a duplicate of logical PK.
- No native `optimize` or M4 `CompactNow` was invoked; automatic work triggered by each arm’s flush is measured as maintenance.
- Synthetic baseline mix: 20% JSON, 20% logs, 35% code, 25% Markdown/text; 4–10 fragments/file, ~2.8k text characters/fragment. Each burst adds 8 JSON, 8 log, and 4 code files.
- Workspace budgets are monitored soft budgets in this owner-authorized exploratory mode. Live writes may make traversal incomplete; uncertainty is preserved, the receipt remains non-PASS, and the report is never performance-eligible or qualification evidence.
- See `config.json`, raw logs, profiles, containment receipts, and per-arm host-before/after snapshots for provenance.
