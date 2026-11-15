# RAG-GS — Stabilization Verification & Readiness Report

**Verdict: `READY_FOR_INDEPENDENT_QUALIFICATION`.**
This is a readiness verdict, not a production or generalization qualification.
No GQ5 campaign was run in this mission.

## 1. Candidate identity

| Item | Value |
|---|---|
| Candidate | `typed-grounding-v1` |
| Candidate freeze | `8a26c91e67732707ef09c3a47cfd1b472e734d0885bdc88daa3ed09fc939660f` |
| Tool identity | `bafa862ee17c84fae2b9bf617ca690eb9e95b6de7e19ed41dc125ec703091e9f` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Corpus manifest | `d7052d3f…fc4a` |
| Bindings | `d8b40b0b…a8203` |
| Frozen | yes |

Frozen tools:

- `typed_resolution_20261115.py`
- `grounding_20261115.py`
- `evaluate_gq_v2_20261115.py`
- `typed_resolution_regression_20261115.py`
- `verify_gs_20261115.py`
- `test_eval_contract_20261115.py`
- `liveness_20261115.py`
- `refint_20261115.py`

## 2. Gate A regression

| Check | Result |
|---|---|
| Adversarial liveness tests | **PASS** |
| Gate A verifier | **PASS**, 96 checks |
| False commented `VERIFIED` binding | none |
| Binding states | 13 VERIFIED / 2 AMBIGUOUS / 0 UNRESOLVED |
| Direct / wrapper / name-only | 1 / 12 / 2 |
| Comment/string/preprocessor cases | PASS |

## 3. Typed regression foundation

`typed_resolution_regression_20261115.py`:

- **19 checks, 0 failures, PASS**
- Synthetic property/DbSet vs view exact identity;
- rename preserves entity kind;
- irrelevant sibling does not change target;
- comment/uncomment changes liveness;
- source drift changes indexed range;
- multi-role union preserves both roles/provenance;
- corpus property/view/unknown/method/negative controls.

These are regression fixtures, not independent qualification.

## 4. Evaluation contract tests

`test_eval_contract_20261115.py` — **5/5 PASS**:

- non-gold selection is `UNKNOWN`, not `MISLEADING`;
- wrong entity kind is separate from misleading;
- open-world negative claim is not accepted as safe refutation;
- explicit closed-world/method-body basis is accepted;
- negative-safety metric denominator is correct.

## 5. Before/after GQ4 regression

GQ4 is an observed historical/regression set, not independent generalization.

| Metric | Candidate v5 (before) | Typed candidate (after) |
|---|---:|---:|
| Dev required-group recall | 2/4 = 0.500 | **4/4 = 1.000** |
| Dev role precision | 0.667 | **1.000** |
| Dev unknown selections | 1 | **0** |
| Dev unsupported acceptance | 1 | **0** |
| Dev wrong entity kind | not separated | **0** |
| Dev negative safety | not proven | **1/1** |
| Held-out group recall | 1/1 | **1/1** |
| Held-out role precision | 1.000 | **1.000** |
| GQ4-01 property/type | view selected | **property + type** |
| GQ4-05 unknown SQL | SQL/wrappers selected | **abstain** |
| GQ4-03 negative | SQL/wrappers selected | **control only, local basis** |

### GQ4 v2 summary

| Split | Cases | Groups | Group recall | Role precision | Wrong kind | Misleading | Negative safety | Strict completeness |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Dev | 3 | 4 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 |
| Held-out | 3 | 1 | 1.000 | 1.000 | 0 | 0 | n/a | 1.000 |

### Other observed regressions

GQ/GQ2/GQ3 typed receipts also resolve required groups and roles at 1.0 on the
current rerun. Their older receipts lack the new negative-basis field, so they
are not used as negative-safety qualification; GQ4 is the final regression.

## 6. Independent verifier

`verify_gs_20261115.py`:

- **124 checks, 0 failures, PASS**
- frozen tool/binding/corpus/regression identities;
- source path and exact range existence;
- role/entity-kind/provenance/liveness fields;
- evaluation denominators and arithmetic;
- wrong-entity-kind and unknown separation;
- negative-basis scope and `dynamic_sql=False`;
- no gold/required-group runtime reference;
- deterministic typed resolver;
- liveness of all `VERIFIED` binding sources;
- binding targets exist.

## 7. Resource / determinism

| Split | Wall | Peak RSS | Units | Abstain |
|---|---:|---:|---:|---:|
| GQ4 dev | 1.17 s | 50.38 MiB | 4 | 0 |
| GQ4 held-out | 1.07 s | 50.85 MiB | 1 | 2 |

No token counter binary was available in this worktree; token cost is reported
as `tokens=0`/not measured, not silently estimated. Character/ranges and unit
counts are recorded.

## 8. Readiness criteria

| Criterion | State |
|---|---|
| Gate A correctness failures not recurring | PASS |
| Typed entity resolution on regression suite | PASS |
| Evidence roles/ambiguity contract | PASS |
| Evaluator semantics versioned | PASS (`gq-eval-v2`) |
| Independent verifier | PASS |
| Source identity, determinism, resources | PASS |
| Known critical correctness regression | none found |
| Candidate frozen | PASS |
| Independent qualification campaign | **NOT RUN** |

## 9. Capability and unsupported cases

Supported:

- exact C# type/property/method;
- `DbSet<T>` mapping by type relation;
- SQL procedure/view exact identity;
- comment/string/preprocessor liveness;
- verified direct/wrapper binding;
- local method-body negative basis;
- multi-role provenance.

Unsupported / not claimed:

- dynamic SQL execution authority;
- global absence proof;
- non-C# semantic parsing;
- production deployment proof;
- independent generalization;
- human-independent gold labels.

## 10. Proposed GQ5 independent qualification plan

Do not execute without owner authorization.

### Coverage matrix

| Area | Proposed cases |
|---|---|
| C# cross-file type/property/method | ≥3 |
| `DbSet<T>` property/type mapping | ≥2 |
| SQL procedure wrapper/direct | ≥2 |
| SQL view exact identity | ≥1 |
| commented/inactive/raw/interpolated | ≥3 |
| wrong entity kind / substring collision | ≥2 |
| unknown/no-answer | ≥2 |
| negative with explicit closed-world basis | ≥1 |
| negative with open-world/incomplete basis | ≥1 |
| source drift / stale range | ≥1 |
| multi-role overlap | ≥1 |
| Vietnamese + English queries | both |

Proposed split: at least **8 dev + 8 held-out** fresh cases, not copied from
GQ/GQ2/GQ3/GQ4, sourced from an unobserved area of the corpus or a new
source-only slice. Freeze cases, labels and candidate identities before running
the candidate. Adjudicate source-first; where human adjudication is unavailable,
label the result machine-assisted and do not claim human independence.

### Gates

- Gate A correctness: zero false `VERIFIED`, zero non-live evidence, zero
  fabricated paths/ranges.
- Required-group recall ≥0.80 each split.
- Role precision ≥0.85; wrong-entity-kind admission = 0.
- Misleading admission ≤1 on dev, 0 on held-out.
- Correct abstention ≥0.80; false abstention ≤1.
- Negative safety = 1.0 for accepted negative claims; open-world claims must
  abstain or remain unproven.
- Strict evidence completeness ≥0.80.
- Deterministic output; source/dataset/candidate hashes recorded.
- Resource cap: wall ≤5 s, peak RSS ≤200 MiB for this corpus.

## 11. Owner decisions

| ID | Decision |
|---|---|
| **D-GS-1** | Accept `READY_FOR_INDEPENDENT_QUALIFICATION` for `typed-grounding-v1`; no production promotion. |
| **D-GS-2** | Authorize a future GQ5 campaign using the coverage matrix and gates above. |
| **D-GS-3** | Keep production Selection/Expansion, ranking, model, schema and binary frozen. |
| **D-GS-4** | If GQ5 fails, use failure attribution; do not weaken Gate A or evaluation v2 gates. |

## 12. What remains unproven

- Generalization to independent sources.
- Human-independent adjudication.
- Dynamic SQL and cross-language behavior.
- Production latency and index footprint.
- Any claim that this is a production grounding capability.

The candidate is stable enough to receive an independent qualification
campaign; it is not qualified by this report.
