# RAG R4 documentation index (updated 2026-11-20)

**Current handoff:** [`r4-external-agent-handoff-20261120.md`](r4-external-agent-handoff-20261120.md).
**2026-11-20 adapter milestone:** the private experimental adapter for the
three-arm comparison is implemented and contract-qualified with end-to-end
smoke on the frozen corpus; see
[`r4-external-agent-adapter-20261120.md`](r4-external-agent-adapter-20261120.md).
**2026-11-20 technical audit:** concurrency, crash, accounting and isolation
verified; one CRITICAL budget race and one HIGH verify-reservation defect
fixed and pinned (29/29 contract tests); mediated answerer driver with
negative controls; review bundle issued. See
[`r4-adapter-audit-20261120.md`](r4-adapter-audit-20261120.md). Verdict:
**READY_FOR_BENCHMARK** (harness/accounting) with Gate B still blocked on
private-source egress authorization and Gate C NOT_MEASURED.
It records the active branch/HEAD, model and dataset identity, retained receipts,
blockers, and the exact safe next action. The 2026-11-18 handoff is historical;
its method-level-index proposal is not current authorization.

**Current architecture gate:** Gate A PASS; Gate B **OPERATION_BLOCKED** until
an authorized, private-source-compatible inference environment and identifiable
model/config are available; Gate C **NOT_MEASURED**. The frozen C3 D01–D12
required-step addendum is [`r4-task-correctness-c3-checklist-20261120.md`](r4-task-correctness-c3-checklist-20261120.md)
(SHA-256 `2bd4c63dff8b6638cad7db72a37ceeaaa6fd1031acf5f0cd15e3ed92ae8751bb`).
The architecture audit is
[`r4-external-agent-architecture-validation-20261120.md`](r4-external-agent-architecture-validation-20261120.md).
Neither file reports an answer-quality result.

## Current retrieval result

The corrected 2026-11-19 representation baseline/valid E2/E3 result is **PARTIAL
/ NO-GO**: macro **0.4861**, selected **8/25**, below GO **0.5722**. The valid
contained pool is **12/25**; the older 19/25 parent-range-overlap figure is not
raw-fragment reachability. Three representation hypotheses did not meet their
frozen acceptance. No new method-vector/index rebuild is authorized by the
current architecture mission. See the results and amendments for exact metric
definitions, attribution and receipts.

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
| `r4-deep-optimization-20261118.md` | historical analysis: two hypotheses falsified; declaration-range representation was identified as a deficit |

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

## What is established (with scope)

- The frozen index was verified against the audited cases; representation runs
  are retrieval evidence, not answer-quality evidence.
- The fair bounded hybrid baseline is **0.2500**. Do not compare it with the
  raw/unbounded 0.5000 retrieval reference as though budgets matched.
- The valid E2 workflow run used explicit limits. `WorkflowBudget::default()`
  is 12 calls / 64 source reads, so a future R0 run must pass its stricter
  cumulative budget explicitly.
- The 2026-11-20 static audit found one embedding model configured for R4 and no
  in-engine generative model; the direct MCP agent surface still lacks symbols
  and typed relations.

## Current blockers and constraints

1. **Gate B inference environment:** no privacy-approved, identifiable model
   runtime was found. Do not send private source, prompts containing source
   identifiers, packs or answers to an unapproved remote provider.
2. **Gate C answer quality:** C3 required-step lists are now frozen, but no
   answerer or independent grader has run; correctness remains NOT_MEASURED.
3. **No current authorization for implementation/optimization:** do not resume
   method-level index work, R4 heuristic tuning, model changes or public MCP/API
   exposure under this handoff. The older interactive planner with corpus
   access remains blocked because it would expose private source.
4. **Next action:** wait for an explicitly permitted private-source-compatible
   inference environment with model/version/config available for recording.
   After that, preregister a same-model paired comparison for Options 1–3 and
   enforce the cumulative session budget; this handoff alone is not permission
   to run a new inference/benchmark operation.
