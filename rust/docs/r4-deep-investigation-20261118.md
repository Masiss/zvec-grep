# R4 deep investigation & autonomous resolution

Branch `work/r4-deep-20261118` (from `45c3e19`). Frozen corpus/index, frozen E3
plan (`e8a33dde…`), locked evaluation contract; 25 workflow tests pass;
citation accuracy 1.000 in every run.

## Gate 1 — deep investigation

Reproduced the standing results exactly (r4b 0.3194, plan arm 0.2083, pool
14/25; oracle 0.5833; baseline 0.1667). New findings:

**F1 — the two arms retrieve complementary gold.** The no-plan arm's pool holds
D09/D10/D11 gold (lexical + FTS routes); the plan arm's pool holds
D02/D05/D12 gold (plan-hybrid queries). Union of the two pools = **19/25, macro
0.8472**. Neither arm can see the other's evidence because the 6-call budget
forced a choice: the plan arm spent its calls on plan symbols + plan queries and
never ran the scoped lexical round.

**F2 — per-file cap and breadth are NOT binding.** An oracle selector over the
union pool *under the current caps* (per-file ≤4, ≤10 items, 24 KiB, compact
units) still reaches **19/25 (0.8472)**. Every previously published "selection
loss" is therefore an ordering/signal problem, not a capacity problem. The
"2 per-file cap + breadth starves multi-gold files" hypothesis is now
**superseded**: with the right candidates present, the caps admit all of it.

**F3 — blind plan symbols are pure waste.** Exact symbol lookups on blind-guessed
names contributed zero gold spans while consuming 3 of 6 calls — the single
largest recoverable budget item.

**F4 — compaction does not enforce its own cap on whole-method matches.** A
candidate whose match covers an entire large method (e.g. ContractLabor
136–387, 13.4 KiB delivered) passes through at full size because
`compact_range` only caps the *header path*, not the window. One such span eats
55 % of the pack. (Found, but see E3: fixing it by trimming loses coverage of
deep gold — the trade is unfavourable under the overlap metric, so it stays
open as a representation question, not a quick fix.)

**F5 — score ties among same-file sub-units break toward the smallest method**
(size penalty), which is an anti-signal: nothing ranks the gold method above its
siblings except weak plan terms.

## Gate 2 — three experiments

| Exp | Change | r4b | plan arm | plan pool | plan selected | Verdict |
|---|---|---:|---:|---:|---:|---|
| base | — | 0.3194 | 0.2083 | 14/25 | 4/25 | — |
| **E1** | drop blind symbols, plan-hybrid queries 2, return the saved calls to the scoped lexical round | 0.3194 | **0.4444** | **18/25** | **7/25** | **accepted** |
| E2 | AgentPlan route bonus 7000−10·rank | 0.3194 | 0.3611 | 18/25 | 6/25 | falsified — displaces FTS/lexical gold (D09/D10/D11) |
| E3 | enforce the 2.5 KiB cap on compact windows | 0.2500 | 0.3611 | 18/25 | 6/25 | falsified — trimming cuts deep gold coverage; large windows are load-bearing under the overlap rule |

E1 is the shipped state: plan arm **0.4444** with pool 18/25 and selected 7/25;
no-plan arm unchanged at **0.3194** (independent variable isolated — only the
plan arm's call allocation changed).

## Gate 3 — paired qualification (locked contract, single run)

| Arm | Macro R@10 | Pool gold | Selected gold |
|---|---:|---:|---:|
| hybrid snippets ≤24 KiB (baseline) | 0.1667 | 2/25 | – |
| R4b, no plan | 0.3194 | 12/25 | 6/25 |
| **R4b + frozen E3 plan, E1 allocation** | **0.4444** | **18/25** | **7/25** |
| policy oracle over the same pool | 0.8472 | 19/25 | 19/25 |
| GO target | 0.5722 | | |

Plan-arm per-case selected gold: D03 1, D04 1, D07 1, D08 2, D09 1, D11 1
(=7). Zero-selection cases: D01, D02, D05, D06, D10, D12 — all cases whose gold
is inside entity-range candidates selected as narrow sub-units/windows (F4/F5).

Resources: ≤6 calls, ≤32 files, pack ≤24 KiB, delivered ≤26.9 KiB (<64 KiB),
hash verification on every span, provenance + typed gaps + abstention intact.
Task correctness **NOT_MEASURED**. Dev-split development result, no
generalization claim.

## Verdict

**PARTIAL.**
- **Product improvement: MET** — the plan arm now beats the no-plan arm
  (0.4444 > 0.3194) and the fair baseline (0.1667) under the same contract,
  with the improvement attributed to a single, isolated change (E1 call
  rebalancing).
- **Intermediate target: NOT MET** — selected 7/25 < 10/25.
- **RAG MVP GO: NOT MET** — 0.4444 < 0.5722, though the policy oracle
  (0.8472 over the reachable pool) shows the target is *not* budget-infeasible.

## Root-cause map (verified, replaces earlier attributions)

| Cause | Status |
|---|---|
| Call-budget crowding between route families (F1/F3) | **fixed by E1** (+0.24 macro) |
| Per-file cap / breadth / 24 KiB budget | **not binding** (F2) — closed as a cause |
| Same-file sub-unit ordering has no signal (F5) | open — the remaining selection loss |
| Whole-method matches bypass the compaction cap (F4) | open — but trimming hurts more than it helps (E3 falsified) |
| Zero-pool gold (D02 CoreDBContext/Employee/EmployeeDetail) | retrieval: no route ranks them; needs entity-aware acquisition or a plan with corpus access |

## One next step (proposal, not started)

The remaining 11/25 unreachable-to-pack gold splits cleanly:
- **5 spans** (D02's 3 EmployeeCommand methods + D05 + D01's EmployeeEdit) sit
  in *pooled* entity ranges but the selector picks the wrong sub-unit. A
  **planner with corpus access** — allowed to run its own searches while
  authoring the plan, still blind to gold — is the evidence-backed way to get a
  real ranking signal, because every blind heuristic tried so far
  (identifiers, symbols, plan terms, size) has failed.
- **6 spans** (D02 CoreDBContext ×2, Employee, EmployeeDetail, D10 CoreDBContext,
  D12 sp_UpdateOrgTotalEmployee) are never pooled at all: entity-aware
  acquisition (DbSet/SaveChanges traversal from R2/R3 facts) or the same
  corpus-access planner.
If neither moves selected ≥10/25, the pack metric itself (10 items, 24 KiB)
needs an owner decision, because the oracle shows the information is present.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
R4B_DECISIONS=1 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow --test-threads=1
```

Receipts: `benchmarks/rag-impact/results/r4b-paired-20261116.json`,
per-candidate trace `r4-evidence-trace-20261116.json`.
