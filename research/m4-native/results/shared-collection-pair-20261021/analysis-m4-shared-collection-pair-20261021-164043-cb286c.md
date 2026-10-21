# Shared-collection vs immutable-shard exploratory pair — 2026-10-21

Operation: `m4-shared-collection-pair-20261021-164043-cb286c`
Raw evidence: [`m4-shared-collection-pair-20261021-164043-cb286c/`](m4-shared-collection-pair-20261021-164043-cb286c/)
Status: `COMPLETED_WITH_WORKSPACE_CAVEATS`; evidence class `EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING`.

## Design and provenance

One randomized-order matched pair; the order was **shared-collection first, immutable-shard second**. Both arms used the same binary (`79a06b7d67a95aff5a20a95743a6823771e6278c73ecc37a1ddd0d1b9e8a5034`) and Jieba dictionary tree (`9c05e30b201663f0045db0cd9d810b831b7adba59055b1658353a1bd7ae5fa5c`). Workload: 500 documents, five writer rounds, four concurrent readers, one query per reader per round (20 reader queries/arm), analyzer cache disabled, durability enabled. The contract capped each arm at 1 GiB memory, zero swap, 200% CPU, 128 tasks, 180 s, with existing host RAM/disk reserves and monitored workspace soft budgets. No I/O controller was available; process-tree writes are not physical-device attribution. Raw operation receipts preserve the exact policy digest and context. The policy's approval-reference string was later found to carry a one-day-future date relative to the UTC run timestamp; the current owner mission did authorize the exploratory pair, but the text is a provenance-label defect. It is corrected in the live policy without rewriting this operation's receipts.

## Results (single pair; descriptive only)

| Metric | Shared-collection | Immutable-shard |
|---|---:|---:|
| Writer cycle p50 / p95 / p99 (ms) | 91.002 / 99.369 / 99.369 | not emitted as the same metric |
| Writer operation p50 / p95 / p99 (ms) | commit 62.279 / 72.952 / 72.952 | commit 655.732 / 870.953 / 870.953 |
| Reader end-to-end p50 / p95 / p99 (ms) | 7.913 / 11.239 / 11.671 | 1.349 / 2.707 / 2.821 |
| Cgroup CPU usage (s) | 1.011 | 3.529 |
| Observed cgroup memory peak (MiB) | 250.0 | 552.2 |
| Process-tree write bytes (MiB) | 36.61 | 58.23 |

Shared-collection GC, five cycles: total GC p50 21.332 ms / max 38.409 ms; M4 search+delete p50 2.779 ms / max 3.517 ms; M4 flush p50 9.084 ms / max 9.881 ms; native delete p50 2.792 ms / max 3.004 ms; native flush p50 4.818 ms / max 5.730 ms. One generation was examined per cycle; no compaction was triggered in these five cycles. The first GC checkpoint was 18.363 ms; later checkpoints were about 1.2–1.5 ms.

Both workloads returned 0; each reported 500 live rows in both stores and zero coherence errors/failures. Both owned systemd units were verified stopped/collected. Cgroup telemetry was complete; no OOM, swap, task-limit, CPU-throttle, or capacity-reserve failure was observed. Sampled postflight workspace traversals had no enumerated scan errors, but are explicitly non-atomic: allocation/apparent observations were 9.74/34.67 MB (shared) and 3.31/11.50 MB (immutable), not hard-cap proof or verified lifetime peaks. Raw receipts therefore retain `measurement_quality=INCOMPLETE` / workspace `UNKNOWN`; do not promote this to a clean/PASS measurement.

## Interpretation and next step

This pair suggests the expected trade-off: shared-collection's observed writer work is much lower, while its observed reader tail is higher. These are not stable tail estimates: five writer samples make nearest-rank p95/p99 the maximum; each arm ran once. More importantly, host I/O pressure changed substantially between arms: full PSI avg10 was 4.41% at shared preflight (4.33% post-arm) versus 10.25% at immutable preflight (9.06% post-arm). The immutable arm also ran second. The reader latency gap and some writer/resource differences are therefore confounded by arm order and changing host pressure; this does **not** identify a causal architecture effect or establish a winner.

Next useful experiment after source instrumentation: collect per-reader generation-pin wall time, snapshot-lock wait, vector/FTS stage time, and merge time on shared-collection, then run a new fresh randomized pair only when the operational preflight is valid and host conditions support interpretable comparison. Use a new operation ID; do not replay or overwrite this receipt. In parallel, investigate cross-process lease and native-access coordination; process-local `EpochPins` and `snapshot_mutex_` do not prove cross-process safety.

Checksums in the raw operation directory are unchanged and cover the harness receipts. This analysis is a separate derived artifact and does not rewrite any raw evidence.