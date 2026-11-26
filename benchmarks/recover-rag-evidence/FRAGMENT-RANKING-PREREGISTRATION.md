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

## Amendment H — RRF-dominant relevance

G4/G5 kept the same selection defect as G2: mixed FTS/vector/coverage weights
reordered fragments away from the proven RRF pool. H1 tests RRF-dominant
relevance (`1.0*RRF + 0.02*coverage + bounded metadata`) with the G4 selection
utility. Budgets and gates are unchanged.

## Amendment I — minimal RRF relevance

H1's C# latency/quality still trailed G1. I1 removes the expensive metadata
bonus path entirely in RRF-only mode and uses the proven rank-ordered budget
selection (`rag_budget_select`), keeping fragment relevance ranking and
verified pack assembly. Gates unchanged.

## Amendment J — bounded structural support

I1 recovered Rust precision but C# still lost to G1. J1 adds bounded entity
structural support (symbol/definition bonus capped at 0.25) to RRF relevance,
still keeps fragment identity and rank-ordered budget selection. This directly
tests whether C# needs structural metadata as a supporting signal. Gates
unchanged.
