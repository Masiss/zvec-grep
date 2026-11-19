# RAG R4 documentation index (updated 2026-11-19)

For the latest evidence-representation result, start with
[`r4-evidence-representation-results-20261119.md`](r4-evidence-representation-results-20261119.md).
[`../../HANDOFF-20261118-NEXT.md`](../../HANDOFF-20261118-NEXT.md) remains the
historical handoff for the preceding R4 line. This index maps the documents and
preserves the older results as historical evidence.

## Current investigation status

The 2026-11-19 representation investigation is **PARTIAL / NO-GO** under its
frozen gate: corrected plan macro **0.4861**, pool **12/25 fragment-overlap**,
selected **8/25**; GO remains **0.5722** and correctness **NOT_MEASURED**.
Three preregistered implementation hypotheses did not meet acceptance. The
index is verified; no new method-vector/index rebuild is recommended. See the
linked report for attribution, receipts, and remaining call-allocation blocker.

## Historical best result (pre-32-file correction)

The prior reported **0.5278** (pool 19/25, selected 9/25, citation 1.000) remains
a historical quality landmark, not qualified under the later ≤32 distinct
source-read gate. The frozen dev split's GO threshold is **0.5722** and the
fair 24 KiB baseline is **0.2500**. Task correctness remains **NOT_MEASURED**;
that result was on branch `work/r4-deep-opt-20261118` @ `7030bd7`.

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
| `r4-evidence-representation-results-20261119.md` | fragment/context/typed-reference investigation; E1–E3 | **0.4861; no GO** |

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
- The bottleneck is multi-stage: broad parent ranges inflate candidate
  coverage, fragment projection narrows the pool, and the six-call allocation
  constrains typed cross-file acquisition. This mission did **not** establish
  that adding finer index units would improve selected evidence.

## What is open

1. **Call allocation and candidate anchors** — current evidence points above
   the index layer. Any work beyond the three-experiment representation mission
   needs a new scope and preregistration; do not raise the six-call cap silently.
2. **Task correctness run** — rubric frozen; requires C3 required-step lists,
   a privacy-valid independent answerer, and a separate grader. It remains
   NOT_MEASURED/BLOCKED.
3. **PLANNED/BLOCKED**: interactive planner with corpus access (private-source
   exposure), and any embedding-model change (index/model identity).
