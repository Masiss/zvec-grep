# Shared-reader lock prototype — exploratory evidence (2026-10-22)

## Provenance and disposition

- Baseline completed pair: `m4-shared-collection-pair-20261022-013401-a450e2`, binary `35b4d2a1…`; shared arm first. Both arms rc=0, zero coherence errors; both receipts classify workspace uncertainty / measurement quality incomplete. Host pressure was changing, so this is descriptive only.
- Prototype operation: `m4-shared-collection-pair-20261022-013803-1f9ce2`, binary `2b1fc52a…`. Its shared arm completed rc=0 with zero coherence errors. Inter-arm recovery rejected the second arm on the unchanged `M4-CA-MEM-CAPACITY` reserve: worst `MemAvailable` 4,119,168 KiB (required 4,194,304 KiB; ~73 MiB short), then 4,156,360 KiB (~37 MiB short). Operation status `OPERATION_INCOMPLETE`; immutable arm did not run. Preserve this receipt; no retry of this operation and no hard-limit change.
- Full prototype bridge correctness/durability/crash regression passed 4,633 checks, 0 failures. The new shared-query-lock code was built before the incomplete pair.

## What instrumentation established

Baseline shared arm (20 concurrent reader samples): reader total p50/p95 5.896/9.829 ms; generation pin p95 0.008 ms; coordinator snapshot-lock wait p95 0.001 ms; M4 query-lock wait p50/p95 4.255/7.888 ms; FTS-stage p50/p95 4.363/8.382 ms; vector 1.505/3.358 ms; merge 0.020/0.032 ms. The old M4 `recursive_mutex` serialized queries as well as writers. Thus the principal observed wait is inside M4, not generation pin or coordinator snapshot lock.

Prototype shared arm: reader total p50/p95 6.564/7.439 ms; pin p95 0.010 ms; snapshot wait 0; M4 shared/exclusive query-lock wait p50/p95 4.220/5.029 ms; FTS stage 4.407/5.415 ms; vector 1.573/2.426 ms; merge 0.022/0.038 ms. Writer commit p50/p95 62.611/69.687 ms, GC 20.465/23.969 ms, cycle 77.938/88.249 ms. This single arm cannot establish a change: median was not better, p95 shifted favorably, and host/operation variability plus arm incompleteness prevent causal comparison. Note that the wait metric's lock changed semantics: baseline exclusive recursive mutex (reader + writer queue); prototype reader/writer lock (writer contention only for concurrent readers).

Source interpretation: `SearchFtsFiltered` held the global recursive mutex for the whole FTS query. The prototype lets uncached filtered readers share a `shared_mutex`; mutation APIs take it exclusively, preserving old-or-new state visibility. Cache-enabled filtered queries remain exclusive. In the prototype, readers still wait on writer-owned critical sections (`ApplyBatch`/`Flush`); their exclusive duration is the next optimization target. This is evidence for shared-reader admission, not a final architecture selection.

A follow-on, not yet measured code iteration in the working tree preserves state-mutex serialization but postpones `ApplyBatch`'s exclusive query lock until the durable WAL frame exists, then releases query exclusion during `FlushInternal`'s local segment build/write and reacquires before any in-memory state or manifest/tombstone/WAL publication. This is intended to let uncached readers continue over an unchanged logical state during WAL and segment I/O. It preserves the WAL-before-apply and manifest-before-WAL-reset order; full bridge correctness/durability/crash regression passes (4,633 checks, 0 failures). The instrumented operation `013803` predates this follow-on and must not be cited as its performance evidence. Pair evaluation remains deferred after the inter-arm capacity gate was short by 37–73 MiB; no hard limit changed and no operation was retried.

## Next work

1. Keep current code as an unpromoted research prototype; review lock order and ensure all mutation/recovery paths are covered. Existing 4,633-check regression is strong but add focused concurrent read/write stress if needed.
2. Analyze reducing writer exclusive hold time safely, starting with `ApplyBatch`'s durable WAL-before-state-apply protocol and `FlushInternal`; do not weaken fsync, atomic manifest publication, or query snapshot correctness.
3. Avoid another real pair while inter-arm capacity fails. Continue with source/correctness work and a low-resource diagnostic path; only create a fresh unique pair after its own capacity preflight passes.

No production source, ABI, or schema was changed. Raw operation receipts remain untouched; this file is derived analysis.
