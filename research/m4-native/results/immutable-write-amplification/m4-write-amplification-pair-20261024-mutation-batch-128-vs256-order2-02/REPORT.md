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
| 1 | candidate | m4-batch | 5.07 | 4.15 | 322.8 | 316.1 | 0.0 | 345.2 | 46686208 | 95.5 | 107.7 | 46682100 | 2.52x |
| 1 | control | m4-batch | 5.48 | 4.45 | 322.3 | 315.5 | 0.0 | 337.2 | 49537024 | 95.6 | 106.9 | 49532900 | 2.68x |

## CPU attribution (process CPU milliseconds, per run)

| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 853.3 | 49.9 | 110.5 | 64.1 | 3074.2 |
| 1 | control | m4-batch | 1046.9 | 62.3 | 110.1 | 78.4 | 3150.4 |

## M4 snapshot write attribution

These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.

| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | initial | 0 | 8033628 | 2941712 | 17 | 1762800 | 4704979 | 2941712 | 10 | 2 | 0 | 0 | 0 |
| 1 | candidate | m4-batch | burst1 | 0 | 9260462 | 3386892 | 122 | 2094333 | 5482106 | 3386892 | 13 | 3 | 0 | 0 | 96 |
| 1 | candidate | m4-batch | burst2 | 0 | 10488359 | 3832024 | 205 | 2418897 | 6252349 | 3832024 | 16 | 4 | 0 | 0 | 184 |
| 1 | candidate | m4-batch | burst3 | 0 | 11717693 | 4268148 | 291 | 2751286 | 7912156 | 5158460 | 19 | 5 | 1 | 1 | 272 |
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

## Durable WAL replay probe

The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.

| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | candidate | m4-batch | 8033630 | 585.2 | 74.228 | 8033910 | 0/1965 | 618.734 | 602.7 | 2400 |
| 1 | control | m4-batch | 8033840 | 584.0 | 35.936 | 8034110 | 0/1965 | 758.329 | 745.1 | 2400 |

## Latency percentiles (ms; pooled by role, arm and stage)

| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |
|---|---|---|---|---:|---:|---:|---:|
| candidate | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.659 | 1.659 | 1.659 |
| candidate | m4-batch | burst1 | freshness_lag_ms | 1 | 0.022 | 0.022 | 0.022 |
| candidate | m4-batch | burst1 | query_ms | 45 | 0.412 | 1.034 | 1.131 |
| candidate | m4-batch | burst2 | file_delete_batch_ms | 1 | 2.286 | 2.286 | 2.286 |
| candidate | m4-batch | burst2 | freshness_lag_ms | 1 | 0.026 | 0.026 | 0.026 |
| candidate | m4-batch | burst2 | query_ms | 45 | 0.458 | 0.795 | 0.855 |
| candidate | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.136 | 1.136 | 1.136 |
| candidate | m4-batch | burst3 | freshness_lag_ms | 1 | 0.033 | 0.033 | 0.033 |
| candidate | m4-batch | burst3 | query_ms | 45 | 0.589 | 1.346 | 1.392 |
| candidate | m4-batch | initial | query_ms | 45 | 0.351 | 0.567 | 0.626 |
| candidate | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.600 | 2.162 | 2.212 |
| candidate | m4-batch | mutation_calls | batch_update_ms | 3 | 32.280 | 39.044 | 39.645 |
| candidate | m4-batch | mutation_calls | batch_upsert_ms | 13 | 59.195 | 80.471 | 97.519 |
| candidate | m4-batch | reopen | query_ms | 45 | 0.496 | 0.927 | 1.207 |
| candidate | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 10 | 62.394 | 74.228 | 79.102 |
| control | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.618 | 1.618 | 1.618 |
| control | m4-batch | burst1 | freshness_lag_ms | 1 | 0.015 | 0.015 | 0.015 |
| control | m4-batch | burst1 | query_ms | 45 | 0.485 | 0.793 | 0.804 |
| control | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.711 | 1.711 | 1.711 |
| control | m4-batch | burst2 | freshness_lag_ms | 1 | 0.020 | 0.020 | 0.020 |
| control | m4-batch | burst2 | query_ms | 45 | 0.480 | 0.811 | 0.830 |
| control | m4-batch | burst3 | file_delete_batch_ms | 1 | 1.369 | 1.369 | 1.369 |
| control | m4-batch | burst3 | freshness_lag_ms | 1 | 0.027 | 0.027 | 0.027 |
| control | m4-batch | burst3 | query_ms | 45 | 0.567 | 0.959 | 1.161 |
| control | m4-batch | initial | query_ms | 45 | 0.376 | 0.679 | 0.741 |
| control | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.573 | 1.646 | 1.653 |
| control | m4-batch | mutation_calls | batch_update_ms | 6 | 16.341 | 36.902 | 39.668 |
| control | m4-batch | mutation_calls | batch_upsert_ms | 25 | 34.966 | 66.021 | 81.595 |
| control | m4-batch | reopen | query_ms | 45 | 0.459 | 0.825 | 0.842 |
| control | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 19 | 32.189 | 35.936 | 36.137 |

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
