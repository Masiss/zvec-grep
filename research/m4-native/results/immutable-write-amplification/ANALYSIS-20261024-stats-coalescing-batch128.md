# Stats-event coalescing at ApplyBatch chunk 128 — 2026-10-24

## Question and binding

Does stats-event coalescing change write and latency behavior at the 128-mutation ApplyBatch chunk used by the supported workload, with WAL-LZ4 disabled? The control and candidate both used 1,200 files, 128-mutation batches, 4 MiB stats-log threshold, delta 1,536, per-level run cap 4, and durable ApplyBatch WAL. The sole arm change was `coalesce_stats=false` vs `true`. Symmetric per-call mutation tracing was enabled. No durability or correctness boundary was relaxed.

Both operation roots passed `VALIDATED_NO_LAUNCH` on the policy-bound 128 MiB qgroup `0/259` using binary SHA `252debff281ef3535df5776ce31669c7997879808f8dec5f76308dccb3960323` and policy SHA `8d1c1d7f22515f841d6492baa60901235e9705049080731a4b471836f0d65843`.

## Control-first pair — complete, exploratory only

The [report](m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-control-01/REPORT.md) records both arms exiting 0, containment `PASS` / `HARD_QUOTA_BOUND`, complete workspace accounting, exact snapshot/oracle/reopen/retrieval parity, and no mismatches. The archive has 79 SHA-256-verified files.

| Metric | Control | Candidate | Difference |
|---|---:|---:|---:|
| Process-attributed writes | 77,242,400 B | 72,175,600 B | −6.56% |
| Process CPU | 6.60 s | 6.92 s | +4.85% |
| Process wall | 7.63 s | 7.83 s | +2.62% |
| Stats-log bytes at initial snapshot | 3,525,600 B | 5,803 B | −99.84% |
| Stats-log bytes at burst 3 | 0 B (checkpointed) | 10,207 B | different epoch/checkpoint timing |
| Durable replay-probe ACK p95 | 37.669 ms | 45.498 ms | +20.77% |

WAL appends, segment bytes, fsync counts, compactions, logical snapshots and retrieval were equal. Query p95 improved at initial/burst1/burst2 (−18.4%/−22.3%/−21.3%) but worsened at burst3/reopen (+18.4%/+5.4%). This single order therefore shows a write/log-volume benefit with mixed latency, not a qualified candidate.

## Candidate-first counterorder — consumed, incomplete

The candidate arm exited 0 with containment `PASS`; the following control arm exited 2 during burst-3 flush. Its receipt is `NONZERO_EXIT`, not a quota/resource refusal. Captured stderr contains `FATAL M4 burst flush`; cleanup was verified, and the runner reported no workspace-capacity errors. The partial candidate-first archive contains 77 SHA-256-verified files, including both receipts, host snapshots, profiles and raw logs; see [STATUS.md](m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-candidate-01/STATUS.md).

The benchmark previously discarded the returned M4 `Status.message` at both flush assertion sites, so the underlying flush error cannot be determined from this consumed operation. The research benchmark now includes the status text on future flush failures. This is an observability fix only; it neither explains nor repairs the underlying failure. The updated target builds, and `m4_correctness` passes. The failed identity was preserved and will not be retried; do not combine the control-first measurement with the partial counterorder as a pair.

## Decision

**REWORK / INCOMPLETE — stats-event coalescing at chunk128 remains research-only.** One complete order reduces process writes and the stats log while increasing CPU, wall time and replay-ACK p95; its counterorder failed on an M4 flush with no captured status detail. There is no paired estimate and no basis to promote, reject, or change defaults. Preserve the successful and failed roots separately. No retry of the consumed candidate-first operation.

The earlier batch256 coalescing studies remain separate evidence; this result does not retrospectively qualify them or establish a chunk-size interaction.

## Verification and limits

- Profile tests: 13/13; benchmark-policy tests: 30/30.
- Pyright: 0 errors/warnings; Ty passes. Fresh LSP pull reports zero findings in the profile test; the profiler module has heuristic warnings on deliberate fail-closed policy/telemetry parsing and numeric-output decoding. No suppression was added.
- Native benchmark and `m4_correctness` targets build; `m4_correctness` CTest passes 1/1.
- This is exploratory evidence only. Do not change production/default settings.
