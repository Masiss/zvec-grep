# WAL-LZ4 × ApplyBatch chunk-size interaction — 2026-10-24

## Decision

**Keep LZ4 only as a research candidate at 256 mutations; REWORK/avoid it at 128; no default change and no qualification claim.** In this 600-file study, LZ4 saved roughly the same process writes at each chunk size (28.6% at 128; 29.4% at 256). Its CPU and wall effects differed sharply: the two-order mean at 128 was +15.1% CPU / +12.3% wall, while at 256 it was −15.4% CPU / −14.1% wall. At 256, most query p95 stages and durable replay-ACK p95 improved in both counterorders. At 128, CPU/wall and replay-ACK effects reversed with arm order, and query tails were mixed. This indicates a plausible compression-call amortization interaction, but the sample is small and prior 1,200-file LZ4 studies remain order-sensitive/non-qualifying. LZ4 stays OFF by default.

## Contract and method

All four fresh pairs used 600 files, the same benchmark binary/dictionary, `m4-batch`, stats coalescing OFF in both arms, delta1536, total-run cap12, per-level cap4, 4 MiB stats threshold, LZ4 acceleration1, WAL replay probe enabled, and symmetric per-call latency tracing. Within each pair, the sole treatment was WAL-LZ4 OFF vs ON; both arms had the same chunk size. For each chunk size, one pair was control-first and one candidate-first. This is a counterbalanced 2×2 exploratory extension of the completed LZ4-off 128-vs-256 cells, not a fully powered factorial study.

The harness bound each result root and arm scratch/index below the existing Btrfs `squota` workspace, qgroup `0/259`, hard cap 134,217,728 bytes. All four roots passed `VALIDATED_NO_LAUNCH`; actual arm receipts were `PASS` / `HARD_QUOTA_BOUND`. Each pair had complete workspace accounting, exact document/token/file and snapshot/oracle/reopen parity, zero retrieval/top-10 mismatch, and `top10_overlap_mean=1.0`. Each root was archived with a 77-file SHA-256 manifest, then only that exact quota-root directory was removed. Per-arm peak RSS was about 338 MB, PSS about 331 MB, swap zero; full host before/after snapshots and process profiles are retained.

## Counterbalanced aggregate

| Chunk size | Metric | LZ4 off mean | LZ4 on mean | LZ4 delta |
|---|---|---:|---:|---:|
| 128 | Process-tree writes | 48,906,240 B | 34,922,496 B | −28.59% |
| 128 | Process CPU | 4.74196 s | 5.45567 s | +15.05% |
| 128 | Wall time | 5.87292 s | 6.59767 s | +12.34% |
| 256 | Process-tree writes | 47,505,408 B | 33,529,856 B | −29.42% |
| 256 | Process CPU | 4.69498 s | 3.97220 s | −15.39% |
| 256 | Wall time | 5.68289 s | 4.88201 s | −14.09% |

Order-specific effects show why these means are descriptive only:

| Chunk/order | CPU delta | Wall delta | Process-write delta |
|---|---:|---:|---:|
| 128, control-first | +44.06% | +37.73% | −25.98% |
| 128, candidate-first | −13.89% | −14.13% | −31.16% |
| 256, candidate-first | −21.07% | −18.70% | −30.03% |
| 256, control-first | −9.04% | −8.91% | −28.79% |

At 256, the CPU/wall/write direction was consistent across orders. At 128, writes were consistently lower but CPU and wall reversed. The 128 result therefore does not support an unconditional LZ4 recommendation.

## Reader and replay latency

Mean query p95 changes (LZ4 on vs off, averaging the two arm orders) were:

| Stage | Chunk 128 | Chunk 256 |
|---|---:|---:|
| Initial | +19.1% | −29.5% |
| Burst 1 | +14.6% | −14.4% |
| Burst 2 | −13.5% | −39.6% |
| Burst 3 | −40.7% | −16.5% |
| Reopen | −52.4% | −15.8% |

The chunk-128 burst3/reopen means are dominated by one unusually slow control-first control arm; per-order values reverse or vary. Query latency at chunk128 is therefore mixed/noisy, not a reliable speedup. At chunk256, query p95 improved in both orders for all but burst1 in the control-first pair (+23.2% there); the two-order mean improved in every stage. These stage samples are 45 queries/arm and remain exploratory.

Durable WAL replay-probe ApplyBatch ACK p95 changed from 45.15 to 50.26 ms at chunk128 (+11.3% mean; one order +57.7%, the other −30.1%) and from 86.27 to 68.94 ms at chunk256 (−20.1% mean; both orders improved). ACK batch counts were equal within each chunk-size pair (19 at 128; 10 at 256); the larger chunk changes the work represented by each individual ACK.

## Conclusion and next work

The evidence supports a narrow research hypothesis: 256-mutation chunks amortize LZ4 CPU sufficiently to make the write-reduction candidate more promising than at 128. It does **not** qualify the combination: each condition has only two single-pair orders on a 600-file fixture; 128 exhibits strong order dependence; and larger existing LZ4 studies still have order-sensitive query tails or workspace-quality caveats. Keep both options research-only and keep defaults unchanged. Do not rerun previously consumed 1,200-file LZ4 operations or relabel them. A later qualification would need a separately authorized representative workload with complete accounting, fresh operation identity, and unchanged 128 MiB cap.

## Reproduction artifacts

- Chunk128 control-first: [report](m4-write-amplification-pair-20261024-lz4-batch128-interaction-order1-01/REPORT.md)
- Chunk128 candidate-first: [report](m4-write-amplification-pair-20261024-lz4-batch128-interaction-candidate-first-01/REPORT.md)
- Chunk256 candidate-first: [report](m4-write-amplification-pair-20261024-lz4-batch256-interaction-order2-01/REPORT.md)
- Chunk256 control-first: [report](m4-write-amplification-pair-20261024-lz4-batch256-interaction-control-first-01/REPORT.md)
- All four archives contain their raw profiles/logs, receipt/host snapshots and SHA-256 manifests.

Binary SHA-256 `252debff281ef3535df5776ce31669c7997879808f8dec5f76308dccb3960323`; policy SHA-256 `624c2f97499e190e6562a348175e73bfbb721b7de99ad0cb36cac4fb7487c6eb`; dictionary-tree SHA-256 `9c05e30b201663f0045db0cd9d810b831b7adba59055b1658353a1bd7ae5fa5c`. No filesystem quota, host resource setting, production default, or durability boundary changed.

## Evidence-integrity note

Repeated automated line diagnostics cited `profile_native_m4_paired.py` lines 863/870 and test line 123. Fresh source reads show CPU aggregation/dictionary entries and the new test function at those locations; the alleged invalid operator is absent and the mentioned keyword occurs only once. Fresh Pyright reports 0 errors and Ty passes. LSP publishes no findings but cannot confirm clean status (inconclusive); no suppressions were added.

<!-- END -->
