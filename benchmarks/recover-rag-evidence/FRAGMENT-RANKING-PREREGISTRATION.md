# Fragment Ranking & Selection preregistration

Continuation from `work/recover-rag-evidence-20261126@0ca0090`.

## New flow

`Fragment Fusion → Fragment Relevance Ranking → Budget-driven Selection → Verified Evidence Pack`

- Ranking uses fragment-level normalized RRF, FTS score, Potion vector score
  and fragment-content query coverage. Entity metadata is a bounded supporting
  bonus only; no file diversity and no delivery cutoff in ranking.
- Selection is greedy by `relevance × complementarity / cost`, bounded by
  24 KiB and 10 items. Overlap is penalized proportionally, not used to drop a
  fragment wholesale. Multiple fragments from the same file/entity may remain.
- Verified Evidence Pack is assembled from the selected items; no second
  pruning pass.

Unchanged: chunking, Potion, discovery, fragment fusion, normal search.

## Arms

| Arm | Binary | Switches |
|---|---|---|
| G0 | baseline `5a4c8201…` | none |
| G1 | previous fragment candidate `6d05ed17…` | `FRAGMENT_FLOW=1, BUDGET_SELECT=1`, R1 pack |
| G2 | new candidate | `FRAGMENT_RELEVANCE=1, FRAGMENT_FLOW=1, BUDGET_SELECT=1`, R1 pack |
| G3 | new candidate | G2 + `RANK_LIMIT=200` |

## Gates

At fixed 24 KiB / 10 items, G2/G3 must beat G1 and G0 on exact coverage with
no precision regression, <=25% token growth vs G1 and <=2x p95 latency.
Task correctness, oracle ceiling, CPU/RAM and normal-search parity reported.
No held-out, no production promotion.

## Amendment G — revised selection utility

G2/G3 lost evidence at selection despite a high loaded-pool oracle and had high
C# latency. G4 keeps the fragment relevance ranking unchanged and replaces
selection with a precomputed, score-first utility:

`utility = score + 0.25*query_coverage - 0.35*max_range_overlap - 0.05*(cost/24KiB)`

G5 is G4 with rank limit 200. Thresholds and budget are unchanged.
