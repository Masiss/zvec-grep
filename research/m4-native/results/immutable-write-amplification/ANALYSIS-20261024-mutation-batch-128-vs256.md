# Mutation ApplyBatch chunk size: 128 vs 256 — 2026-10-24

## Decision

**REWORK; keep 128 as the current setting, and do not promote 256.** Chunk 256 reduced ApplyBatch call counts and process-tree writes in both arm orders, but the write reduction was modest (1.69% and 5.76%, 3.74% across the two one-pair orders) and CPU/wall-time direction changed with order. Query p95 moved in opposite directions by stage and order; there is no reliable writer/reader tail win. The results are exploratory and non-qualifying. Do not change defaults. The current 256-mutation maximum remains in force; this study did not attempt to exceed it.

## Contract and method

The bounded `mutation-batch-128-vs-256` policy compares `m4-batch` in both roles, with all other M4 options held constant (LZ4 and stats coalescing OFF; same compaction and stats-log limits). It uses 600 files to fit the existing hard-quota workspace conservatively, and enables `M4_P42_LATENCY` symmetrically so each durable mutation call is recorded. Each order contains one control/candidate pair; the two orders are counterbalanced. The different 600-file fixture means these observations are only comparable within this profile, not to the existing 1,200-file studies.

Both operation roots were direct children of the attested Btrfs `squota` workspace `0/259`, with the existing 134,217,728-byte hard cap. Each final policy binding returned `VALIDATED_NO_LAUNCH`; the actual run receipts were `PASS` / `HARD_QUOTA_BOUND`. Both operation trees fit below the cap (47,591,424 B and 47,734,784 B), were copied to the result archive, byte-for-byte SHA-256 verified, and removed from the quota workspace. Each archive contains 77 source files and an `ARCHIVE-MANIFEST.sha256`.

| Order | Control | Candidate | Control CPU / wall | Candidate CPU / wall | Control process-tree writes | Candidate process-tree writes |
|---|---|---|---:|---:|---:|---:|
| 1 | 128 first | 256 second | 3.65767 / 4.48495 s | 3.80467 / 4.58952 s | 48,488,448 B | 47,669,248 B |
| 2 | 256 first | 128 second | 4.44812 / 5.47906 s | 4.15195 / 5.07360 s | 49,537,024 B | 46,686,208 B |
| Mean by role | — | — | 4.05290 / 4.98201 s | 3.97831 / 4.83156 s | 49,012,736 B | 47,177,728 B |

Counterbalanced mean deltas for 256 vs 128 were CPU **−1.84%**, wall time **−3.02%**, and process-tree writes **−3.74%**. The order-specific CPU deltas were +4.02% and −6.66%; writes were −1.69% and −5.76%. These are descriptive means of two one-shot observations, not estimates with inferential precision.

Per-call event counts changed as expected: batch upserts 25→13 and batch updates 6→3; batch deletes stayed 3. Upsert-call p95 rose from 40.36 to 84.01 ms in order 1 and from 66.02 to 80.47 ms in order 2. Update-call p95 rose from 23.14 to 39.01 ms and from 36.90 to 39.04 ms, respectively. Larger chunks therefore reduce call count but increase the cost of an individual durable call; the available workload does not establish that the total work or tail tradeoff is consistently favorable.

Query p95 by stage (candidate vs control) also reversed or varied with order:

| Stage | Order 1 | Order 2 |
|---|---:|---:|
| Initial | +40.9% | −16.5% |
| Burst 1 | +25.5% | +30.3% |
| Burst 2 | +14.9% | −2.0% |
| Burst 3 | −20.5% | +40.3% |
| Reopen | −2.2% | +12.4% |

Each stage contains 45 query samples/arm; the cross-order disagreement precludes a stable tail claim. The larger chunk is not demonstrably neutral for readers under this workload.

## Correctness, durability and resource evidence

Both order summaries report complete workspace accounting, exact document/token/file counts, equal oracle outputs, zero oracle/retrieval/top-10 mismatches, and top-10 overlap 1.0. Both arms completed with return code 0; containment cleanup receipts verified, swap was zero, and watchdog OOM-kill count was absent/zero. Peak process RSS was about 338 MB and peak PSS about 331 MB in both orders, within the existing 1 GiB cgroup memory cap. Full before/after host snapshots (including load, memory, PSI, affinity and process state) and process-attributed CPU/RSS/PSS/swap evidence are retained per arm; these observations are not admission gates.

The reports correctly mark the evidence **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**, `measurement_quality=COMPLETE`, and `performance_eligible=false`. The complete accounting and correctness receipts do not make one pair/order into performance qualification. No durability boundary or fsync setting changed: both arms use the same existing durable `ApplyBatch` path.

## Reproduction artifacts

- [Control-first report](m4-write-amplification-pair-20261024-mutation-batch-128-vs256-order1-02/REPORT.md)
- [Candidate-first report](m4-write-amplification-pair-20261024-mutation-batch-128-vs256-order2-02/REPORT.md)
- Both archives contain policy/config hashes, raw stdout/stderr and latency profiles, containment receipts, host snapshots, and verified SHA-256 manifests.

No production or default change is made. Revisit 256 only if a specific representative workload and repeat plan can distinguish the modest write reduction from the order-sensitive CPU and reader-tail effects; otherwise proceed to the next independent candidate.

## Reproducibility identity

Both runs used benchmark binary SHA-256 `252debff281ef3535df5776ce31669c7997879808f8dec5f76308dccb3960323`, policy SHA-256 `c46ca8824c9ee15eb367490cd63361e01539b1a006427c725d492978dfedcb37`, and dictionary-tree SHA-256 `9c05e30b201663f0045db0cd9d810b831b7adba59055b1658353a1bd7ae5fa5c`. The root-owned quota attestation was verified for subvolume `0/259`, maximum referenced bytes 134,217,728; no host quota, attestation, or global policy was changed by these runs.

## Evidence integrity note

The automated stale diagnostics repeatedly cited source lines 863/870 and test line 123. Fresh inspection showed lines 863/870 contain CPU aggregation expressions and line 123 assigns the policy object; neither alleged expression/duplicate keyword exists. Fresh Pyright reported 0 errors and Ty passed. LSP diagnostics were inconclusive (silent-on-clean server), not evidence of a clean check. No suppression or unrelated code edit was made.

<!-- END -->
