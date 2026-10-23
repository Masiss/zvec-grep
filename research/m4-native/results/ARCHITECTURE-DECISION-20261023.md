# M4 architecture decision review — 2026-10-23

## Decision

**Do not promote either architecture on current performance evidence. Keep immutable-shard as the comparison baseline and correctness reference; retain shared-collection as the leading research alternative, not as the selected winner.** This is a deliberate `NO_ARCHITECTURE_WINNER` decision, not a claim that immutable-shard is faster or operationally superior. The only direct shared-vs-immutable run completed its workload but failed workspace measurement integrity (`performance_eligible=false`), and its stage/latency boundaries are not fully matched.

Do not start another prototype merely to produce more exploratory numbers. The next decision-relevant action, if a fresh operation later passes all existing gates, is a preregistered matched architecture comparison with equivalent write/reader boundaries and durability, continuous readers through native flush and GC, and atomic workspace/resource evidence. No hard resource gate or consumed operation is changed or retried by this decision.

## Evidence and what it can establish

### Direct architecture pair

Operation `shared-collection-pair-20261023-021949-4a92c5` ran immutable-shard then shared-collection, 500 documents × 5 rounds. Both arms completed, reopened, and reported zero coherence/workload failures. It is **exploratory raw evidence only**: both receipts are `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`, `measurement_quality=INCOMPLETE`, and `performance_eligible=false` because postflight traversal was not an atomic complete snapshot. Per-arm cgroup I/O attribution was unavailable.

Descriptive observations:

- Cgroup peak: immutable 326,701,056 B; shared 223,338,496 B (shared lower by 103,362,560 B, 31.64% in this one run).
- 20-query reader p95: immutable 1.386 ms; shared 2.293 ms. This sample is too small to establish a tail regression or win.
- Shared commit p95 90.634 ms, GC p95 32.755 ms, native collection flush p95 48.787 ms. Immutable's reported `commit` boundary is not equivalent to shared `writer_cycle`/separate GC, so those writer figures are not an A/B comparison.
- The workload did not guarantee reader/flush overlap. The measured low reader lock wait therefore cannot rule out native-flush reader stalls.

The 16-query-per-reader-cycle follow-up was `PREFLIGHT_BLOCKED` with `benchmark_started=false` (MemAvailable 3,979,276,288 B vs 4,294,967,296 B required). It produced no architecture evidence and is consumed; no retry or gate relaxation.

Sources and immutable receipts: [`shared-collection-pair-20261023/ANALYSIS-20261023.md`](shared-collection-pair-20261023/ANALYSIS-20261023.md) and operation bundle `m4-shared-collection-pair-20261023-021949-4a92c5/`.

### Source-grounded structural differences

The bridge harness's immutable-generation coordinator stages generation data and publishes a manifest, while readers pin a generation through leases. This gives a simple old-or-new visibility boundary and naturally retains old data for existing readers; the structural costs are generation construction/write amplification, temporary old+new storage/memory, and deferred reclamation while leases remain.

The shared coordinator reuses one native collection and tags/version-controls rows by generation, then reclaims obsolete generation data through a durable GC watermark. This avoids creating a separate native collection per generation and has a plausible lower steady/peak footprint, but transfers complexity to generation filtering, reader leases, ordered M4/native mutation, GC progress, and recovery across two storage components. In the pinned native source, `CollectionImpl::flush()` holds `schema_handle_mtx_` exclusively through `writing_segment_->flush()`, while native query paths take it shared. That is a structural contention path for shared readers, not a proven cause of the 20-query p95 above.

These are harness architecture prototypes, not a production migration or an end-to-end service comparison. The real application uses a resident daemon broker and cross-process workspace locks; direct client-process opens of a live native collection are unsupported. Any shared-collection path must stay brokered and retain generation/lease boundaries.

### Nearby M4 lock evidence (not architecture A/B)

- M4 query-lock publication staging lowered explicit-flush query-lock hold dramatically (about 7.2–7.6 ms to about 0.001 ms in its comparison), but writer-state lock still spans flush. The later timestamp-verified reader-through-flush pair is exploratory and fixed-order; it does not establish a stable overall architecture win.
- The matched pending-flush/group-commit pair observed 4× fewer concurrent WAL fsyncs (8 vs 32), but writer p95 direction reversed between one-shot pairs. Group commit remains opt-in/default-off. It is a mechanism signal, not a robust latency decision.
- Direct writers hold the state mutex across WAL append/fsync and ordered application. State wait dominated measured state-lock wait samples, but scheduler wake/fairness and fsync causality are not fully isolated. This is a writer serialization bottleneck present within M4, not evidence in favor of either collection architecture.
- Concurrent flush remains deferred: current whole-WAL reset and shared pending stats require frozen/active delta generations, WAL prefix/suffix preservation, coherent tombstone/stats/PK snapshots, and atomic publication/recovery. Query-lock staging already narrows reader exclusion without changing that model.

See [`ACTIVE-EXECUTION.md`](ACTIVE-EXECUTION.md) and [`lock-contention-microbench/ANALYSIS-20261023.md`](lock-contention-microbench/ANALYSIS-20261023.md) for exact pair results and limitations.

## Bottleneck classification

| Concern | Assessment | Decision relevance |
|---|---|---|
| Post-release analyzer PSS residual | Allocator free-arena growth nearly matches PSS residual while allocator-live returns near baseline in one run; residency is the leading explanation, not leak proof. | Not a reason by itself to choose shared architecture at current 500-document scale. Historical 4,096-document cap failure remains unexplained; large-scale memory risk remains open. |
| Shared native flush / M4 GC flush | Native flush p95 ~48.8 ms and GC p95 ~32.8 ms in the exploratory pair; native flush excludes shared queries behind an exclusive schema mutex. | Potential structural tail and writer costs; overlap/tail impact remains unmeasured. |
| M4 writer-state serialization | Writers serialize across WAL durability and state application; group commit cuts fsync count but latency is inconsistent. | Real optimization opportunity within either architecture. Avoid moving cost to query locks, GC, or recovery. |
| Query-lock hold during M4 flush | Metadata staging removes most of the explicit publication hold while preserving atomic old/new visibility. | Locally improved mechanism; end-to-end stable reader-tail benefit not yet qualified. Keep the simpler staging design, not concurrent flush. |
| Immutable generations / shared GC | Immutable is simpler to reason about and isolates readers but has generation-copy/retention cost. Shared reuses native collection but adds coordinated GC/recovery and lock coupling. | Structural tradeoff; cannot settle it with current incomplete one-shot measurements. |

## Disposition of alternatives

1. **Immutable-shard:** retain as baseline/reference and safe fallback. It has the clearest reader snapshot and publication boundary. Do not describe it as the measured winner; its write and peak-space costs require a valid matched comparison.
2. **Shared-collection:** continue as the primary alternative under the existing daemon broker. The lower one-run memory peak is promising, but it does not offset unresolved native-flush reader tail, GC cost, write amplification, and measurement-quality limitations. Do not promote or migrate production.
3. **Concurrent flush / another new architecture:** defer. Current correctness/storage model makes concurrent flush a WAL/delta-generation redesign, not lock-scope cleanup. No evidence currently shows enough writer-through-flush cost to justify it.
4. **Small, low-risk M4 optimization:** retain query-lock metadata staging and group commit as research-only/opt-in; investigate the writer serialization mechanism only if a future decision can separate queue wait, scheduler delay, and durable-WAL service time without weakening per-write durability.

## Revisit criteria

A future architecture decision needs, in one preregistered protocol:

- Same binary, corpus, mutation semantics, reader concurrency, query mix, durability, and equivalent measured phase boundaries; randomized/counterbalanced arm order and enough paired repetitions to report uncertainty rather than a single p95.
- Continuous reader timestamps proving overlap with native flush, M4 flush, and GC; writer calls deliberately crossing flush boundaries, plus exact old/new generation visibility checks.
- Successful reopen/replay and crash-boundary checks for publication, component flushes, and GC watermark; no change to WAL ordering, atomic visibility, generation leases, durability, or BM25 semantics.
- Atomic workspace/resource evidence, cgroup peak and per-arm I/O attribution, CPU and writer/reader tail metrics, and bytes written normalized to logical updates. Preserve all existing hard gates.
- A preregistered acceptance rule covering reader-tail non-regression, writer tail, memory peak, total/logical write amplification, GC/recovery cost, and operational complexity; no single metric may compensate for a correctness or durability regression.

Until those conditions are met, the decision remains: **immutable-shard is the baseline, shared-collection is an unselected research alternative, and no architecture winner is established.**

## Scope

Research-only decision record. No production changes, benchmark launches, gate changes, retries, or architecture implementation are authorized by this review. Existing working-tree edits outside this record remain untouched. This record does not supersede operation receipts or upgrade their measurement eligibility.

**Recorded:** 2026-10-23
