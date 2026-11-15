# RAG-S1 Selection experiments and failure attribution — 2026-11-15

Status: **NO-GO.** Three bounded Selection hypotheses were evaluated against
the frozen candidate pool. The budget and provenance work is sound; the
runtime Selection signal is not sufficient.

## 1. Frozen hypotheses and gates

Gates fixed before the final runs: Recall@10 ≥0.60, strict ≥0.30, selected
spans ≥16/25, per-case tokens ≤3,716, deterministic, no gold leakage, valid
source ranges, and no serious multi-hop regression. Held-out H01–H08 was run
only after the policy was frozen.

| ID | Hypothesis | Result |
|---|---|---|
| S1-H1 | Exact structural identifiers alone recover precise evidence at low cost | **INCONCLUSIVE**: 6/25 dev, .361/.319, 766 tokens; 7/20 held-out |
| S1-H2 | Member/block structural scoring adds recall over exact matching | **NO-GO**: same 6/25 dev with 2,691 tokens; held-out 9/20, .625/.500 |
| S1-H3 | Real-token budget packing preserves recall while enforcing 3,716 tokens | **PASS cost / FAIL recall**: max 3,674 dev; oracle loses 1 span to trimming |

No held-out tuning was performed. Held-out was not used to choose weights.

## 2. Candidate and selection behaviour

The structural pool can cover **16/25** dev spans with ≤10 units (oracle). The
deployable exact and structural policies both select **6/25**; the oracle,
also constrained to 3,716 tokens, selects 15/25. The runtime gap is **10
spans** (16 ceiling − 6 selected).

| Case | Gold | Selected | Ceiling | Attribution |
|---|---:|---:|---:|---|
| D01 | 2 | 0 | 2 | 2 candidates ranked out (method selected, not controller) |
| D02 | 6 | 0 | 2 | 2 no runtime signal; 3 not in pool; 1 not locatable |
| D03 | 1 | 0 | 1 | 1 ranked out |
| D04 | 1 | 1 | 1 | selected strict |
| D05 | 1 | 0 | 1 | 1 no runtime signal |
| D06 | 3 | 0 | 2 | 2 ranked out; 1 no runtime signal |
| D07 | 3 | 1 | 1 | selected strict; 1 not in pool; 1 not locatable |
| D08 | 2 | 2 | 2 | selected strict + container-only |
| D09 | 1 | 1 | 1 | selected strict |
| D10 | 2 | 0 | 1 | 1 ranked out; 1 not in pool |
| D11 | 1 | 1 | 1 | selected strict |
| D12 | 2 | 0 | 1 | 1 no runtime signal; 1 not in pool |

Totals: **5 strict**, **1 container-only**, **6 ranked out**, **5 no runtime
signal**, **6 not in pool**, **2 not locatable**. The 8 spans not in the pool
or not locatable are `NOT_SELECTABLE` by this Selection unit, not hidden
successes.

The six selected spans are all structurally named or exact-symbol cases:

- `EmployeeUpdateInfo` (D04), `RestoreEmployeeById` (D07),
  `DeleteEmployeeByIds` in service + controller (D08),
  `EmployeeBaseViews.sql` (D09), `UpdateEmployeeAttr` (D11).

The six ranked-out spans have a locatable declaration but lose to unrelated
methods with higher lexical/class scores: D01 controller/service, D03 service,
D06 service ×2, D10 `CoreService`. The five no-signal spans are semantic or
cross-language (personal update, detail/atomicity, “cập nhật tổng nhân sự”,
“Proc tên này”) where no gold-free query term identifies the right declaration.

## 3. S1-H1 — exact-symbol selection

Policy: select declarations whose normalized symbol/path tokens contain a code
token from the question; no member expansion, no rg. Ranking by exact match,
then engine rank, with source provenance and token budget.

Result (dev): **6/25**, Recall@10 **0.3611**, strict **0.3194**, 766 tokens mean,
max 3,389; source reads 249 files / ~3.0 MB. Held-out: **7/20**, 0.5313/0.4063,
1,053 tokens mean, max 3,360.

Verdict: **INCONCLUSIVE**. Precision is good (5 of 6 selected are strict), but
coverage is far below the gate. It is a useful conservative fallback, not a
qualified Selection layer.

## 4. S1-H2 — structural member/block selection

Policy: expand class items into direct methods/properties, split long methods
at blank-line statement blocks, score by TF-IDF query overlap in name/body,
class context, engine rank and size; dedupe; cap 10; enforce token budget.

Result (dev): **6/25**, 0.3611/0.3194, 2,691 tokens mean, max 3,674; source
reads same 249 files. Held-out: **9/20**, 0.6250/0.5000, 2,954 tokens mean, max
3,675.

Verdict: **NO-GO**. Structural expansion adds **no dev recall** over exact
matching and costs ~3.5× the tokens. Held-out improvement is not evidence for
qualification because dev fails the same pre-registered gate. Blank-line blocks
help strictness on some spans but do not resolve the file/method ambiguity.

## 5. S1-H3 — budget-aware packing

The prototype counts the packed headers + source lines with the frozen
tokenizer (truncation disabled) and drops lowest-ranked units until each pack
is ≤3,716 tokens.

- Exact: max 3,389 dev / 3,360 held-out; no budget failures.
- Structural: max 3,674 dev / 3,675 held-out; no budget failures.
- Oracle: 16/25 ceiling becomes **15/25** selected after budget.
- Baseline fixed FTS: 8,049 tokens mean, max 28,426; 6/12 dev packs exceed the
  budget and are not a valid qualified baseline.

Verdict: **PASS for cost enforcement, FAIL for quality**. Budgeting is not the
bottleneck; the unselected spans are not lost to token limits.

## 6. Verification and negative controls

Independent verifier: `verify_selection_s1_20261115.py`.

- Recomputed Recall@10/strict from receipt ranges and gold; PASS for exact and
  structural, dev and held-out.
- Every selected range is inside a frozen candidate-pool range and inside the
  source file; per-file SHA-256 checked against the case references.
- Actual tokenizer counts match receipt `tokens`; per-case budget checked.
- Determinism: rerun hash identical (`65bf94e2…`).
- Gold leakage: sanitized dev cases produce identical selected units.
- Negative controls rejected: fabricated range outside the pool, duplicate
  unit, wrong path, outside-budget/tampered token count, and source-identity
  mutation.
- Held-out isolation: separate probe/cases/receipts; no D/H mixing.

## 7. Root cause

1. **Selection signal, not candidate supply, is dominant.** 11/25 dev spans are
   locatable in the pool but not selected; 6 are ranked out, 5 have no runtime
   term that identifies them.
2. **Exact identifiers solve only a narrow slice.** D04/D07/D08/D09/D11 are
   selected; these are named entities with matching query tokens or SQL files.
3. **Semantic/cross-language and cross-file cases remain unsolved.**
   “Personal update”, “detail atomicity”, “cập nhật tổng nhân sự”, and “Proc
   tên này” have no gold-free lexical bridge to the right method in the current
   metadata. The DbContext/SQL spans are not in the pool at all.
4. **Structural expansion increases cost without enough gain.** Blocks and
   members are correct in form but not discriminative enough; a larger context
   pack does not repair rank ambiguity.
5. **The 16/25 ceiling is not reachable at runtime without a new signal**
   (semantic query expansion, references/call graph, SQL binding or an agent
   loop). Those are outside S1 and remain frozen.

## 8. Disposition

- Selection production remains frozen.
- No Discovery/Expansion/R1 implementation was opened.
- The only reusable pieces are the exact-symbol fallback, the provenance/token
  verifier, and the failure map.
- A production Selection plan must first solve the 11 locatable-but-unselected
  spans without gold; otherwise it cannot reach 16/25, let alone 0.60.
