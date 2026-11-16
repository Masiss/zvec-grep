# R4 evidence preservation — Gate 1–3

Branch `work/r4-preservation-20261116` (from `cdfb014`). Dev split only; frozen
target 0.5722 unchanged; no production/index/model change.

## Gate 1 — attribution (reproducible)

Added a typed per-candidate trace (`TracedCandidate` + `CandidateDecision`,
`WorkflowOutcome::traced`) covering admission, score, items, duplicate, per-file
cap, subject share, span bytes and verification.

Measured on D01–D12 (`R4B_DECISIONS=1`): **2,085 pooled candidates rejected with
`rejected_file_budget`, zero with any other reason, and 20 of 25 gold spans had no
candidate at all.** The admission-time file budget (`max_files = 96`, first-come)
was therefore not just dropping evidence — it was hiding the fact that the
remaining losses are *retrieval*, not selection.

## Gate 2 — fix applied

The file budget now bounds the **pack** (`select` enforces distinct selected files
≤ `max_files`) and the pool is bounded at `8 × max_files` for safety, so candidate
visibility is decided by rank, not arrival order. Rationale: a 10-item pack can
never use 96 files, so the admission cap only starved the pool.

Result: pools grew 434→953 units in the largest case with no loss elsewhere; 21
workflow tests pass.

## Gate 3 — paired results

| Arm | Macro Recall@10 |
|---|---:|
| hybrid raw top-10 (reference only) | 0.4167 |
| hybrid under the same 24 KiB pack (fair baseline) | 0.1667 |
| R4b best (compaction + breadth + dedup + this fix) | **0.3194** |
| PARTIAL / GO | 0.5000 / 0.5722 |

Pool 12/25, selected 6/25, citation accuracy 1.000, all spans verified. No change
in gold coverage from the file-budget fix alone: the pools that grew contained no
gold, confirming the binding constraint is discovery.

## Verdict

**NO-GO** for MVP: 0.3194 < 0.5000, and below raw hybrid 0.4167; ahead of the fair
byte-bounded baseline (0.1667) by +15.3 pp with full verification.

Verdict detail: evidence **preservation** is now instrumented and the one real
silent-loss mechanism (admission file budget) is fixed; the remaining 19/25 gold
spans are simply never retrieved (D02 six spans, D12 two spans, D05, D06, D07,
D09, D10 in files that no route ranks), so no selection work can convert them.

## Next single step (not started)

Query-to-entity discovery for the zero-pool cases, using the agent-plan protocol
already specified (blind plan frozen + hashed per case), evaluated against the
same 24 KiB contract; success criterion is pool ≥ 18/25 with the current
selection unchanged. Anything that only grows the pool without moving selected
gold is not an improvement.
