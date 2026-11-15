# RAG-S1b signal experiment — 2026-11-15

Status: **NO_GO.** One integrated query-to-evidence signal hypothesis was tested
on the frozen candidate pool. It improves dev recall but fails the pre-registered
gate, does not generalize to held-out, and produces a high false-edge rate.

## 1. Hypothesis

**Query-to-Evidence Relationship Signals.** Direct identifiers, call targets,
SQL procedure strings, class/namespace context and structural ranges can rank
already-retrieved evidence better than lexical/rank-only Selection.

Admitted signals:

- **direct call** from a candidate body, only if the target is a frozen
  candidate method;
- **direct SQL string** (`execute/exec sp_*`), only if the target is a frozen
  SQL candidate;
- **identifier match** between query terms and capitalized identifiers in the
  candidate body;
- **class header/property group** inside a frozen class range;
- **member/block/prefix ranges** inside a frozen method/class range;
- engine lexical/rank scores as a prior.

Unknown or unresolved relationships are never promoted to evidence. No new
candidate range outside the frozen pool is introduced.

## 2. Pre-registered gates and result

| Gate | Target | S1b dev | Result |
|---|---:|---:|---|
| Recall@10 | ≥0.60 | **0.5694** | FAIL |
| Strict | ≥0.30 | **0.4861** | PASS |
| Selected spans | ≥16/25 | **10/25** | FAIL |
| Pack tokens | ≤3,716 | max **3,711** | PASS |
| Deterministic | required | PASS | PASS |
| No gold leakage | required | PASS | PASS |
| Provenance / source identity | required | PASS | PASS |
| Held-out behavior | no regression | spans 9/20, strict 3/20 | FAIL |

The policy was frozen before held-out. Held-out was not used for tuning.

## 3. Dev vs S1

| Case | S1 spans | S1b spans | S1 strict | S1b strict |
|---|---:|---:|---:|---:|
| D01 | 0 | **2** | 0.00 | 0.50 |
| D02 | 0 | 0 | 0.00 | 0.00 |
| D03 | 0 | **1** | 0.00 | 1.00 |
| D04 | 1 | 1 | 1.00 | 1.00 |
| D05 | 0 | 0 | 0.00 | 0.00 |
| D06 | 0 | **1** | 0.00 | 0.33 |
| D07 | 1 | **0** | 0.33 | 0.00 |
| D08 | 2 | 2 | 0.50 | 0.50 |
| D09 | 1 | 1 | 1.00 | 1.00 |
| D10 | 0 | **1** | 0.00 | 0.50 |
| D11 | 1 | 1 | 1.00 | 1.00 |
| D12 | 0 | 0 | 0.00 | 0.00 |
| **Total** | **6** | **10** | **5** | **8** |

Recovered 5 spans (D01 ×2, D03, D06, D10); lost 1 (D07 Restore). Net +4.

D01: controller prefix with `Form` plus service prefix via the direct
`EmployeeEditThongTinCaNhan` call/identifier. D03: prefix containing
`FullName`/`Keyword` statements. D06: prefix containing the first personal-update
write path. D10: `CoreService` class header/property group.

D07 regression: the exact `RestoreEmployeeById` method was displaced by
higher-scoring headers/prefixes. This is a ranking error, not a missing signal.

## 4. Held-out

| Case | S1 spans | S1b spans | S1 strict | S1b strict |
|---|---:|---:|---:|---:|
| H01 | 1 | 1 | 1.00 | 1.00 |
| H02 | 1 | **0** | 1.00 | 0.00 |
| H03 | 0 | 0 | 0.00 | 0.00 |
| H04 | 2 | 2 | 1.00 | 1.00 |
| H05 | 2 | 2 | 0.00 | 0.00 |
| H06 | 0 | **4** | 0.00 | 0.00 |
| H07 | 0 | 0 | 0.00 | 0.00 |
| H08 | 3 | **0** | 1.00 | 0.00 |
| **Total** | **9** | **9** | **7** | **3** |

H06 gains four spans but no strict evidence; H02 and H08 are lost. Same recall,
much worse strict. The dev-specific header/prefix/identifier weights do not
transfer to the English held-out questions.

## 5. False edges and distractors

| Set | Selected units | Non-gold units | True edges | False edges |
|---|---:|---:|---:|---:|
| Dev | 57 | 47 | **2** | **6** |
| Held-out | 37 | 32 | **3** | **18** |

False-edge rate is 75% on dev and 86% on held-out. Examples of false promotion:
`LogEdit`/`LogDelete` helpers, `ChangeCompare`, `EmployeeValidation` prefixes,
`DeleteContractLaborByIds` and `EmployeeBaseViewOne` for unrelated queries.
The direct call exists in source, but the target is not evidence for the query.

This is the decisive failure: the signal often promotes a real relationship to
the wrong candidate. It is not a fabricated edge, but it is a false evidence
edge.

## 6. Multi-hop and missing-signal cases

- D02 (personal update) remains 0/6: query has no identifier bridging to
  `EmployeeCommand`/`DbContext`, and the DbContext/Employee file candidates are
  absent from the pool.
- D05 (detail/atomicity) remains 0/1: `EmployeeDetails` is a real identifier,
  but competing prefixes outrank the correct block.
- D07 loses its one selected span; SQL/Org spans remain `NOT_IN_POOL`.
- D12 remains 0/2; “Proc tên này” has no proc name and SQL is not in the engine
  pool.
- No no-answer cases exist in the frozen dev/held-out sets; no-answer behavior
  was not measured.

## 7. Resource cost

- Dev: 249 source files / 2.995 MB; wall 45.5 s total (~3.8 s/case), user CPU
  45.1 s, peak RSS 112,636 KiB; 3,151.7 tokens mean, max 3,711.
- Held-out: 301 files / 3.498 MB; 3,228.4 tokens mean, max 3,588.
- S1 structural read the same 249 dev files but produced 2,691 tokens and 6
  spans. S1b adds 460 tokens and 0.9 s/case for +4 dev spans, while held-out
  strict regresses.

## 8. Verification

- Independent verifier PASS: pool/source identity, range containment, source
  SHA-256, token counts, metric arithmetic, edge-source existence, determinism,
  D/H isolation.
- Negative controls rejected: fabricated range, duplicate, outside-pool unit,
  fabricated edge path/name, identity/token tamper.
- Sanitized dev run produced identical selected units.
- Determinism rerun hash identical (`c1dcd9b6…`).

## 9. Root cause and architectural implication

Direct references and SQL strings exist, but they are **not discriminative
enough** at the query-to-evidence boundary:

1. Call edges often point to shared helpers or sibling operations; the target
   is a real reference but not the requested evidence.
2. The strongest improvements come from structural aliases (class header,
   method prefix, identifier terms) that overfit the Vietnamese dev wording.
3. Held-out English wording changes the identifier/term distribution; the
   same weights promote unrelated prefixes.
4. Remaining misses require cross-file DbContext/SQL binding or semantic
   translation, neither present in the current index.

A production solution needs a **reference-aware Discovery layer** with
typed/call/SQL bindings and resource gates, not another ranking heuristic over
the same metadata. S1b does not justify building that layer now.

## 10. Disposition

- **NO_GO** for S1b.
- Selection and Expansion remain frozen.
- Do not continue tuning ranking on the same metadata.
- If revisited, the next unit must be semantic/reference-aware Discovery with
  explicit false-edge and resource gates; it is not authorized by this task.
