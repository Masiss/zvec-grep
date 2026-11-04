# m4-batch vs m4-batch paired workload profile

M4 stats-event coalescing: control=False, candidate=False.
M4 LZ4 WAL compression: control=False, candidate=False; acceleration=1 vs 1.
Control max_delta_docs: 1536; candidate max_delta_docs: 1536; stats_log_threshold: 4194304 vs 4194304 bytes; mutation batches: 128 vs 256.
Runs: 2; stop reason: none.
Evidence class: **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; measurement quality: **COMPLETE**; performance-eligible: **false**.
Raw logs, profiles, host snapshots, and query-level comparisons are retained alongside this report.

## Resource profile

| Pair | Role | Arm | Process wall (s) | Process CPU (s) | Peak RSS (MiB) | Peak PSS (MiB) | Peak swap (MiB) | Cgroup peak memory (MiB) | Process-tree write bytes | Mean CPU (% one core) | Peak CPU (% one core) | Physical writes (B) | Text-byte WA |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 4.48 | 3.66 | 322.7 | 316.1 | 0.0 | 379.2 | 48488448 | 95.7 | 107.5 | 48484400 | 2.62x |
| 1 | candidate | m4-batch | 4.59 | 3.80 | 322.7 | 316.1 | 0.0 | 344.7 | 47669248 | 96.5 | 107.5 | 47665200 | 2.58x |

## CPU attribution (process CPU milliseconds, per run)

| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 802.2 | 50.8 | 122.4 | 68.4 | 2613.9 |
| 1 | candidate | m4-batch | 844.3 | 53.4 | 127.4 | 72.3 | 2707.3 |

## M4 snapshot write attribution

These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.

| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | initial | 0 | 8033835 | 2941712 | 17 | 1762800 | 4704980 | 2941712 | 19 | 2 | 0 | 0 | 0 |
| 1 | control | m4-batch | burst1 | 0 | 9260714 | 3386892 | 122 | 2094333 | 5482107 | 3386892 | 24 | 3 | 0 | 0 | 96 |
| 1 | control | m4-batch | burst2 | 0 | 10488656 | 3832024 | 205 | 2418897 | 6252350 | 3832024 | 29 | 4 | 0 | 0 | 184 |
| 1 | control | m4-batch | burst3 | 0 | 11718035 | 4268148 | 291 | 2751286 | 7912157 | 5158460 | 34 | 5 | 1 | 1 | 272 |
| 1 | control | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |
| 1 | candidate | m4-batch | initial | 0 | 8033628 | 2941712 | 17 | 1762800 | 4704979 | 2941712 | 10 | 2 | 0 | 0 | 0 |
| 1 | candidate | m4-batch | burst1 | 0 | 9260462 | 3386892 | 122 | 2094333 | 5482106 | 3386892 | 13 | 3 | 0 | 0 | 96 |
| 1 | candidate | m4-batch | burst2 | 0 | 10488359 | 3832024 | 205 | 2418897 | 6252349 | 3832024 | 16 | 4 | 0 | 0 | 184 |
| 1 | candidate | m4-batch | burst3 | 0 | 11717693 | 4268148 | 291 | 2751286 | 7912156 | 5158460 | 19 | 5 | 1 | 1 | 272 |
| 1 | candidate | m4-batch | reopen | 0 | 0 | 4268148 | 291 | 2751286 | 0 | 0 | 0 | 0 | 0 | 0 | 272 |

## LZ4 WAL codec attribution

Cumulative per-index diagnostics count only eligible frames passed to LZ4; accepted-frame byte savings compare original payload bytes with compressed payload bytes. Compression CPU uses the calling writer thread clock.

| Pair | Role | Arm | Stage | Attempts | Attempted input B | Compressed frames | Accepted input B | Output B | Saved B | Codec CPU ms |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|

## Durable WAL replay probe

The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.

| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 8033840 | 539.1 | 35.020 | 8034110 | 0/1965 | 526.391 | 523.7 | 2400 |
| 1 | candidate | m4-batch | 8033630 | 521.5 | 64.286 | 8033910 | 0/1965 | 553.440 | 548.8 | 2400 |

## Latency percentiles (ms; pooled by role, arm and stage)

| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |
|---|---|---|---|---:|---:|---:|---:|
| candidate | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.264 | 1.264 | 1.264 |
| candidate | m4-batch | burst1 | freshness_lag_ms | 1 | 0.017 | 0.017 | 0.017 |
| candidate | m4-batch | burst1 | query_ms | 45 | 0.511 | 0.924 | 1.212 |
| candidate | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.346 | 1.346 | 1.346 |
| candidate | m4-batch | burst2 | freshness_lag_ms | 1 | 0.052 | 0.052 | 0.052 |
| candidate | m4-batch | burst2 | query_ms | 45 | 0.514 | 0.903 | 0.971 |
| candidate | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.699 | 1.699 | 1.699 |
| candidate | m4-batch | burst3 | freshness_lag_ms | 1 | 0.064 | 0.064 | 0.064 |
| candidate | m4-batch | burst3 | query_ms | 45 | 0.780 | 1.314 | 1.514 |
| candidate | m4-batch | initial | query_ms | 45 | 0.395 | 0.772 | 1.010 |
| candidate | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.291 | 1.577 | 1.603 |
| candidate | m4-batch | mutation_calls | batch_update_ms | 3 | 31.200 | 39.007 | 39.701 |
| candidate | m4-batch | mutation_calls | batch_upsert_ms | 13 | 55.801 | 84.007 | 84.258 |
| candidate | m4-batch | reopen | query_ms | 45 | 0.566 | 1.086 | 1.220 |
| candidate | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 10 | 56.423 | 64.286 | 64.418 |
| control | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.843 | 1.843 | 1.843 |
| control | m4-batch | burst1 | freshness_lag_ms | 1 | 0.016 | 0.016 | 0.016 |
| control | m4-batch | burst1 | query_ms | 45 | 0.410 | 0.736 | 0.853 |
| control | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.104 | 1.104 | 1.104 |
| control | m4-batch | burst2 | freshness_lag_ms | 1 | 0.025 | 0.025 | 0.025 |
| control | m4-batch | burst2 | query_ms | 45 | 0.460 | 0.786 | 0.807 |
| control | m4-batch | burst3 | file_delete_batch_ms | 1 | 2.287 | 2.287 | 2.287 |
| control | m4-batch | burst3 | freshness_lag_ms | 1 | 0.019 | 0.019 | 0.019 |
| control | m4-batch | burst3 | query_ms | 45 | 0.784 | 1.652 | 2.020 |
| control | m4-batch | initial | query_ms | 45 | 0.320 | 0.548 | 0.604 |
| control | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.795 | 2.184 | 2.219 |
| control | m4-batch | mutation_calls | batch_update_ms | 6 | 15.226 | 23.143 | 23.851 |
| control | m4-batch | mutation_calls | batch_upsert_ms | 25 | 28.080 | 40.356 | 52.112 |
| control | m4-batch | reopen | query_ms | 45 | 0.607 | 1.111 | 1.278 |
| control | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 19 | 30.216 | 35.020 | 35.367 |

## WAL/system sync counts

Counts below come from `strace -D` on the target process; the syscall trace adds wall-time overhead and is not used as an untraced baseline.
| Pair | Role | Arm | fsync | fdatasync | path contains WAL | WAL sync succeeded |
|---:|---|---|---:|---:|---:|---:|
| 1 | control | m4-batch | not traced | not traced | not traced | not traced |
| 1 | candidate | m4-batch | not traced | not traced | not traced | not traced |

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
