# RAG evidence relevance and grounding — architecture / decision record

Status: **experimental candidate. `MISSION_PARTIAL`.** No production retrieval,
selection, daemon, index or binary was changed.

## 1. Result in one page

The previous reference mission showed that candidate coverage can be raised to
22/25 dev and 20/20 held-out, but the selector admitted 90–92% non-gold units.
This mission adds:

1. **Bounded method → SQL binding** with `VERIFIED` / `AMBIGUOUS` / `UNRESOLVED`
   states and provenance.
2. **Task-conditioned evidence grounding** that emits only control, wrapper and
   SQL/VIEW evidence for symbol/procedure questions, with refusal on unknown
   symbols.
3. A **small source-grounded evidence-role evaluation** and an independent
   verifier.

On the authored fixtures, the prototype hits all 9/9 required groups on both
dev and held-out, with 0 misleading admissions and correct abstention on 6/6
and 6/6 cases. The three adversarial near-name probes also abstain correctly.

These numbers are **fixture-level**, not production generalization. The fixture
set is small and manually authored by the implementer, and the policy was
developed against the dev half. The verdict therefore remains partial.

## 2. SQL binding

### States

| State | Meaning |
|---|---|
| `VERIFIED` | Method body contains an explicit `execute/exec sp_*` for one resolvable SQL definition, or a wrapper chain is unique at every step and ends in such a direct binding. |
| `AMBIGUOUS` | Multiple declarations/definitions, or a method-name-only match; never promoted. |
| `UNRESOLVED` | Explicit SQL text but no matching definition. |

### Scope resolution

A call target is resolved by **same type scope**, then **same namespace**, before
falling back to a global name. This is necessary because `CoreService` is a
partial class spread across files and namespaces; a global name match would
wrongly make `RestoreEmployeeById → UpdateOrgTotalEmployee` ambiguous.

### Counts

| Binding kind | Count |
|---|---:|
| Direct explicit | 1 |
| Wrapper chain | 14 |
| Name-only (ambiguous) | 2 |
| **VERIFIED** | **15** |
| **AMBIGUOUS** | **2** |
| **UNRESOLVED** | **0** |

Key verified chain:

```
RestoreEmployeeById (EmployeeCommand 550–560)
  → UpdateOrgTotalEmployee (OrganizationCommand 1057–1073)
    → "execute sp_UpdateOrgTotalEmployee"
      → DB/Store/sp_UpdateOrgTotalEmployee.sql 1–103
```

`DeleteEmployeeByIds` has no SQL binding: its body contains an inline
`ExecuteSqlCommand` update, not a procedure. This is correctly represented as
“no binding”, not “abstain from the whole question”.

Name-only matches (method name equals procedure name) remain **AMBIGUOUS** by
design; the mission explicitly forbids treating them as verified.

## 3. Grounding policy

Inputs: question, frozen corpus, independent binding artifact. Labels/required
groups are not inputs; a sanitized run produces identical units.

Pipeline:

1. Extract code tokens and a small intent lexicon
   (`write/read/sql/flow`).
2. Find exact method/type/SQL names mentioned by the question.
3. For an exact method:
   - add the method body (or `SaveChanges` windows / blocks when long);
   - if the question asks about SQL/procedure/table and the method has verified
     bindings, add wrapper-chain intermediates and the SQL definition;
   - if the question is negative (“Does X invoke Y?”) and no verified binding
     exists, return the method without the unrelated SQL.
4. For an exact SQL name, add the SQL definition and **direct** verified
   invokers only; indirect callers are not required evidence.
5. For an exact view, add only the view definition.
6. Unknown SQL/method near-names produce no candidates and `abstain=true`.
7. Pack ≤4 units, ≤3000 tokens; no generic header/entity expansion.

The policy intentionally does **not** reuse the reference selector's broad
header/entity expansion. That expansion caused the prior 90–92% non-gold rate.

## 4. Evidence roles at runtime

The runtime emits provenance roles, not evaluation labels:

| Runtime role | Source |
|---|---|
| `CONTROL` | exact method/body or write window |
| `WRAPPER` | verified binding source/intermediate |
| `SQL` | procedure definition |
| `VIEW` | view definition |
| `DATA` | resolved property/entity target (rarely emitted) |

Evaluation labels (`REQUIRED`, `SUPPORTING`, `MISLEADING`, etc.) are separate
and are never read by the policy.

## 5. Evaluation result

| Metric | Dev | Held-out | Adversarial |
|---|---:|---:|---:|
| Cases | 6 | 6 | 3 |
| Required groups hit | **9/9** | **9/9** | — |
| Required units hit (≥50% coverage) | **9/9** | **9/9** | — |
| Role precision | **1.00** | **1.00** | — |
| Misleading admissions | **0** | **0** | 0 |
| Correct abstention | 6/6 | 6/6 | **3/3** |
| False abstention | 0 | 0 | 0 |
| Tokens total / mean | 4,525 / 754 | 2,634 / 439 | 0 |
| Wall / peak RSS | 0.70 s / 50.7 MB | 0.73 s / 50.6 MB | ~ |

Naive exact-symbol baseline on the same fixtures:

| Metric | Dev | Held-out |
|---|---:|---:|
| Required-group recall | 0.444 (4/9) | 0.556 (5/9) |
| Role precision | 0.667 | 0.714 |
| Unknown selected | 2 | 2 |
| False abstention | 1 | 0 |

The binding-aware prototype improves required completeness and removes unknown
units on these fixtures. This is evidence that the **specific missing
capabilities** matter; it is not proof of generalization.

## 6. What failed / remains unproven

- **Semantic labels are authored, not independently adjudicated.** The verifier
  checks source/range/provenance, not whether each role is semantically ideal.
- **Small, same-corpus fixtures.** Six dev and six held-out cases; all C#/SQL;
  no non-C# representative data.
- **No claim generation**; claim-to-evidence support and false acceptance were
  not measured.
- **No dynamic SQL, deployment authority, conflicting live/running definitions,
  or non-C# domains.**
- **Held-out is not a clean generalization gate**; it is new relative to the
  S1/S1b held-out, but the implementer authored it.
- **Token budget and role thresholds are fixture-tuned**; a production gate
  would need a larger adjudicated set.
- **SQL binding covers one procedure**; wrapper resolution is scoped but not a
  full C# semantic resolver.

## 7. Decision ledger

| ID | Hypothesis | Evidence | Verdict |
|---|---|---|---|
| H-E1 | Explicit + scoped wrapper chain can produce verified method→SQL bindings | 1 direct + 14 wrapper verified; name-only remain ambiguous | **PASS (bounded)** |
| H-E2 | Role/necessity filtering removes header/entity noise while retaining required evidence | 9/9 groups on dev/held-out; 0 misleading | **PASS on authored fixtures** |
| H-E3 | Unknown near-names trigger correct abstention | 3/3 adversarial abstain; 0 false answer | **PASS on probes** |
| H-E4 | This generalizes to independent, larger, non-C# data | no such evaluation | **UNPROVEN** |
| H-E5 | Naive exact-symbol selection is sufficient | baseline 4/9 and 5/9 groups; unknown units | **REJECT** |
| H-E6 | Claim support/false acceptance is controlled | no claim generation | **NOT EVALUATED** |

## 8. Production implications

### Reusable now

- The binding model and state rules.
- The negative/unknown abstention rules.
- The evidence role/provenance schema.
- The independent verifier.

### Not approved

- Wiring this into production retrieval.
- Replacing the reference selector/ranking.
- Persisting SQL binding metadata in the engine index.
- Using these fixtures as a release gate.

### Migration / rollback

No production migration. The experimental sidecar is external JSON; rollback is
branch/artifact deletion. If integrated later, use a versioned sidecar with
rebuild-only activation and keep the v3 index rollback intact.

## 9. Owner decisions

| ID | Decision |
|---|---|
| **D-ER-1** | Accept `MISSION_PARTIAL`; keep production frozen. |
| **D-ER-2** | Authorize creation of a larger independently annotated evaluation set (including non-C#, dynamic SQL, conflicting evidence) before any production claim. |
| **D-ER-3** | Decide whether the binding sidecar and abstention policy should be engineered into the engine; not approved now. |
