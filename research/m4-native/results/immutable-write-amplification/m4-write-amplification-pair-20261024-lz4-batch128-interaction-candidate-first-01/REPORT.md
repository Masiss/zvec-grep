# m4-batch vs m4-batch paired workload profile

M4 stats-event coalescing: control=False, candidate=False.
M4 LZ4 WAL compression: control=False, candidate=True; acceleration=1 vs 1.
Control max_delta_docs: 1536; candidate max_delta_docs: 1536; stats_log_threshold: 4194304 vs 4194304 bytes; mutation batches: 128 vs 128.
Runs: 2; stop reason: none.
Evidence class: **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; measurement quality: **COMPLETE**; performance-eligible: **false**.
Raw logs, profiles, host snapshots, and query-level comparisons are retained alongside this report.

## Resource profile

| Pair | Role | Arm | Process wall (s) | Process CPU (s) | Peak RSS (MiB) | Peak PSS (MiB) | Peak swap (MiB) | Cgroup peak memory (MiB) | Process-tree write bytes | Mean CPU (% one core) | Peak CPU (% one core) | Physical writes (B) | Text-byte WA |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 4.94 | 4.09 | 322.9 | 316.0 | 0.0 | 344.8 | 33931264 | 95.7 | 111.8 | 33927200 | 1.83x |
| 1 | control | m4-batch | 5.75 | 4.75 | 322.8 | 315.9 | 0.0 | 337.3 | 49291264 | 96.8 | 107.0 | 49287200 | 2.66x |

## CPU attribution (process CPU milliseconds, per run)

| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 876.7 | 58.2 | 141.6 | 77.9 | 2933.8 |
| 1 | control | m4-batch | 1102.1 | 62.2 | 169.1 | 93.0 | 3321.3 |

## M4 snapshot write attribution

These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.

| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | initial | 0 | 2746500 | 2941712 | 17 | 1762800 | 4704980 | 2941712 | 19 | 2 | 0 | 0 | 0 |
| 1 | candidate | m4-batch | burst1 | 0 | 3168867 | 3386892 | 122 | 2094333 | 5482107 | 3386892 | 24 | 3 | 0 | 0 | 96 |
| 1 | candidate | m4-batch | burst2 | 0 | 3591552 | 3832024 | 205 | 2418897 | 6252350 | 3832024 | 29 | 4 | 0 | 0 | 184 |
| 1 | candidate | m4-batch | burst3 | 0 | 4014333 | 4268148 | 291 | 2751286 | 7912157 | 5158460 | 34 | 5 | 1 | 1 | 272 |
| 1 | candidate | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |
| 1 | control | m4-batch | initial | 0 | 8033835 | 2941712 | 17 | 1762800 | 4704980 | 2941712 | 19 | 2 | 0 | 0 | 0 |
| 1 | control | m4-batch | burst1 | 0 | 9260714 | 3386892 | 122 | 2094333 | 5482107 | 3386892 | 24 | 3 | 0 | 0 | 96 |
| 1 | control | m4-batch | burst2 | 0 | 10488656 | 3832024 | 205 | 2418897 | 6252350 | 3832024 | 29 | 4 | 0 | 0 | 184 |
| 1 | control | m4-batch | burst3 | 0 | 11718035 | 4268148 | 291 | 2751286 | 7912157 | 5158460 | 34 | 5 | 1 | 1 | 272 |
| 1 | control | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |

## LZ4 WAL codec attribution

Cumulative per-index diagnostics count only eligible frames passed to LZ4; accepted-frame byte savings compare original payload bytes with compressed payload bytes. Compression CPU uses the calling writer thread clock.

| Pair | Role | Arm | Stage | Attempts | Attempted input B | Compressed frames | Accepted input B | Output B | Saved B | Codec CPU ms |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | initial | 19 | 8033460 | 19 | 8033460 | 2746040 | 5287420 | 9.689 |
| 1 | candidate | m4-batch | burst1 | 24 | 9260230 | 24 | 9260230 | 3168290 | 6091940 | 11.187 |
| 1 | candidate | m4-batch | burst2 | 29 | 10488100 | 29 | 10488100 | 3590860 | 6897240 | 12.462 |
| 1 | candidate | m4-batch | burst3 | 34 | 11717400 | 34 | 11717400 | 4013520 | 7703880 | 13.788 |

## Durable WAL replay probe

The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.

| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 2746500 | 536.9 | 33.371 | 2746780 | 0/674 | 614.988 | 612.3 | 2400 |
| 1 | control | m4-batch | 8033840 | 653.2 | 47.733 | 8034110 | 0/1965 | 601.525 | 594.6 | 2400 |

## Latency percentiles (ms; pooled by role, arm and stage)

| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |
|---|---|---|---|---:|---:|---:|---:|
| candidate | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.406 | 1.406 | 1.406 |
| candidate | m4-batch | burst1 | freshness_lag_ms | 1 | 0.030 | 0.030 | 0.030 |
| candidate | m4-batch | burst1 | query_ms | 45 | 0.415 | 0.758 | 0.812 |
| candidate | m4-batch | burst2 | file_delete_batch_ms | 1 | 0.830 | 0.830 | 0.830 |
| candidate | m4-batch | burst2 | freshness_lag_ms | 1 | 0.024 | 0.024 | 0.024 |
| candidate | m4-batch | burst2 | query_ms | 45 | 0.477 | 0.833 | 0.911 |
| candidate | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.542 | 1.542 | 1.542 |
| candidate | m4-batch | burst3 | freshness_lag_ms | 1 | 0.044 | 0.044 | 0.044 |
| candidate | m4-batch | burst3 | query_ms | 45 | 1.059 | 1.716 | 1.794 |
| candidate | m4-batch | initial | query_ms | 45 | 0.518 | 1.078 | 1.265 |
| candidate | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.304 | 1.453 | 1.467 |
| candidate | m4-batch | mutation_calls | batch_update_ms | 6 | 21.659 | 34.707 | 34.918 |
| candidate | m4-batch | mutation_calls | batch_upsert_ms | 25 | 31.032 | 38.479 | 64.194 |
| candidate | m4-batch | reopen | query_ms | 45 | 0.615 | 1.171 | 1.273 |
| candidate | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 19 | 28.814 | 33.371 | 35.069 |
| control | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.352 | 1.352 | 1.352 |
| control | m4-batch | burst1 | freshness_lag_ms | 1 | 0.022 | 0.022 | 0.022 |
| control | m4-batch | burst1 | query_ms | 45 | 0.563 | 1.008 | 1.027 |
| control | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.218 | 1.218 | 1.218 |
| control | m4-batch | burst2 | freshness_lag_ms | 1 | 0.020 | 0.020 | 0.020 |
| control | m4-batch | burst2 | query_ms | 45 | 0.812 | 1.509 | 1.660 |
| control | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.789 | 1.789 | 1.789 |
| control | m4-batch | burst3 | freshness_lag_ms | 1 | 0.027 | 0.027 | 0.027 |
| control | m4-batch | burst3 | query_ms | 45 | 0.876 | 1.582 | 2.108 |
| control | m4-batch | initial | query_ms | 45 | 0.570 | 0.918 | 1.172 |
| control | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.304 | 1.691 | 1.725 |
| control | m4-batch | mutation_calls | batch_update_ms | 6 | 20.134 | 37.089 | 39.164 |
| control | m4-batch | mutation_calls | batch_upsert_ms | 25 | 40.468 | 51.636 | 67.293 |
| control | m4-batch | reopen | query_ms | 45 | 0.797 | 1.413 | 1.419 |
| control | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 19 | 33.243 | 47.733 | 48.772 |

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
