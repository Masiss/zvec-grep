# RAG reference-intelligence evaluation — 2026-11-15

## 1. Evaluation contract

- Frozen dev D01–D12: 25 required spans.
- Held-out H01–H08: 20 spans; evaluated with the frozen policy.
- Recall@10: macro over cases; one selected unit per span.
- Strict: unit covers ≥50% of gold and is ≤ `max(100, 3 × gold length)`.
- Candidate union: any generated candidate overlapping gold, before selection;
  **not** Recall@10.
- Tokens: frozen tokenizer, budget 3,716; no gold/source needle in runtime.
- No no-answer or hard-negative case exists in these frozen sets.

## 2. Candidate coverage

| Set | Initial union | Reference union | Delta |
|---|---:|---:|---:|
| Dev | 18/25 | **22/25** | +4 |
| Held-out | not remeasured | **20/20** | — |

Reference union per dev case:

| Case | Union | Gold |
|---|---:|---:|
| D01 | 2 | 2 |
| D02 | 5 | 6 |
| D03 | 1 | 1 |
| D04 | 1 | 1 |
| D05 | 1 | 1 |
| D06 | 3 | 3 |
| D07 | 2 | 3 |
| D08 | 2 | 2 |
| D09 | 1 | 1 |
| D10 | 2 | 2 |
| D11 | 1 | 1 |
| D12 | 1 | 2 |

The remaining dev gaps are SQL: D07's `sp_UpdateOrgTotalEmployee` body and
D12's proc definition. The reference index has the SQL file but only one
explicit `exec sp_*` method edge; method-name→procedure binding is missing.

## 3. Selection quality

| Policy | Dev spans | Recall@10 | Strict | Tokens mean/max | Held-out spans | Held-out recall | Held-out strict |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 structural | 6/25 | 0.361 | 0.319 | 2,691 / 3,674 | 9/20 | 0.625 | 0.500 |
| S1b signal | 10/25 | 0.569 | 0.486 | 3,152 / 3,711 | 9/20 | 0.500 | 0.250 |
| **Reference prototype** | **10/25** | **0.500** | **0.458** | **2,955 / 3,602** | **8/20** | **0.563** | **0.375** |

Reference selection matches S1b's dev span count but loses 1 strict span and
0.069 Recall@10. It improves held-out strict (0.250→0.375) and reduces tokens,
but selects fewer spans (8 vs 9). It does **not** meet the historical selection
gate.

### Gate result (dev)

| Gate | Target | Result |
|---|---:|---:|
| Recall@10 | ≥0.60 | **0.500 FAIL** |
| Strict | ≥0.30 | **0.458 PASS** |
| Selected spans | ≥16/25 | **10/25 FAIL** |
| Pack tokens | ≤3,716 | **PASS** (max 3,602) |
| Deterministic | required | PASS |
| Gold leakage | none | PASS (sanitized units identical) |
| Provenance | required | PASS |

Held-out is evaluation, not a clean independent lock: S1/S1b had already
observed it. The held-out result is useful for consistency, not a claim of
generalization.

## 4. False evidence / distractors

| Set | Selected units | Units overlapping gold | Non-gold units | Non-gold rate |
|---|---:|---:|---:|---:|
| Dev | 101 | 10 | **91** | **90.1%** |
| Held-out | 64 | 5 | **59** | **92.2%** |

Reference expansion increases the candidate pool, but the selector still fills
the budget with headers, entities and prefixes that are structurally reachable
but task-irrelevant. This is the decisive quality failure.

Selected relation distribution (dev): headers 30, property-type entities 35,
identifier-type entities 16, call targets 9, prefixes 8, members 6, initial 7,
exact symbols 4, type headers 5.

## 5. Per-query behavior

| Case | Union | Selected | Strict | Note |
|---|---:|---:|---:|---|
| D01 | 2 | 2 | 2 | controller/service path retained |
| D02 | 5 | 1 | 1 | entity/header noise; `EmployeeCommand` method not ranked |
| D03 | 1 | 0 | 0 | prefix not selected |
| D04 | 1 | 1 | 1 | `EmployeeUpdateInfo` retained |
| D05 | 1 | 0 | 0 | detail method not ranked |
| D06 | 3 | 0 | 0 | Org method in union but not selected |
| D07 | 2 | 1 | 1 | Restore method; SQL missing |
| D08 | 2 | 2 | 1 | exact delete methods retained |
| D09 | 1 | 1 | 1 | SQL view retained |
| D10 | 2 | 0 | 0 | context type not selected |
| D11 | 1 | 1 | 1 | exact update retained |
| D12 | 1 | 1 | 1 | Org method; SQL missing |

D02/D05/D06/D10 demonstrate the relevance gap: the candidate graph often
contains the right declaration, but query-conditioned ranking does not select
it.

## 6. Cost

| Run | Wall | User CPU | Peak RSS | Source files | Source bytes |
|---|---:|---:|---:|---:|---:|
| Dev | **25.28 s** | 25.07 s | **133,620 KiB** | 462 | 3,848,766 |
| Held-out | **8.72 s** | 8.61 s | **110,696 KiB** | 545 | 4,381,129 |

- Reference index artifact: **12 MB**, 30,323 edges, 8,666 resolved.
- No GPU, daemon, network or production index was used.
- Candidate selection is single-process and deterministic.
- Wall/RSS include corpus parsing each run; no persisted in-memory index was
  used. Index build wall was not separately captured; the 12 MB artifact is the
  measured storage cost.

## 7. Independent verification

| Check | Result |
|---|---|
| Dev verifier | **PASS**, 119,394 checks |
| Held-out verifier | **PASS**, 119,155 checks |
| Determinism | dev receipt hash identical across rerun (`7c75cd6c…`) |
| Gold leakage | sanitized dev selected units identical |
| Reference index identity | all source/type/method/property/edge ranges exist |
| Fabricated edge negative | rejected (`NO_SUCH.cs`, exit 1) |
| Metric/duplicate/path negatives | rejected |
| Token counts | recomputed with frozen counter, PASS |
| Held-out isolation | verified by case-set separation |

The verifier is a separate tool (`verify_refint_20261115.py`); it re-parses the
source for range/name identity and recomputes metrics/tokens. It does not use the
prototype's PASS output as an oracle.

## 8. Comparison with S1/S1b

- Candidate coverage is the clear gain: **22/25 dev**, **20/20 held-out**.
- Selection is not: **10/25 dev**, **8/20 held-out**; non-gold rate >90%.
- S1b had fewer candidates but slightly better dev recall; reference selection
  improves held-out strict while reducing context.
- Neither is qualified for production selection.

## 9. Verdict

**MISSION_PARTIAL.** Reference intelligence demonstrates that typed
cross-file/property/SQL expansion can close most of the candidate gap with
verifiable provenance. It does **not** demonstrate task-relevant evidence
selection, false-edge control, or generalization. No production integration is
authorized.
