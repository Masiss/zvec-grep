# m4-batch vs m4-batch paired workload profile

M4 stats-event coalescing: control=False, candidate=True.
M4 LZ4 WAL compression: control=False, candidate=False; acceleration=1 vs 1.
Control max_delta_docs: 1536; candidate max_delta_docs: 1536; stats_log_threshold: 4194304 vs 4194304 bytes; mutation batches: 128 vs 128.
Runs: 2; stop reason: none.
Evidence class: **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; measurement quality: **COMPLETE**; performance-eligible: **false**.
Raw logs, profiles, host snapshots, and query-level comparisons are retained alongside this report.

## Resource profile

| Pair | Role | Arm | Process wall (s) | Process CPU (s) | Peak RSS (MiB) | Peak PSS (MiB) | Peak swap (MiB) | Cgroup peak memory (MiB) | Process-tree write bytes | Mean CPU (% one core) | Peak CPU (% one core) | Physical writes (B) | Text-byte WA |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 7.63 | 6.60 | 397.0 | 390.2 | 0.0 | 447.0 | 77246464 | 95.4 | 107.4 | 77242400 | 2.30x |
| 1 | candidate | m4-batch | 7.83 | 6.92 | 397.1 | 390.2 | 0.0 | 412.7 | 72179712 | 96.3 | 113.7 | 72175600 | 2.15x |

## CPU attribution (process CPU milliseconds, per run)

| Pair | Role | Arm | Indexing + mutations | Flush/maintenance | Timed search | Oracle/snapshot validation | Setup/fixture/other |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 1571.6 | 48.8 | 231.1 | 115.8 | 4635.8 |
| 1 | candidate | m4-batch | 1608.2 | 44.2 | 208.0 | 119.8 | 4935.7 |

## M4 snapshot write attribution

These are M4 index-state counters emitted at each validated snapshot, distinct from process `write_bytes` and host diskstats. `WAL B` is current file size; `WAL appended B` is complete record bytes submitted since this Index opened; other byte totals are cumulative gauges unless the name indicates otherwise.

| Pair | Role | Arm | Stage | WAL B | WAL appended B | Segments B | Tombstones B | Stats log B | Bytes written excl. WAL | Segment bytes written | WAL fsyncs | Stats-log fsyncs | Compactions | Merges | Dead docs |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | initial | 0 | 16073870 | 5883780 | 17 | 3525600 | 9410516 | 5883780 | 38 | 4 | 0 | 0 | 0 |
| 1 | control | m4-batch | burst1 | 0 | 17301655 | 6328528 | 127 | 3857133 | 10873290 | 7014375 | 43 | 5 | 1 | 1 | 96 |
| 1 | control | m4-batch | burst2 | 0 | 18530146 | 6773741 | 219 | 4184589 | 11646570 | 7459588 | 48 | 6 | 1 | 1 | 188 |
| 1 | control | m4-batch | burst3 | 0 | 19758910 | 7218747 | 314 | 0 | 13316687 | 8795627 | 53 | 7 | 2 | 2 | 283 |
| 1 | control | m4-batch | reopen | 0 | 0 | 7218747 | 314 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 283 |
| 1 | candidate | m4-batch | initial | 0 | 16073870 | 5883780 | 17 | 5803 | 5890719 | 5883780 | 38 | 4 | 0 | 0 | 0 |
| 1 | candidate | m4-batch | burst1 | 0 | 17301655 | 6328528 | 127 | 7271 | 7023428 | 7014375 | 43 | 5 | 1 | 1 | 96 |
| 1 | candidate | m4-batch | burst2 | 0 | 18530146 | 6773741 | 219 | 8739 | 7470720 | 7459588 | 48 | 6 | 1 | 1 | 188 |
| 1 | candidate | m4-batch | burst3 | 0 | 19758910 | 7218747 | 314 | 10207 | 8809297 | 8795627 | 53 | 7 | 2 | 2 | 283 |
| 1 | candidate | m4-batch | reopen | 0 | 0 | 7218747 | 314 | 10207 | 0 | 0 | 0 | 0 | 0 | 0 | 283 |

## LZ4 WAL codec attribution

Cumulative per-index diagnostics count only eligible frames passed to LZ4; accepted-frame byte savings compare original payload bytes with compressed payload bytes. Compression CPU uses the calling writer thread clock.

| Pair | Role | Arm | Stage | Attempts | Attempted input B | Compressed frames | Accepted input B | Output B | Saved B | Codec CPU ms |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|

## Durable WAL replay probe

The probe uses durable `ApplyBatch` ACKs, closes without `Flush`, advises only the probe index files with `POSIX_FADV_DONTNEED`, and records `mincore` residency before/after. A zero post-advice residency is required to call the target index files cold; the rest of the host/page cache remains warm.

| Pair | Role | Arm | WAL B | Write CPU ms | ACK p95 ms | Index bytes | Resident pages after/total | Replay-open wall ms | Replay-open CPU ms | Recovered docs |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | control | m4-batch | 16073900 | 1139.2 | 37.669 | 16074100 | 0/3928 | 1223.950 | 1212.0 | 4800 |
| 1 | candidate | m4-batch | 16073900 | 1321.6 | 45.498 | 16074100 | 0/3928 | 1383.760 | 1375.6 | 4800 |

## Latency percentiles (ms; pooled by role, arm and stage)

| Role | Arm | Stage | Operation | N | P50 | P95 | P99 |
|---|---|---|---|---:|---:|---:|---:|
| candidate | m4-batch | burst1 | file_delete_batch_ms | 1 | 0.910 | 0.910 | 0.910 |
| candidate | m4-batch | burst1 | freshness_lag_ms | 1 | 0.019 | 0.019 | 0.019 |
| candidate | m4-batch | burst1 | query_ms | 45 | 0.832 | 1.467 | 1.619 |
| candidate | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.208 | 1.208 | 1.208 |
| candidate | m4-batch | burst2 | freshness_lag_ms | 1 | 0.062 | 0.062 | 0.062 |
| candidate | m4-batch | burst2 | query_ms | 45 | 0.795 | 1.775 | 1.945 |
| candidate | m4-batch | burst3 | file_delete_batch_ms | 1 | 2.620 | 2.620 | 2.620 |
| candidate | m4-batch | burst3 | freshness_lag_ms | 1 | 0.020 | 0.020 | 0.020 |
| candidate | m4-batch | burst3 | query_ms | 45 | 0.987 | 2.015 | 2.276 |
| candidate | m4-batch | initial | query_ms | 45 | 0.748 | 1.342 | 1.899 |
| candidate | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.142 | 2.336 | 2.442 |
| candidate | m4-batch | mutation_calls | batch_update_ms | 6 | 16.140 | 30.518 | 32.063 |
| candidate | m4-batch | mutation_calls | batch_upsert_ms | 44 | 32.719 | 66.832 | 79.141 |
| candidate | m4-batch | reopen | query_ms | 45 | 1.027 | 2.028 | 2.458 |
| candidate | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 38 | 36.992 | 45.498 | 49.478 |
| control | m4-batch | burst1 | file_delete_batch_ms | 1 | 1.415 | 1.415 | 1.415 |
| control | m4-batch | burst1 | freshness_lag_ms | 1 | 0.017 | 0.017 | 0.017 |
| control | m4-batch | burst1 | query_ms | 45 | 0.955 | 1.887 | 2.309 |
| control | m4-batch | burst2 | file_delete_batch_ms | 1 | 1.577 | 1.577 | 1.577 |
| control | m4-batch | burst2 | freshness_lag_ms | 1 | 0.016 | 0.016 | 0.016 |
| control | m4-batch | burst2 | query_ms | 45 | 1.036 | 2.255 | 2.378 |
| control | m4-batch | burst3 | file_delete_batch_ms | 1 | 2.091 | 2.091 | 2.091 |
| control | m4-batch | burst3 | freshness_lag_ms | 1 | 0.049 | 0.049 | 0.049 |
| control | m4-batch | burst3 | query_ms | 45 | 0.949 | 1.701 | 1.976 |
| control | m4-batch | initial | query_ms | 45 | 0.892 | 1.645 | 1.878 |
| control | m4-batch | mutation_calls | batch_delete_ms | 3 | 1.527 | 1.938 | 1.974 |
| control | m4-batch | mutation_calls | batch_update_ms | 6 | 16.417 | 33.479 | 33.601 |
| control | m4-batch | mutation_calls | batch_upsert_ms | 44 | 32.416 | 56.742 | 79.575 |
| control | m4-batch | reopen | query_ms | 45 | 1.148 | 1.924 | 2.157 |
| control | m4-batch | wal_replay_probe | durable_apply_batch_ack_ms | 38 | 32.054 | 37.669 | 39.471 |

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

Known `json parse` diagnostic, pair 1, m4-batch, burst1: actual=123 oracle=123 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst2: actual=139 oracle=139 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst3: actual=139 oracle=139 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, initial: actual=120 oracle=120 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, reopen: actual=139 oracle=139 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst1: actual=123 oracle=123 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst2: actual=139 oracle=139 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, burst3: actual=139 oracle=139 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, initial: actual=120 oracle=120 missing=0 extra=0; equality=True.
Known `json parse` diagnostic, pair 1, m4-batch, reopen: actual=139 oracle=139 missing=0 extra=0; equality=True.

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
