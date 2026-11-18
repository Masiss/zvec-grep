# R4 interactive agent discovery & typed acquisition — Gates A/B/C

Branch `work/r4-interactive-20261118` (from `a08a5aa`). Frozen corpus/index,
frozen E3 plan; 25 workflow tests pass; citation 1.000 everywhere; all budgets
within the R0 contract.

## Gate A — reconciled 25-span ledger (E1 plan arm, single run, receipt-backed)

| Status | Spans | Cases |
|---|---:|---|
| **Delivered (selected + verified, covering gold)** | 7 | D03, D04, D07(EmployeeCommand), D08×2, D09, D11 |
| Pooled, **wrong sub-unit chosen** (entity range selected, compact landed on a sibling method) | 5 | D02×2, D06×2, D12 |
| Pooled, **unexamined** (ordering: pack filled first) | 6 | D01×2, D05, D07×2, D10 |
| **Never pooled** (acquisition) | 7 | D02 CoreDBContext×2/Employee/EmployeeDetail, D06 OrganizationCommand, D10 CoreDBContext, D12 sp_UpdateOrgTotalEmployee |

This reconciles the earlier "5 pooled + 6 retrieval" split (which under-counted:
the ledger shows 5 wrong-sub-unit + 6 unexamined + 7 not-in-pool = 18 missing).
Union pool 19/25 remains a multi-arm ceiling, not a single-run pool: within
≤6 calls the reachable single-run pool is 18/25 (E1) and the binding choice is
which route family gets the calls.

## Gate B — two directions

### Direction 1 — interactive external-agent discovery: **BLOCKED**
An interactive planner must read corpus search results to adapt its queries.
Those results are private source, and routing them through the model provider
requires owner permission that this mission does not grant. Per the stop rules
this is reported as **BLOCKED**, not simulated. The blind plan stays frozen at
`r4b-blind-plan-20261117.json` (sha256 `e8a33dde…`).

### Direction 2 — typed entity acquisition: **IMPROVED, kept**
The expand stage's typed traversal (R2/R3 facts: DbSet element types, invocation
targets, `SaveChanges` contexts, SQL definition names) already existed but
never received a call under the 6-call budget. Rebalancing one call from the FTS
seed (whose D11 gold also carries a lexical rank-0 candidate) to the typed
symbol lookup:

| Arm | Macro R@10 | Pool | Selected |
|---|---:|---:|---:|
| E1 baseline (`a08a5aa`) | 0.4444 | 18/25 | 7/25 |
| **E1 + typed expand call** | **0.4861** | 18/25 | **8/25** |

Isolated variable: only `use_fts=false` on the plan arm; the no-plan arm is
unchanged at 0.3194. D11 retained (hybrid+lexical), D01 gained one gold span via
the typed lookup. D02/D10/D12 acquisitions need 2–3 more typed calls than the
budget allows — that is cannibalization, not engineering, so it stops here.

### Selection-side experiments this mission (3, all falsified or no-op)
1. Depth pass prefers already-represented files — no-op (pass 0 alone filled
   all 10 items).
2. Breadth pass reserves 3 depth slots — no-op (byte-starved, not slot-starved:
   D01's noise span takes 13.4 KiB = 55 % of the pack, D06's 15.9 KiB = 66 %).
3. Fair-share cap binds a subject's *first* span — regression (0.4028/0.2778):
   some large spans are the gold carriers; capping them deletes evidence.

## Gate C — end-to-end qualification (locked contract, single run)

| Arm | Macro R@10 | Pool | Selected | Delivered |
|---|---:|---:|---:|---|
| hybrid snippets ≤24 KiB | 0.1667 | 2/25 | – | ≤24 KiB |
| R4b no plan | 0.3194 | 12/25 | 6/25 | ≤26.9 KiB |
| E1 plan arm | 0.4444 | 18/25 | 7/25 | ≤26.9 KiB |
| **E1 + typed acquisition** | **0.4861** | 18/25 | **8/25** | ≤26.9 KiB |
| oracle (policy-respecting) | 0.8472 | 19/25 | 19/25 | – |
| **GO target** | **0.5722** | | | |

All runs: ≤6 calls, ≤3 rounds, ≤32 files, pack ≤24 KiB, delivered ≤64 KiB,
hash-verified, provenance + typed gaps + abstention intact. Per-case selected
gold (best arm): D01 1, D03 1, D04 1, D07 1, D08 2, D09 1, D11 1 = 8.
Task correctness **NOT_MEASURED** (no grader; not frozen in this mission).
Dev-split development result only.

## Verdict

**PARTIAL.**
- Improvement gate: **MET** — 0.4861 > 0.4444 > 0.3194 > 0.1667 under the same
  contract, with the gain attributed to one isolated change (typed expand call).
- Intermediate (more selected gold): **MET** 7 → 8/25, though still below the
  historical 10/25 bar.
- RAG MVP GO: **NOT MET** — 0.4861 < 0.5722, and the remaining gap needs
  capabilities this mission cannot authorize.

## What actually blocks GO now (measured)

1. **Wrong-sub-unit (5 spans):** choosing the gold method among same-file
   siblings requires semantic understanding of method bodies vs the question.
   Every blind signal tried (identifiers, symbols, plan terms, size, width) has
   failed; trimming/capping large spans measurably destroys gold coverage.
2. **Acquisition (7 spans):** reachable via typed traversal but needs 2–3 more
   calls than the ≤6-call contract allows without cannibalizing proven gold
   (D09/D10/D11 lexical, D02/D05/D12 plan-hybrid).
3. **Noise spans (D01/D06):** the hybrid route's own top hits are 13–16 KiB
   whole-method noise; the pack spends half its bytes on them, and every
   attempted cap regressed because some large spans carry gold.
4. **Task correctness** remains unmeasured; GO is unreachable in principle
   without a grader.

## Owner decisions required (next step, not started)

1. **Permission for an interactive planner with corpus access** (local CLI only,
   blind to gold, traces frozen+hashed). This is the single evidence-backed path
   to the wrong-sub-unit and acquisition spans; everything blind has been tried.
2. Alternatively/additionally: authorize a **held-out-quality second corpus**
   and an embedding model evaluation for method-level ranking — a change to
   index/model identity that this mission may not make.
3. Freeze a **task-correctness grader** before any MVP claim; citation accuracy
   1.000 is not correctness.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
R4B_DECISIONS=1 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow --test-threads=1
```

Receipts: `benchmarks/rag-impact/results/r4b-paired-20261116.json`,
`r4-evidence-trace-20261116.json`.
