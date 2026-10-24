# M4 incremental index — evidence (historical series begins 2026-10-17)

## Latest write-path candidate — 2026-10-24

**Current quota status:** Attestations, `btrfs inspect-internal rootid`, mount and ownership checks map the same-filesystem workspaces to `0/258` (`/var/tmp`, 128,782,336/134,217,728 bytes referenced) and `0/259` (PWD, 129,359,872/134,217,728). Both visible directories are empty; no open fd targets either path and no M4 process is active. The immediate EDQUOT is explained: `0/259` had 4,857,856 bytes free, 213,351 bytes below the 5,071,207-byte first Jieba snapshot file. The exact extents/snapshot references retaining usage cannot be identified: qgroup/subvolume listing ioctls return `Operation not permitted`, while sysfs exposes counters without provenance. No cleanup target is verified; no snapshot or quota was changed. The existing 64 MiB reserve is empirical (32,419,840 bytes / 93.5% above the largest prior sampled full-size peak of 34,689,024 bytes), not a lifetime guarantee or a capacity fix; no additional gate is added. No benchmark starts unless the existing live-headroom check passes. Independent recovery coverage now verifies that a corrupt LZ4 suffix preserves the valid prefix and that a coalescing-enabled batch recovers after a post-WAL-sync process crash, flushes stats, and reopens with the legacy writer setting; final `m4_correctness` passed. See [ACTIVE-EXECUTION](ACTIVE-EXECUTION.md) and [quota diagnosis](immutable-write-amplification/ANALYSIS-20261024-quota-followup.md).

### Mutation ApplyBatch chunk size 128 vs 256

A fresh 600-file, counterbalanced two-order exploratory comparison held all other M4 settings fixed, disabled WAL-LZ4/coalescing symmetrically, and recorded per-call mutation latency. Both orders completed under the attested 128 MiB qgroup `0/259`, with exact correctness/reopen/retrieval parity, complete workspace accounting, `PASS` / `HARD_QUOTA_BOUND` receipts and verified archive/cleanup. Candidate 256 lowered process-tree writes by 1.69% and 5.76% per order (3.74% mean) and changed call counts from 25 to 13 batch upserts and 6 to 3 updates; CPU/wall results reversed by order, individual-call p95 increased, and query p95 was stage/order-sensitive. **REWORK; keep 128 and do not promote 256.** This remains exploratory/non-qualifying. Full result: [analysis](immutable-write-amplification/ANALYSIS-20261024-mutation-batch-128-vs256.md).

### WAL LZ4 × ApplyBatch chunk-size interaction

Four fresh 600-file pairs counterbalanced LZ4 OFF/ON at chunks 128 and 256, with two arm orders per chunk. All had COMPLETE workspace accounting, exact correctness/reopen/retrieval parity, zero mismatches, hard-quota PASS receipts, and verified 77-file archives/cleanup; they remain exploratory/non-qualifying. LZ4 saved similar process writes at both chunk sizes (28.6% at 128; 29.4% at 256), but CPU/wall mean effects diverged in this 600-file sample: at 128, CPU +15.1% / wall +12.3% with strong order reversal; at 256, CPU −15.4% / wall −14.1%. This was a scale-limited exploratory result, not a general recommendation. The full-size chunk128 follow-up is summarized next. No default/qualification change; full analysis: [600-file counterbalanced analysis](immutable-write-amplification/ANALYSIS-20261024-lz4-batch-interaction.md).

### Full-size WAL-LZ4 at chunk128

Two fresh 1,200-file OFF/ON pairs counterbalanced arm order. Both were COMPLETE with exact parity, `PASS` / `HARD_QUOTA_BOUND` receipts, and verified 79-file archives/cleanup. Mean LZ4 effects: process writes −31.35%, CPU −5.58%, wall −5.86%; query p95 improved 15–44% in every stage and both orders. Replay-probe ACK p95 mean increased 5.34%, and CPU/wall/ACK directions reversed by order. Compression telemetry recorded 182 frames, about 71.66 MB input→24.50 MB WAL output, ~109 ms mean compression CPU (about 1.1% of process CPU); this cannot explain the much larger order-dependent CPU spread. Prior full-size chunk256 LZ4 pairs are already complete; their raw reports lack matching per-call trace evidence and showed CPU/wall/query-tail regressions. The different-size results suggest a workload/chunk interaction but remain exploratory/non-qualifying; keep LZ4 research-only and defaults unchanged. [Analysis](immutable-write-amplification/ANALYSIS-20261024-lz4-batch128-full1200.md), [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-full1200-control-first-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-full1200-candidate-first-01/REPORT.md).

### Stats-event coalescing at chunk128 (1,200 files)

The complete control-first pair reduced process writes 6.56% and stats-log bytes 99.8%, but CPU/wall rose 4.85%/2.62%; replay-probe ACK p95 rose 20.8%, and query p95 effects were mixed. The counterbalanced candidate-first operation is incomplete: its candidate arm passed, but the control arm exited 2 during burst-3 flush (`NONZERO_EXIT`), with the M4 status text omitted by the old benchmark assertion. This is not a paired result; do not pool it or infer a stable effect. The failure and all raw evidence are preserved; the operation is consumed and will not be retried. Benchmark flush assertions now include returned status details for future diagnostics. Coalescing remains research-only; no default or qualification change. [Analysis](immutable-write-amplification/ANALYSIS-20261024-stats-coalescing-batch128.md), [complete control-first report](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-control-01/REPORT.md), [partial candidate-first status](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-candidate-01/STATUS.md).

### Mutation chunk 128 vs256 at 1,200 files — blocked before workload

The control-first operation failed during dictionary-snapshot initialization before ingesting any workload data. A focused native Jieba test reproduced `Disk quota exceeded` when its `TMPDIR` was inside the capped qgroup `0/259`; the same test passed with default `/tmp`. This confirms the 128 MiB workspace lacks enough available write headroom; exact qgroup usage is not readable by the unprivileged runner. Read-only `btrfs subvolume list -s /` and `-d /` also return `Operation not permitted`, so no snapshot state is inferred. No candidate-first arm ran and there is no performance result. The consumed identity will not be retried; benchmark scratch will not be moved outside the cap or the cap raised. The native error now reports path/OS error. [Analysis](immutable-write-amplification/ANALYSIS-20261024-mutation-batch-128-vs256-full1200.md), [preserved operation status](immutable-write-amplification/m4-write-amplification-pair-20261024-mutation-batch-128-vs-256-full1200-control-01/STATUS.md).

### Separate-record Upsert group-fsync candidate

Implemented `writer_group_commit_per_upsert_records` as a distinct research mode: one ordered WAL frame per Upsert, one fsync per overlapping group, individual application/reader boundaries, per-request statuses after durable sync/apply. It is not `ApplyBatch` atomicity. Added ordered duplicate-PK handling, prefix sequence checkpoints, deferred auto-flush (so WAL reset cannot discard a durable unapplied suffix), and a reopen-required guard after uncertain sync/apply outcomes. Normal and ThreadSanitizer `m4_correctness` CTest each passed 1/1 with the added two-compressed-frame/one-sync, durable-prefix visibility, duplicate-PK, auto-flush, pre/post-sync crash, append-prefix/sync/apply failure, queue saturation, orderly shutdown and reopen-recovery coverage. Existing mixed legacy/LZ4 and batch-atomic checks also remain in the suite. Current focused profiler/policy/containment suites and both final `--validate-only` bindings pass; see the live checkpoint for exact counts.

One bounded pair [operation](lock-contention-microbench/20261024/m4-lock-contention-microbench-writer-group-commit-per-upsert-20261024-021428-13d064/operation-plan.json) formed eight groups of four and reduced concurrent WAL fsyncs 32→8 (total 16→40), while process writes fell 1,004,024→1,001,804 B (-0.22%) and segment bytes fell 0.45%. Candidate/control writer p50/p95 was 0.813/8.254 vs. 0.592/39.296 ms; reader p95 was 0.042 vs. 0.163 ms, but the candidate was first, the pair had only 32 calls/arm, and concurrent compaction differed 7.43 vs. 38.76 ms. Workspace status was `COMPLETE_TRAVERSAL_NOT_ATOMIC`, no independent capacity upper bound, so operation status is `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` and `performance_eligible=false`. Source trace also finds watcher file replacements serialize under `StoreState`'s mutex, so the pipeline has no overlapping native Upsert callers for this group-fsync path; cross-file `ApplyBatch` would alter per-file visibility/error boundaries, and the current 256-mutation experiment chunks are smaller than the storage layer's 1,024-doc Upserts. Reject watcher integration under current semantics. Treat fsync reduction as mechanism evidence only; no stable tail or total-write win, no qualification/default change. Do not retry the consumed one-shot profile. Full semantics, source trace, resource context, limitations, and remaining test matrix: [analysis](lock-contention-microbench/ANALYSIS-20261024.md).

### LZ4 target-index-file-cold replay counterbalance

Two new isolated LZ4 OFF/ON roots (coalescing OFF, same `m4-batch`/256 durable ApplyBatch chunks) repeated WAL savings: bytes16.073MB→5.485MB (-65.87%) both orders; WAL-only process writes fell63.0–63.1%. After `POSIX_FADV_DONTNEED`, `mincore` verified0 resident index-file pages in every arm, and all4,800 docs recovered/searchable. ACK p95, write CPU and target-file-cold replay latency reversed by arm order (ACK p95 283→173ms then125→160ms; write CPU3.940→2.281s then1.955→2.527s; replay-open2.282→1.762s then1.911→2.656s). Both reports are `INCOMPLETE_WORKSPACE_CAVEAT` because the control role has uncertain workspace sampling in both orders (candidate is PASS), and host snapshots show unrelated CPU-active processes; no causal latency or qualification claim. **Keep LZ4 opt-in/OFF by default**. Full details: [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-cold-replay-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-cold-replay-order2-01/REPORT.md), [contract/results](immutable-write-amplification/lz4-cold-replay-PREPARED-20261024.md).

Combined stats-event coalescing + LZ4 candidate was measured against both-OFF baseline in two earlier warm-query pairs: [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-warm-01/REPORT.md) and [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-warm-order2-01/REPORT.md). Both had COMPLETE workspace accounting, exact snapshot/oracle/retrieval/reopen parity and ~30.2–30.4% lower process writes; however query p95 reversed strongly by order, so latency is unresolved. A later control-first [root02](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-02/REPORT.md) and candidate-first [root03](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-03/REPORT.md) added the isolated durable WAL ACK/replay subprobe; [root01](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-01/NO-LAUNCH.txt) is a preserved pre-launch workload-binding rejection, not a benchmark. The launched pairs completed with stop_reason=none, returncode0 and exact snapshots/oracle/reopen/retrieval (0 mismatch; Jaccard1.000). Candidate process writes fell38.9–39.1% in both orders and WAL appends65.8%; segment bytes/writes, fsync counts and compactions matched. Candidate-first query p95 was within+9.8%, but delete-batch latency varied by stage/order. Both later roots are performance-ineligible because the workspace observer retained UNKNOWN sampled peaks after known live-writer directory activity; this was not `/tmp` exhaustion, not an abort and not a reason to waive the gate.

The isolated WAL-only probe disables coalescing in both roles and compares LZ4 OFF/ON with `fsync_wal=true`: candidate-first root03 reduced probe WAL16.073MB→5.485MB (-65.87%) and process writes17.412MB→6.251MB (-64.1%). Durable ApplyBatch ACK p95 was62.5ms vs68.4ms; fresh-index replay-open was1.046s vs1.042s with warm page cache. Candidate ACK p95 was lower in both roots (root02 control-first:87.608ms vs219.787ms; root03 candidate-first:62.513ms vs68.393ms), but the gap magnitude varied sharply; neither workspace-incomplete root establishes a stable latency effect. The probe recovered 4,800 live docs and searchable postings in both arms. `m4_correctness` passed `checks=20603 failures=0`, including mixed legacy/LZ4, corrupt checksum, torn WAL suffix, post-fsync crash recovery, stats-coalescing replay and flush crash boundaries. No cold-media claim or defaults change. Earlier `/tmp` fixture exhaustion was routed to `/var/tmp`; benchmark `TMPDIR` stays inside each arm workspace.

A fresh, counterbalanced interaction pair directly compared LZ4-only against LZ4+stats-coalescing, both using `m4-batch`/256 mutation chunks. Both roots completed with COMPLETE workspace accounting and exact snapshot/oracle/reopen/retrieval parity. Coalescing reduced process-attributed writes9.97% control-first and13.62% candidate-first (11.80% pooled); stats-log bytes fell3.526MB→10,207B, while WAL appends, segment writes, fsyncs, and compactions matched. Process CPU was effectively flat; query p95 stayed within+8.7% per stage, but single-sample delete-batch timings varied and continuous-reader/cold-start tails remain unqualified. **KEEP coalescing as an opt-in candidate on top of LZ4+bounded ApplyBatch; it adds stats-log savings in this workload, but does not qualify a default or production change.** See [incremental candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-incremental-01/REPORT.md), [counterbalanced](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-incremental-order2-01/REPORT.md), and [prepared contract/results](immutable-write-amplification/lz4-stats-coalescing-incremental-PREPARED-20261024.md).

The individual write-path and group-commit evidence must be interpreted by acknowledgment semantics. The three-pair `ApplyBatch` vs single-`Upsert` study reduced process writes90.29%, but `ApplyBatch` is batch-atomic (≤256 mutations, one durable WAL frame/fsync before publication), not a per-Upsert ACK. The current `writer_group_commit` queue gathers Upsert calls into one `ApplyBatch` and returns one shared status to all waiting callers; it therefore also has group-atomic visibility/error semantics despite synchronous per-caller waiting. Tests prove pre/post group reader visibility and post-fsync whole-group recovery, but writer-tail effects reverse by ordering and reader-tail data do not qualify a stable win. The Rust watcher seam replaces each file individually and calls `finalize_writes` once per pass; it is not a durable ACK-per-file contract. Do not imply either batch implementation is semantics-equivalent to independent per-file transactions. Any shared-fsync strategy must state its group visibility/error contract, retain each caller's durability wait, and pass reader/crash tests; no ACK shortcut or `fsync_wal` change is acceptable. Details: [write-amplification analysis](immutable-write-amplification/ANALYSIS-20261023.md), [group-commit/readers analysis](lock-contention-microbench/ANALYSIS-20261023.md).

Three counterbalanced M4 pairs at delta1536 compared per-level compaction caps4 and5. Candidate cap5 consistently reduced compactions2→1 and segment writes10.12%, with process-attributed writes down2.36–4.06% and exact snapshot/oracle/retrieval/reopen parity. Candidate-first query p95 stayed within5.2%; the control-first order had two latency outliers (+11.1%, +16.4%). **Keep cap5 only as a research candidate; do not change defaults or claim performance qualification** while reports are marked performance-ineligible. Details: [cap5 record](immutable-write-amplification/cap5-delta1536-PREPARED-20261024.md).

An opt-in LZ4 WAL frame candidate (legacy replay preserved; same fsync boundary) reduced M4 WAL appends65.83% in five 1,200-file pairs; process-attributed writes fell22.16–27.93% per pair and25.05% pooled. Exact snapshot/oracle/retrieval/reopen parity held; segment bytes/fsync counts and maintenance counters were unchanged. Un-warmed and warmed query p95 reversed with arm order. Two additional isolated roots verified 65.87% WAL-byte and ~63% WAL-probe process-write reductions, with0 resident target-index pages after cache advice; ACK p95, write CPU and replay-open effects again reversed with order. All new roots remain workspace-incomplete/non-qualifying. This is an index-file-cold replay estimate, not a fully cold-machine start; no stable latency effect. **KEEP LZ4 opt-in/OFF by default; do not promote.**

The first LZ4 acceleration-1-vs-4 attempt was quota-stopped in old qgroup `0/258` and remains an incomplete historical operation ([status](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-acceleration-1-vs-4-order1-01/STATUS.md)). To run under PWD without bypassing the hard cap, the owner provisioned a persistent 128 MiB qgroup `0/259` under ignored `research/m4-native/.work/`; no old quota or snapshots were changed. Fresh counterbalanced pairs completed with exact correctness and verified cleanup. Counterbalanced CPU was effectively unchanged (−0.05%), process writes +2.09%, and query p95 was worse in most stages; acceleration 4 had no robust advantage. The newer quota-backed harness also completed the 600-file mutation-batch pair under the same hard cap, with both reports marking `HARD_QUOTA_BOUND`; both exact roots were archived and removed. Keep acceleration 1/defaults unchanged; do not request administrative headroom output or bypass the cap.

Stats-event coalescing was then tested in two counterbalanced warm-query pairs. Both reduced process-attributed writes: candidate-first -8.31% (53.154MB vs57.975MB, workspace UNKNOWN on one arm) and control-first -8.69% (52.695MB vs57.713MB, workspace COMPLETE). Exact snapshots, close/reopen, oracle, and retrieval parity held; WAL/segment/fsync/compaction counters were unchanged. The COMPLETE control-first pair had warmed query p95 from -12.6% to+0.4% (candidate-vs-control), with peak RSS equal at348.4MiB. Candidate-first results were more variable by stage. These one-pair measurements are exploratory and do not qualify cold-start or causal latency or justify defaults. **REWORK further qualification; coalescing remains OFF by default.** The combined coalescing+LZ4 interaction has since been measured in control-first and candidate-first order above; both operation roots remain workspace-INCOMPLETE and non-qualifying.

Evidence and limitations: [analysis](immutable-write-amplification/ANALYSIS-20261023.md), [coalescing candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-warm-query-02/REPORT.md), [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-warm-query-03/REPORT.md), and [five LZ4 reports](immutable-write-amplification/).


### Full operation index — 2026-10-24

This table covers every `m4-write-amplification-pair-20261024-*` root present in this results directory (52 roots). Successful P4 runs remain **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; each linked report is authoritative for detailed metrics, quality caveats, correctness, durability, and cleanup. Failed/blocked roots are preserved, not performance results; a consumed root is never retried.

| Experiment | Root | Outcome |
|---|---|---|
| Cap5 initial candidate-first (delta512) | [cap5-01](immutable-write-amplification/m4-write-amplification-pair-20261024-cap5-01/REPORT.md) | Completed; p95 failure at burst1/burst3; historical evidence, superseded for write tradeoff by delta1536 series. |
| Cap4 vs cap5, delta1536 | [pair 1](immutable-write-amplification/m4-write-amplification-pair-20261024-cap5-delta1536-01/REPORT.md), [order 2](immutable-write-amplification/m4-write-amplification-pair-20261024-cap5-delta1536-order2-01/REPORT.md), [order 3](immutable-write-amplification/m4-write-amplification-pair-20261024-cap5-delta1536-order3-01/REPORT.md) | Three complete pairs: segment writes −10.12%, process writes −2.36–4.06%, compactions 2→1; p95 outliers depend on order. At final reopen cap5 had L0=5/L1=1 vs control L0=3/L1=2 (one more total run, +814B segments): less compaction deferred maintenance debt; research candidate only. |
| Delta 1024 candidate | [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-delta1024-01/REPORT.md), [counterbalanced](immutable-write-amplification/m4-write-amplification-pair-20261024-delta1024-order2-01/REPORT.md) | Two complete pairs; process writes −11.18%/−13.85%, segment writes −26.43%, compactions 6→2; both carry workspace caveats and p95 reverses with order. |
| Delta 512 vs1536 | [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-delta1536-01/REPORT.md), [counterbalanced](immutable-write-amplification/m4-write-amplification-pair-20261024-delta1536-order2-01/REPORT.md) | Two complete pairs; segment writes −36.83%, compactions 6→2, process writes −14.85–16.10%; p95 outliers moved with order. |
| Mutation chunk 256 vs512 | [root artifacts](immutable-write-amplification/m4-write-amplification-pair-20261024-mutation-batch-01/), [decision](immutable-write-amplification/mutation-batch-PREPARED-20261024.md) | Candidate exited before initial indexing: supported `ApplyBatch` limit is 256 mutations. No paired result; not bypassed or retried. |
| Mutation chunk 128 vs256 | [analysis](immutable-write-amplification/ANALYSIS-20261024-mutation-batch-128-vs256.md), [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-mutation-batch-128-vs256-order1-02/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-mutation-batch-128-vs256-order2-02/REPORT.md) | Two complete counterbalanced 600-file exploratory pairs, exact parity, hard-quota receipts PASS. Candidate writes −1.69%/−5.76%, pooled −3.74%; CPU/wall and reader p95 are order/stage-sensitive. REWORK; keep 128 and do not promote 256. |
| Mutation chunk 128 vs256, full-size | [analysis](immutable-write-amplification/ANALYSIS-20261024-mutation-batch-128-vs256-full1200.md), [blocked control-first status](immutable-write-amplification/m4-write-amplification-pair-20261024-mutation-batch-128-vs-256-full1200-control-01/STATUS.md) | Initialization failed before ingestion; focused test confirmed `Disk quota exceeded` in quota-backed TMPDIR. No paired result; counterorder not launched; no retry/cap change. |
| WAL LZ4 × chunk size (600 files) | [analysis](immutable-write-amplification/ANALYSIS-20261024-lz4-batch-interaction.md), [128 control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-interaction-order1-01/REPORT.md), [128 candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-interaction-candidate-first-01/REPORT.md), [256 candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch256-interaction-order2-01/REPORT.md), [256 control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch256-interaction-control-first-01/REPORT.md) | Four fresh counterbalanced exploratory pairs, quota-bound and COMPLETE. Writes −28.6%/−29.4% mean at 128/256. CPU/wall at 128 +15.1%/+12.3% (order-reversed); at 256 −15.4%/−14.1% with mostly improved query p95. The 600-file effects are scale-limited; see the full-size follow-up. No default/qualification change. |
| WAL LZ4 at chunk128 (1,200 files) | [analysis](immutable-write-amplification/ANALYSIS-20261024-lz4-batch128-full1200.md), [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-full1200-control-first-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-batch128-full1200-candidate-first-01/REPORT.md) | Two complete counterorders, quota-bound, exact parity, verified archives. Writes −27.8%/−34.8%, CPU +6.3%/−19.4%, wall +6.4%/−20.0%; query p95 improved in all stages/orders, replay ACK p95 reversed. Mean writes −31.35%, CPU −5.58%, wall −5.86%, ACK p95 +5.34%. Exploratory/non-qualifying; LZ4 remains research-only. |
| Stats-event coalescing, earlier | [initial](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-01/REPORT.md), [order 2](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-order2-01/REPORT.md), [order 3](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-order3-01/REPORT.md) | Three complete runs across orders; write savings observed, with p95/workspace caveats. See per-report durability settings. |
| Stats-event coalescing, strict durability | [durable](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-durable-01/REPORT.md), [durable order 2](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-durable-order2-01/REPORT.md) | Two complete pairs; exact parity and fewer writes, but latency/workspace caveats prevent qualification. |
| Stats-event coalescing at chunk128 (1,200 files) | [analysis](immutable-write-amplification/ANALYSIS-20261024-stats-coalescing-batch128.md), [complete control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-control-01/REPORT.md), [partial candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-candidate-01/STATUS.md) | One complete order: writes −6.56%, stats log −99.8%, but CPU/wall and replay ACK p95 worsened; query p95 mixed. Counterorder nonzero exit during control burst-3 flush. No paired estimate, no retry, research-only. |
| Stats-event coalescing, warm-query | [stale-contract root](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-warm-query-01/NO-LAUNCH.txt), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-warm-query-02/REPORT.md), [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-coalescing-warm-query-03/REPORT.md) | Root 01 rejected before fixture because policy still bound WAL-LZ4; corrected before new roots. Roots 02/03 completed: process writes −8.31%/−8.69%; root02 workspace UNKNOWN, root03 COMPLETE; warm p95 varies with order. Coalescing stays OFF. |
| Stats-log threshold 4 vs16MiB | [report](immutable-write-amplification/m4-write-amplification-pair-20261024-stats-log-threshold-01/REPORT.md), [decision](immutable-write-amplification/stats-log-threshold-PREPARED-20261024.md) | Completed; process writes −1.05%, WAL/segment counters unchanged, replay-open regressed18.5%; reject16MiB, retain4MiB. |
| WAL-LZ4 completed pairs | [initial](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-01/REPORT.md), [order 2](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-order2-01/REPORT.md), [warm](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-warm-query-01/REPORT.md), [warm order 2](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-warm-query-order2-01/REPORT.md), [contained-TMP repeat](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-contained-tmp-02/REPORT.md) | Five pairs: WAL appends −65.83%, process writes −22.16–27.93% per pair (25.05% pooled), exact parity; cold/warm p95 order effects. LZ4 stays OFF. |
| WAL-LZ4 contained harness failures | [artifact placement](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-contained-tmp-01/ABORTED-CONTAINED.txt), [dictionary TMPDIR](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-order3-01/ABORTED-CONTAINED.txt), [index freshness](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-tmpdir-01/ABORTED-CONTAINED.txt), [TMPDIR inside index](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-lz4-workspace-tmp-01/ABORTED-CONTAINED.txt) | Four pre-fixture harness/environment aborts. Roots are consumed and retained; cleanup verified where a contained unit launched. No performance data and no host-pressure refusal. |
| Combined stats-event coalescing + LZ4; isolated durable WAL ACK/replay subprobe | [earlier candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-warm-01/REPORT.md), [earlier control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-warm-order2-01/REPORT.md), [root01 no launch](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-01/NO-LAUNCH.txt), [control-first root02](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-02/REPORT.md), [candidate-first root03](immutable-write-amplification/m4-write-amplification-pair-20261024-wal-replay-probe-03/REPORT.md), [validation](immutable-write-amplification/wal-replay-probe-validation-20261024.md) | Earlier complete-workspace pairs: process writes−30.2–30.4%, but query p95 reversed by order. Roots02/03: exact parity, ~39.0% lower process writes and65.8% lower WAL appends. Isolated LZ4 probe: WAL−65.87%, physical writes−64.1%; candidate ACK p95 lower in both orders but gap magnitude varied; replay-open near parity. Known directory-activity races made both newer roots UNKNOWN, so neither qualifies. No default change. |
| Incremental stats coalescing on LZ4 + ApplyBatch | [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-incremental-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-coalesce-incremental-order2-01/REPORT.md), [prepared contract/results](immutable-write-amplification/lz4-stats-coalescing-incremental-PREPARED-20261024.md) | Two COMPLETE-workspace roots, exact parity; coalescing added −9.97%/−13.62% process writes (11.80% pooled) by shrinking stats log3.526MB→10.2KB. WAL/segment/fsync/compactions unchanged; CPU nearly flat, query p95 within+8.7%, single-sample delete-batch latency noisy. KEEP as opt-in research candidate atop LZ4+bounded ApplyBatch; still NON-QUALIFYING/no default change. |
| LZ4-only target-file-cold replay | [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-cold-replay-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-cold-replay-order2-01/REPORT.md), [contract/results](immutable-write-amplification/lz4-cold-replay-PREPARED-20261024.md) | Two counterbalanced roots, exact parity,0 page-resident files after advice; WAL−65.87%, probe writes−63.0–63.1%. ACK p95, write CPU and replay-open reverse with order; the control role has workspace uncertainty in both orders (candidate PASS). KEEP LZ4 opt-in/OFF by default; NON-QUALIFYING, no stable latency effect. |
| Quota-backed LZ4 OFF/ON follow-up | [control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-quota-wal-lz4-order1-01/REPORT.md), [candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-quota-wal-lz4-order2-01/REPORT.md), [analysis](immutable-write-amplification/ANALYSIS-20261024-quota-followup.md) | Two complete 1,200-file pairs; WAL bytes −65.9%, process-write savings repeated, but candidate CPU/latency regressions remain; exploratory/non-qualifying, LZ4 OFF by default. |
| LZ4 acceleration 1 vs4 sensitivity | [old quota-stopped attempt](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-acceleration-1-vs-4-order1-01/STATUS.md), [fresh control-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-acceleration-1-vs-4-pwd-order1-01/REPORT.md), [fresh candidate-first](immutable-write-amplification/m4-write-amplification-pair-20261024-lz4-acceleration-1-vs-4-pwd-order2-01/REPORT.md), [analysis](immutable-write-amplification/ANALYSIS-20261024-quota-followup.md) | New PWD workspace qgroup 0/259, same 128 MiB cap. Both fresh orders complete, hard-quota receipts PASS, correctness exact. Counterbalanced CPU −0.05%, process writes +2.09%; query p95 mostly worse and WAL +0.51%. No-go acceleration 4; keep acceleration 1 and defaults unchanged. Archives hash-verified, temp roots cleaned. Verify qgroup headroom before further runs. |

## Immutable-shard WAL batching diagnostic — 2026-10-23

Three 1,200-file M4-only pairs show `ApplyBatch` (up to 256 mutations / 8 MiB per durable batch) used 52.49/52.18/52.62 MB process-attributed writes versus 533.70/536.47/550.07 MB for single-Upsert control: pooled reduction 90.29% (10.30× fewer), with text-byte WA 2.82–2.84× versus 28.85–29.73×. In the instrumented pair, WAL frame bytes were nearly equal (19.87 MB vs 19.76 MB), while final WAL sync counts were 6,187 vs 28 and segment bytes/logical state matched. This supports repeated fsync/writeback and Btrfs CoW metadata as the proximate amplification mechanism, not a proven device-level attribution. On the WAL-attribution pair, MemAvailable was 4.28→3.57 GiB, load-1 3.35–3.58, and I/O PSI-full avg10 peaked at 5.02%; these were telemetry only and limit latency interpretation. Correctness/oracle checks had zero mismatches. **KEEP as exploratory research candidate only**: the single-Upsert arm had incomplete workspace accounting in each pair, so all reports set `performance_eligible=false`. Candidate durability is atomic per bounded batch rather than per individual Upsert. Focused `m4_correctness` CTest passed 1/1. Full analysis, limitations, operation IDs, and next step: [immutable write-amplification diagnostic](immutable-write-amplification/ANALYSIS-20261023.md).

Owner decision now implemented: host PSI, load, and MemAvailable are telemetry only, never admission/abort/measurement-quality gates. Real disk/cgroup/runtime/required-telemetry/correctness/durability/cleanup safeguards remain. Earlier entries below that say a run was blocked by host-pressure thresholds are historical and are not current policy. This policy change does not retroactively qualify old evidence.

Follow-up source attribution added `IndexStats::wal_bytes_appended` and P4 serialization. The WAL-attribution pair showed nearly equal WAL frame bytes but sync counts 6,187 vs 28. Total-run cap 12→16 had no effect because total runs stayed below 12. Two per-level cap4-vs6 pairs reduced segment writes 18.43% and process writes 6.96–7.49%, with two fewer compactions, but p95 regressed 23–103% in the counterbalanced pair; **REWORK cap6, not default**. The cap6 linear-vs-heap screen was correct and write-neutral but failed its per-stage p95 target; the heap implementation was removed. Next is a candidate-first cap4-vs5 pair to test the intermediate trade-off. Workspace uncertainty on the counterbalanced cap pair prevents performance eligibility. See [analysis](immutable-write-amplification/ANALYSIS-20261023.md), [per-level report](immutable-write-amplification/m4-write-amplification-pair-20261023-level-cap-01/REPORT.md), [heap screen](immutable-write-amplification/m4-write-amplification-pair-20261023-term-union-heap-01/REPORT.md), and [`ACTIVE-EXECUTION.md`](ACTIVE-EXECUTION.md).

## Immutable-shard baseline: reuse verified analyzers across live generations — 2026-10-23

Promoted `Options::reuse_analyzer_cache` to ON by default in standalone M4. This is a weak-reference cache: live immutable generations share the `Analyzer::Impl`, but the registry itself cannot extend its lifetime. Both dictionary fingerprint passes remain enabled, so each open still verifies manifest identity and detects dictionary mutation during open. Historical paired evidence from two reverse-order 500×20 B3 comparisons (20/20 cache hits per candidate arm) showed ratio-of-sums reductions of 40.0% process CPU, 47.6% writer CPU, 32.8% commit p95, and 51.2% M4 Open CPU p50; candidate peak RSS was ~217 MiB vs ~292 MiB in control. Reader p95 changed direction between pairs; no reader effect is claimed. Regressions: full native build/CTest 9/9 and analyzer lifecycle, dictionary identity, cold-process, race/TOCTOU tests pass under the new default. Exact historical evidence and limitations: [Open attribution/cache report](m4-m4-open-attribution-cache-20261018.md). This does not integrate M4 into production.

## Hash temporary-memory reduction for dictionary fingerprints — 2026-10-23

Reworked the standalone SHA-256 helper to process complete blocks directly from `string_view` and pad with at most 128 bytes of stack storage, instead of copying the entire input and allocating a padded copy. This applies to dictionary fingerprint checks during `Index::Open`, preserving the same digest and both identity/TOCTOU passes. Independent known vectors spanning SHA padding boundaries through a 1 MiB input pass; full native CTest 9/9 and focused correctness/analyzer-lifecycle CTests 2/2 pass. The changed files are LSP-clean. No policy-qualified end-to-end open/RSS measurement was run, so benefit is presently a guaranteed reduction in hash scratch allocation, not a measured latency or process-peak claim. The next measured search target is the redundant ALL-filter candidate materialization described in [ACTIVE-EXECUTION.md](ACTIVE-EXECUTION.md).

## Skip candidate materialization for `ALL` FTS filters — 2026-10-23

`SearchFtsFiltered` now dispatches `FilterExpr::ALL` directly to ordinary `SearchFts`, avoiding construction/copy/sort of the entire live-document ID universe and bypassing the filter-cache mutex. This is semantically identical: the regression compares hit order, scores, IDs, and diagnostics against unfiltered search across update/delete, flush, compaction, WAL replay, and reopen. Full M4 build/CTest passes 9/9; diagnostics confirm zero explicit filter candidates/time. In the historical 10k-doc/64-query hybrid diagnostics, filter work was 1.661 ms median of 1.724 ms FTS total for 10,000 candidates while only 10 candidates were checked. No post-change latency result is claimed. The policy hard gate prevented a new profile at `MemAvailable=3,485,768 KiB` (< `5,477,762 KiB`); no workload was launched and no threshold was changed.

## Seek immutable-segment prefix filters — 2026-10-23

`STRING_PREFIX` evaluation on immutable runs now lower-bounds the lexically sorted persisted value list and visits only its matching range; the bounded mutable delta remains a linear scan. This is an O(log V + M) lookup per run with no extra retained index/cache, and leaves suffix semantics unchanged. The regression checks exact PK sets across two 512-value runs, update-in/out, delta flush, compaction, and reopen; the comparison diagnostic remains below 128 visits for 1,024 distinct values. This validates correctness and search-work complexity, not wall-clock latency. Full CTest passed 9/9 and bridge integration passed 4,662 checks/0 failures. No benchmark was started because latest `MemAvailable=5,028,116 KiB` remains below the unchanged 5,477,762 KiB guard.

## Architecture decision review — 2026-10-23

No architecture winner is established. Immutable-shard remains the baseline/correctness reference; shared-collection remains an unselected research alternative. The only direct pair is measurement-ineligible due to incomplete workspace evidence, non-equivalent writer timing boundaries, and no guaranteed query overlap with native flush. Its lower one-run shared peak is descriptive only. See the [architecture decision review](ARCHITECTURE-DECISION-20261023.md) for source-grounded tradeoffs, bottleneck classification, decision rationale, and evidence required to revisit.

## Shared collection vs immutable-shard pair — 2026-10-23

Operation `m4-shared-collection-pair-20261023-021949-4a92c5` completed both 500-document × 5-round arms with zero coherence failures, but both receipts are `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` / `measurement_quality=INCOMPLETE` / `performance_eligible=false`. Hard cgroup telemetry and cleanup completed; no swap/OOM/limit events; per-arm cgroup I/O accounting unavailable. Do not treat it as a qualifying performance comparison or retry it. The shared arm's one-run cgroup peak was 223,338,496 B vs immutable-shard's 326,701,056 B (31.64% lower), while 20-query reader p95 was 2.293 ms vs 1.386 ms; this is descriptive only and the reader tail sample is too small. Shared commit p95 was 90.634 ms, GC p95 32.755 ms, and native collection flush p95 48.787 ms. Source inspection confirms native flush holds an exclusive schema lock shared by native queries, but the one-query-per-reader-per-round schedule does not guarantee overlap with flush. Full evidence, stage analysis, limitations, and next profile are in [the pair analysis](shared-collection-pair-20261023/ANALYSIS-20261023.md); raw checksummed receipts are in that operation bundle.

## Concurrent allocator-accounting follow-up — 2026-10-23

Distinct one-shot profile `m4-lock-contention-microbench-memory-churn-two-versions-concurrent-allocator-20261023-014604-71f400` passed with complete containment and verified checksums. `mallinfo2` live allocations rose +156,643,040 B with both analyzers open, then returned to within +4,112 B of baseline after both released. PSS remained +43,592 KiB, while allocator free-arena bytes rose +44,650,480 B—within 12,272 B of the PSS residual in bytes. This is strong one-run evidence that the observed post-release residency is allocator-retained freed pages rather than a comparable amount of live malloc-tracked cache data; not object-level attribution, leak proof, performance evidence, or a capacity result. `memory.peak` 359,325,696 B, no swap/OOM; workspace samples completed but are sampled-not-proof. Full analysis and raw checksummed bundle: [ANALYSIS-20261022.md](lock-contention-microbench/ANALYSIS-20261022.md).

## Cgroup memory-stat cache-churn follow-up — 2026-10-23

Operation `m4-lock-contention-microbench-memory-churn-two-versions-eviction-20261022-180201-4a4544` completed `PASS` with 1,024 docs × four arms and two dictionary identities under the existing 512 MiB/no-swap contract. 22/22 runtime rows contained cgroup-v2 `memory.stat`; watchdog had no abort, OOM/max events, or swap, and cleanup was verified. Kernel `memory.peak` was 324,321,280 B (309.30 MiB); max sampled current was 271,630,336 B (259.05 MiB), process-tree PSS 265,852 KiB. At that sample: anon 256,233,472 B, file 12,210,176 B (including 5,595,136 B shmem), kernel 1,044,480 B. The maximum sample came 193 ms after the final arm marker, during the source-verified two-version analyzer-churn helper. The ~50.25 MiB difference between cgroup high-water and sampled current indicates a transient missed by 100 ms sampling; its exact type/cause remains unknown. Findings suggest transient anonymous native allocation during dictionary initialization, not page cache, but do not identify the internal copy or explain the earlier 4,096-document/512 MiB abort. Two version init times were 310.950/298.662 ms; PSS after init/release remained 182,355 KiB versus 168,767 KiB baseline (allocator retention, not proof of leak). No performance or capacity claim. Checksummed raw operation artifacts are under `lock-contention-microbench/20261022/m4-lock-contention-microbench-memory-churn-two-versions-eviction-20261022-180201-4a4544/`.

## Analyzer cache-construction copy-reduction prototype — 2026-10-23

Source review found `BuildDictCacheEntry` sorting a full `DictUnit` copy, then deep-copying parsed units into the cache while both were live. The isolated patch `patches/cppjieba-dict-cache-lifecycle-memory-optimized.patch` replaces that with scalar weight min/max/upper-median selection and moves parsed units into the immutable entry. Exact weight/lifecycle tests pass; the full reproducible-overlay CTest suite passed 9/9, policy suites 60/60 and 23/23. Source identities: patch SHA `2e81ef69aebfd221aae604bc1e077acdb34b89eafd6c383b5caaff0af602a864`, overlay tree SHA `93635d1eed3c9e31f61c732d056e6f0c06cea40c75004221489dbb8547729f1a`, candidate binary SHA `722a6ac23d7121ec7b37a6cab910e09433a756335f3a616af275d3a4956e38bc`. One bound same-cap operation `m4-lock-contention-microbench-memory-churn-two-versions-memory-opt-20261022-182340-f4bbce` completed PASS with 20/20 samples and verified cleanup. Versus the prior eviction run, memory.peak fell 17.3% (324,321,280→268,075,008 B), max sampled current fell 25.6% (271,630,336→202,014,720 B), and max anon fell 26.2% (256,233,472→189,054,976 B). Two per-version init times were also lower, while retained post-release PSS increment was unchanged (+13,588 KiB); the unsampled memory.peak/current gap widened, so another transient remains. These are one-run directional observations under similar but uncontrolled host conditions and different arm order, not causal proof, stable performance, or capacity claims. The earlier 4,096-document abort remains unattributed. Detailed limitations and checksums: [analysis](lock-contention-microbench/ANALYSIS-20261022.md); active state: [checkpoint](ACTIVE-EXECUTION.md).

## Trie-construction direct-insert follow-up — 2026-10-23

A source-derived prototype removed `DictTrie::CreateTrie`'s per-analyzer copied `vector<Unicode>` and pointer vectors by inserting base/user `DictUnit`s directly into a local trie in unchanged order; the existing vector-taking `Trie` constructor remains. Full overlay CTest passed 9/9; policy tests 61/61 and benchmark-policy tests 23/23. One same-cap 1,024-doc/two-version operation (`m4-lock-contention-microbench-memory-churn-two-versions-trie-stream-20261022-183742-f0439d`) completed `PASS`/`COMPLETE`, 21/21 samples, all four arms and two dictionary versions successful, zero coherence/durability/writer/reader failures, verified cleanup. `memory.peak` 260,734,976 B; max sampled current 257,064,960 B (anon 245,170,176 B, file 8,847,360 B, kernel 1,089,536 B); swap and OOM/max events zero. The peak/current gap was 3,670,016 B vs 66,060,288 B in the preceding copy-reduction run, but absolute peak fell only 2.7% while max current/anon rose 27–30%; arm order and host state differed. Init samples were 210.413/247.890 ms, slower than that prior run's 193.858/189.750 ms. Workspace traversal is not atomic (`SAMPLED_NOT_PROOF`), and cgroup I/O telemetry is unavailable; the operation is not performance-eligible. This supports further investigation of transient timing but is not causal memory attribution, stable latency/memory evidence, or capacity proof. A bounded external-sort count of the pinned 348,982-word dictionary found 497,991 trie nodes (including root), 201,008 per-node maps, and 157,162 unary maps (78.2%). At local ABI sizes (TrieNode 16 B, map 56 B, pair 16 B, first-insert bucket count 13), node/map/value/bucket payload alone has a conservative 48,096,976 B (45.9 MiB) floor; hash-node and allocator overhead are excluded. This suggests an inline-first-child prototype, not a measured saving or RSS attribution. See [analysis and raw receipts](lock-contention-microbench/ANALYSIS-20261022.md).

## Inline-first-child Trie prototype — 2026-10-23

A source-derived candidate stores one edge inline per `TrieNode`, retaining per-node unordered maps only for additional children. The exact overlay patch/headers and binary are fingerprinted in the operation plan. The overlay lifecycle test covers the legacy vector constructor, prefix and branch lookup, insertion, deletion/pruning through inline and map children; full CTest passed 9/9, containment policy 62/62, benchmark policy 23/23. One same-cap operation (`m4-lock-contention-microbench-memory-churn-two-versions-inline-child-20261022-185330-3595c0`) passed with all functional/durability/coherence counts zero, 16/16 process/workspace samples, no swap/OOM events, and cleanup verified. At 512 MiB/zero-swap, `memory.peak` was 250,105,856 B and max sampled current 227,295,232 B (anon 214,683,648 B). Relative to the preceding trie-stream trace these are −4.1% peak and −11.6% current/−12.4% anon, but the high-water/sample gap grew from 3,670,016 B to 22,810,624 B. Per-analyzer PSS deltas over each run's own baseline were +24,564/+19,616 KiB vs +20,040/+17,696 KiB previously, so the direct residency signal contradicts a simple footprint-reduction claim. Init samples (141.694/245.158 ms vs 210.413/247.890 ms) are single-run and inconsistent. No cgroup I/O telemetry; performance is ineligible. Treat as correctness and bounded-execution evidence only; investigate phase/allocation attribution before another Trie workload. Full raw evidence and caveats: [analysis](lock-contention-microbench/ANALYSIS-20261022.md).

## Concurrent analyzer version coexistence — 2026-10-23

A separate policy-bound profile initialized two distinct dictionary fingerprints concurrently and retained both analyzers for a joint memory sample. Both tokenized successfully; all workload/writer/reader/durability/coherence counts were zero. In-process PSS rose from 144,957 KiB baseline to 276,841 KiB with both live (+131,884 KiB), stayed unchanged after the first release, then fell to 191,701 KiB after the second (+46,744 KiB vs baseline). The residual is directionally consistent with cppjieba's source-confirmed path-keyed process-retained parsed dictionaries; PSS does not provide exact ownership, and allocator release behavior complicates the intermediate sample. This test intentionally bypasses M4's same-key single-flight cache, but uses two different content identities, which are not coalesced in normal operation. Do not treat it as a production forecast or compare causally against sequential single-run PSS.

Cgroup `memory.peak` was 368,136,192 B (68.6% of unchanged 512 MiB cap), max sampled current 327,503,872 B, and max anon 305,967,104 B; zero swap/OOM/limit events. The 40,632,320 B peak-current sampling gap leaves transient ownership unresolved. All functional checks returned zero, but one workspace scan caught `DIRECTORY_ACTIVITY (inotify mask=2)`, so receipt status is `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` / `measurement_quality=INCOMPLETE`; no clean workspace-quality or performance result. Unit/TMPDIR cleanup and 17 checksums verified. No retry or cap change. Full details: [analysis](lock-contention-microbench/ANALYSIS-20261022.md).

## Latest watchdog timing-gap follow-up — 2026-10-22

One explicitly owner-authorized fixed synthetic containment rehearsal completed `PASS` (32 KiB, 1 s; 11 samples; no abort; cleanup verified). Maximum live sample gaps were 0.111149375 s monotonic and 0.111148095 s boottime; the prior 15.323005 s anomaly was not reproduced, so its cause remains unknown. The 2 s gate is unchanged. Full native build/CTest passed 8/8, TSan passed 2/2, and Python suites passed 48/48 + 11/11 + 4/4. This is verification/containment evidence, not a native performance result. [Analysis](lock-contention-microbench/ANALYSIS-20261022.md) · [raw rehearsal receipts](containment-probes/20261022/m4-contention-rehearsal-20261022T113503Z-clock-gap-probe-01/) · [verification report](lock-contention-microbench/verification-20261022/REPORT.md).

## Active M4 mission status — renewed exploratory authorization

### Timestamp-verified reader-through-flush pair — 2026-10-23

Operation `m4-lock-contention-microbench-flush-reader-staging-pair-20261023-061457-0d491c` PASSed with return code 0; both arm timelines passed timestamp and `M4_PHASE` overlap validation and all 17 artifact checksums verify. This was one fixed-order exploratory pair, not a stable-tail qualification; the registered one-shot operation is consumed.

| Metric | Baseline | Metadata staging | Observation |
|---|---:|---:|---|
| Continuous reader queries through flush | 21 (10/11 per reader) | 28 (14/14) | Every reader had ≥2 pre, ≥1 overlapping, ≥2 post; actual overlap 5/6 and 9/9 |
| Reader wall p50 / p95 / p99 | 1.118 / 3.288 / 3.522 ms | 0.589 / 1.119 / 1.177 ms | Candidate p95 ~66% lower in this run |
| Query-mutex wait p95 | 1.993 ms | 0.137 ms | ~93% lower |
| Flush query-lock hold / state-lock hold | 1.912 / 7.521 ms | 0.0079 / 5.828 ms | Query hold reduced; writer-state lock remains |
| Concurrent-compaction writer p95 / state-wait p95 | 28.925 / 28.130 ms | 36.486 / 35.929 ms | No observed writer-tail improvement; not measured during explicit flush |
| Bytes written / segment bytes / WAL fsyncs | 8,811,226 / 4,281,688 / 96 | 8,811,226 / 4,281,688 / 96 | Identical; no logical-byte amplification ratio available |

Paired writer-call traces show state-wait subtraction p95 0.773/0.693 ms and wait-thread CPU p95 0.145/0.102 ms against 28–36 ms state-lock wall waits; queue/acquisition dominated observed writer tail, while scheduler wakeup/fairness remains unseparated. Peak cgroup memory 159,002,624 B; swap 0. Host I/O PSI-full avg10 1.47→1.75%, and cgroup I/O attribution was unavailable. The runner marked `performance_eligible=false`; report only as an exploratory mechanism signal. Full analysis, operation path, limitations, and evidence reference: [`ANALYSIS-20261023.md`](lock-contention-microbench/ANALYSIS-20261023.md).

### Writer group-commit prototype — 2026-10-23

One bounded, contained, two-arm direct-Upsert vs opt-in group-commit operation `m4-lock-contention-microbench-writer-group-commit-20261023-040914-808d58` PASSed with zero writer/reader/coherence/durability failures and verified checksums. The group arm formed eight four-record batches and observed 8 WAL fsyncs vs 32 direct; writer p95 was 16.353 ms vs 54.849 ms in this single fixed-order run. Host load-1 exceeded the historical envelope (observation-only for this profile), and the run is exploratory/non-qualifying; no stable tail, CPU/I/O, or architecture claim. Reader p95 used only 64 short queries and did not overlap the full ~15 ms compaction. The source remains opt-in and default-off. After measurement, queued `Document` copies were removed by retaining caller references until synchronous completion; final normal and TSan correctness tests passed. That consumed default-compaction profile was not rerun against the source-only refinement. A separate current-source snapshot-rebase/group-commit operation is recorded below. Full limitations, resource evidence, checksums, and the local pinned-artifact replacement note: [analysis](lock-contention-microbench/ANALYSIS-20261023.md).

### Snapshot-rebase + group-commit paired operation — 2026-10-23

A distinct policy-pinned one-shot operation `m4-lock-contention-microbench-writer-group-commit-snapshot-rebase-20261023-042945-98fd8c` PASSed: direct vs queued group-commit Upserts, both concurrent with WAL-preserving snapshot rebase. Writer p95 was 14.433 ms direct vs 1.713 ms grouped, with 32 vs 8 WAL fsyncs and four-record groups. Reader p95 moved in the opposite direction, 0.179 vs 0.426 ms (reader-mutex-wait p95 0.0026 vs 0.147 ms); only 64 queries/arm, so this is a potential trade-off, not a stable regression. All writes/reopen/durability checks passed and checksums verify. Its post-concurrent flush (after reader threads had ended) held state/query locks 4.17–4.79/2.58–3.12 ms; segment write, stats, tombstone, and manifest stages were each about 0.7–1.5/0.7–1.0/0.8–0.9/0.9–1.0 ms. Source analysis confirms the state lock remains held across segment construction and query lock covers metadata publication, WAL reset, GC, and delta reset. The operation was admitted under its unchanged 3-GiB reserve + 512-MiB cgroup contract; MemAvailable was below the historical success envelope but that comparison is explicitly observation-only in this profile. Cgroup peak 182,243,328 B, zero OOM/swap/throttling; cgroup I/O stats unavailable. No architecture or performance qualification. [Full stage/resource analysis](lock-contention-microbench/ANALYSIS-20261023.md).

### Query-lock publication staging follow-up — 2026-10-23

Fresh operation `m4-lock-contention-microbench-writer-group-commit-20261023-053935-50c983` PASSed the same registered two-arm profile as pre-change operation `...040914-808d58`; checksums and reopen/correctness validation pass. Comparing the post-explicit-flush stage diagnostics, query-mutex hold was 7.226/7.588 ms before vs 0.00123/0.00105 ms candidate, while candidate writer-state hold remained 3.66/4.73 ms. This supports shorter reader-lock critical section, **not reduced writer serialization**. The two runs had mismatched host conditions; writer p95 and flush wall-time changes are not causal. All reader samples ended before explicit flush, so reader tail during flush remains unmeasured. Candidate peak cgroup memory 182,136,832 B; no swap/OOM. No stable performance or architecture claim. [Full analysis and checksummed artifacts](lock-contention-microbench/ANALYSIS-20261023.md).

### Latest memory-cause diagnosis — 2026-10-22

Source review identified process-lifetime growth in cppjieba's path-keyed parsed-dictionary cache: M4 assigned a fresh temporary path for every analyzer initialization/reopen. The M4 prototype now reuses an immutable content-addressed snapshot path with reference-counted leases. A fresh, policy-bound 1,024-document-per-arm four-arm operation completed under the unchanged 512 MiB cap at an observed cgroup `memory.peak` of 189,665,280 B, with no OOM/swap events; the old 4,096-document OOM remains a consumed raw failure and was not rerun. The post-fix operation is `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` / `measurement_quality=INCOMPLETE`, so it is exploratory memory evidence only, not PASS or performance evidence. Native cache lifecycle/correctness tests and policy suites pass. The same source audit found 228 stale legacy snapshot directories in `/tmp` (456 byte-verified dictionary/model files, 1,275,691,008 B allocated); only these inactive generated copies were removed and the exact allocation was reclaimed. The cleanup receipt and limitations are recorded in [post-fix analysis](lock-contention-microbench/ANALYSIS-20261022.md) and [cleanup report](lock-contention-microbench/20261022/ORPHANED-ANALYZER-SNAPSHOT-CLEANUP-20261022.json). To prevent recurrence in this harness, each contained microbench now uses an operation-private `TMPDIR` on the same temp filesystem; the runner removes only that root after verified unit/cgroup cleanup and preserves it on uncertainty. The cleanup helper's focused tests pass, and its safety behavior was validated without launching a workload. A separate registered 2,048-document-per-arm post-fix scale trace then completed under the unchanged 512 MiB cap: cgroup `memory.peak` 194,039,808 B, zero OOM/swap events, all four arms verified, measurement quality complete. This is one memory-scaling observation, not performance evidence or 4,096-document capacity proof; the consumed 4,096 OOM was not rerun. Full phase/memory and host telemetry: [analysis](lock-contention-microbench/ANALYSIS-20261022.md).

**IN PROGRESS / ARCHITECTURE OPEN.** The owner explicitly resumed the full M4 research mission and authorized bounded implementation, correctness work, and repeated exploratory measurements without per-milestone approvals. This supersedes the historical pause recorded in [M4-STATUS-20261021.md](M4-STATUS-20261021.md); it does not waive resource, containment, correctness, durability, or cleanup gates.

Latest M4 writer/reader diagnostic: one contained paired compaction operation `m4-lock-contention-microbench-20261022-090439-70e7a5` completed once (inner `PASS`, rc=0, workload failures/coherence errors 0), but its wrapper status is preserved as `OPERATION_INCOMPLETE` due to a post-run parser that expected single-line JSON. Saved output was parsed and contract-verified offline after the parser fix; raw plan/receipt/status were not rewritten and no retry occurred. Result remains `performance_eligible=false` (non-atomic workspace accounting; historical MemAvailable envelope below threshold). Descriptively, default writer p95/state-wait p95 were 30.869/30.156 ms and reader p95 0.043 ms; snapshot mode writer p95/state-wait p95 22.850/22.162 ms, query-wait p95 0.962 ms, reader p95 1.563 ms, and its compaction aborted as superseded, leaving 6 runs vs 2 after Flush. One operation with 32 writes and 64 queries/arm cannot establish stable tails or a winner. Source analysis found two document-wide scans in snapshot tombstone capture under state/query locks; the current unbenchmarked code now filters tombstone IDs by selected run ranges. CTest passes 8/8 and ThreadSanitizer 18,936 checks/0 failures after the source edit. [Detailed report](lock-contention-microbench/ANALYSIS-20261022.md) · [post-processing recovery](lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-090439-70e7a5/POSTPROCESS-RECOVERY.md).

Latest fully completed instrumented pair `m4-shared-collection-pair-20261022-013401-a450e2` is descriptive only: workspace accounting is uncertain and the receipt is not performance-eligible. In its shared arm M4 query-lock wait p50/p95 was 4.255/7.888 ms, while coordinator snapshot-lock wait p95 was 0.001 ms; see [derived analysis](shared-collection-pair-20261022/analysis-shared-query-lock-20261022.md).

The changed-binary operation `m4-shared-collection-pair-20261022-013803-1f9ce2` completed only its shared arm; inter-arm recovery stopped before the immutable arm. A later fresh pair `m4-shared-collection-pair-20261022-040857-4ee12c` was refused before launch at 3,794,853,888 B versus its 4,294,967,296 B operation reservation. Neither operation was retried. The 4-GiB sum derives from that pair's 1-GiB cgroup cap plus 3-GiB host reserve; it is not a universal host-safety minimum. See the [lock-microbench analysis](lock-contention-microbench/ANALYSIS-20261022.md) for source, gate scope, and exact evidence.

To advance lock research without waiting on the 500×5 pair, the owner authorized a bounded single-arm 64×10 diagnostic with an unchanged 3-GiB reserve and 512-MiB cgroup cap. Its first 256-MiB contract was stopped by the watchdog on memory-cap events (no OOM); a fresh operation under the revised cap completed all 10 cycles and 40 reader samples, zero coherence errors/failures, and verified cleanup. Descriptive p50/p95: M4 query-lock wait 0.001/0.009 ms; reader total 0.849/1.867 ms; GC 12.145/19.356 ms; writer commit 37.405/43.807 ms; writer cycle 50.072/57.459 ms. Workspace accounting remains non-atomic/INCOMPLETE, so evidence is not performance-eligible and supports no A/B or causal claim. Correctness/durability/crash regression remains 4,658 checks, zero failures. The exact current state and next action are in [ACTIVE-EXECUTION.md](ACTIVE-EXECUTION.md).

A research-only native C API subprocess probe confirms read-only collection open is rejected while another process holds the same collection read-write (`Can't lock read-only collection`), then succeeds after writer close. M4 `Index::Open` also mutates its directory and has no read-only option. The actual Rust application uses a resident daemon broker, in-process read-session cache, and existing cross-process workspace locks; **brokered shared M4 is a provisional architecture fit**, not a performance/production selection. Direct client-process opens remain unsupported; immutable-shard remains fallback pending stable shared-reader and durability evidence.

## Latest operation — corrected shared-pair measurement (2026-10-21)

One newly authorized, rebuilt same-binary pair completed both arms: immutable-shard then shared-collection, 500 docs × 5 churn rounds, four readers. Both arms passed 500-live-doc/coherence checks and cleanup; workspace scans were complete but non-atomic, so lifetime peaks remain unverified and measurements are `INCOMPLETE`/non-qualifying. Baseline writer `commit` p50/p95 was 586/1074 ms; shared `writer_cycle` (commit plus GC) was 464/482 ms. Reader p50/p95 was 1.58/2.02 ms versus 64.78/97.69 ms. The shared arm had much higher host I/O PSI (10.11% vs 3.68%), process-tree writes (~209 MB vs ~61 MB), and retained apparent root (~34.7 MB vs ~11.5 MB). Single-run, uncontrolled contention, differing writer boundaries, and small sample size prohibit a causal or architecture-wide win claim. Stage samples show shared M4 search/delete in GC at 350–383 ms (total GC 374–407 ms; compaction 0 ms); source inspection shows exclusive snapshot locking around commit and GC, with shared locking across queries. This motivates a bounded lock-duration/GC investigation, without attributing all reader delay to M4. **Decision: keep immutable-shard as the current baseline; defer shared-collection promotion** while investigating reader latency and GC/storage cost. The initial post-arm parser error was repaired by parsing the saved `documents` record; neither arm was rerun. Full analysis, raw outputs, telemetry, and checksums: [corrected pair report](shared-collection-pair-20261021/m4-shared-collection-pair-20261021-221036-a40ca2/pair-report.md).

## Previous operation — shared-pair watchdog binding refusal (2026-10-21)

The 4.46% host I/O PSI admission gate was removed only for the approved 500×5 exploratory shared-collection pair; PSI remains recorded, and global policy is unchanged. The fresh amended-contract capacity preflight passed (MemAvailable 7,785,968 KiB; `/var/tmp` free 87,455,379,456 B; PSI full avg10 3.04%). Two recorded wrapper invocations failed before the native harness started because the independent watchdog accepted `aa-first`/`aa-second` but not `pair-first`/`pair-second`. Both have `benchmark_started=false`, controller error receipts, and verified unit cleanup; no arm or pair measurements exist. The second invocation was accidental during reproduction, is preserved, and does not authorize retry.

The validator now admits only the pair phase names for this operation; first-arm runtime phase mapping is corrected so the larger two-root reserve remains in effect. Focused watchdog/policy regression tests and no-launch command binding pass. Full contention-policy tests: 41/42; the lone failure is an unrelated legacy full-sync test blocked earlier by compiler-input mismatch for the modified bridge source. Operation receipts/checksums: [shared pair evidence](shared-collection-pair-20261021/). **Status: OPERATION_BLOCKED / OWNER_DECISION_REQUIRED for any new native attempt.**

## Latest follow-up — durable shared-GC cursor and crash replay (2026-10-21)

The test-only shared-collection coordinator now persists a checksummed, atomically replaced GC watermark. Steady-state collection examines only newly eligible generations and newly reserved orphan IDs, instead of rescanning the full historical generation range. The cursor advances only after M4/native deletes are flushed; crashes after either store deletion replay idempotently. The expanded prototype test passes **1,046 checks, 0 failures**, covering crashes after M4 GC, after native GC but before cursor persistence, and after each unpublished commit stage. The full M4 bridge/crash suite passes **1,247 checks, 0 failures**. This is tiny-fixture correctness evidence, not a performance result or cross-process lease proof.

No immutable-shard/shared-collection performance pair was run. At 2026-10-21 13:35 UTC, MemAvailable was 4,083,716 KiB, below the existing generic 5,477,762 KiB floor; `/var/tmp` had 87,494,950,912 B free and host I/O-full PSI avg10 was 3.36%. No operation-bound microbenchmark contract or shared-Btrfs I/O risk acceptance has been approved. The owner-directed task asks not to apply the sustained 500×20 envelope to microbenchmarks, but the repo policy still requires an explicit per-operation contract before launch. No wait/retry is running.

## Previous follow-up — writer-tail optimization review (2026-10-21)

Separate evidence-based dispositions: (1) scalar initialization is **DEFERRED** pending internal phase measurements; existing empty-collection timings show a strong but noisy scalar-count association, while HNSW graph construction was not exercised. The requested phase sweep is **PARTIAL / OPERATION_BLOCKED** because mandatory preflight failed; no native measurement or instrumentation rebuild occurred. (2) Shared-collection compaction remains **DEFERRED for promotion**, but a test-only bounded correctness prototype now has exact-generation GC, reader-lease backpressure, and recovery cleanup of uncommitted WAL-reserved rows. It passes 931 checks with zero failures, including crash interruption after reserve/M4/native staging and during GC. This does not demonstrate full-size performance or cross-process reader safety; immutable shards remain the baseline. (3) Cached fingerprint is **KEEP opt-in only; reject default promotion**: the single 500×5 run observed 38–42 ms lower M4 opens and no native-open change. Digest invalidation before open and cold-process full verification are covered; the opt-in skips detection of a source-path change in the interval after initial hashing, so strict verification remains default. The candidate did not show a measurable memory or physical-I/O benefit.

Full reasoning and status: [writer-tail phase trace / three-direction review](writer-tail-phase-trace-20261021/REPORT.md); raw timing basis: [writer-tail investigation](writer-tail-investigation-20261021/REPORT.md). No combined candidate was run. No production or native source changed.

## Previous follow-up — bounded scalar-open latency diagnostic (2026-10-21)

One separately authorized direct diagnostic completed once: scalar counts 0–6 × HNSW/FLAT × three repetitions, **84 API opens**, no documents/queries; contained command 5.721 s, exit 0. Per-cell create+open/reopen medians and ranges are in the [full report](scalar-open-latency-diagnostic-20261021/m4-scalar-open-latency-diagnostic-20261021-r1/REPORT.md); all raw API calls and analysis are preserved beside it.

**COMPLETED_WITH_WORKSPACE_UNCERTAINTY / WORKSPACE_CAPACITY_UNVERIFIED; EXPLORATORY / NON-QUALIFYING.** 44 traversals completed but were non-atomic, 9 had known mutation races, and one lost mutation coverage after the bounded event drain. Workload completion is not capacity proof; no verified peak or qualification PASS is claimed. Hard cgroup/timeout/correctness guards passed, host reserve floors passed, and cleanup was verified. Host load and positive swap/reclaim deltas remain observations, not M4 attribution. The scalar-open diagnostic's one-run authorization is spent. Current owner-directed research permits further M4 investigation, but does not waive fresh operation-bound planning or unchanged resource gates. See `research/m4-native/docs/benchmark-policy.json` for the operation boundary.

## Previous follow-up — resource-only exploratory contract amendment (2026-10-21)

**PARTIAL / MEASUREMENT_INCOMPLETE.** One owner-approved full workload ran: 7 scalar counts × HNSW/FLAT × 3 reps; create/close/reopen/close = **84 API opens**, empty collections, one root at a time. It returned 0 in 3.4919 s (reported 3.4706 s); this was resource observation, not a latency benchmark. Approved operation-specific floors were MemAvailable ≥3 GiB and `/var/tmp` free ≥8 GiB; both passed at readiness (3,626,200 KiB and 10,738,757,632 B). Cgroup sampled peak RAM 49,082,368 B, swap 0, max tasks 66/128, CPU 2,845,805 µs; no cgroup memory/swap/pids limit or OOM events/throttling. Under the amended resource-only contract, load-1 (5.19 vs historical 3.14) is observation-only; host swap-in/out and direct-scan deltas are recorded/analyzed but positive movement alone is not distress or M4 attribution. Workspace observer lost mutation coverage (“events exceed one bounded drain”): accounting remains UNKNOWN/INCOMPLETE, no verified peak or complete resource-measurement claim. **No retry**; no diagnostic, qualification, or latency claim. Contract amendment: [record](open-probe-resource-plan-20261021/CONTRACT-AMENDMENT-20261021.md), [machine-readable policy](../docs/benchmark-policy.json); original raw receipts remain unchanged: [resource-plan report](open-probe-resource-plan-20261021/REPORT.md), [rehearsal directory](open-probe-resource-plan-20261021/m4-scalar-open-rehearsal-20261021/).

Read-only inventory counted 209 `/var/tmp/m4-*` roots (1,415,430,144 B allocated); results, source, builds and the 20 analyzer snapshots are preserved because snapshot non-use was not fully verified. No cleanup beyond the owned empty rehearsal workspace and transient cgroup/unit, independently verified.

Focused `m4_analyzer_cache_lifecycle` build/CTest passed 1/1 (3.95 s; 38 checks, 0 failures). New rehearsal binary built and containment policy tests pass 36/36. Active LSP timeout remains inconclusive. No runtime algorithm, production code, or benchmark qualification changed.

## Latest result — 2026-10-21 bounded writer-tail diagnosis

No new sustained/native arm. Bounded collection-open probes found exact six-scalar-field HNSW/FLAT medians ~64–80 ms across runs (sample ranges59–91 ms) versus ~8–13 ms without scalar fields; schema assembly was <0.1 ms. One-field controls show no lone dominant scalar field; a heavily perturbed strace summary saw substantially more futex/fsync/fdatasync activity with scalar indexes. This supports scalar-index setup/synchronization as recurring ordinary cost but leaves cycle 7's 180.306 ms open unexplained. One 500×5-per-arm diagnostic of the existing opt-in cached-fingerprint mode reduced M4 open by ~38–42 ms and the compaction-cycle M4 open by 41.40 ms; commit percentiles are directional, not qualification. Existing analyzer lifecycle (1/1) and M4 candidate integration (582 checks, 0 failures) passed. Strict mode remains default; no runtime/native/production change. **At the time of this report, no further native measurement was authorized; the later separate single-trace authorization and preflight refusal are recorded below.** Full report and raw microbench/host receipts: [writer-tail investigation](writer-tail-investigation-20261021/REPORT.md).

### Previous result — 2026-10-21 completed native500x20 after4579e5a

**Requested single exploratory diagnostic completed, with workspace-accounting caveats.** [Full report](pending-drain-native-20261021/REPORT.md), [analysis](pending-drain-native-20261021/analysis.json), [20rounds CSV](pending-drain-native-20261021/per-round.csv). Fresh provenance/readiness PASS; exactly one native arm(PID784988), exit0,20commits/80queries, coherence0; generation21/live500 post-close reopen/errors0. No retry/second arm. Runtime controller/policy/native code unchanged from4579e5a;34focusedtests plus actual same-controller32KiBwatchdog e2e PASS.

| Wall stage, ms | p50 | p95 | p99=max(n20) |
|---|---:|---:|---:|
| Writer commit |231.399|267.359|369.428|
| Native vector build |102.032|117.603|223.503|
| M4 update |103.357|119.271|121.150|
| Post-M4 SyncTree |15.385|26.212|26.304|
| Whole post-M4-to-round-end |24.051|37.658|40.929|

Main phase processCPU4.336s, reopening0.511s; owned cgroup totalCPU4.979s/RAMpeak323,055,616B(308.090MiB)/swap0/tasksmax80. Process-reported writes(main+reopen)220,258,304B, not physical-device attribution. No hard reserve/OOM/task/swap/distress/throttle failure; verified cleanup. Host load6.12–6.32, memoryfullPSI5.63–9.73%, IOfull0.75–1.34%, swap-in+51,823pages: shared contention context, not M4-caused impact. Raw **COMPLETED_WITH_WORKSPACE_UNCERTAINTY / measurementINCOMPLETE**:77complete traversals(notatomic),24knownraces,3pending-drainUNVERIFIABLErows remain UNKNOWN;0sampled budgetoverages, no verified lifetime peak. No fake qualification PASS, stablep99/comparator/candidatewin claim. This new1/1spent; historical4/4campaign and preceding1/1abort/receipts unchanged. No further native authority.

### Previous workspace amendment and aborted attempt (historical)

Owner approved contention-aware-only workspace soft budgets plus **one new native diagnostic arm**, separate from the exhausted4/4campaign. [Amendment report](workspace-soft-budget-20261021/REPORT.md):79distincttests PASS, final32contention rerun, direct4path typecheck PASS; actual independent-watchdog32KiB rehearsal PASS with18budget-warning samples and verified cleanup. Raw receipt is COMPLETED_WITH_WORKSPACE_WARNINGS, not fake PASS. Known scanner races may continue as UNKNOWN/INCOMPLETE; hard reserve/distress/telemetry/time/cgroup checks remain aborting. Qualification/other benchmarks untouched. **Actual outcome: PARTIAL / OPERATION_BLOCKED / NEW_ATTEMPT_EXHAUSTED.** Commitfdef863 preceded fresh readiness/provenance PASS and one native launch(PID716330); after1.175346733s, bounded-drain pending inotify events were treated as fatal coverage loss. Raw WATCHDOG_ABORT/INCOMPLETE, exit-15;0completedarms/0cyclepayloads, writer-tail/stage/coherence unavailable. No OOM/distress/reserve failure observed in20partial samples; cgroup peak229,666,816B/swap0,CPU824,026us,tasks76,process-treewrites39,514,112Bpartial. Cleanup independently verified; no retry. New1/1spent, old4/4unchanged. Exact pending-event accounting case corrected prospectively, raw UNKNOWN retained;81finalsynthetic tests PASS, no native validation of correction. See report/raw/native-analysis.json for full scope/uncertainties.

### Previous strict-contract conclusion (historical)

**PARTIAL / OPERATION_BLOCKED; native writer-tail measurements remain incomplete.** Observer correction is committed in **`6439ed2`** with **72 distinct tests PASS**, final-controller synthetic reruns and four-file direct typecheck PASS. No native execution during this repair; campaign remains **4/4 consumed, zero remaining** (one historical native arm launched, zero completed arms/pairs).

**Focused capacity-feasibility investigation COMPLETE; choose NO-LAUNCH under the current contract.** [Report and receipts](capacity-feasibility-20261021/REPORT.md): read-only ioctl confirms shared Btrfs subvol5 for both roots, quota status EPERM/unverified; no cheap scoped quota. Writer-side logical reservation is feasible for participating M4 code, not demonstrated for the whole native-vector arm. Six tiny model/filesystem checks PASS; final direct typecheck and active LSP clean. No runtime source, cap, ledger or native execution change. Hard dual-cap proof is not established; the small historical final footprint does not bound transient occupancy or monitor overshoot. Investigation ends here, not another repair loop. Any revised risk contract or isolated storage scope requires a separate owner decision, and native still requires fresh authority/budget.

Start with [the feasibility decision](capacity-feasibility-20261021/REPORT.md), [the correction report](workspace-observer-repair-20261020/REPORT.md) and [canonical handoff §58–§60](../../../rust/docs/m4-incremental-native-index.md). The detailed chronological entries below remain historical; older GO/next-step statements do not override current readiness or authority. The repair checksum manifest is pinned to **`6439ed2`** and must be checked at that revision when documentation has subsequently changed. Raw receipts, ledger and native binary provenance are unchanged.

This file keeps the historical S2 evidence (streaming compaction via temp
sections), the S3 slice (direct v2 writes, no temp-section copy), the S4
durability/delete-heavy/scaling validation, and the S5 debt-policy experiment.

Single run per case, each in its own process (clean `VmHWM`), same fixture and
toolchain (`g++ 16.2.1`, `-O2`). S2/S3 used `/tmp` tmpfs; S4 used `/var/tmp`
btrfs and reports `/proc/self/io` physical bytes. The older
`total_phys_write_bytes` field in S2/S3 JSON is the prototype's logical write
counter, not a disk-device counter. Host was under variable background load
(observed load ~6–12, MemAvailable ~0.5–5.7 GB); absolute latency is
directional.

Commands:

```sh
# pre: in-memory merge buffer
/tmp/m4_bench_pre_final2 --one <mode> <docs> <updates>
# post: streaming k-way merge
/tmp/m4_bench_post_final2 --one <mode> <docs> <updates>
```

Raw JSON: `benchmark-20261017-pre.jsonl`, `benchmark-20261017-post.jsonl`.
The pre binary was built from a snapshot taken immediately before the streaming
change, with the same telemetry and benchmark harness. The older
`benchmark-20261017-initial-inmemory.jsonl` is the previous gate's artifact and
is not used for this comparison. Environment: `environment.txt`.

## Incremental workload (12 updates; build is separate)

| docs | mode | build wall | update p50 | update CPU total | compaction wall (total) | writes | disk | peak RSS | peak PSS | compaction tracked peak | query p95 | runs |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 0.64 s | 0.351 ms | 6.1 ms | 0.37 s | 17.0 MB | 5.8 MB | 94.8 MB | 88.5 MB | 19.0 MB | 0.41 ms | 7 |
| 10k | streaming | 0.66 s | 0.388 ms | 12.6 ms | 0.25 s | 22.1 MB | 5.8 MB | 35.6 MB | 30.0 MB | 8.8 MB | 0.43 ms | 7 |
| 50k | in-memory | 4.40 s | 0.342 ms | 6.0 ms | 3.25 s | 111.5 MB | 15.1 MB | 421.0 MB | 412.0 MB | 94.3 MB | 1.70 ms | 7 |
| 50k | streaming | 4.97 s | 0.347 ms | 10.5 ms | 2.71 s | 163.6 MB | 15.1 MB | 95.3 MB | 89.5 MB | 8.8 MB | 2.01 ms | 7 |
| 150k | in-memory | 18.61 s | 0.227 ms | 3.7 ms | 14.17 s | 424.3 MB | 40.7 MB | 1196.5 MB | 1190.0 MB | 282.5 MB | 3.36 ms | 7 |
| 150k | streaming | 18.34 s | 0.238 ms | 5.6 ms | 11.22 s | 670.3 MB | 40.7 MB | 241.7 MB | 230.1 MB | 8.8 MB | 3.46 ms | 7 |
| 300k | in-memory | 80.07 s | 0.357 ms | 9.6 ms | 74.52 s | 1174.8 MB | 81.4 MB | 2345.5 MB | 2328.2 MB | 565.0 MB | 9.98 ms | 7 |
| 300k | streaming | 47.06 s | 0.425 ms | 16.0 ms | 36.43 s | 1992.7 MB | 81.4 MB | 464.8 MB | 459.5 MB | 8.8 MB | 11.98 ms | 7 |

Peak PSS reduction: **2.95× / 4.60× / 5.17× / 5.07×** at 10k / 50k / 150k /
300k. Compaction working-set reduction (in-memory estimate vs streaming
tracked budget): **2.2× / 10.7× / 32× / 64×**. Update p50 remains sub-ms at
every size. Query p95 is comparable at 10k–150k and +20% at 300k.

Costs:

- Logical writes rise **1.30× / 1.47× / 1.58× / 1.70×** because streamed
  dict/meta/payload/docs are written to temp files and then copied into the
  final segment. This is bounded (<2×) but is the main follow-up.
- Build wall is equal at 10k–150k and **41% lower** at 300k (the in-memory
  baseline was under memory pressure). Compaction wall is lower at 10k–300k
  (0.66× / 0.84× / 0.79× / 0.49×).
- Final storage and run count are unchanged (7 runs, same bytes).

## S3: direct v2 writes vs S2 temp-section streaming

Paired `--one incremental` runs in separate processes, same fixture and
toolchain. `/tmp` is tmpfs, so `prototype writes` is the logical counter
(segment + metadata bytes), not a disk-device counter. The 150k pair was run
again in reversed order because the first S3 run overlapped heavy host load;
the table uses the reversed-order pair. Absolute wall time remains directional.

| docs | mode | build wall | compaction wall (total) | prototype writes | disk | peak PSS | compaction tracked peak | query p95 | runs |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | S2 temp | 0.517 s | 0.216 s | 22.1 MB | 5.80 MB | 31.0 MB | 8.80 MB | 0.384 ms | 7 |
| 10k | S3 direct | 0.673 s | 0.226 s | 17.1 MB | 5.83 MB | 31.0 MB | 4.57 MB | 0.362 ms | 7 |
| 50k | S2 temp | 4.398 s | 2.529 s | 163.6 MB | 15.10 MB | 89.6 MB | 8.80 MB | 2.030 ms | 7 |
| 50k | S3 direct | 4.698 s | 2.497 s | 112.3 MB | 15.13 MB | 90.2 MB | 4.59 MB | 1.883 ms | 7 |
| 150k | S2 temp | 19.400 s | 12.298 s | 670.3 MB | 40.72 MB | 231.8 MB | 8.80 MB | 3.922 ms | 7 |
| 150k | S3 direct | 18.469 s | 12.288 s | 427.1 MB | 40.75 MB | 238.3 MB | 4.60 MB | 5.332 ms | 7 |
| 300k | S2 temp | 50.956 s | 37.402 s | 1992.7 MB | 81.40 MB | 455.1 MB | 8.80 MB | 10.212 ms | 7 |
| 300k | S3 direct | 52.772 s | 37.666 s | 1180.9 MB | 81.43 MB | 457.7 MB | 4.61 MB | 9.416 ms | 7 |

Write reduction: **23% / 31% / 36% / 41%** at 10k / 50k / 150k / 300k.
Compaction wall ratio S3/S2: **1.05× / 0.99× / 1.00× / 1.01×**. Build wall
ratio: **1.30× / 1.07× / 0.95× / 1.04×**; the 10k regression is +156 ms of
fixed overhead (dictionary/final header), not a scaling slope. Peak PSS is
effectively unchanged (direct writes reduce write I/O, not the live index
working set); the tracked compaction working set is ~4.6 MB versus 8.8 MB.
Final storage is unchanged. Query p95 is −6% / −7% / +36% / −8%; 150k is the
noisiest case (repeated runs varied by >30% in both directions), so it is not
treated as a demonstrated regression. At 300k, S3 writes 41% fewer bytes at
essentially the same wall time and peak PSS.

The physical-write check uses `/var/tmp` (btrfs) and `/proc/self/io`; tmpfs
cannot expose physical writes. Physical bytes (build + 12 updates, including
filesystem/CoW effects) were **241.0 MB vs 162.7 MB** at 50k and **911.7 MB vs
573.7 MB** at 150k (S2 vs S3), i.e. −32% / −37%, consistent with the logical
counter. Btrfs wall time is noisier because writeback dominates; the tmpfs
table is the latency comparison. Raw JSON: `benchmark-20261017-s3.jsonl`.

## Controlled rebuild-on-mutation baseline

| docs | mode | update p50 | update CPU total | compaction wall | writes | peak PSS | query p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 237.7 ms | 2025 ms | 2.19 s | 39.9 MB | 91.9 MB | 0.33 ms |
| 10k | streaming | 121.9 ms | 1009 ms | 1.22 s | 67.9 MB | 32.1 MB | 0.40 ms |
| 50k | in-memory | 1081.2 ms | 11827 ms | 12.94 s | 220.1 MB | 478.9 MB | 1.41 ms |
| 50k | streaming | 431.3 ms | 3595 ms | 5.51 s | 380.9 MB | 91.8 MB | 1.24 ms |

Streaming is **2.0–3.3× lower CPU** on the O(collection) baseline and **2.9–5.2×
lower peak PSS**, at the same ~1.7× write cost. This is the strongest evidence
that the merge path itself is the improvement, not fixture noise.

## Correctness / safety

- Independent full-scan oracle: **11,536 checks, 0 failures** (expanded in
  S4; the S3-era gate was 6,290).
- ASan + UBSan: **clean** (11,536 checks, 0 failures).
- Covered: OR/AND/phrase FTS, scalar EQ/range, randomized updates/deletes,
  flush/compaction/reopen, WAL replay after destruction without flush,
  bounded run count, corpus-growth stale-bound regression, hybrid ranking,
  and a forced compaction-memory-budget failure that leaves the old runs
  searchable.
- Streaming output is read back by the same `Segment::Open` and rechecked by
  every reopen/compaction oracle test.
- Injected faults at three boundaries: after segment rename (before publish),
  before manifest publish, and after durable manifest publish. The first two
  leave the old runs searchable; the third is recovered by reopening and
  seeing the new run.

## S4: delete-heavy churn and scaling (2026-10-17)

Workload: stable live cardinality, 8 cycles, 6,000 ops/cycle (2,000
delete+replacement-insert pairs, 4,000 updates), all docs contain a `common`
term and `status=active`. `/var/tmp` (btrfs), real filesystem, one process per
case. `fsync_wal=false` for this benchmark; WAL/durability uses the separate
correctness gate with fsync on. `query p95` is the last churn cycle before the
final drain; `post p95` is measured after `CompactNow`.

| docs | build wall / CPU | churn wall / CPU | total CPU | max dead | runs max | compactions | tracked peak | churn logical / physical | total logical / physical | final disk | peak PSS | reopen | p95 last / post | p99 last / post |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k | 61.9 s / 73.3 s | 50.8 s / 65.6 s | 138.9 s | 47,085 | 6 | 55 | 4.65 MB | 234.2 / 253.0 MB | 1,279.2 / 1,480.0 MB | 137.8 MB | 624.5 MB | 0.80 s | 70.3 / 30.6 ms | 73.8 / 30.8 ms |
| 1M | 151.9 s / 181.3 s | 87.8 s / 118.6 s | 299.9 s | 47,727 | 6 | 116 | 4.74 MB | 372.0 / 390.1 MB | 2,804.3 / 3,186.6 MB | 275.4 MB | 1,205 MB | 0.82 s | 168.4 / 37.4 ms | 209.1 / 37.9 ms |

Host headroom before each run was ~4.0 GiB available (load ~9); the 1M process
peaked at 1,205 MB PSS with ~30 GiB free on `/var/tmp`, so 1M was run after
500k passed. Both cases ended with `live_docs` exactly equal to the live set,
`dead_docs=0`, one run, no temp/orphan files, and no WAL files after the final
flush. Query p95/p99 rose 2.3× (500k) and 4.5× (1M) while tombstones accumulated to 47k,
then fell back to 30.6/37.4 ms after the final compaction. This is visible
read amplification from tombstone filtering plus run fan-out; the run cap and
auto-compaction kept both bounded. There was no correctness or liveness
mismatch. Final storage is ~275 B per live doc at both sizes; process PSS is
~1.3 KB/live doc at 500k and ~1.2 KB/live doc at 1M.

Correctness/recovery in the same tree:

- Oracle suite: **11,536 checks, 0 failures**; ASan/UBSan clean.
- Added delete-heavy churn oracle: updates, deletes, replacement inserts,
  reinsert of deleted keys, exact `common` count, live-token equality,
  FTS/INVERT/range/phrase/hybrid checks after repeated compactions.
- Added WAL replay after churn (mutations without flush), pre-manifest fault
  after churn, `dead_docs=0` and one-run GC checks, and no-temp-file check.
- Existing fault tests cover after-rename, before-manifest and after-manifest
  boundaries; manifest fallback and tombstone GC remain green.

Limitations of this evidence:

- `fsync_wal=false` in the large churn benchmark; durability is validated by
  the smaller fsync-on correctness test, not by the 1M timing run.
- Physical writes include btrfs CoW/metadata and base compaction; churn-only
  and total columns are both reported. Final storage is 137.8 MB / 275.4 MB;
  total physical writes are 1.48 GB / 3.19 GB because the base build performs
  repeated tiered merges before the final live-set drain.
- Peak PSS includes the benchmark's live-key vector and full-count liveness
  query, not just the index. The tracked compaction budget is separate
  (4.65/4.74 MB).
- Query latency under debt is a policy/performance issue, not a correctness
  failure. A production SLA would need a compaction-debt trigger tuned for
  latency, not only run count/tombstone ratio.

## S5: compaction debt policy and query latency (2026-10-17)

`QueryDiagnostics` now records unique runs read, term×run parts, postings
scanned, dead postings filtered, liveness lookups, live candidates and
setup/execution/finalize timing. The policy is research-only and **default
off**; it runs only after `Flush`, never per mutation, and keeps format v2,
BM25, shared liveness, WAL/manifest and search semantics unchanged.

Policy: one adjacent-pair merge per flush with the highest
`dead_postings / rewritten_docs`, minimum 3 runs, 1 s cooldown, and input cap
`live_docs/4`. The base corpus is built with the policy **off**; ON runs
reopen after the base compaction so bulk-load behavior is not compared.

| case | mean p95 | median p95 | max p95 | post p95 | mean runs | mean scans | mean dead | churn merges | logical writes | physical writes | CPU | peak PSS | update p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k OFF | 51.8 ms | 53.8 ms | 69.7 ms | 20.8 ms | 4.38 | 146k | 5,170 | 3 | 234.2 MB | 252.4 MB | 96.2 s | 617.6 MB | 0.077 ms |
| 500k ON | 31.3 ms | 31.4 ms | 38.9 ms | 24.7 ms | 2.00 | 124k | 3,957 | 7 | 281.0 MB | 300.5 MB | 73.9 s | 600.4 MB | 0.062 ms |
| 1M OFF | 186.0 ms | 187.4 ms | 236.6 ms | 60.7 ms | 4.38 | 276k | 5,044 | 3 | 372.0 MB | 391.3 MB | 411.1 s | 1,240.2 MB | 0.206 ms |
| 1M ON | 73.8 ms | 72.4 ms | 89.6 ms | 54.3 ms | 2.00 | 238k | 3,852 | 7 | 419.5 MB | 439.0 MB | 226.5 s | 1,127.6 MB | 0.082 ms |

Mean query p95 falls **40% at 500k** and **60% at 1M**; max p95 falls 44%/62%.
Runs stay at 2, scans fall 15%/14%, and dead postings fall 23%/24%. Setup and
finalize are negligible; at 1M OFF execution averaged 95.4 ms, while ON query
execution fell to 54.1 ms. Runs read and dead-posting fraction both correlate
with p95 (`r≈0.86` / `r≈0.84`), so tombstone ratio alone is not the signal.

Trade-off: churn logical writes rise **20% at 500k / 12.7% at 1M**, physical
writes **19% / 12.2%**; update p95 does not regress in these runs. Post-drain
p95 was 20.8→24.7 ms at 500k and 60.7→54.3 ms at 1M, so it is not uniformly
better; the churn-window distribution is the decision metric. The policy keeps
the tracked compaction peak at 4.74 MB. Churn maintenance merges are
3→7 (one bounded merge per affected cycle), not a full compaction per latency
spike; total CPU falls because queries scan fewer runs/dead postings. Final
storage is unchanged (137.8/275.4 MB).

**S5 verdict: GO for research policy.** The structural policy materially
reduces the latency tail at a bounded 13–20% write cost and lower CPU, without
changing format or search semantics. Production remains **NO-GO**: the
prototype is single-threaded/one FTS field, and policy tuning beyond this
single A/B is not approved.

## Verdict

**GO for the S5 research policy** (and S4 remains GO for research): mean p95
falls 40%/60% at 500k/1M, max p95 44%/62%, with 13–20% more churn writes and
lower CPU. **NO-GO for production integration or migration**: the format is
research-only and the prototype is single-threaded with one FTS field; no
further policy tuning is approved without a new experiment.

Follow-up, in order:

1. Stop policy tuning; the S5 policy is the accepted research point.
2. Multi-field/tokenizer parity and background compaction, with the same
   oracle/WAL/fault gates.
3. Production migration remains unapproved.

## M4-P4: isolated Native Zvec vs M4 FTS/INVERT A/B (2026-10-17)

Three paired 1,200-file runs on `/var/tmp` btrfs completed in isolated fresh
index directories. On the retained workload, snapshots matched and both arms
had zero independent-oracle retrieval mismatches and zero cross-engine
retrieval-set mismatches. Median M4 whole-process CPU was 4.72 s vs 6.63 s
(native), peak RSS/PSS was 277/270 MiB vs 564/556 MiB, and final index space
was 7.24 vs 36.3–37.4 MB. Trade-offs: M4 wall time +25%, update/delete p95
slower, and physical writes 527.5 vs 101.2 MB (5.22×); timed search CPU was
0.155 vs 1.401 s. Six top-10 order cases differed (mean Jaccard 0.9943).

**P4 verdict: PARTIAL, research-only.** A small smoke exposed native-only
extra hits for `"json parse"` against the independent analyzer oracle/M4.
That exact query was removed from both full-run arms and replaced by
`"json request"`; the finding is retained and not counted as passing
compatibility. Further investigation or behavior changes require separate
approval. Do not infer whole-`zg` performance and do not integrate/migrate to
production. Full metrics, provenance, host state and raw artifacts are in
`m4-p4-native-vs-m4-20261017.md`, its sibling provenance/checksums, and
`results/artifacts/m4-p4-*.tar.gz`.

## M4-P4.1: write-amplification attribution (2026-10-18)

One 60-file traced diagnostic matched 1,088 live docs / 967,119 tokens. M4
had 1,588 WAL fsyncs (one per mutation) vs 4 native WAL fsyncs (four explicit
flushes), while WAL syscall bytes were nearly equal (4.49 vs 4.44 MB). Process
physical writes were 132.41 vs 35.79 MB (3.70×), but successful writes to
index files totaled less for M4 (8.74 vs 16.21 MB). This strongly points to
sync granularity/filesystem writeback as the primary cause; the exact physical
byte share is unproven. The 5.22× P4 full-run result was not rerun here.

Durability boundaries differed (`fsync_wal=true` for M4; native WAL sync at
flush), and final maintenance states were not equivalent/proven: M4 ended with
2 runs / 0 dead docs; native pending compaction debt was unavailable. No engine,
format, or durability behavior changed. Diagnostic markers are now opt-in and
default-off; a 60-file default-off run confirmed no extra output. Detailed
evidence, limits and the format-preserving batch recommendation:
`m4-p4.1-write-amplification-20261018.md`;
raw traces and host snapshots: `artifacts/m4-p4.1-writeamp-60-20261018.tar.gz`.
P4 remains PARTIAL, research-only; production integration/migration remain
unapproved.

## M4 Marathon — P4.2 durable atomic batch and Branch A (2026-10-18)

P4.2 adds `ApplyBatch` only under `research/m4-native/`: max 256 mutations / 8 MiB, one checksummed WAL transaction, fsync before visibility when enabled, and locked all-or-nothing reader visibility. Existing per-document APIs are unchanged. Current strict correctness and ASan+UBSan/leak-detection gates each pass `checks=18157 failures=0`; focused CMake correctness + Jieba identity tests pass 2/2. Batch tests cover pre-WAL failure, crash after sync and partial in-memory apply, torn-tail repair and append, same-PK update/delete order, non-reused DocIds, concurrent reader atomicity, concurrent same-PK writers, active-reader compaction/tombstone GC, and one WAL sync per durable batch.

Two successful paired 60-file synthetic FTS/INVERT runs show parity with the independent oracle and between arms. In the telemetry-enabled pair, cumulative M4 WAL fsyncs were 1,588 (per-document) vs 10 (batch, including initial setup and explicit flushes); physical writes were 125.90 MB vs 12.06 MB; CPU 1.514 vs 1.097 s; mutation bursts 398/482/501 ms vs 75/75/79 ms. Three further attempts stopped at the configured I/O PSI guard before candidate execution; partial runs were preserved and excluded, with no further benchmark started after the stops. These results are single-run per pair and synthetic; no production or whole-`zg` claim. Detailed report/raw profiles: `m4-p4.2-durable-batch-20261018.md`, `/var/tmp/m4-p42-ab-60-retry-20261018/` and `/var/tmp/m4-p42-ab-60-repeat-20261018/`.

Branch A native adapter is NO-GO at the current seam: native batch APIs expose per-document results and `CollectionImpl::write_impl` loops per doc; native segment/IDMap/delete-store/version/recovery and FTS/INVERT are coupled, so an independent M4 store cannot share a commit/visibility point with vector writes. No native checkout file was edited. Trace: `m4-marathon-branch-a-20261018.md`. Branch B's independent M4 candidate passed the stated adversarial gates; see `m4-marathon-correctness-20261018.md`. Production integration/migration remain NO-GO.

## Phase A — P4 native vs M4 ApplyBatch (2026-10-18)

On the 1,200-file P4 fixture, two untraced alternating-order pairs show M4 ApplyBatch lower in all primary resource metrics: mean process CPU 3.17 vs 6.82 s (−53.5%), physical writes 52.04 vs 100.68 MB (−48.3%), peak PSS 276 vs 532 MiB (−48.2%), and mean query p95 ~1.3–1.5 vs 21–26 ms. The separate traced pair counted 28 successful M4 WAL-path fsyncs vs 4 native (116 vs 57 total `fsync` calls); these durability boundaries differ (M4 fsyncs every bounded batch, native syncs at explicit flushes). Both pairs matched live doc/token/file snapshots; M4 had zero logical-oracle retrieval mismatches. The retained `json parse` query produced native-only extra hits (32/32/32/44 by stage, no M4-only hits) in both pairs; full query/ranking detail and precise caveats are in `m4-phase-a-native-vs-m4-batch-20261018.md`. Four `common_timeout` top-10 order divergences remain, consistent with known live-vs-historical ranking-stat semantics. This is a synthetic research fixture, not a whole-`zg` or production result.

Phase B classification: no new B1/B2 issue was shown by P4, but Phase D's bounded 50k/4-cycle churn trace did show a B3 p95 tail rise (1.49→2.20 ms) as run/dead-posting debt grew. Re-testing the accepted S5 policy (base built OFF, opt-in after reopen; ratio 0.01/cap 250k) reduced mean cycle p95 1.92→1.57 ms and final-cycle p95 2.20→1.88 ms, with +9.7% total physical writes and near-flat CPU. Keep the policy opt-in/default-OFF; do not tune its threshold. Strict oracle and ASan+UBSan both pass 18,157/0; Jieba identity test passes 173/0. Details and host artifacts: `m4-phase-b-d-churn-debt-20261018.md` and `m4-phase-d-churn-50k-4x2000-20261018/`.

## Phase E — final P4 A/B and mission decision (2026-10-18)

Compared native with the best research candidate: M4 ApplyBatch plus S5 policy enabled only after the initial base flush/reopen. Two untraced pairs show CPU 3.24 vs 6.74 s (−51.9%), physical writes 53.47 vs 99.35 MB (−46.2%), PSS 276 vs 532 MiB (−48.1%), peak CPU 108 vs 175% of one core (−38.5%), and query p95 ~1.18–1.53 vs 19.81–25.57 ms. Exact after-base-policy trace confirms 28 successful M4 WAL fsyncs vs 4 native; native-only `json parse` extras and four live-vs-historical top-10 order cases remain visible. Full decision/trade-offs: `m4-phase-e-final-native-ab-20261018.md`.

**Mission result: SUCCESS for the independent research candidate only.** ApplyBatch + optional S5 policy passes correctness/durability and improves the stated synthetic churn fixture with quantified write/durability trade-offs. Native compatibility is still PARTIAL, and no whole-`zg`/production claim is made. Production integration, migration, schema/ABI changes, and deployment remain NO-GO/unauthorized. Raw Phase A/E configs, query-level comparisons, stdout, profiles, host snapshots, and syscall logs are archived at `artifacts/m4-native-m4batch-phase-a-e-20261018.tar.gz` with a sibling SHA256 manifest.

## M4 Bridge — cross-component snapshot correctness (2026-10-18)

The research bridge race was an inconsistent mutable native segment view: the vector index emitted row IDs 49–71 while `DocFilter`'s forward bitmap contained 49 rows. A coordinator shared/exclusive snapshot lease now keeps the native vector search and M4 FTS generation coherent, including a single hybrid read lease across both components. Deterministic publish, new/old-generation, compaction/GC, crash/reopen/WAL and hybrid assertions pass `checks=1240 failures=0`, including three concurrent hybrid readers blocked across staged publish; five additional fresh-process repetitions pass 1,139 checks each. Current-source ASan+UBSan+LeakSanitizer also passes 1,240/0. The one-run 500-row profile measures writer 45.11 ms and a deliberately blocked query 45.80 ms; this is an explicit full-commit reader-stall cost, not a scale/performance claim. Old generation rows are retained; generation-level reclamation remains unimplemented. Production stays NO-GO. Report and raw host/time/JSON evidence: `m4-bridge-snapshot-consistency-20261018.md`, `/var/tmp/m4-bridge-lock-perf-20261018-074647/`.

## M4 Bridge — B2 delta-vector lifecycle (2026-10-18)

The standalone B2 prototype has immutable vector base+delta shards, global top-k merge, scalar-filter refill, four-shard compaction, durable catalog/manifest/WAL recovery, crash boundaries, a pinned hybrid reader, and harness-local pin-aware retirement/backpressure checks. Current source `research/m4-native/tests/m4_bridge_integration.cc` SHA-256 `d9b37b64c96efba86d7879ab763b6914d9c5b0fd71976390f4a28b0c2e77992f`; normal binary `d9161e9684d07abeb33d4539aafdedfd7bcf2a26eed1910279355985f5bce091`.

Current-source candidate smoke passed `checks=1380 failures=0`; B1 lifecycle regression passed `checks=3549 failures=0 generation=22 live_rows=23 max_retained_generations=3`; current-source ASan/UBSan/LSan passed 1380/0 with no diagnostics. Paired B1/B2 500×20 and 1,000×5 were each run twice with reversed order on repeat. Two-run mean commit p95: 791.1→577.9 ms (-26.9%) at 500×20, and 821.1→576.5 ms (-29.8%) at 1,000×5. Mean process CPU was lower 31.9%/40.2%, physical writes 6.5%/5.4% lower; B2 reader p95 was 53.2%/32.1% higher. At 1,000×5 B2 retained 1.86× bytes with 11 stale rows after one compaction. These small synthetic single-host repeats are descriptive only, not significance or architecture/production GO. Full paired data and receipts: `m4-b2-cost-model-20261018/paired-benchmark/summary.md`.

B2 remains research-only: retirement/GC/backpressure helpers are not integrated into one publisher; full M4 FTS/INVERT rebuilds per mutation, and compaction rebuilds all live vectors. PSS, peak CPU%, reader blocked-wait, and per-compaction physical writes were not measured; B1 process-wide per-stage CPU was contaminated by reader work and is excluded. Production integration/migration remain unapproved. Continue with the previously scoped usage-first M4-P2 scalar/filter compatibility audit, not BM25 tuning.

## B2/B3 M4 `Index::Open` attribution and analyzer-cache candidate (2026-10-18)

Instrumented `Index::Open` CPU/wall stages show that 46–52 ms CPU per open is native Jieba analyzer initialization, with another ~45 ms in initial and verification fingerprint reads. Segment loading is <0.5 ms median and therefore does not warrant a segment-handle cache. The opt-in weak-reference analyzer cache keeps both fingerprint checks, shares only immutable analyzer state while indexes are alive, and leaves run ownership, WAL, manifest, and recovery unchanged. Two 500×20 B3 pairs (second reversed order) show mean writer CPU −47.6%, total CPU −40.0%, commit p95 −32.8%, and M4 Open CPU p50 −51.2%; both cache arms have zero coherence errors and 20/20 cache hits. Reader p95 varied between pairs and physical-write counters are not attributable. M4 correctness 18,157/0 and ASan/UBSan/LSan candidate 578/0 pass. This is a standalone opt-in research candidate, not a production or architecture GO. Full attribution, commands, hashes, host state, and raw outputs: `m4-m4-open-attribution-cache-20261018.md` and `m4-b2-cost-model-20261018/paired-benchmark/open-attribution/`.

## B3.1 reader-compaction crossover — 2026-10-18

One owner-requested 1,024-doc AB/BA crossover compared the existing natural
four-shard vector compaction with a no-writer control. Same fixed 8 query IDs ×
4 repeats, paired per `(pair, query_id, repetition)`, with native-vector and
M4 wall/CPU, per-thread scheduler delay, and system PSI. All active queries
finished inside the compaction windows. Native API paired median delta was
+0.330 ms in pair 1, +0.026 ms in reversed-order pair 2, combined +0.135 ms
(p10/p90 −0.383/+0.564 ms); the effect did not reproduce consistently. Close
the compaction-causality hypothesis as unsupported for this fixture; do not
change search or compaction policy. Next focus: independent search-execution
cost on a fixed snapshot without a writer. Exit 0, 48,891 checks, zero failures.
Full paired analysis, receipts and checksums:
`m4-b2-cost-model-20261018/paired-benchmark/reader-compaction-crossover-20261018/`.

## Search instrumentation + PK lookup microbenchmark — 2026-10-18

Current-source warning-clean M4 correctness and ASan+UBSan+LSan each pass
18,456 checks, zero failures after fixing WAND upper-bound accounting for delta
cursors and accounting for valid AND short-circuit paths. A reversed-order
baseline/candidate scalar-INVERT PK lookup microbenchmark (1,024 hits/query,
3,000 measured queries/run, 50 warmups) gives candidate median 101.761 ms vs
baseline 240.001 ms (−57.6%); this is a narrow isolated result, not evidence of
end-to-end latency improvement. Host had active GUI/browser processes and
nonzero I/O PSI, so broader bridge/HNSW profiles were not launched. Marathon
DoD remains incomplete. Full commands, resource receipts, raw output, hashes
and caveats: `m4-b2-cost-model-20261018/paired-benchmark/pk-lookup-microbench-20261018/analysis.md`.

Current-source bridge recovery/lifecycle ASan/UBSan validation passed
`checks=3581 failures=0 generation=22 live_rows=23 max_retained_generations=3`.
The fixed-snapshot diagnostics A/A sequence is complete: all eight arms exited
0, and query signatures/counter invariants matched. The sequential native-HNSW
+ M4 counter rerun also exited 0 after the exact empirical four-metric resource
envelope passed. It covered 32 queries across vector-filter match counts 1,
341, 512 and 1,024 and top-k 8/64; all query signatures exactly match the
preceding sequential cohort. Median query wall was 0.898 ms (p95 1.082 ms),
vector 0.690 ms, native API 0.580 ms, M4 0.210 ms. Native trace recorded 30
filtered-HNSW decisions on the 1,024-doc base and 40 native route-counter
records. This is a one-reader synthetic attribution profile, not a paired
performance win or a representative production workload. It confirms similar
M4 operation counts across the native vector-filter selectivity strata, while
HNSW traversal remains active; no optimization is selected from this run alone.

Full rerun analysis, raw traces, source/binary hashes and validated checksums:
`m4-b2-cost-model-20261018/paired-benchmark/native-hnsw-attribution-20261018/hnsw-route-coverage-rerun2-20261018/analysis.md`.
A/A analysis and receipts:
`m4-b2-cost-model-20261018/paired-benchmark/reader-search-aa-current-20261018/analysis.md`.

## Repeated M4 filter-candidate cache — 2026-10-18

Added regression coverage for the existing bounded `Options::cache_filter_candidates`:
uncached/cached hit-set and score parity, exact within-index hit ordering and
DocId stability across cache miss/hit, mutation invalidation and repopulation.
The final warning-clean oracle passes `checks=18481 failures=0`; the final
ASan/UBSan/LSan run also passes `checks=18481 failures=0`.

One four-arm, 4-reader ABBA profile (control/cache/cache/control) used the same
1,024-doc fixed snapshot, 32 queries, four vector-filter selectivities and
top-k 8/64. All four harness runs exited 0 (`checks=8616 failures=0` each),
all 32 query hit signatures matched, and filter-cache telemetry reported 0/32
hits in both controls and 32/32 in both cache arms. Per-query M4 wall p50
paired deltas were −0.264 ms and −0.117 ms; reader-thread M4 CPU p50 deltas
were −0.126 ms and −0.139 ms. End-to-end query p50 deltas were −0.472 ms and
−0.503 ms. M4 reader-CPU deltas were negative for every query in both pairs;
end-to-end and M4-wall ranges include both signs. There are only two ABBA pairs,
and counter-only diagnostics/native tracing add
instrumentation. The usual postings/liveness counters are unchanged as
expected: they count membership checks after candidate construction, not the
skipped `FilterCandidates` expression scan. The direct scan count/time is not
exposed by this counters-only mode. Treat this as a promising local signal, not
a general latency claim. Keep the existing option research-only/default-off;
no production default or integration change is justified.

Full preparation, build provenance, raw arm logs/traces, host receipts, source
hashes and analysis:
`m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-optimization-20261018/analysis.md`.

A separate diagnostics-off primary control profile completed once on the same
fixed 256-D hybrid fixture: 32 query signatures match the counter-only control,
checks pass, and route tracing records 120 HNSW dispatch and 10 brute-force-by-
PK events. Median query/M4 wall is 1.478/0.314 ms. This is a one-run,
native-instrumented profile, not paired latency evidence. Analysis and receipts:
`m4-b2-cost-model-20261018/paired-benchmark/reader-search-primary-control-20261018/analysis.md`.
The diagnostics-off primary cache ABBA completed after two no-launch preflight
stops at I/O PSI full avg10 5.11% and 5.50%; the threshold was never relaxed.
All four subsequent arms passed pre/post resource gates and exited 0. Hit
signatures and native dispatch counts match in every arm (120 HNSW, 10
brute-force-by-PK). Paired query-wall p50 deltas are −0.404/−0.287 ms; M4-wall
−0.272/−0.189 ms; M4 reader-CPU −0.145/−0.124 ms. M4 CPU deltas were negative
for all 32 query keys in both orders; query-wall ranges overlap zero and this
remains a two-pair synthetic test with native route tracing enabled, not a
production latency claim. Detailed results, logs, receipts and hashes:
`m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-primary-abba-20261018/analysis.md`.
No production/default change follows. Broader M4 marathon DoD remains
incomplete; production remains untouched.

## Search execution cost-model point checks — 2026-10-19

Current-source Flat/HNSW fixture point checks at 1,024 and 10,000 docs (256-D,
eight queries, one run/mode/size) suggest a potential small-shard crossover:
HNSW was slower at 1,024 (+0.022 ms paired median; 6/8 slower) and faster at
10,000 (−0.111 ms; 3/8 slower), while median HNSW distances fell from 1,051
to 5,887 against Flat's 1,024 and 10,000. This is exploratory, not a stable
latency win or threshold. At 10k, separate HNSW builds produced 78/80 vs
80/80 exact-hit overlap; discrepancy unresolved. Host CPU activity and query
latency spread limit inference. No production changes.

Raw receipts, route counters, summaries and analysis:
`m4-search-execution-marathon-20261018/phase-b-cost-model/analysis.md`.
Next: investigate the 10k recall discrepancy, then run one paired diagnostics-
off whole-hybrid route comparison on the frozen 1,024-doc fixture. Keep
counters-on operation accounting separate from primary latency; marathon DoD
remains incomplete.

## M4 filter-candidate cache capacity cost check — 2026-10-19

Phase C attribution found scalar filter candidate construction dominated M4 FTS
on this synthetic whole-hybrid workload. The existing opt-in cache already
helps repeated filters when eligible, but its 64-KiB cap rejected the 80,000-
byte, 10k-DocId candidate vector. In isolated research code, raised the cap to
128 KiB; cache remains default-off. At 10k, cache-on logged 32/32 hits per
backend; median filter time fell 1.485→0.00037 ms (Flat) and
1.425→0.00030 ms (HNSW), FTS stage 1.537→0.045 ms and 1.476→0.046 ms.
Candidate vector capacity was 80,000 bytes; max RSS 379,200→380,284 KiB.
At 1,024 docs, cache hits were 32/32 and filter time ~0.099→0.0003 ms, with
identical Flat/HNSW hit lists across arms. One run per arm, diagnostic timing,
synthetic fixture: promising cost evidence, not a stable latency claim. At
10k, independently built HNSW arms still differed on one top-k query; this is
not attributed to the M4 cache and no ANN quality conclusion follows.

Strict oracle and ASan/UBSan/LSan passed `checks=18481 failures=0` on the
temporary cap-128 source and again after restoring the canonical 64-KiB bound,
before the separate Boolean-constant simplification below.
The prior canonical handoff explicitly closed this cache optimization branch
and prohibited another implementation attempt, so the cap change was rolled
back to 64 KiB. Cap-128 measurements remain exploratory, not an accepted
candidate; cache stays default-off. Full raw evidence, checksums, host receipts
and summaries:
`m4-search-execution-marathon-20261018/phase-b-cost-model/filter-candidate-cache-20261019/`.
Marathon DoD remains incomplete. Source review later confirmed HNSW graph-level
randomness across independent builds; see the next section.

## M4 Boolean filter constant simplification — 2026-10-19

The hybrid bridge constructs `AND(generation = snapshot, ALL)`. `EvalFilter`
previously built a `LiveUniverse()` vector for that identity and intersected it
with the generation candidates. Added oracle cases for `AND(ALL,x)`,
`OR(NONE,x)`, absorbing constants and negated compositions, then skipped direct
constant identities under De Morgan negation. This is a distinct correction;
the previously closed candidate-cache-cap branch remains rolled back at 64 KiB.

Strict oracle and ASan/UBSan/LSan on the current candidate both pass
`checks=18643 failures=0`; warning-clean CMake correctness/bridge targets.
Two 10k, 32-query diagnostics-off pairs in opposite order show paired median
whole-hybrid ratios candidate/baseline 0.745/0.769 (Flat) and 0.666/0.791
(HNSW), with 64/64 Flat top-k matches. Candidate was faster on 30/32 and 23/32
Flat queries, 32/32 and 22/32 HNSW queries. Separate attribution showed M4
filter time 1.432→0.558 ms (Flat) and 1.477→0.550 ms (HNSW); run/posting
counts are unchanged. Candidate DocId-vector capacity increases 80,000→131,072
bytes and observed max RSS by roughly 0.3–0.5 MiB. One synthetic fixture;
reverse pair had higher host load, so report within-pair deltas/ratios only.
Research-only KEEP for the standalone evaluator; no production/default change.

The native profile HNSW source seeds random-level assignment from
`system_clock` (`hnsw_algorithm.h:52`); the fixture fixes M=16/ef=100 but no
seed. Exact HNSW top-k equality across independent graph builds is therefore
not deterministic; preserve per-build Flat overlap and do not change ANN
parameters. Detailed raw arm logs, host snapshots, hashes, summaries and
analysis: `m4-search-execution-marathon-20261018/phase-b-cost-model/filter-constant-identity-20261019/`.

A resource-gated paired baseline/candidate reader-compaction crossover
completed on 1,024 docs (four readers, 64 queries per condition per binary).
Each compaction arm overlapped concurrent upsert/delete and vector/M4
compaction; both binaries passed `checks=48909 failures=0`. This supplies
eight fixed query IDs and one maintenance generation. M4 median wall was
0.522/0.437 ms baseline and 0.482/0.473 ms candidate for control/compaction;
M4 CPU was effectively flat. The paired process end-to-end/native medians were
not attributable because HNSW graphs are independently time-seeded.

A resource-gated sustained 500-doc × 20-round pair is now complete (candidate
first, baseline second): 4 readers, 80 queries/arm, five compactions, 21
published generations, final 500 live docs. Every reader pin was unambiguous;
all 80 queries per arm had generation lag 0 at completion, and both arms had
zero coherence errors. Candidate/baseline measured physical writes were
251,011,072/247,373,824 B (+1.47%); retained root bytes matched. Reader
p95/p99 was 3.406/4.598 vs 3.289/3.983 ms; writer-commit p95/p99 was
431/594 vs 247/270 ms. This single pair shows no sustained win and raises a
writer-tail concern, but is insufficient to attribute the large delta. The
sustained harness uses one fixed vector/FTS query, so the prior 1,024-doc
crossover supplies query-ID diversity. Static Flat/HNSW quality evidence is
referenced in the cost-model analysis; this workload checks live-generation
coherence, not ANN recall. Full raw records, PSS samples, receipts, hashes and
analysis: `m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-20261019/`.

The reverse-order sustained 500×20 pair (baseline then candidate) completed
2026-10-19 under the validated resource envelope, using the identical binaries,
mode/query mix and fresh roots. Candidate writer p95/p99 were 477.1/498.4 ms vs
baseline 240.0/242.0 ms (+98.8%/+106.0%); this repeats the higher candidate
writer tails in the candidate-first pair (+74.7%/+119.6%), so the direction
survives order reversal but is not causally attributed by only two pairs.
Reader p95/p99 were 4.689/8.540 vs 4.582/7.726 ms (+2.3%/+10.5%). Process CPU
was +4.0%, while physical writes were −8.85% and sampled peak PSS −2.4%;
results are mixed and do not establish a general win or regression. Candidate
had unusually slow first four vector-build cycles (204.8–256.7 ms), alongside
higher vector-build p95 and writer tails; no claim that the filter-only change
caused this is justified. Same five compactions, generation 21, 500 live docs,
zero stale vector rows/coherence errors. M4 Open p50 was 55.3 vs 54.6 ms and
reopen 294 vs 381 ms (one observation each). No query interval overlapped a
publish (0/80), so generation-lag-zero completion does not verify reader
pinning across publish. Raw per-cycle latencies and full analysis:
`m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-reverse-20261019/README.md`.
The two-pair sustained claim remains inconclusive on causality; candidate stays
research-only, and production integration/migration remain NO-GO. Do not
optimize candidate based only on these runs. Current-source oracle/sanitizer
evidence is separate and was not rerun for this benchmark-only pair.

The 2026-10-19 writer-tail reconstruction joined all 80 existing per-cycle
records and reviewed the bridge/native durability call graph; it does not
causally attribute the first-cycle candidate tails. A test-only trace build now
records per-phase native-vector/M4/generation preparation timings and buffered
`fsync`/`fdatasync`/`syncfs`/`msync` events with paths and TIDs. Warning-clean
builds and a 64×1 tmpfs functional smoke pass (360 sync events, no drops or
failures, coherent 4-reader reopen); this is not performance or persistent
storage evidence. No source behavior was corrected. A 500×20 diagnostics-off/on
A/A remains blocked by the latest resource preflight: MemAvailable 4,581,316
KiB, 896,446 KiB below the 5,477,762-KiB minimum; load, I/O PSI and `/var/tmp`
free space pass. This was superseded by later passing per-arm receipts. The v2
OFF/ON A/A completed but had excessive overhead (commit p95 212.2→293.7 ms);
its 3,399 sync records had zero drops/failures, useful for coverage only.
A bounded research-harness correction skips outer per-file fsync of M4 FTS
after durable `Index::Flush`, retaining directory sync and all other syncs.
Three resource-gated same-source 500×20 pairs completed, all with five
compactions, generation 21, 500 live docs, zero stale rows/errors and equal
retained bytes. Treatment/control commit p95/p99 were 213.7/214.1 vs
361.0/383.1 ms (treatment first), 369.2/390.3 vs 257.4/258.7 ms (treatment
second), and 211.5/228.6 vs 464.4/497.1 ms (treatment first). Outcomes are
order-sensitive and inconclusive; in all three pairs the second arm had higher
writer p95/p99 regardless of variant (+68.9%/+78.9%, +43.5%/+50.9%, and
+119.6%/+117.4%), and start I/O PSI also rose first-to-second each time
(0.32→1.23%, 2.33→3.02%, 0.43→1.34%). Carry-over/host pressure is a plausible
confound, not a proven cause; no stable causal win/regression is claimed.
Current-source M4/vector/lifecycle oracles report 18,643/1,401/3,581 checks,
zero failures; ASan/UBSan/LSan reports `ASAN_PASS`. Harness-only change, no
production or M4-library change. The 50-ms `/proc` sampler records process
I/O, PSI avg10+totals, memory/swap, swap-in/out, diskstats and monotonic time;
per-sample file flushes were removed. Eight-row sampler smoke passed. The
commit/PSI join analyzer passed a two-cycle synthetic monotonic-alignment test;
pre/during/post windows stop at adjacent cycle boundaries to avoid overlap. The new
same-binary A/A orchestrator uses two fresh roots, a 60-s baseline stability
check and three consecutive 10-s recovery windows with pressure/load/I/O/swap
activity no worse than baseline maxima, MemAvailable/SwapFree no lower than
baseline minima, plus the existing resource gate; no fixed-sleep assumption. Both old 60-s host-only captures were rejected as references: they
flushed each row and drifted; follow-up PSI full avg10 was 0.92–5.31%, swap-in
8,236 pages, swap-out 24, and MemAvailable minimum 4,931,476 KiB. The sampler is now buffered. The rejected-baseline evidence and updated resource status are recorded below.

No A/A workload launched. Three 60-s ambient captures (50-ms sampling, 1,200 rows each) failed the unchanged stability gate. The latest post-attempt snapshot (2026-10-19 16:39:41 +07:00) passes all four resource limits: MemAvailable 9,221,468 KiB, load-1 2.57, I/O PSI full avg10 3.49%, and `/var/tmp` free 19,255,046,144 B. The remaining blocker is host stability during the 60-s baseline, not disk capacity. Full per-window evidence and resume condition: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-wait-20261019/README.md`. If A/A reproduces second-arm slowness
despite verified recovery, that shows only order/host drift; further evidence
must separate filesystem writeback/page cache, thermal throttling, swap and
background-process activity. If not, the next comparison must be
baseline/candidate under matching host conditions. A/A binary SHA-256
`70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`.
Receipt:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-final-resource-no-launch-20261019Tcurrent.json`.
Runner: `.../run_host_carryover_aa.py`; cycle analyzer:
`.../analyze_host_pressure.py`. Sync-elision remains diagnostic-only and not
durability-equivalent: source trace shows `FlushInternal` truncates WAL after
manifest publication without fsync; the skipped outer file fsync may be what
persists the truncation. No power-loss recovery proof exists. Raw artifacts
and interpretation:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`;
canonical handoff `rust/docs/m4-incremental-native-index.md` §35.

## M4 writer-tail stability and A/A readiness investigation — 2026-10-19

Offline replay of the three pre-existing buffered 60-s baselines confirmed the unchanged all-feature stability rule rejected every trace: the first reported feature is `load1_max`, but the raw profiles have 23/21/15 feature-window misses. The captures are complete and cadence-valid (1,200 rows, six ~9.95-s windows each); the failures are not missing samples or short windows. I/O PSI full avg10 crossed its 4.46% snapshot ceiling in 1,128/1,200, 1,058/1,200 and 1,158/1,200 samples. No baseline supports the claim that only tiny benign load noise caused rejection.

After completing the offline audit, synthetic replays and process/Btrfs attribution, one authorized post-audit attempt used the same binary. Both the pre-launch snapshot and runner preflight passed the unchanged four limits; the valid 60-s baseline failed the same strict envelope (`load1_max` drift down), so the runner launched no arm. During this capture, MemAvailable was below the existing 5,477,762-KiB limit in 64/1,200 samples (minimum 5,304,712; shortfall 173,050 KiB). A sanitized post-capture snapshot then showed 5,472,864 KiB, 4,898 KiB below the limit; load, I/O PSI and `/var/tmp` passed. No retry was started. The current A/A is **not run**, and there is no writer-tail result, variant comparison or causal conclusion.

Diagnostics now distinguish `SAFETY_GATE_FAIL`, `STABILITY_GATE_FAIL` and `MEASUREMENT_INVALID`; validate timestamp monotonicity, cadence gaps, sample brackets and full 10-s windows; and retain all failed feature/window values without changing thresholds. Current replay covers the three historical traces, the separate post-audit attempt and 11 synthetic cases. The arm-time process/PSI sampler now buffers records until the child exits and records monotonic sample brackets; a smoke confirms no telemetry output file is opened while the measured process is live. Analyzer synthetic join passes non-overlap/monotonic checks. Host pressure attribution remains inconclusive for the original failures: later read-only observations saw I/O stalls, swap-in and block writes not explained by sampled process `write_bytes`, but no evidence identifies a causal process or Btrfs compaction. No process was interrupted.

Policy outcome: retain all established limits and the first-three/last-three envelope. A continuous application of absolute resource limits is a bounded option for owner approval, strengthened by the post-audit memory dip; no policy was changed and no rejected baseline was reclassified as PASS. Detailed audit, policy options, raw host attribution, exact execution receipt, tests and checksums:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`; canonical handoff `rust/docs/m4-incremental-native-index.md` §36.

A fresh recheck at 18:23 +07 passed the read-only and runner preflight limits,
but its valid 60-s ambient baseline again failed the unchanged stability gate
(`load1_max` drifted below the first-three-window envelope: 0.84 vs 0.88).
I/O PSI full avg10 also peaked at 8.02% during the baseline, above the 4.46%
preflight ceiling. The runner launched neither arm. This adds no A/A or latency
evidence and does not justify relaxing the gate. Raw receipt:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T112337Z/`;
runner log `/tmp/pi-bg/job-2966907-46.log`; canonical handoff §37.

The owner-requested DIAGNOSTIC/QUALIFICATION split was then implemented in the
research runner. All four existing preflight limits remain HARD SAFETY and are
also watched during arms; only a valid stability-envelope failure is
non-blocking in DIAGNOSTIC. Existing first-three-window SwapFree/memory-PSI
pressure bounds are retained as additional hard stops, with no new numeric
thresholds. Four policy tests and Python compilation pass. One diagnostic
attempt had passing point snapshots but failed the independent baseline
memory/swap safety check: SwapFree fell 70,800 KiB below its initial three-
window floor and memory PSI reached 0.70% from a 0.00% envelope; I/O PSI full
avg10 was 6.05–10.77% across the ambient windows. No arm ran. A subsequent
source audit added samplewise checking of the unchanged hard I/O PSI ceiling;
the same raw trace also violates it. This is a **single-run safety refusal**,
not a project block and not an A/A result. Details and artifacts:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/diagnostic-fixes-and-tests.md`;
receipt `.../host-carryover-aa-pair-20261019T121316Z/`; handoff §38.

## M4 cgroup-v2 containment and safety calibration — 2026-10-19

Implemented the research-only per-arm transient user-service runner and independent watchdog in `research/m4-native/tools/m4_containment_runner.py`. The initial suite had 8 cases; the current suite has 9 tests covering hard-gate refusal, memory/swap pressure rules, missing telemetry, normal descendant ownership, nonzero and SIGTERM/SIGKILL exits, runtime abort, and controller SIGKILL recovery/cleanup. Current suite: 9 passed, 0 failed; no privileged actions or production changes. Initial supervisor-crash testing caught and fixed a watchdog NameError; final crash receipts verify `supervisor_lost`, post-receipt `cgroup.kill`, socket cleanup and removal of the owned cgroup. Earlier failure receipts remain retained.

Capability evidence: cgroup v2 and delegated CPU/memory/pids work through `Delegate=yes`; user-slice I/O is not delegated, so `io.stat`/`io.max` and an I/O cap are unavailable. `io.pressure`, host diskstats and vmstat are sampled. Historical 500×20 process PSS peaks were 304,227/286,915 KiB and sampled swap 0/0, but no cgroup memory or active workspace peak was recorded; therefore no numerical cgroup memory/swap cap or workspace quota is selected. Approved budgets only; sampled workspace limits are not kernel quotas. All established hard guards remain unchanged.

No 500×20/A/A workload ran at the original §39 snapshot. Snapshot 2026-10-19 20:14:49 +07:00 had MemAvailable 5,771,328 KiB PASS; `/var/tmp` free 15,469,010,944 B FAIL by 2,749,989,056 B; I/O PSI full avg10 4.32% PASS; load-1 1.62 PASS. The owner-authorized cleanup later restored disk headroom. The existing A/A orchestrator is now wired per arm to containment; current readiness is documented below. Full policy, metric semantics, receipts, probe, snapshot and checksums: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/containment-calibration.md` and `containment-tests-20261019T131427Z/`; canonical handoff §39.

## Same-binary A/A readiness — 2026-10-19

The orchestrator now records/enforces exact binary SHA-256, current Git revision and deterministic M4 source-tree and measurement-runner digests, plus resolved shared-library paths/hashes, before each arm. Integration coverage checks identity equality across both isolated arms. Current checks: containment 9/9, real-systemd sequencing integration 1/1, gate policy 4/4, `py_compile`, and `git diff --check` all PASS. No active M4 workload or running transient M4 service remained after tests.

A recorded readiness snapshot at 20:56:34 +07 had MemAvailable 6,886,092 KiB PASS (+1,408,330); `/var/tmp` free 19,470,082,048 B PASS (+1,251,082,048); load-1 1.76 PASS; I/O PSI full avg10 **4.52% FAIL** (unchanged 4.46% ceiling exceeded by 0.06 percentage points). No baseline capture or benchmark arm launched. Receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-readiness-gate-20261019T205634+0700.json`. The source-build provenance is partial: current bridge harness source checksum differs from the archived build checksum, so any A/A result will apply to the preserved binary, not certify the current harness source. The measured runner source-tree fingerprint is `ebda49add2738cef83622ccf21e94073897f4159b5fcb71c7ec11fcb7294318d`; exact details are in the receipt and canonical handoff §40. Resume once the host naturally meets all four unchanged hard limits; do not weaken thresholds or poll in a retry loop.

## Harness repair package closure — 2026-10-19

Packaged the repository-global and scoped M4 instructions, machine-readable policy and explanatory Markdown, containment runner, same-binary A/A orchestrators, policy/containment/scenario regression tests, calibration/readiness receipts, `README.md`, `RESULTS.md`, and canonical handoff. Exact staged paths are limited to the research M4 package and its handoff; no production Rust `.rs` changes are in the staged set. Policy enforcement reads `benchmark-policy.json`; Markdown contains no independent numeric thresholds. Legacy retained orchestrators now load the JSON policy, covered by regression tests.

Focused verification for this closure: policy tests **5/5**, harness scenario/termination contracts **4/4**, A/A gate tests **7/7**, synthetic containment tests **11/11**, tiny contained A/A sequencing integration **1/1**, `py_compile`, and staged whitespace check. These are harness/control-flow checks only; no benchmark arm launched. The diagnostic same-binary binary provenance remains partial and is handled separately in the writer-tail investigation. The isolated package was committed as `e1309f31d5d8788e1da5095ccd2fad62e02b8fed` (`research(m4): package writer-tail harness repair`); Phase B evidence remains separate.

## Writer-tail cause review and current-source build provenance — 2026-10-19

**Disposition: root cause remains unresolved; no new measurement launched.** Offline review of both order directions in the sustained 500×20 filter-identity A/B finds candidate writer p95/p99 above baseline in each: candidate-first +74.7%/+119.6%; baseline-first reverse pair +98.8%/+106.0%. This weakens the simple “candidate ran second” explanation, but two pairs do not establish causality. Reader CPU rose only modestly (+6.2% in the first pair, +2.5% in reverse), while candidate native-vector-build p95 rose to 246.4/232.1 ms from 111.0/119.3 ms and M4-update p95 to 107.7/113.5 ms from 103.7/78.3 ms. `commit_residual_after_vector_m4_publish_retire` p95 also rose (72.6/128.4 vs 43.9/56.5 ms). Native-vector build writer-thread CPU p95 rose much less (70.1/66.4 vs 60.5/53.5 ms), so most of the wall-time delta is not accounted for by bridge-writer-thread CPU; this is consistent with wait/other-worker/host delay but does not identify which. The retained measured binary emitted total vector build wall/CPU only—not per-cycle open, document-conversion, upsert, and flush wall/CPU arrays. Current source now contains those finer arrays, but its binary is not built or measured yet. These are concurrent stage correlations, not a causal decomposition; per-cycle host-pressure is also absent.

The high-tail cycles do not require scheduled compaction: in the candidate-first pair both p95 and max commit observations are non-compaction cycles; in the reverse pair p95 is non-compaction while the max is a compaction cycle. Thus vector compaction is not a necessary explanation, though ordinary native vector build/upsert/flush remains implicated as a proximate stage. The largest candidate commit tails cluster early: candidate-first pair cycles 0–1 were 593.9/431.1 ms, with total vector builds 307.1/246.4 ms and no compaction; in the reverse pair cycles 0–3 were 406.6–498.4 ms, with vector builds 204.8–256.7 ms (the first three non-compaction, fourth compaction), followed by substantial overlap with baseline. Initial base-vector build was 159.1 ms for candidate vs 115.9 ms baseline in candidate-first order, and 292.8 vs 107.8 ms in reverse order. This suggests startup/cold-state or early-cycle effects worth testing alongside candidate-associated reader/native contention; it does not identify the cause. Candidate physical writes were 8.85% lower in reverse order, which does not support a general write-amplification explanation. Three separate sync-reduction pairs showed a second-arm tail pattern and increasing host I/O pressure, supporting carry-over as a plausible confound for those pairs, but not proving it explains the order-reversed filter-identity A/B. The 1,024-document reader/compaction cohort has no writer-ack latency and is not evidence on this question. Hypotheses remaining: candidate-associated reader/native contention, host carry-over/scheduler or I/O interference, native vector upsert/flush variability, M4 update variability, and uninstrumented commit residual.

## 2026-10-19 continuation — guard scope, current-source build, and post-M4 attribution

**Build-guard scope:** `research/m4-native/docs/benchmark-policy.json` defines the four unchanged limits (MemAvailable, `/var/tmp` retained free bytes, I/O PSI full avg10, load-1) for standalone research measurement/A/A operations. Their callers are measurement preflight, contained arm runtime checks, and watchdog; CMake configure/compile is not a caller and the policy does not classify compilation as a measurement. Build instructions provide commands, not build-specific resource thresholds; no separate owner-approved numeric build-only thresholds exist. The earlier stressed snapshot was therefore not treated as a compile prohibition. Instead, compilation was allowed only after a fresh build-specific RAM/swap/disk/process review and under a bounded transient systemd cgroup; the empirical benchmark thresholds were neither changed nor bypassed for a benchmark.

**Build and runtime verification:** at 22:58:34 +07 before compilation, MemAvailable 7,440,828 KiB, SwapFree 11,834,068 KiB, load-1 2.50, I/O PSI full avg10 3.82%, `/var/tmp` free 14,298,071,040 B, build tree 446,525 B, comparable previous bridge output 10,162,330 B, and M4 research workspace 540,363,155 B. The benchmark disk floor was below this free-space reading, but it was out-of-scope for compilation. Current source built with the recorded one-job command in `m4-current-source-build-20261019.service`, KillMode=control-group and RuntimeMaxSec=600; no numeric memory ceiling was set. Build job `job-207232-294` succeeded in 46.828 s (46.119 CPU s), peak memory 623M, swap 0B. The receipt fingerprints 59 actual in-repo/native-profile compiler dependencies, CMake/toolchain/configuration, binary SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, ELF build ID, and resolved linked-library hashes. Correctness-smoke job `job-207232-302` passed the fresh-root `--generation-lifecycle` test with 3,581 checks/0 failures (26.877 s; peak memory reported 2.2G; swap 0B) in its own control-group service. It was not a performance arm. Full receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/current-source-build-20261019/current-source-build-receipt.json`; retained binary and configuration are in the same directory, sealed by `SHA256SUMS.txt`.

**Offline stage update:** the existing two sustained 500×20 pairs remain historical-binary observations. In addition to the previously measured vector/M4/publish stages, `build_end_ms - m4_build_end_ms` is derivable from their raw timeline markers. Its p50/p95 was candidate-first 28.55/39.10 ms vs baseline 26.30/33.14; reversed order 16.41/102.89 vs 29.09/50.01. The reverse candidate’s top cycles had 111.21/102.89/85.00 ms in this post-M4 interval. Source places post-M4 `SyncTree` and snapshot finalization before `build_end`; the interval is therefore sync/finalization-containing, not a pure fsync timer. The M4 clone timer also includes its own `SyncTree` work. These observations localize substantial elapsed time to several measured/derived regions, but still do not establish variant causality or a single root cause. Details and per-cycle rows: `stage-decomposition-offline-20261019.md`.

**Single permitted readiness attempt:** job `job-207232-317`, after current-source identity/build validation, ran the qualification-mode same-binary A/A runner through one fresh baseline preflight at 23:07:48 +07 and returned `OPERATION_BLOCKED` at `baseline-preflight`, gate `M4-RG-DISK`: resource-root free 14,288,429,056 B vs unchanged minimum 18,219,000,000 B (short 3,930,570,944 B). MemAvailable 6,043,024 KiB, I/O PSI full avg10 1.79%, and load-1 2.36 passed. No 60-second ambient baseline and no benchmark/A/A arm launched; no automatic retry. Receipt and SHA-256 manifest (`SHA256SUMS.txt`, manifest SHA-256 `1fea67dd70db4136f2b6f34edc35c95791e1e485d00e16c6233ce255e793dc14e`): `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T160748Z/`. Do not free space by deleting unreviewed artifacts or weaken the threshold. Recovery requires a fresh snapshot satisfying all unchanged guards and a new explicitly authorized attempt.

**Status: `PARTIAL / OPERATION_BLOCKED` for this A/A operation; mission remains open.** Build scope and current-source binary provenance/runtime correctness are now established. Same-binary A/A, current-source sustained validation, adversarial validation, and causal attribution remain outstanding. No production migration is authorized by this work.

## A/A read-only readiness snapshot — 2026-10-20

At 11:05:30 +07 (04:05:30Z), a read-only readiness snapshot was recorded at `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-readiness-20261020T040530Z.json`. This was not an A/A operation attempt: no runner, 60-second ambient baseline, output-root creation, or benchmark arm ran. Three resource guards passed—MemAvailable 6,313,076 KiB (floor 5,477,762), `/var/tmp` free 22,254,964,736 B (floor 18,219,000,000), and I/O PSI full avg10 0.27% (ceiling 4.46%); **M4-RG-LOAD failed** at 4.35 vs 3.14. Stop before A/A; do not retry by polling or change thresholds.

Pinned identity remained unchanged: binary SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, source revision `e1309f31d5d8788e1da5095ccd2fad62e02b8fed`, current source-tree SHA-256 `e6e4702945f2ee29f1a651d6a51d1226c104a4c0b16c4d7a485e6f09c2334ec0`, and measurement-runner-tree SHA-256 `dec0a54c33304f295a34a4201a108f806d798738e67461d4a1d3f5839e851b30`. The retained build manifest verifies 16/16 entries; `/var/tmp` remains the protocol resource root. Required tokenizer dictionary files are present.

Containment remains **PASS as harness evidence** (11/11 synthetic tests and 1/1 tiny sequencing integration in the packaged test record), but a live service launch was not probed. User systemd currently reports `degraded` with 17 inactive failed `m4-contained-*` units whose listed commands are synthetic 30-second fixtures; no active M4 unit or matching M4/A/A process was found. Cgroup v2 user-service `cpu memory pids` controllers are enabled, consistent with policy; no I/O throttling claim. Do not reset or alter those units in this task.

The prior one-shot readiness authorization was consumed by job `job-207232-317`, which blocked on disk before baseline/arms. The current task text conditions execution on existing authorization and does not unambiguously renew that one-shot, so another attempt is not authorized on that basis. Separately, this snapshot fails `M4-RG-LOAD`. The prior A/A receipt's six SHA256SUMS entries verify, but the digest previously printed for that manifest in this report is malformed (65 characters); the verified actual manifest SHA-256 is `1fea67dd70db4136f2b6f34edc35c957ee1e485d00e16c6233ce255e793dc14e`. Preserve the historical value; use the readiness JSON for the correction and new snapshot digest.

**Decision: `BLOCKED / NO LAUNCH`.** Next: require a new explicit owner authorization and a later fresh snapshot passing all four unchanged guards; then let the qualification runner enforce its baseline, telemetry/stability, containment, and inter-arm recovery gates. No cleanup, migration, threshold change, or production work was performed.

## 2026-10-20 continuation — workspace closure and diagnostic A/A no-launch

Workspace migration is committed separately as `d6280b2` (`research(m4): localize temporary workspace`). Exact scope: `research/m4-native/.gitignore`, `AGENTS.md`, `README.md`, and `tools/test_m4_containment_runner.py`; unrelated Rust edits and untracked M4 source/evidence were not staged. `.work/{tmp,build,sanitizer,scratch}` is ignored. Protocol benchmark data roots remain explicit (`/var/tmp` where Btrfs is required); project-local `TMPDIR` applies only where compatible and does not silently redirect a benchmark from tmpfs to Btrfs or vice versa. Focused regression results: containment runner 11/11, benchmark policy 5/5, A/A gate 7/7, contained-pair integration 1/1. No benchmark was used for workspace verification.

Current-source provenance was rechecked without rebuilding: the retained binary is SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, build ID `e889629e65a9ac929fa89a4a95ef549b1ec39062`; Release GCC 16.2.1 `-O3 -DNDEBUG`; native runtime library hash `6d55b9131766d943fbad1f77dbebc6a2db7951ce8d89ad46a92b7619cc92d950`. The retained build manifest passed 16/16, all 59 compiler dependency hashes plus CMake and bridge source hashes matched, and generation lifecycle smoke remains 3,581/3,581 PASS. No stale binary was substituted.

The one newly authorized diagnostic A/A readiness sample failed hard guards `M4-RG-MEM` (1,136,008 KiB vs 5,477,762 minimum; short 4,341,754 KiB) and `M4-RG-LOAD` (6.11 vs 3.14 maximum; +2.97). `M4-RG-DISK` passed at 22,254,845,952 B free (4,035,845,952 B headroom) and `M4-RG-IOPSI` passed at 0.00% vs 4.46%. SwapFree was 13,767,716 KiB; user systemd reported `degraded`, so containment readiness was not asserted. No ambient baseline or arm started, and no retry/poll was made. Receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-readiness-20261020Tcurrent-no-launch.json` (SHA-256 `6a4fa3076eb21942f08d16483cf26daff23447de8714a53b5ef478f590aa5be5`). Future measurement requires new explicit authorization and one fresh passing preflight; do not delete unknown artifacts or weaken guards.

| Hypothesis | Offline evidence and status |
| --- | --- |
| H1 host carry-over/order | Reversal weakens a simple second-arm explanation for the two filter A/B pairs; three separate sync-reduction pairs show order-associated tails/rising I/O PSI. No cycle-aligned host telemetry in the filter pairs and no A/A arms here. Plausible confound, **inconclusive**. |
| H2 native vector build | Candidate p95 246.42/232.13 ms vs baseline 110.98/119.29 ms; writer-thread CPU delta was much smaller; tails occur without scheduled compaction. Strongest proximate-stage correlation, but worker CPU/host state missing; **causality unproven**. |
| H3 post-M4 finalization | Post-M4 p95 39.10 vs 33.14 ms candidate-first and 102.89 vs 50.01 ms reversed; includes `SyncTree`/snapshot finalization, not pure fsync. **Localized but confounded/inconclusive**. |
| H4 M4/vector interaction | M4-update p95 107.69/113.55 ms vs baseline 103.70/78.34 ms; M4 clone timer itself includes `SyncTree`; no same-cycle host/native-worker isolation. **Inconclusive**. |

No single root cause is established; best current characterization is compound wall-time delay across vector-build and sync/finalization-containing spans, with host carry-over plausible. Confidence in any single cause is low. No speculative code change is justified. Mission remains `PARTIAL / OPERATION_BLOCKED`; current-source sustained/adversarial validation and causal attribution remain open, production migration is not authorized.

## Containment signal propagation and failed-unit retirement — 2026-10-20

Bounded owner-requested runner correction only: bootstrap now maps subprocess `-signal` to conventional exit `128 + signal`, retaining signed `workload_returncode` and explicit `workload_signal` alongside `bootstrap_exit_status`. Unit retirement preserves systemd metadata and receipt before stop, verifies inactive/absent unit and empty/absent cgroup, then resets failed state. No `--collect` or early metadata removal. Unverified cleanup remains fail-closed and does not reset failed state.

Synthetic containment regressions **12/12 PASS**: exit 7, SIGTERM/SIGKILL receipts retain 7/−15/−9; bootstrap/systemd report 7/143/137; owned units are collected after metadata retention. Initial evidence: `containment-signal-retirement-20261020/`. Closure audit prevents a not-found unit from overriding a known populated cgroup and preserves `pre_retirement_outcome` on cleanup failure. Source-matched 12/12 closure regressions include loaded/not-found unit responses with a populated cgroup; both refuse reset and report `CLEANUP_FAILED` / `SAFETY_STOP`. Current source hashes, receipts and log: `containment-lifecycle-closure-20261020/`. No benchmark, readiness retry, production change, or broader harness repair.

## Mission closure: containment → causal engineering decision — 2026-10-20

**Status: PARTIAL / OPERATION_BLOCKED. Causal classification: INCONCLUSIVE; decision: DEFER performance correction.** Prior continuity is separately packaged in `b30c86f`; lifecycle repair/source-matched 12/12 tests in `bfaf443`. The sole readiness observation identified four failed supervisor-crash fixture units with already-cleaned cgroups. `7777af8` fixes that concrete test teardown omission: preserve crash metadata/receipt, verify cgroup absence, retire owned unit; focused suite 12/12 PASS. Only the four units matched to owned run-state/abort receipts were archived/retired, not unknown units or host processes. Evidence: `containment-crash-fixture-retirement-20261020/`.

The single evaluation passed all unchanged resource limits: MemAvailable **5,885,576 KiB**, `/var/tmp` free **19,843,407,872 B**, I/O PSI full avg10 **1.37%**, load-1 **2.67**. Overall readiness did not pass: user systemd was `degraded` at that observation, and the executable still uses diagnostic outer FTS file-sync elision including WAL after truncate, with durability equivalence explicitly unproven (§35). No new corruption is claimed, but correctness/reopen checks do not waive this mission prerequisite. No ambient baseline or arm launched, no baseline-pressure/cadence/integrity qualification, no retry after fixture cleanup. No-launch is not performance evidence.

All 59 compiler dependency hashes, CMake/binary, all six recorded shared-library hashes and 16-file build manifest match current artifacts. Binary `57f3a19e…ba342f`; generation lifecycle remains 3,581/0. No rebuild or production change.

New direct offline join validates **80 historical cycles and 320 reader events**. All readers released their snapshot before same-cycle M4 build/publish; max reader windows per arm were 4.075/4.665/7.825/8.616 ms. This weakens direct late reader-lock contention, not shared CPU/cache/writeback interference. Native vector span includes shard setup/open/conversion/upsert/flush **and durable shard metadata**, not pure HNSW; compaction aliases it, not an additional cost. Post-M4 contains sync and snapshot/Coordinator finalization, not pure fsync or manifest publish. Candidate first-four/later-sixteen commit medians were 337.907/232.241 ms candidate-first and 476.895/197.386 ms reversed. Early effects are descriptive, not excluded from reported latency. No independent percentiles were combined.

| Hypothesis | Support | Contradiction / limitation | Confidence / implication |
| --- | --- | --- | --- |
| H1 host/order | Separate pairs show order/PSI association; early clustering. | Candidate direction survives reversal; no A/A or cycle-aligned host PSI. | Low causal; test non-variant variability. |
| H2 native build | Large same-cycle spans with much lower writer-thread CPU. | Span includes durable metadata; native worker CPU unavailable. | High localization, low causality; existing substep timers needed. |
| H3 post-M4 | Reverse cycles 1/2 show 111.209/102.894 ms there. | Candidate-first cycle 1 tails with only 24.991 ms post-M4. | Medium localization, low causality; not sole cause. |
| H4 shared bottleneck | Same flush/metadata path; readers already gone at late stages. | New writer collection is not pinned readers' old collection; no named shared bottleneck measured. | Low; no speculative lock correction. |
| H5 measurement artifact | Different historical/current fingerprints, weaker reverse provenance, broad timers/known trace overhead. | Raw workload arrays/coherence consistent; direction repeated. | High limitations, low sole-cause confidence. |

**One proposed next experiment, requiring owner approval:** after restoring normal full outer file-sync behavior or an approved durability-equivalence proof, one diagnostics-off same-binary 500×20 A/A using existing fine-stage timers and host telemetry, fresh Btrfs roots and unchanged gates. It can reveal asymmetry without a code variant, not establish candidate regression. No new infrastructure, policy change or A/B campaign proposed. Runner/fixture correction CPU, RAM/PSS, physical writes and maintenance impact: **NOT MEASURED**. Existing evidence is exhausted at its timing resolution; missing historic host/worker telemetry cannot be reconstructed.

Full report/commands, raw-cycle joins, input hashes, provenance, no-launch and retention plan: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/mission-closure-20261020/DECISION.md`; canonical handoff §47. The binary remains retained outside Git under the original results path, with exact hash/retention plan; raw arm stdout/process-sampling inputs are packaged separately from lifecycle. No push. This decision supersedes earlier next-action notes; further measurements need new authorization, not an automatic retry.

## Outer FTS/WAL durability restoration — 2026-10-20

Owner-authorized small research-only correction: `BuildDeltaVectorSnapshot` now performs full `SyncTree(generation_root)` before publication; the broad skip of `fts/` regular files is removed. Clone still skips only immutable hardlinked segment inodes. No WAL/snapshot design, file format, policy, native or production code changed. Previous outer skip omitted the file fsync after `FlushInternal` WAL truncate; directory fsync was not a substitute. Restores the original stronger sequence, not a physical power-loss guarantee.

Historical filter-identity A/B intended only an evaluator difference; no intentional write-order/WAL/fsync difference is documented, but complete original source/fingerprint provenance is insufficient for exact certification. Sync-reduction treatment/control **did deliberately differ** in outer FTS/WAL file syncs and cannot substantiate durability-equivalent performance claims.

Fresh Release binary **`ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`**; old diagnostic `57f3a19e…ba342f` retained, not overwritten. New receipt records exact commands, flags, actual 61 compiler dependencies, ELF/runtime identity and six shared-library hashes. Only bridge source differs among the prior receipt's 59 inputs.

Focused tests: **1,401 vector/recovery + 582 incremental FTS checks, zero failures**. Five syscall/fault cases and four recovery oracles PASS: old binary lacks post-truncate outer WAL fsync; restored binary syncs it; injected EIO/SIGKILL at the decoded WAL fsync cannot publish the new generation; pre-publish crash retains old generation; post-publish-WAL crash promotes the new one with coherence. These are process-crash/error tests, not device/cache power-loss simulation. Initial fault-test format assertion failure is retained with diagnosis; a corrected strace signal assertion passed on fresh roots without weakening a guard.

Build/correctness/fault jobs were bounded transient cgroups; no measurement or readiness attempt. Reported peak memory 630.7M/933M/394.7M, swap zero. Physical writes, peak PSS, maintenance impact and steady-state latency **NOT MEASURED**. Writer-tail remains INCONCLUSIVE; further A/A requires new authorization and new full-sync provenance/readiness checks. Evidence: `durability-restore-20261020/REPORT.md`, manifests, build receipt/config, original/corrected syscall traces and recovery logs; canonical handoff §48. Both binaries remain retained outside Git at explicit result paths.

## Full-sync same-binary A/A: authorized single no-launch — 2026-10-20

**OPERATION_BLOCKED; zero arms, no ambient baseline or retry.** Prerequisites passed: full-sync executable `ef0221f0…1212e7f`, 61 compiler dependencies, all six actual linked-library hashes and 50 retained manifest entries match; systemd running, no failed units/active M4 tests, lifecycle source matches verified regression evidence; Btrfs resource root `/var/tmp`. Durability correction remains valid. Live arm containment/baseline-pressure/cadence integrity were not exercised after preflight refusal.

The sole evaluation at **12:05:35 +07** failed `M4-RG-MEM` **4,134,692 KiB < 5,477,762** (short 1,343,070) and `M4-RG-LOAD` **6.56 > 3.14** (excess 3.42). Disk **22,096,510,976 B** and I/O PSI full avg10 **1.06%** passed unchanged limits. Job `job-1335348-140` stopped at baseline preflight; no workload roots or arms created, no polling, guard relaxation or process signaling.

Metadata-only orchestrator correction selects the actual build receipt (`M4_AA_BUILD_RECEIPT`) and configured full-sync description instead of stale elision text/archive; no gate, workload or telemetry changes. Existing gate regressions 7/7 PASS. Receipts were checked to contain the actual full-sync identity.

Evidence under `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`: `full-sync-aa-20261020/` and `host-carryover-aa-pair-20261020T050534Z/`, sealed with checksums; canonical handoff §49. No new per-cycle comparisons are possible; H1–H5 and writer-tail cause remain INCONCLUSIVE. Historical measurements are not relabeled current full-sync results. Another attempt requires fresh authorization and all unchanged readiness requirements; no automatic retry. Production untouched.

### Later explicit owner authorizations — 2026-10-20

Two separate user messages authorized one attempt each; both stopped at their sole resource preflight, with no ambient baseline or arms:

- “chạy đi, tôi tắt zen rồi”, 12:13:05 +07: MemAvailable 9,133,088 KiB PASS; load-1 **4.84 > 3.14 FAIL**; disk 21,718,110,208 B and I/O PSI 1.95% PASS. Job `job-1335348-147`; receipts `full-sync-aa-20261020-after-zen/` and `host-carryover-aa-pair-20261020T051305Z/`.
- “retry đi”, 12:14:02 +07: MemAvailable **4,450,464 < 5,477,762 KiB FAIL** (short 1,027,298); load-1 **4.40 > 3.14 FAIL**; disk 21,698,637,824 B and I/O PSI 1.69% PASS. Job `job-1335348-149`; receipts `full-sync-aa-20261020-owner-retry-2/` and `host-carryover-aa-pair-20261020T051402Z/`.

All receipt paths are under the same writer-tail causal directory above. Full-sync binary/source/runtime checks and systemd running passed. No autonomous retry/poll, policy changes or process interference; historical/new host snapshots do not supply a latency experiment. Writer-tail remains INCONCLUSIVE; another attempt needs new authorization. Canonical handoff §50.

### New owner authorization: I/O PSI refusal — 2026-10-20

“ổn rồi đó, chạy đi” authorized one new attempt. Binary/compiler/runtime/manifest, lifecycle/systemd and Btrfs prerequisites passed. At **13:09:15 +07**, sole readiness preflight failed **M4-RG-IOPSI: 4.68% > 4.46%** (+0.22 percentage points); memory **9,933,104 KiB**, load-1 **1.53**, disk **21,736,423,424 B** passed. Job `job-1335348-153`: OPERATION_BLOCKED, no ambient baseline/arms/roots or automatic retry/poll. Under the causal directory, receipts/checksums: `full-sync-aa-20261020-owner-retry-3/` and `host-carryover-aa-pair-20261020T060915Z/`. No new performance evidence; writer-tail INCONCLUSIVE. Further attempt needs new authorization; canonical handoff §51.

## I/O PSI policy audit and inactive amendment — 2026-10-20

**OWNER_DECISION_REQUIRED; no benchmark/readiness/baseline capture.** Exact ID `M4-RG-IOPSI`: currently a hard operational resource guard in both modes (`HISTORICAL_ENVELOPE_RESOURCE_GUARD`), not a universal safety limit and not a current measurement-quality-only waiver. Its 4.46% full-avg10 threshold comes from the declared historical successful-run envelope. Legacy `hard_safety_failures` fields are compatibility aliases, not classification authority. Existing diagnostic continuation permits only valid stability-envelope failures after all resource, pressure, integrity and containment checks pass; it cannot waive I/O PSI at preflight, baseline, watchdog/runtime or recovery.

Draft `../docs/io-psi-diagnostic-amendment.proposed.md` proposes an explicitly approved, one-attempt full-sync 500×20 diagnostic A/A exception for valid I/O PSI exceedances only. Raw comparisons stay FAIL; evidence stays exploratory. Qualification/default behavior, 4.46% threshold, all other guards and recovery counts/timeouts remain unchanged. Missing/nonfinite/invalid telemetry is not eligible. Owner must accept that there is no independently established safe upper I/O pressure limit or delegated per-arm I/O control. Proposal approval must precede implementation and synthetic acceptance checks; a benchmark still requires explicit execution authorization. No live JSON or runner edits were made.

Validation: **5 policy + 7 A/A gate tests PASS**, plus synthetic exact-4.68% replay confirming both modes still block and diagnostic recovery refuses. Retained active-policy/runner hashes are identical before/after. Receipts: `io-psi-policy-audit-20261020/`; canonical handoff §52. No new host/performance observation; writer-tail remains INCONCLUSIVE. No retry is authorized by this audit.

## Contention-aware diagnostic engineering — 2026-10-20

**IMPLEMENTED / SYNTHETICALLY VERIFIED; real A/A NOT AUTHORIZED / NOT RUN; writer-tail INCONCLUSIVE.** Owner's real-contention mission replaces the prior narrow I/O-only exception design, without activating that draft or reclassifying old refusals. Qualification and default diagnostic retain every pre-existing executable policy field (comparison against `475ea4c` PASS). New explicit `contention-aware` mode records all four empirical historical guard PASS/FAIL comparisons but does not require an idle host. It instead requires an approved operation-bound resource plan: finite owned memory/swap/CPU caps, workload-derived RAM/swap/disk reservations, sampled workspace bounds, timeout/monitor resolution, current policy/binary identity, and separate execution/I/O-risk approval references. No real approved plan is supplied; the pending template is deliberately rejected.

Outer preflight, ambient samples, contained-arm preflight, runtime/watchdog, recovery and evidence receipts share the scoped context. Independent watchdog validates/read-backs kernel caps and acknowledges a canonical context digest before releasing M4. Missing/invalid/inconsistent/incomplete telemetry, real capacity shortfall, observed host OOM, owned allocation/swap/oom distress, cap/controller loss, timeout, correctness/durability/identity or cleanup failure still stop M4 only. Recovery observes three valid capacity/integrity windows, stopping on failure rather than waiting for idle. Existing index roots and duplicate operation reuse are refused. Results are always **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; raw historical FAILs stay FAIL.

**51 tests PASS** (22 new contention, 5 shared policy, 4 harness contracts, 12 tiny real-systemd containment/lifecycle, 7 legacy gate, 1 tiny sequential-pair integration), source frozen before/after; fresh six-file `ty check` PASS. Intermediate controller-crash fixture failure retained: abort initiation was visible before socket cleanup; terminal cleanup publication/verification now removes the race without weakening assertions or extending deadlines. The exact failed fixture unit was verified and retired, not unrelated services. Final suite job `job-1335348-183`; no performance executable or readiness/ambient measurement launched.

Static full-sync verification PASS: **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**, source byte-identical to `7c88272`, 61 compiler inputs, six actual runtime libraries, 50 durability-manifest entries. Retained rebuild predates git commit publication; no post-commit-timestamp rebuild is claimed, and no C++ input changed. Existing corrected five-case fault/four-recovery evidence applies to these exact bytes, not physical power-loss safety.

Offline 80-cycle replay: long writer tails also occur without compaction (candidate-first candidate max 593.906 ms, native-vector elapsed/caller CPU 307.111/50.569 ms in that cycle). Native-worker CPU, runqueue vs blocking-I/O delay and per-syscall fsync wait remain unseparated; wall-minus-caller CPU is not identified I/O wait. Post-M4 residuals cannot all be called SyncTree without the newer split-stage telemetry. No speculative fix or performance win/regression is claimed.

Evidence/report: `contention-policy-20261020/REPORT.md`, test/provenance receipts, failed and final fixture records, reproducible offline review and checksums; policy guide and rejected template under `../docs/`; canonical handoff §53. **Remaining launch decision:** owner must choose/approve concrete evidence-based resource budgets and one operation, accept missing hard I/O isolation and sampled disk/observer limitations, and separately authorize a bounded pair. No idle-readiness loop or request to quiet other projects. No production/native/daemon/ABI/schema/index or unrelated Rust change.

## Autonomous writer-tail campaign preparation — 2026-10-20

**PARTIAL / OWNER_DECISION_REQUIRED_RESOURCE_PLAN; research INCONCLUSIVE, NOT COMPLETE.** New owner mission grants engineering/offline autonomy and a maximum three sequential diagnostic pairs **after resource/risk approval**. No envelope approved: campaign unactivated, **0/3**, no native benchmark, readiness/ambient observation or idle polling. No performance win/regression or production claim. A/B/C completion conditions remain unmet.

**`f6c182e`** fixes a concrete missing task/thread bound in existing contention containment: `pids_max`/TasksMax/pids.max, kernel read-back, task telemetry and fail-closed distress. Tiny owned-unit 64-task cap/cleanup verified; not a real approved budget. Qualification/default-diagnostic JSON unchanged outside the new section versus `6267e69`; historical raw FAILs preserved, no old guard relaxed. **56 distinct focused tests PASS**, fresh six-file `ty check` PASS; final selection in `analysis-release-v2-verification.json` and referenced receipts/logs. Earlier analysis checks are not counted twice.

Source exclusions: direct reader/schema-mutex contention on an unpublished new shard and intentional cohort waits within measured native/post-M4 stages do not fit inspected ownership/clock placement. Native-vector envelope includes collection/six scalar-index setup, HNSW, upsert, flush and durable metadata, so not pure HNSW. Old residual cannot isolate post-M4 SyncTree from finalization. No unsafe fsync/index removal or latency fix. New offline resource extraction from four retained arms: about 302 MiB process RSS, 4.96–6.38 s old wall, 216–239 MiB process writes, 6.85 MiB retained root; **not** cgroup/task/workspace peaks or current full-sync measurements. Pure offline counter bounds preserve read brackets/eight-digit clock serialization uncertainty and kernel accounting lag, without physical-wait attribution; no real causal output yet.

Concrete **pending** proposal: M4 1 GiB RAM/zero swap, two CPU-equivalent quota, 128 tasks; sampled 512 MiB allocated/1 GiB apparent workspace per arm; 1 GiB host-memory and 2 GiB disk reserves; watchdog 120 s (systemd 125 s), monitor gap 2 s; proposed GNU supervisor prefix `timeout --signal=TERM --kill-after=5s 355s` (360 s ceiling, not tool auto-background), maximum three sequential pairs; verified owned cleanup still required. These are unvalidated administrative budgets, not safety facts. No hard IO/total-write isolation, disk quota, exact device attribution or observer hard caps. Shared Btrfs/writeback/sampled overshoot/inherited-limit/other-project risks require explicit owner acceptance. Current proposal policy SHA `be7eca56…`; no approval inferred from engineering autonomy.

Static current full-sync ELF `ef0221f0…`, 61 compiler inputs, six runtime libraries and 50 durability entries revalidated; retained corrected fault/recovery evidence applies to exact bytes. No rebuild/correctness repetition. First static-module import lacked required environment **before any guard ran**; configuration failure retained separately, then first actual static verification PASS. No failed operation retried. Production/native libraries/daemon/ABI/schema/indexes and unrelated Rust edits untouched.

Evidence, source-map/hypotheses, pending exact JSON, test/provenance receipts, reproducible extraction, conservative counter analysis and budget ledger: `autonomous-campaign-20261020/`; canonical handoff **§54**. **Next:** owner approves/edits proposed envelope/IO risks for up to three named pairs; then one fresh capacity/provenance decision and one bounded pair, analyze before another. Claimed/preflighted refusal/abort consumes its slot; not permission for automatic retry or cap increase. Do not spend slots merely to exhaust budget. Research acceptance remains open.

## Owner-approved bounded campaign — launch refusal and control correction — 2026-10-20

**Stopped correctly / DEFER writer-tail; 1/3 slots consumed, zero native arms started.** Current owner approved the resource/risk envelope and autonomous pairs, replacing §54's pending-approval/A-B-C closure state. Approved plan reconciled conservatively to watchdog115s/systemd120s, GNU supervisor TERM355s/KILL+5s; all memory/swap/CPU/tasks/workspace/reserve limits unchanged from approval. No policy/qualification relaxation. Initial static full-sync `ef0221f0…`/61 inputs/six libraries/50 durability entries and delegation capability checks PASS.

Job **job-1335348-229**: valid 1200-sample/59.951s ambient observation, approved capacity/integrity PASS; raw historical disk FAIL1200/1200 and IO PSI FAIL1193/1200 preserved (full avg10 4.30–13.81%). Load1 1.54–2.65/stability drift FAIL remains nonblocking as authorized. Min MemAvailable7,278,576KiB; free disk18,041,962,496B; host OOM delta0, swap-in/out565/1 pages. No native M4 attribution.

First-arm launch refused **before native GO**: `systemd-run` rejected `CPUQuota=200.000%`, then controller accept timed out. Raw outer OPERATION_FAILED and arm LAUNCH_FAILED/CONTROLLER_ERROR/NOT_STARTED retained; no caps/readback reached, `benchmark_started=false`, no arm2. Owned failed unit collected, exact follow-up not-found/inactive/cgroup absent, native target absent; empty private parent retained. No unresolved cleanup/safety incident. Delayed closure host sample is explicitly not an immediate post-arm sample.

Independent minimal fix: serialize exact integer CLI percentages as **200%/50%**, keeping raw CPU quota and other caps identical. A new tiny regression reads actual kernel CPU200000/100000, memory1GiB, swap0, tasks128, exits and verifies retirement. **57 focused tests + fresh three-file ty check PASS**, job **job-1335348-233**; no native C++/measurement instrumentation/retrieval/ABI/schema/durability change, rebuild or performance retry. Fractional formatting unchanged, no generic support claim. **KEEP control fix/full sync; DEFER latency optimization.** Startup cause confirmed, historical writer-tail not diagnosed. H1 order effects and H2/H3/H4 native/shared costs remain unmeasured; no p95/cost/regression/qualification claim.

Two slots remain conditional/unspent but not eligible under owner §5: no valid prior native pair evidence; relaunching the first intended workload under a new ID is not authorized. Bounded campaign closes at its authorized stop after independent defect correction/verification; substantive writer-tail research remains INCONCLUSIVE and decision-dependent. Owner may explicitly authorize a new attempt/retry after the fix; no cap increase or quiet-host request. No automatic retry, push, production or unrelated Rust change.

Raw attempt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/m4-contention-campaign-20261020-pair-1/`. Report, approval/reconciliation, job/exit/closure evidence, offline script/result, updated ledger and tiny-test/source receipts: **`autonomous-campaign-20261020/REPORT-AUTHORIZED-CAMPAIGN.md`** and adjacent files. Current owner DoD reconciled there; canonical handoff **§55**. Historical raw FAILs/receipts/manifests unchanged, separate new checksums retained.

## Explicit replacement R1 — integrity admission refusal — 2026-10-20

**PARTIAL / OPERATION_BLOCKED; native measurement NOT obtained.** Owner explicitly added R1 after14f77dc: max4 total attempts including original no-launch; original Pair1/slots2/3 preserved. **2/4 consumed, zero native arms, two original conditional slots unspent.** R1 job **job-1335348-244**, ID **m4-contention-campaign-20261020-replacement-1**. Fresh actual kernel cap fixture, integer CPUQuota200%, full-sync ef0221f0…/61 inputs/six libraries/50 evidence identities and delegation/no-active-arm checks PASS. All approved caps/timeouts unchanged (watchdog115/systemd120/supervisor360), tracing off, Btrfs fresh roots. Real operation invoked once.

Ambient1200 valid samples/59.951s: approved capacity/integrity PASS, raw drift FAIL remains nonblocking. Load1 3.81–4.82; IO full avg10 1.76–5.05%; memory full0–0.55%; MemAvailable min5,605,092KiB; disk17,269,014,528B; host OOM delta0, swap-in/out12,099/7,647pages. Historical raw FAIL load1200/disk1200/IO487 preserved, not the refusal reason.

Actual caps applied/read back, then watchdog refused first bootstrap sample: **required_telemetry_missing / incomplete process-tree telemetry**, IO complete=false, memory complete=true. No native GO/arm2. Raw **SAFETY_STOP** and controller-cleanup false remain; final unit retirement true and separate exact-unit/cgroup/socket/root verification subsequently prove current cleanup complete before independent work. No raw safety downgrade or restart. Bootstrap memory peak16,023,552B/CPU55,717us, zero max/OOM/throttle; not M4 resource outcomes.

Confirmed one-line parser defect: proc IO keys have trailing colon; sampler expected uncolonized keys. Normalize terminal colon, retain missing/read/permission error failure and strict completeness guard. New tiny full-cap regression verifies real initial process IO/memory completeness and unchanged strict validator, not merely cap values. **58 focused tests + fresh three-file ty PASS**, job **job-1335348-248**, eight synthetic units retired. Execution controller da8c14b0… at14f77dc; corrected c4bc68de…; native measurement source43d62bc4… unchanged. No native rebuild/instrumentation/ABI/schema/index/durability change, no benchmark retry or unrelated staging.

Writer p50/p95/p99 and stage/event/order/coherence/native cost data **unavailable (N=0), not zero**. H1 ambient variation only; H2/H3/H4 unresolved; H5 parser artifact confirmed, not historical writer-tail. **KEEP parser/CPU control corrections, strict telemetry and full sync; DEFER latency optimization.** No regression, p95 composition, qualification or performance-win claim. Current formal refusal prevents automatically repurposing original slots as another retry; owner decides whether an existing unspent slot may be used after correction. No further budget expansion requested.

Authority/accounting snapshot, approved context, exact command/job, raw safety/cleanup evidence, reproducible analysis/hashes, focused tests and full reconciliation: **`autonomous-campaign-20261020/REPORT-REPLACEMENT-R1.md`**, adjacent `replacement-1-*`, `proc-io-parser-tests/`, ledger; canonical **§56**. Historical Pair1/raw failure preserved; no push, production changes or request to stop other projects.

## Real strict rehearsal PASS; original slot2 memory refusal — 2026-10-20

**PARTIAL / OPERATION_BLOCKED; native objective NOT fulfilled.** Owner authorized actual tiny end-to-end rehearsal then existing-slot continuation without another parser-specific request. Commit **277245a** adds fixed synthetic-command/context admission to the existing strict contention path, not a mock/legacy override; native binding/caps/policy/fullsync unchanged. **59 focused tests PASS**, annotation-only type errors corrected,25 contention tests rerun + fresh4-file ty PASS, hashes rechecked after crash.

Actual rehearsal **m4-contention-rehearsal-e2e-1**, job **job-1335348-259**, PASS: systemd/cgroup/kernel readback/bootstrap/child/proc IO/independent contention watchdog/context digest/runtime sampling/exit0/receipt and verified unit/cgroup/socket cleanup.32KiB fsync/read +1s hold, **12 complete strict samples**, memory peak22,282,240B/swap0/allocated workspace peak32,768B. CPU200000/100000,memory1GiB/swap0/tasks128, watchdog115/systemd120 unchanged. Five proc-IO fields verified including valid zero physical reads. Synthetic only, **not native benchmark evidence**; generic benchmark_started=true refers to Python rehearsal, zero native slots.

Then original **slot2** prepared (not Original1/R1 retry), accounting now **3/4 consumed, native arms0, one original conditional slot unspent**. Preparatory wrapper import-path error before any resource check/native command retained/fixed, same already-debited operation advanced to first formal check. Fresh native full-sync61-input/6-library/50-durability provenance verification PASS, no active units. At **15:44:07.135544Z** saved **M4-CA-MEM-CAPACITY FAIL**: available1,972,884KiB versus2,097,152KiB required (1GiB workload+1GiB host reserve), short124,268KiB (**127,250,432B**). Not historical threshold/IO/parser/quota refusal. Native command/orchestrator never invoked; no operation directory/baseline/arm. No automatic retry, including after user-reported crash.

After crash, background registry/log/exit unavailable; no fabricated exit or crash attribution. Current owned units/controllers/native processes absent, rehearsal unit/cgroup/socket verified retired again. Delayed post-crash snapshot is closure only, not a replacement capacity decision. Original1/R1 receipts/statuses unchanged. Writer p50/p95/p99/stage/order/reader/coherence/native resource-cost evidence **unmeasured N=0**, no candidate regression/win/qualification claim.

**KEEP** control/parser correction, bounded strict rehearsal and full sync; **DEFER** latency optimization. Final slot conditional/unspent, no specific discriminating native question from absent data; no new-ID retry after formal refusal, cap/budget expansion, stopping other projects or polling until green. Highest value: owner decision on further native measurement after capacity recovery within existing budget if explicitly allowed. Report/known limits/source changes/eight owner items/raw receipts/reproducible analysis: **`autonomous-campaign-20261020/REPORT-E2E-CONTINUATION.md`**, adjacent e2e artifacts, pair2 context/ledger and `pair-2-execution/`; canonical **§57**. No production/native/ABI/schema/index change or push, unrelated Rust edits preserved.

## Final original slot3 — actual native launch; workspace telemetry abort — 2026-10-20

Current explicit owner **“chạy đi”** used last existing slot with unchanged caps/gates/history. **4/4 attempts consumed, none remaining; one actual native arm launched, zero completed arms/pairs. PARTIAL / OPERATION_BLOCKED / BUDGET_EXHAUSTED**, not completed writer-tail evidence. Original1/R1/original2 unchanged. ID **m4-contention-campaign-20261020-pair-3**, job **job-8740-9**, supervisor exit1.

Fresh provenance61 compiler-input/six-runtime-library/50-durability identities, same-controller strict rehearsal, capacity/integrity/no-active-unit checks PASS. Unchanged full-sync ELFef0221f0…/source43d62bc4…/controlleraa1103b4…, no native rebuild or code/ABI/schema/production-index/sync change. Caps2CPU/1GiB/swap0/tasks128, watchdog115/systemd120/GNU355+5 unchanged. Ambient1200 valid samples59.950846022s, stability memory drift FAIL nonblocking. Native ELF **PID139597 actually started16:14:40.679230Z**,18runtime samples/17active, last active observation966.446534ms.

**Mandatory workspace_telemetry_incomplete** at16:14:41.687678Z: `fts/tombstones-2-1.bin.tmp.139597` missing between directory enumeration/stat (ENOENT), consistent with native atomic .tmp publication lifecycle. No scanner relaxation/reclassification/retry. Watchdog SIGTERM, native return-15; arm WATCHDOG_ABORT/ABORTED_CONTAINED, outer OPERATION_BLOCKED. Raw process-oriented measurement_quality COMPLETE does not validate failed workspace or incomplete workload. Partial failed-scan counts not workspace peak/cap proof. Observed partial memory peak219,193,344B/swap0/CPU1,087,464us, no max/OOM/task/throttle events; not full native costs. No native stdout/cycle payload, per-cycle writer p50/p95/p99/stage/coherence/reopen/order data unavailable—not0; no candidate regression/win/qualification.

Actual unit m4-contained-580593efa0104d61.service verified collected/not-found/inactive, cgroup/socket absent; no owned units. Arm2 neverstarted. Private partial generation1 retained at `/var/tmp/m4-writer-sync-pair-z9h8aj8w`, no other-project action. Rehearsal success proved launch path but did not exercise dynamic rename churn. **KEEP strict telemetry/fullsync/caps, DEFER latency optimization.** Highest value is focused bounded race-aware scanner design/regression, not swallowing ENOENT or a fifth native attempt. Any later native campaign requires new authority; budget exhausted.

Editor-only clang fallback include cascade fixed using research `.clangd` and verified CMake database mirror; unchanged C++ passes recorded compiler syntax-only and active LSP checks (full clangd feature check timeout retained, not PASS). New launcher/analysis/static verification and raw report/checksum references: **`autonomous-campaign-20261020/REPORT-FINAL-PAIR-3.md`**, adjacent final-slot artifacts/ledger/`pair-3-execution/`, raw sibling causal operation root; canonical **§58**. No push; unrelated Rust edits preserved.

## Bounded workspace observer correction — 2026-10-21

**Research correction verified; native readiness PARTIAL / OPERATION_BLOCKED.** No native execution; accounting remains **4/4 consumed, zero remaining**. Descriptor-relative no-follow single-pass scanner classifies known rename/unlink/create/replacement/growth races separately from permission/EIO/scope/budget/event-coverage errors. Complete traversal is explicitly not atomic or a capacity upper bound. Partial inventories cannot become complete measurements or verified peaks. Runtime/postflight use one capacity decision; unverifiable capacity still fails closed. No independent scoped allocated+apparent accounting backend exists on this shared Btrfs setup; inotify, IO/cgroup counters and global free space are not substitutes. Shared-disk reserve credit from sampled gross allocation is now zero. No CPU/memory/swap/task/workspace/reserve threshold, independent watchdog, runtime, native durability or production change.

**72 tests PASS** in six suites (19 observer,15 lifecycle,25 contention,5 benchmark policy,7 A/A gate,1 synthetic contained-pair integration), fresh job **job-8740-32**. Deterministic filesystem interleavings cover all requested races, sparse divergence, near-cap allocation and non-ENOENT errors plus bounded budgets/coverage loss/forged proof. Real tiny Python/systemd fixtures confirm unknown-capacity abort and actual single-file over-cap refusal before GO, with final retirement verified. Existing exit7/SIGTERM/SIGKILL/cleanup regression coverage PASS. Failed v2 test expectation retained: guard correctly refused launch; assertion corrected for existing raise-after-receipt API, not a guard change. Active LSP0errors with2hints/30numeric-parse warnings retained; no warning-free claim. Closure has no owned units.

**Native launch path NOT ready**: correct race classification does not supply the missing independent capacity proof. Remaining recovery is a separately bounded validated accounting design for both capacity counters, not ignored ENOENT, rescans or another native attempt. Even after engineering recovery, native requires fresh explicit authority. Report, exact test commands/source hashes, raw fixtures/failures and file-only replay: **`workspace-observer-repair-20261020/REPORT.md`**, `verification-v3/test-receipt.json`, `verification-summary.json`, `analyze_verification.py`; canonical **§59**. Historical §58 receipts/binary/ledger unchanged. Research-only commit, no push; unrelated Rust edits preserved.

## Owner-authorized 0–6 scalar open phase trace — preflight blocked — 2026-10-21

**PARTIAL / OPERATION_BLOCKED; no native launch or measurements.** The owner explicitly authorized one exploratory sweep (0–6 scalar indexes × HNSW/FLAT × create/reopen, three repetitions) with 1-GiB memory, swap 0, CPU 200%, tasks 128, 20-second runtime, and accepted shared-Btrfs I/O contention because `io.max` is unavailable. Fresh read-only preflight at **2026-10-21T06:43:58.627134Z** failed two unchanged hard gates: MemAvailable **3,992,232 KiB < 5,477,762 KiB**, and `/var/tmp` free **10,815,062,016 B < 18,219,000,000 B**. I/O PSI full avg10 0.88% and load-1 2.36 passed. These empirical operation gates are not universal host-safety claims; no threshold or policy amendment was authorized. No runner arm, systemd unit, cgroup, native open, or sample was created. Do not bypass or automatically retry; a future launch requires fresh passing evidence and a policy-consistent operation plan.

The source path predicts one shared RocksDB storage create/open followed by per-field scalar column-indexer setup, but there is no new stage timing or 0–6 slope. Existing cycle-7 open outlier remains unexplained. Full source-path analysis, approved scope, raw host/gate receipts, deficits, and next action: **`writer-tail-phase-trace-20261021/REPORT.md`**, `preflight-host.json`, `preflight-guards.json`, `SHA256SUMS.txt`; canonical **§64**. Native source/binary, runtime, policy, and production schemas untouched.

Cached-fingerprint mode remains opt-in. A focused cold/expired-cache assertion now requires full post-initialization fingerprint verification despite the skip option; `m4_analyzer_cache_lifecycle` rebuilt and passed **1/1 (2.78 s)**. This complements the existing candidate in-process generation test but does not eliminate the hot-cache mutation tradeoff; prior final crash/reopen validation used strict options.

## Shared-collection vs immutable-shard after GC/lock changes — exploratory pair — 2026-10-21

One randomized matched pair completed under the current finite containment contract: shared-collection ran first, immutable-shard second; same binary and dictionary; 500 docs × 5 writer rounds, four readers × five queries/arm. Both arms returned zero workload failures/coherence errors with 500 live rows. Hard process/cgroup telemetry and cleanup were complete; workspace accounting remains `UNKNOWN`/`INCOMPLETE` under sampled non-atomic traversal, so these are not clean/qualifying measurements.

Descriptive results: shared writer-cycle p50/p95 91.002/99.369 ms (writer commit p50/p95 62.279/72.952 ms); immutable commit p50/p95 655.732/870.953 ms. Reader end-to-end p50/p95/p99 was 7.913/11.239/11.671 ms shared vs 1.349/2.707/2.821 ms immutable. Shared GC p50/maximum was 21.332/38.409 ms across five cycles; one generation examined per cycle. Cgroup peak memory 250.0 vs 552.2 MiB, CPU 1.011 vs 3.529 s, process-tree writes 36.61 vs 58.23 MiB (not physical-device attribution). Host I/O PSI full avg10 differed materially: 4.41% at shared preflight (4.33% post) vs 10.25% at immutable preflight (9.06% post); order and environment confound architecture comparison. Writer and reader tails are single-pair descriptive estimates, not stable-tail or causal evidence. Full analysis: [`shared-collection-pair-20261021/analysis-m4-shared-collection-pair-20261021-164043-cb286c.md`](shared-collection-pair-20261021/analysis-m4-shared-collection-pair-20261021-164043-cb286c.md); raw receipts are in the linked operation directory.

Next: add reader generation-pin and snapshot-lock wait plus vector/FTS/merge stage observations to the shared workload, run focused/full correctness regression, then use a fresh randomized pair only if preflight remains valid. Cross-process lease and native access coordination remain unproven. Current architecture conclusion: **OPEN**; this pair suggests writer-throughput vs reader-tail tradeoff but cannot select a winner.

## M4 compaction/writer contention — 2026-10-22 follow-up

The latest **completed** three-arm operation (`m4-lock-contention-microbench-20261022-131543-f71669`) completed its fixed workload and containment checks: zero writer/reader/durability/coherence errors, successful rebase publication, and verified cleanup. The WAL-preserving rebase's single-run directional metrics were writer p95 16.359 ms, state-wait p95 15.578 ms, compaction state-lock hold 2.684 ms, and pending flush 0.0029 ms, compared with 32.254/31.096/17.729/14.855 ms in the prior 12:26 operation. This is not a controlled A/B: binaries and host conditions differ, and both results lack a verified lifetime workspace peak. Further, the executable policy's rebase semantics still described flush-on-publication when this binary ran; preserve this receipt as descriptive prototype evidence only, not a policy-matched or performance-eligible result. Policy documentation/tests now describe the WAL-backed tail; the operation was not rerun. The separate `m4-lock-contention-microbench-20261022-102855-992cef` watchdog abort remains no-arm-output telemetry failure (15.323 s gap vs 2 s), cause UNKNOWN, cleanup verified; do not retry or weaken cadence. See [analysis](lock-contention-microbench/ANALYSIS-20261022.md), [active checkpoint](ACTIVE-EXECUTION.md), and [raw completed operation](lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-131543-f71669/operation-plan.json).

### ApplyBatch writer-queue follow-up — 2026-10-22

Moved `ApplyBatch` document normalization before the state mutex and added opt-in per-call timing for normalization, state wait/hold, record encoding, WAL append, query wait/apply, and auto-flush. The four-arm contention harness now compares the same 32 logical writes as 32 individual Upsert calls versus four atomic eight-mutation ApplyBatch calls; all inputs are prepared before the start barrier. Schema v4 binds API type, API-call count, and logical mutation count. This evaluates explicit client batching, not an internal group-commit queue; the batch arm's four call samples are not an equivalent p95 tail to the Upsert arm's 32 samples.

The single fresh four-arm operation (`m4-lock-contention-microbench-20261022-135347-96ce27`) was **PREFLIGHT_BLOCKED** before launch by `M4-CA-MEM-CAPACITY`: 2,950,447,104 B available versus 3,758,096,384 B required. No workload, cgroup unit, or benchmark arm started; the empty operation-owned temporary workspace was removed, and the retained operation manifest verifies. Per the no-retry contract, the blocked operation was not retried; no ApplyBatch performance result exists. Full native build/CTest passed 8/8, focused correctness passed 19,020 checks with zero failures, TSan correctness passed 1/1, and Python contention/benchmark/harness suites passed 48/48, 17/17, and 4/4. See [analysis and verification evidence](lock-contention-microbench/ANALYSIS-20261022.md), [verification checksum list](lock-contention-microbench/verification-20261022/applybatch-verification-SHA256SUMS.txt), [blocked operation plan](lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-135347-96ce27/operation-plan.json), and [latest-operation pointer](lock-contention-microbench/latest-operation.json).

### Explicit post-writer flush diagnostics — 2026-10-22

Source review found that the contention harness captured native flush-stage timings only when `CompactNow` encountered a pending flush; the final post-writer `Flush()` had no standalone breakdown. Added opt-in `Flush(CompactionDiagnostics*)` timing for total/state-query lock intervals and all durable flush stages, emitted as `post_writer_flush_diagnostics` in timing schema 5. Nested `flush_stage_timing` v2 adds the exclusive query-lock hold interval, validates it against pending-flush duration without double-counting, and retains v1 for legacy timing schema 2–4 receipts. The verifier requires finite nonnegative values and validates stage totals against the flush interval. Correctness tests cover captured and disabled timing. This is measurement instrumentation only: the four-arm run remains hard-preflight blocked at 2,950,447,104 B available vs 3,758,096,384 B required, was not retried, and has no performance result. Latest focused build/correctness/policy test results are recorded in [analysis](lock-contention-microbench/ANALYSIS-20261022.md).

### WAL write/fsync substage diagnostics — 2026-10-22

Added optional per-call WAL `write()` and `fsync()` wall durations to `WriteDiagnostics` for `Upsert` and `ApplyBatch`. These sub-stages are emitted per API call in contention timing schema 6. The total WAL append wall/CPU timing and WAL durable ordering are unchanged; no timers run when diagnostics are disabled. The offline verifier validates the new arrays and checks their sum does not exceed append duration, retaining schema 2–5 compatibility. Schema 7 additionally emits `compaction_tracked_peak_bytes` from the existing `IndexStats` internal scratch tracker; it does not represent total process/cgroup memory. After schema 7, benchmark-policy tests passed 22/22, contention-policy tests 48/48, and focused Release `m4_correctness` CTest 1/1; the contention harness compiled in runner-selected Release, and runner `--validate-only` returned `VALIDATED_NO_LAUNCH` (operation `151420-a80e65`, no workload). This is diagnostic-only, not a measured performance result. The prior four-arm operation `135347-96ce27` remains preflight-blocked and must not be retried.

### Subsequent operation-level OOM abort — 2026-10-22

Fresh operation `145824-f0e7c1` passed host admission and started, but the watchdog terminated it after 5.0 seconds when cgroup telemetry disappeared. Kernel journal confirms the 512 MiB cgroup hit its limit (usage=limit=524,288 kB, failcnt=42) and OOM-killed native PID 495352 (`m4_compaction_w`, RSS 500,580 kB). The sampled peak was only 507,445,248 B; workspace accounting UNKNOWN, hard-safety telemetry incomplete, cleanup verified. Preserve the operation receipt/checksums; it is not a valid workload result. Do not retry this operation or the earlier no-launch ID, and do not raise/evade the cap. The runner-selected binary was built before schema 6, so no current WAL substage timings were measured. The new opt-out regression also exposed and fixed `Upsert` diagnostics resetting `capture_timing=false`; current Release build correctness passed 1/1. Details and exact evidence are in the [analysis](lock-contention-microbench/ANALYSIS-20261022.md).

### Smaller memory-probe operation — 2026-10-22

The newly registered 4,096-document `memory_probe` profile kept the same four arms, writer/read counts, 512 MiB cgroup cap, zero swap, and 3 GiB host reserve. Its only operation, `m4-lock-contention-microbench-memory-probe-20261022-152438-8f6561`, passed preflight but was stopped after 4.59 s when the watchdog observed `memory.events.max` rise 0→10. Sampled cgroup memory.current peaked at 514,531,328 B and memory.peak reached the 536,870,912 B cap. OOM/OOM-kill counters and swap remained zero; all 37 samples and hard-safety telemetry were complete. This is a contained allocation-limit abort, not a valid workload or performance result; stdout is empty and the exact growth phase is UNKNOWN. Host snapshots, checksums, watchdog/unit cleanup evidence, and the sampled partial workspace are preserved. Do not retry this profile or the 8,192-document operation. A later source-only change adds CLOCK_BOOTTIME phase markers to stderr, but those were not present in the aborted binary.

### Post-fix memory scale evidence and cache-lifecycle residual — 2026-10-22

The content-addressed snapshot fix was followed by a distinct 1,024-document-per-arm trace (measurement incomplete due workspace-observation uncertainty) and a 2,048-document-per-arm trace that completed under the unchanged 512 MiB cgroup cap at 194,039,808 B `memory.peak`. These are memory observations only, not latency evidence or proof of 4,096-document capacity; the earlier 4,096 abort remains unretried. Detailed receipts and qualifications are in [the lock-contention analysis](lock-contention-microbench/ANALYSIS-20261022.md).

At the time of this earlier post-fix trace, the focused native lifecycle test synchronized four concurrent uncached Jieba opens and verified shared content-addressed snapshot cleanup after the final analyzer lease; full CTest passed 8/8. Source inspection then identified pinned cppjieba's function-static path-keyed parsed-dictionary cache with no eviction. The subsequent isolated eviction prototype and its lifecycle/memory evidence are recorded in the later section below.

A single registered two-version churn operation, `m4-lock-contention-microbench-memory-churn-two-versions-20261022-170148-b72ef4`, confirmed retained process-memory steps after analyzer destruction: baseline PSS 165,810 KiB; version 0 +57,212 KiB; version 1 +43,624 KiB; neither release sample fell. Both unique snapshots/tokenization checks succeeded. The complete operation passed under the same 512 MiB cap with `memory.peak` 334,364,672 B, no OOM/swap/limit events, and verified cleanup. This is uncapped-cache baseline evidence, not the current implementation. Full receipts/checksums and limitations are in [the analysis](lock-contention-microbench/ANALYSIS-20261022.md).

### Bounded cppjieba cache-eviction prototype — 2026-10-23

The pinned cppjieba path-keyed static dictionary map retained every distinct content version until process exit. Added an isolated, reproducible overlay patch with one most-recent strong entry, weak per-key slots for active analyzer leases, same-key in-flight build coalescing, and expensive parsing outside the global mutex; content-hash snapshot identity is unchanged. The overlay is opt-in through the M4 research CMake configuration and is not a production dependency change.

Lifecycle regression checks cover four concurrent same-key opens (one parse), concurrent distinct-key opens with non-aliased terms, same-version sequential reopen without reparse, overlapping active old/new generations, release of the last old-version analyzer, and correct reopen of an older version without stale terms. The distinct-key test validates concurrent callers but does not force overlap inside the parse routine. A distinct 1,024-doc/four-arm operation completed under the unchanged 512 MiB/swap-zero resource contract. New cache trace: baseline PSS 167,890 KiB; version 0 +13,592 KiB; version 1 +0 KiB. Uncapped baseline trace: 165,810 KiB; +57,212 KiB; then +43,624 KiB. These are single-run process-memory snapshots, not a matched causal comparison; PSS remained high after releases due allocator retention. Patched cgroup `memory.peak` (337,891,328 B) was 3,526,656 B higher than baseline (334,364,672 B), so do not claim a whole-workload peak reduction. No OOM/swap/limit events occurred; cgroup I/O telemetry was unavailable and the run is not performance-eligible. Operation receipts, checksums, measurement limits, and the reopening-cost trade-off are documented in [the analysis](lock-contention-microbench/ANALYSIS-20261022.md). After measurement, the patch gained exception-safe retained-path staging so allocation failure wakes same-key waiters; that successful-path-neutral hardening is compile/CTest-verified separately and was not remeasured. The operation's recorded binary hash remains exact for the version it exercised.

## Writer group commit with readers across real staged flush — 2026-10-23

The first cross-factor operation `...flush-reader-pair-20261023-062758-fe414b` passed the generic runner but its direct arm called a no-op final Flush (zero pending docs, zero segment build/write); keep its receipt but exclude that arm from real-flush reader conclusions. A separate registered profile added 256 WAL-durable pending documents before reader launch, required segment build/write, and required all 256 unique markers after reopen. Corrected one-shot `...pending-flush-reader-pair-20261023-063533-5cd60e` passed with timestamped per-reader overlap proof in both arms and all checksums valid.

In this exploratory, performance-ineligible pair, group commit/direct writer p95 was 22.839/60.765 ms, while continuous-reader p95 was 1.105/1.226 ms; reader query-mutex wait p95 was 0.0665/0.129 ms. Explicit flush state-lock hold remained 6.431/5.914 ms; query-lock hold was 8.34/1.29 μs. WAL fsyncs were 73/97 total (8/32 concurrent), bytes written 8,868,393/9,086,837 B. One earlier group-first pair showed the opposite writer-p95 direction, so these do not establish stable latency or byte improvements. Cgroup peak was 157,470,720 B, swap/OOM zero; per-arm physical I/O attribution unavailable. Keep group commit research-only and defaults unchanged; no concurrent-flush architecture decision. See [the detailed analysis](lock-contention-microbench/ANALYSIS-20261023.md).

## Immutable-shard compaction/query follow-up — 2026-10-24

The cap6 replication and candidate-first cap4-vs6 pair showed lower segment writes but query p95 regressions correlated with run fan-out; the counterbalanced pair's workspace accounting was incomplete. The cap6 linear-vs-heap screen completed with complete workspace accounting but failed the p95 threshold, so the heap implementation was removed. A candidate-first cap4-vs5 pair then reduced process writes 7.29% and segment writes 17.09%, compactions 6→4, but burst1/burst3 p95 regressed 11.71%/21.25% and final query fan-out rose 7→9: **REWORK cap5; no default change**. Exact correctness/oracle/retrieval parity and workspace accounting were complete. See [the analysis](immutable-write-amplification/ANALYSIS-20261023.md) and [cap5 report](immutable-write-amplification/m4-write-amplification-pair-20261024-cap5-01/REPORT.md).

Two counterbalanced pairs of `max_delta_docs=512` vs1024 with per-level cap4 reduced process writes 11.18% and 13.85%, segment writes 26.43%, and compactions 6→2. Exact snapshot/oracle/retrieval parity held, but one control arm per pair had workspace UNKNOWN/INCOMPLETE. Query p95 changed from +6–59% slower when candidate ran second to 13–27% faster when it ran first; no stable performance conclusion. Two COMPLETE-workspace 512-vs1536 pairs reduced process writes14.85–16.10%, segment writes36.83%, compactions6→2, final runs7→5, with exact parity. But each order had one +52–54% burst-stage p95 outlier moving with order; **REWORK** for latency qualification, no default change. A 4-vs16MiB stats-log threshold pair reduced process writes1.05% with segment/WAL bytes unchanged, but final replay-open regressed18.5%; **REJECT** 16MiB and preserve 4MiB default. The generated snapshot-completeness flag had a missing `reopen` stage; raw content/oracle parity was exact and the reporter is fixed/tested. A 256-vs512 ApplyBatch chunk attempt stopped before initial indexing because the supported API rejects batches over256 mutations; root consumed, no retry or API-cap bypass. Next, a supported cap4-vs5 pair at delta1536 is prepared. Details: [analysis](immutable-write-amplification/ANALYSIS-20261023.md), [stats-threshold result](immutable-write-amplification/stats-log-threshold-PREPARED-20261024.md), [blocked chunk-size attempt](immutable-write-amplification/mutation-batch-PREPARED-20261024.md), and [cap5 preparation](immutable-write-amplification/cap5-delta1536-PREPARED-20261024.md). No production/default change.

## Quota-backed write-amplification and group-fsync correctness — 2026-10-24

Reused the registered root-attested Btrfs squota verifier for the write-amplification runner, with a fresh no-launch admission path. Two fresh, counterbalanced LZ4 pairs ran inside the 128 MiB qgroup and archived with tree-hash verification; each pair had PASS receipts, `HARD_QUOTA_BOUND`, exact oracle/retrieval parity, and complete workspace accounting. WAL bytes fell65.9%; process writes fell19.8%/35.6%, while CPU rose14.9%/37.6%, durable-ACK p95 rose29.0%/71.8%, target-file-cold replay-open rose23.0%/33.6%, and query p95 was mostly slower/order-sensitive. **REWORK LZ4; keep research-only/OFF by default; performance_eligible=false.** Full details and raw evidence: [quota follow-up](immutable-write-amplification/ANALYSIS-20261024-quota-followup.md).

Added a 24-writer held-group test for bounded queue backpressure, ACK drain, orderly shutdown and all-record recovery. Existing append/sync/apply fault injection and reader-visibility tests remain in the same `m4_correctness` suite; CTest passed. The per-Upsert group-fsync performance attempt remains consumed and is not repeated.
