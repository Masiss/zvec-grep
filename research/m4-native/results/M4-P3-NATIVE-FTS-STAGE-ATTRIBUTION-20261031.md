# Native FTS tail attribution — 2026-10-31

## Question

Why does the native arm show a long FTS-query tail (421-file update/rename p99 43–66 ms) while the Rust store-lock p95 is zero in those passes?

## Evidence source

Existing native-only trace R3, 64-file native Gate B lifecycle, 331 collection queries and 15,272 valid `NATIVE_PROFILE` events:

`research/m4-native/results/native-query-timeline-20261029/m4-rust-lifecycle-pair-20261030t034752z-nquery-sealstage-r3-evidence/`

This trace is native-only; it is not a performance comparison and does not include an M4 arm.

## Stage decomposition

| Stage | n | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `collection_query:total` | 331 | 0.522 ms | 1.307 ms | **184.716 ms** | 668.210 ms |
| `collection_query_stage:schema_handle_lock` | 331 | 0.001 ms | 0.003 ms | **184.030 ms** | 667.770 ms |
| `collection_query_stage:query_validate` | 331 | 0.002 ms | 0.005 ms | 0.008 ms | 0.011 ms |
| `collection_query_stage:get_all_segments` | 331 | 0.003 ms | 0.008 ms | 0.013 ms | 0.023 ms |
| `collection_query_stage:sql_engine_execute` | 325 | 0.463 ms | 1.045 ms | 1.470 ms | 2.677 ms |
| `fts_recall_stage:schema_fetch` | 446 | 0.024 ms | 0.069 ms | 0.100 ms | 0.155 ms |
| `fts_recall_stage:prepare` | 446 | 0.118 ms | 0.288 ms | 0.409 ms | 0.491 ms |
| `fts_recall_stage:filter_compute` | 446 | 0.080 ms | 0.204 ms | 0.312 ms | 0.401 ms |
| `fts_recall_stage:fts_search` | 446 | 0.014 ms | 0.053 ms | **0.095 ms** | 0.129 ms |
| `fts_recall_stage:result_fetch` | 209 | 0.053 ms | 0.126 ms | 0.155 ms | 0.189 ms |

The native FTS tail is therefore **not FTS search**. It is the native collection's `schema_handle_mtx_` acquisition:

- `collection_query:total` p99 184.7 ms vs `schema_handle_lock` p99 184.0 ms.
- Inner FTS stages stay below **0.5 ms** p99.
- `optimize_seal_exclusive_hold` p99 271.7 ms and `collection_flush` p99 529.8 ms are the matching long native holds in the same trace.

## Relation to the 421-file pair

The 421-file native arm showed:

- initial rescan: store-lock p95 133.0 ms, native FTS p95 0.8 ms, service p95 134.5 ms;
- update: native FTS p99 43.2 ms;
- rename: native FTS p99 66.6 ms;
- store-lock p95 zero in update/rename, but service p99 106.8 / 111.5 ms.

This is consistent with the R3 mechanism: the long native FTS-call intervals are native schema-lock waits inside `CollectionImpl::query`, not FTS execution. The 421 pair did not emit `NATIVE_PROFILE` stage lines, so this is **mechanism-level attribution**, not a per-query 421 trace.

Exact 421 per-query attribution would require one native-only 421 run with `M4_NATIVE_FLUSH_TIMELINE=1`; that is a new workload and needs a fresh operation ID and authorization. No such run was performed here.

## Conclusion

Native FTS tail decomposition is resolved at the mechanism level: **schema-lock wait dominates; inner FTS stages are sub-millisecond**. The native capacity problem is lock/publish concurrency, not FTS query cost. This is native-path behavior, not an M4 defect.
