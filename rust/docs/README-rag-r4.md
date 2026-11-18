# RAG R4 documentation index (as of 2026-11-18)

Start with [`../../HANDOFF-20261118-NEXT.md`](../../HANDOFF-20261118-NEXT.md) — it
holds current state, the reconciled metrics table, the falsified-hypothesis list
and the single next authorized action. This index only maps the documents.

## Current best result

**Macro Recall@10 = 0.5278** (pool 19/25, selected 9/25, citation 1.000) on the
frozen dev split, against GO **0.5722** and a fair 24 KiB baseline of **0.2500**.
Task correctness **NOT_MEASURED**. Branch `work/r4-deep-opt-20261118` @ `7030bd7`.

## Reading order

### 1. Measurement foundations (read these before trusting any number)
| Document | Establishes |
|---|---|
| `rag-audit-20261116.md` | forensic reconciliation of every historical metric + defects A1–A3 |
| `rag-measurement-integrity-20261116.md` | trace invariants, snippet-unit baseline, delivered-vs-internal byte accounting |
| `r4-index-truth-audit-20261118.md` | source→index→searchable verification (independent extractor, real-route read-back) |

### 2. Why R4 does not reach the target
| Document | Establishes |
|---|---|
| `r4-query-feasibility-and-selection-audit-20261115.md` | raw question 0/25 spans; hybrid 8/25; embedding / agent-plan / alias feasibility |
| `r4-selection-feasibility-20261118.md` | policy-respecting oracle 14/25 — the budget is not the wall |
| `r4-deep-investigation-20261118.md` | the two arms retrieve *complementary* gold; union 19/25; loss is ordering |
| `r4-deep-optimization-20261118.md` | **latest**: two hypotheses falsified; the deficit is a **representation** gap (declaration-range units) |

### 3. Implementation record (each with receipts)
| Document | Change | Measured effect |
|---|---|---|
| `r4-workflow-results-20261115.md` | first R4 loop | 0.3611 |
| `r4-discovery-results-20261115.md` | discovery routes, provenance, FTS quota | 0.2778 (NO-GO) |
| `r4b-hybrid-selection-results-20261116.md` | hybrid base + P1 selection | 0.2778 (NO-GO) |
| `r4-autonomous-investigation-20261116.md` | fair contract, compaction | 0.2778 → 0.3194 |
| `r4-evidence-preservation-20261116.md` | candidate trace, file-budget fix | 0.3194 |
| `r4-gateab-20261117.md` | contract-budget compliance; blind plan | baseline unchanged; plan NO-GO |
| `r4-interactive-typed-20261118.md` | call rebalancing + typed acquisition | 0.4444 → 0.4861 |
| `r4-sql-symbols-20261118.md` | SQL symbols via R3 parser (no schema change) | baseline 0.1667 → 0.2500 |
| `r4-index-utilization-20261118.md` | string-literal SQL objects as typed refs | **0.4861 → 0.5278** |

### 4. Contracts (frozen; do not edit)
| Document | Content |
|---|---|
| `../benchmarks/rag-impact/REPORT-CONTRACT.md` | citation rules, budgets (≤3 rounds/≤6 calls/≤32 files/≤24 KiB pack/≤64 KiB delivered), forbidden behaviour |
| `r4-task-correctness-contract-20261118.md` | C1–C6 rubric, per-case scoring, external-agent + separate-grader protocol; **grading NOT_MEASURED** |
| `r4-discovery-contract-20261115.md` | discovery route/provenance contract |
| `rag-r1-release-gates.md`, `rag-r4-bottleneck-and-r1-release-audit.md` | release gates and earlier bottleneck audit |

## What is proven

- The index is **correct**: all 25 gold spans are persisted and searchable
  (C#, method granularity; SQL via the new symbol branch). R4's misses are not
  index defects.
- The workflow is **inside the resource contract** at best-arm quality.
- The fair baseline is **0.2500**, not 0.4167 — the raw 0.5000 arm is unbounded
  and is a retrieval reference only.
- The binding limit is **evidence granularity**, measured across seven
  experiments: precision changes lose recall, recall changes deliver noise.

## What is open

1. **Index granularity change** (method-level units with declaration headers) —
   needs owner approval; success criterion pre-registered in the handoff §9.
2. **Task correctness run** — rubric frozen; requires an external agent plus a
   separate grader session, and the per-question required-step lists for C3.
3. **PLANNED/BLOCKED**: interactive planner with corpus access (private-source
   exposure), and any embedding-model change (index/model identity).
