# Versioned base + delta prototype

Status: **research-only; not wired into production storage or search**.

## Why this exists

The current write path publishes a complete searchable index only after
`fragments.optimize()`. Production profiling shows 32–67 s for single-file
Watch jobs, while a production-shaped fixture (DIM=256, Cosine, M=50,
ef=500, 10k tail) measured 3.1–3.4 s for the change phase at 50k/100k
base sizes. The fixture therefore does not explain the production bottleneck.
Gate A remains unresolved; this work proceeds independently.

## Safety boundary

The model prototype is
`rust/crates/zg-engine/tests/base_delta_prototype.rs`.
The runnable native proof is
`rust/crates/zg-engine/tests/native_base_delta_prototype.rs`.
The paired benchmark is
`rust/crates/zg-engine/tests/native_paired_benchmark.rs`.
None changes `IndexStore`, production zvec schemas, the manifest format, or
production query routing.

The existing Watch path remains complete-index semantics:

- watcher debounce: 750 ms;
- watcher maximum wait: 5 s;
- one per-root follow-up writer job;
- every writer still runs `finalize_writes()` before it is published complete.

The scheduler applies a bounded trailing window only before a queued Watch
follow-up starts. It does not skip or defer finalization once the writer
begins. A Manual submission wakes a delayed Watch follow-up immediately.

## Prototype invariants

1. **Generation/tombstone visibility**: the newest generation wins by logical
   document ID; a newer tombstone hides an older base fragment.
2. **Candidate refill**: each segment increases its probe when stale/deleted
   candidates consume the initial Top-K, so stale rows cannot reduce recall.
3. **Hybrid scoring**: the model prototype normalizes vector and BM25 scores
   before weighted merge. The native proof uses rank fusion across independent
   native collections, avoiding incomparable per-corpus BM25 scores.
4. **Crash recovery**: segment data is synced before a WAL record, and the
   manifest is atomically replaced afterward. Recovery replays an acknowledged
   delta whose manifest publish was interrupted; an invalid manifest leaves the
   previous manifest active.
5. **Compaction**: compaction materializes only the latest non-deleted rows and
   publishes a new base after the new segment is durable.

## Gate D results

### D1 — scheduler integration fixture

Manual benchmark command:

```text
cargo test -p zg-daemon watch_burst_benchmark --lib -- --ignored --nocapture
```

A 40-event synthetic file burst reached all 41 expected changes (including the
seed event) with no loss:

| Mode | Watch/optimize calls | Freshness lag | Observed changes |
| --- | ---: | ---: | ---: |
| control, coalescing disabled | 14 | 30 ms | 41 |
| bounded coalescing enabled | 2 | 751 ms | 41 |

The bounded window trades freshness for substantially fewer writer starts. The
5-second maximum is covered by the scheduler timing test, and Manual priority
has a dedicated test proving that a Manual request is not held behind the
trailing window.

### D2 — native Zvec proof

`native_base_delta_prototype` creates two real HNSW+FTS zvec collections,
updates one logical document, tombstones another, queries both, refills stale
Top-K candidates, fuses vector/FTS ranks, recovers a WAL-only delta, rejects an
invalid manifest, shuts down, restarts, and queries again.

Observed result: `base_docs=4 delta_docs=2 restart_hits=2 hybrid_hits=2`.
The test passed with no mock index or in-memory substitute.

### D3 — paired native benchmark

Fixture: 1,000 base documents, 20 identical updates, HNSW+FTS, one native
thread, one bounded run with host snapshots. The benchmark compares current
single-index `upsert + flush + optimize` against base+delta `insert + flush`,
then measures compaction separately.

| Metric | Current single index | Base + delta |
| --- | ---: | ---: |
| Update p50 / p95 | 97.850 / 115.595 ms | 22.547 / 26.053 ms |
| Search p50 / p95 | 0.761 / 1.310 ms | 2.848 / 3.328 ms |
| Update CPU-seconds | 3.040 | 0.850 |
| Total CPU-seconds incl. compaction | 3.040 | 1.350 |
| Peak internal PSS | 344,255 KiB | 375,917 KiB |
| Live storage footprint | 5,643,142 B | 111,251,440 B |
| Footprint after compaction | 5,643,142 B | 5,634,972 B |
| Compaction time | — | 334 ms |
| Correct updates | 20/20 | 20/20 |

This is a small synthetic fixture, not a HIS_EMR prediction. Native mmap writes
reported `proc_write_bytes=0`; that counter is not a valid disk-write measure
for this path, so the result must not be interpreted as zero physical writes.
The live base+delta footprint is also intentionally reported before old
segments are deleted; it exposes the storage amplification that a real
retention policy must control.

Host conditions for the measured run were captured in
`/tmp/zvec-native-paired-benchmark-1000x20-v4/` (load around 4, roughly
2.6 GiB available memory, and active background browser/analysis processes).
Treat the single run as directional evidence, not a performance claim.

## Decision boundary

These results support continuing the research adapter, not production
migration. In particular, base+delta reduced update CPU and latency on this
fixture but increased query latency, peak PSS, and live storage footprint.
The next benchmark should use HIS_EMR-shaped corpus sizes and a longer update
sequence, include a real disk-write/IO measurement, and compare aggregate
CPU-seconds plus compaction cost before any `zg-engine` integration decision.
No production migration is approved.
