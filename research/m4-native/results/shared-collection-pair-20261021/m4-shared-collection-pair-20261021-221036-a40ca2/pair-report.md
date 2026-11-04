# Corrected shared-collection pair — 2026-10-21

**Operation:** `m4-shared-collection-pair-20261021-221036-a40ca2`. **Status:** `COMPLETED_WITH_WORKSPACE_CAVEATS`. The corrected matched pair ran once with one binary (SHA256 `3388c930b675f4f404c43d918cf51a329346c59838bf45d6789255f58365ac9b`), 500 documents, 5 churn rounds per arm, and 4 concurrent readers (20 queries/arm). Order was immutable-shard first, shared-collection second. Both workloads exited 0; both cleanups were verified.

## Results

| Metric | Immutable-shard baseline | Shared-collection candidate |
|---|---:|---:|
| Writer p50 | 586.248 ms (`commit`) | 463.836 ms (`commit + GC cycle`) |
| Writer p95 (n=5; equals observed max) | 1074.448 ms | 482.362 ms |
| Concurrent reader p50 / p95 (20 samples) | 1.578 / 2.020 ms | 64.776 / 97.687 ms |
| Native open/build stage | vector build p50 94.029 ms | initial open 58.064 ms |
| M4 open/update stage | open p50 339.736 ms; update p50 372.209 ms | initial open 412.027 ms |
| Candidate GC per writer cycle | — | 373.526–407.261 ms |
| Process-tree write counter | 60,805,120 B | 209,297,408 B |
| Retained root (apparent / allocated) | 11,496,089 / 3,305,472 B | 34,665,429 / 9,740,288 B |
| Sampled cgroup memory peak | 580,804,608 B | 221,908,992 B |
| Host I/O PSI full avg10 at arm preflight | 3.68% | 10.11% |

Correctness: baseline coherence errors **0**; candidate completed **5/5** rounds, **0** failures, **0** coherence errors, and ended with **500 native / 500 M4 live docs** and **0 M4 dead docs**. Three inter-arm capacity/integrity windows passed. No OOM, swap use, cgroup cap/max events, or CPU throttling were observed.

## Interpretation and limits

The observed shared writer-cycle p95 was lower, but the shared writer measure includes explicit GC while the baseline `commit` uses a different implementation boundary. More importantly, the shared arm's reader p95 was much higher, host I/O PSI full avg10 rose from 3.68% to 10.11%, and the candidate had higher process-tree writes and retained root size. These are one-run descriptive observations; the arm order was fixed by randomization, there is no stationary control/repetition, and PSI is uncontrolled host-wide context. **Do not call this an architecture win or causal comparison.** The trade-off is unresolved; keep immutable-shard as baseline and defer promoting shared-collection pending follow-up focused on reader latency/GC and storage cost.

Both receipts are `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` / measurement quality `INCOMPLETE`: final traversals completed with zero scan errors, but are non-atomic and cannot prove lifetime peak allocation/apparent bytes. No workspace cap/peak proof is claimed. Hard telemetry and containment completed; sampled cgroup memory peaks are valid observations.

The wrapper initially failed to parse the candidate's `documents` field (baseline uses `docs`) after both arms completed. The parser was corrected; both raw outputs and receipts were re-parsed and policy-verified without rerunning either arm. Regression coverage is in `research/m4-native/tools/test_run_shared_collection_pair.py`. The previous failed operation and the earlier manifest initialization failure remain separately preserved under the same dated result directory.

Full raw data, per-arm receipts, host/cgroup telemetry, recovery samples, and checksums are in this operation directory.

## Source-grounded stage diagnosis

The candidate's per-cycle `writer_commit_ms` was **56.6–95.1 ms**; `gc_m4_search_delete_ms` was **349.8–382.5 ms**, within total GC **373.5–407.3 ms**. M4 GC compaction was 0 ms in all five rounds. Source inspection confirms `Coordinator::Commit` holds the exclusive `snapshot_mutex_` across M4/native update, flush, and publish; `Coordinator::Hybrid` needs the shared lock across vector and FTS reads. `CollectBeforeGeneration` also holds the exclusive lock and measures its `SearchFilter` plus per-hit `Delete` loop in `gc_m4_search_delete_ms`. This provides a direct next hypothesis: reader wait under writer commit and expensive per-generation M4 delete discovery dominate the prototype's trade-off. Reader wall times (59.3–97.9 ms) greatly exceed reader-thread CPU (1.3–3.1 ms), but host pressure is uncontrolled; do not assign the full gap to lock contention from this single run. Keep the next step bounded: investigate lock duration and GC's search/delete implementation, preserve leases/durability, then run focused correctness/fault tests before any newly authorized performance comparison.
