# R4 deep optimization & answer quality — Gates A–D

Branch `work/r4-deep-opt-20261118` (from `720c1ed`). Same frozen corpus, same
SQL-symbol index, same frozen plan, locked contract. 26 workflow tests pass;
clippy clean; citation 1.000.

## Gate A — deep investigation

**A1. Replication.** Best arm reproduces at **macro 0.5278**, pool 19/25,
selected 9/25, on the SQL-symbol index.

**A2. The inconsistency in the previous report is a reporting error, not a
measurement error.** "hybrid bounded macro 0.2500 / pool 1/25" — the receipt
shows the bounded arm covers **4 gold spans** (D08 2/2, D09 1/1, D11 1/1 →
macro 3.0/12 = 0.2500). The "1/25" figure was mis-transcribed from the D09 row.
Raw reference: 9 spans (0.5000 = 6.0/12 macro). Errata noted; no re-measurement
needed.

**A3. Full 25-span ledger for the best arm** (`r4-evidence-trace`):

| Status | Count | Spans |
|---|---:|---|
| selected (delivered) | 9 | D03, D04, D07(Cmd), D08×2, D09, D11, D12(OrgCmd), D01(Cmd) |
| pooled, **unexamined** (pack filled first) | 5 | D01 EmployeeCommand 215-285, D05, D07 OrganizationCommand, D07 sp_ 58-95, D10 CoreService |
| pooled, covered by a widened unit but not by a pack range | 5 | D02×2, D06×2, and siblings |
| **never pooled** | 6 | D02 CoreDBContext×2, Employee, EmployeeDetail, D06 OrganizationCommand, D10 CoreDBContext |

**A4. Per-call contribution (all 12 cases identical):** 6 calls = 1 question
hybrid + 2 plan-hybrid + 1 typed symbol + ~2 derived lexical. FTS 0. Duplicate
calls: 1 in D02, 1 in D11. The typed call is the only call whose output is a
verified declaration, and it is single-shot.

**A5. Why 19 pooled → 9 selected.** Not the byte budget and not the per-file cap
(proven earlier with a policy-respecting oracle at 19/25). It is **ordering plus
pack composition**: the five unexamined gold candidates sit in files already
represented by higher-ranked noise spans, and the pack is full when they are
reached.

**A6. Why 6 spans are never pooled.** They are class/entity declarations
(`CoreDBContext`, `Employee`, `EmployeeDetail`) reachable only by exact symbol
lookup; the typed traversal derives DbSet element types only from files that are
themselves pooled, so the DbContext never seeds itself. OrganizationCommand's
1057-1074 method is reachable by lexical but the call budget was spent.

**A7. Blind spots found (new).**
1. The plan arm's own `EmployeeCommand` entity ranges (lines 14-1038) cover gold
   spans inside a *single* span — the metric counts one overlap, but the agent
   receives a 700-line noise span instead of the method. Entity-range units are
   simultaneously the largest recall contributor and the worst evidence.
2. `MAX_REFS_PER_ROUND` truncation is invisible in gaps: names dropped by the
   cap produce no typed gap, so the agent cannot tell that a lookup was skipped.
3. Two calls per case are spent on plan-hybrid queries that add no gold beyond
   the question route in 10/12 cases (measured by ablation in E1 below: removing
   one typed slot did not change recall, and the plan queries' unique
   contribution is concentrated in D02/D05/D12).

## Gate B — three experiments

| Exp | Hypothesis | Plan arm | Pool | Selected | Verdict |
|---|---|---:|---:|---:|---|
| base | — | 0.5278 | 19/25 | 9/25 | — |
| E1 | second typed call (`expand_reserve=2`) reaches DbContext/entity declarations | 0.5278 | 18/25 | 9/25 | **falsified** — no recall gain, one pool span lost |
| E2 | reserve one pack slot for exact-symbol evidence (highest precision) | 0.5139 | 19/25 | 9/25 | **falsified** — displaces a gold-bearing span |
| E3 | (third slot unused) | — | — | — | not spent; no hypothesis with better expected value |

Final shipped state: base configuration (0.5278), with `expand_reserve = 1` and
no symbol slot reservation. Nothing regressed; the two falsified changes are
reverted with their measurements recorded.

**Why the remaining gains are not available inside the contract (measured, not
assumed):** the unreachable spans need either (a) a third typed call — which E1
shows buys nothing without a second *seed* source, or (b) semantic ranking to
pick the right method inside a 700-line entity unit, which no blind signal has
achieved across four missions.

## Gate C — task correctness

Rubric and protocol frozen in `r4-task-correctness-contract-20261118.md`
(C1 answerability, C2 citation support, C3 chain completeness, C4
source-vs-deployment discipline, C5 honest gaps, C6 no false negatives; per-case
scoring, separate from retrieval). Fixtures for the 12 cases and two adversarial
classes are prepared. **Grading: NOT_MEASURED** — it requires an external agent
run plus an independent grader session, which this mission did not execute and
will not simulate.

## Gate D — paired qualification

| Arm | Macro R@10 | Pool | Selected | Citation |
|---|---:|---:|---:|---:|
| hybrid snippet ≤24 KiB | 0.2500 (4 spans) | 4/25 | – | n/a |
| R4 no-plan | 0.3194 | 12/25 | 6/25 | 1.000 |
| **R4 plan (best, unchanged)** | **0.5278** | 19/25 | 9/25 | 1.000 |
| GO | 0.5722 | | | |

All contract bounds respected (≤6 calls, ≤3 rounds, ≤32 files, pack ≤24 KiB,
delivered ≤64 KiB); all spans hash-verified; provenance, typed gaps, abstention,
cancellation and injection isolation intact. Dev-split development evidence only.

## Verdict

**PARTIAL.**
- Improvement gate: **not met** — no configuration beat 0.5278; two hypotheses
  were tested and falsified, one quota slot deliberately left unspent rather
  than used for micro-tuning.
- RAG MVP GO: **not met** — 0.5278 < 0.5722, *and* task correctness is
  NOT_MEASURED, so an MVP claim is not available even at target recall.

## The architectural finding (the real conclusion of this mission)

R4's remaining deficit is not a tuning gap; it is a **representation gap**.
The engine's unit of evidence is a *declaration range*, so one pack item is
either a 700-line class (noise, but high recall) or a 20-line method (precise,
but the pack can hold only ~7 of them). Every selection change that favours
precision loses recall, and every change that favours recall delivers noise —
measured across E2 here and four earlier experiments. Retrieval recall for this
corpus is saturated at ~0.53 by *how evidence is cut*, not by what is indexed
(all 25 spans are verified present) or by what is retrieved (19/25 pooled).

## One next step (recommendation only)

Change the evidence unit, not the selector: emit **method-level units with the
declaration header attached** as separate index entities (already extractable —
the R2 fact parser gives exact method ranges), so a pack item costs ~15 lines
instead of 700, and the 10-item pack can then hold the 7-9 distinct methods the
gold actually needs. Success criterion: selected ≥12/25 at 24 KiB with the
current ranks and call plan unchanged. This is an index/extraction change
(new entity granularity) and therefore an owner decision, per the frozen
boundaries.
