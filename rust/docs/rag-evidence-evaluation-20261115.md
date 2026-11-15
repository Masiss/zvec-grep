# RAG evidence relevance — evaluation report 2026-11-15

**Verdict: `MISSION_PARTIAL`.** The binding-aware grounding prototype is
correct on the authored fixtures, but the evaluation is too small and manually
authored to support a production or generalization claim.

## 1. Fixture results

| Metric | Dev | Held-out |
|---|---:|---:|
| Cases | 6 | 6 |
| Required groups | 9 | 9 |
| Groups hit (≥50% coverage) | **9/9** | **9/9** |
| Required units hit | **9/9** | **9/9** |
| Selected units | 11 | 9 |
| Role precision | **1.00** | **1.00** |
| Misleading admissions | **0** | **0** |
| False answers / false abstentions | 0 / 0 | 0 / 0 |
| Correct abstention | 6/6 | 6/6 |
| Tokens total / mean | 4,525 / 754 | 2,634 / 439 |
| Wall / peak RSS | 0.70 s / 50.7 MB | 0.73 s / 50.6 MB |

Adversarial (post-freeze, not tuning): **3/3 abstain**, 0 selected units.

Naive exact-symbol baseline (same fixtures):

| Metric | Dev | Held-out |
|---|---:|---:|
| Required-group recall | 0.444 (4/9) | 0.556 (5/9) |
| Role precision | 0.667 | 0.714 |
| Unknown selected | 2 | 2 |
| False abstention | 1 | 0 |

The binding-aware prototype is the only policy that reaches 9/9 groups in this
fixture set. This demonstrates the **mechanism**, not production generality.

## 2. Per-case evidence

| Case | Selected | Groups | Misleading | Note |
|---|---|---|---:|---|
| RE-D01 | SQL 1–103, wrapper 1057–1073, control 550–560 | 3/3 | 0 | wrapper chain resolves |
| RE-D02 | SaveChanges windows 215–285, 286–356, 422–425 | 2/2 | 0 | entity/no SQL |
| RE-D03 | SQL 1–103, direct wrapper | 2/2 | 0 | SQL symbol |
| RE-D04 | view 1–10 | 1/1 | 0 | view only |
| RE-D05 | — | — | 0 | unknown proc → abstain |
| RE-D06 | inline method 539–548 | 1/1 | 0 | no procedure |
| RE-H01 | SQL, wrapper, control | 3/3 | 0 | indirect chain |
| RE-H02 | SQL, wrapper | 2/2 | 0 | table + invoker |
| RE-H03 | view | 1/1 | 0 | view only |
| RE-H04 | — | — | 0 | unknown proc → abstain |
| RE-H05 | SQL, direct wrapper | 2/2 | 0 | direct invoker |
| RE-H06 | inline method | 1/1 | 0 | negative answer |

D02 selects two `SaveChanges` windows; the second window covers ~50% of the
required 287–425 range, satisfying the declared ≥50% group threshold. A stricter
100%-coverage contract would require a different pack and is not claimed.

## 3. SQL binding result

| State | Count |
|---|---:|
| VERIFIED | 15 |
| AMBIGUOUS | 2 |
| UNRESOLVED | 0 |
| Direct explicit | 1 |
| Wrapper chain | 14 |
| Name-only (ambiguous) | 2 |

`RestoreEmployeeById → UpdateOrgTotalEmployee → execute
sp_UpdateOrgTotalEmployee → SQL` is verified. Name-only matches are not
promoted. `DeleteEmployeeByIds` is correctly treated as no binding.

## 4. Verification

| Check | Result |
|---|---|
| Dev verifier | **PASS**, 256 checks |
| Held-out verifier | **PASS**, 235 checks |
| Adversarial verifier | **PASS**, 130 checks |
| Sanitized input (roles/groups removed) | selected units identical on dev/held-out |
| Determinism | receipt hashes identical across rerun |
| Binding rule | explicit evidence + scoped wrapper chain; name-only not verified |
| Negative binding | mutated name-only→VERIFIED rejected |
| Fabricated source | rejected |
| Token counts | frozen tokenizer, PASS |
| Corpus manifest | `d7052d3f…fc4a` |

Verifier is a separate tool; it re-reads source ranges, binding provenance,
annotations, sanitized outputs and metric arithmetic. It does **not** re-adjudicate
semantic labels.

## 5. Limitations / falsification

- Authored labels: one implementer; no independent human annotator.
- Six dev + six held-out cases; all C#/SQL; no non-C# or dynamic SQL.
- Held-out is new relative to S1/S1b, but authored by the same implementer.
- D02 required coverage is ~50% for one group; no 100% claim.
- No claim generation, so claim-to-evidence support and false acceptance are
  **not measured**.
- No conflicting live/deployed SQL authority case.
- 100% fixture quality is not a production generalization result.

## 6. Conclusion

The mission produced a coherent, independently checked **mechanism**:
verified method→SQL bindings plus role/necessity-conditioned evidence packing
and abstention. It also produced a **fixture-level** quality gain over naive
exact-symbol selection. It did not produce a qualification-grade evaluation.

Do not merge, promote, replace production selection, or claim production
readiness.
