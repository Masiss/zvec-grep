# 06 — RAG Post-R4 Research Chain

This is a **research history**, not the canonical R0–R6 plan. The chain started
after R4 failed and was performed on separate branches from
`work/zg-recovered` `27b81c0` (except the docs lineage). None of it was merged
into the maintained source branch.

## 1. Why the chain exists

R4 did not fail because packing or token count was bad. It failed because the
retrieval **seed/selection** could not put the required evidence in the
candidate set, and the bounded expander displaced B0 evidence. The post-R4
missions therefore explored:

1. whether Discovery can find the missing cross-file/SQL spans;
2. whether Selection can select them from a fixed pool;
3. whether query-to-evidence signals generalize;
4. whether a reference layer can expand typed relations;
5. whether SQL binding/evidence grounding can be verified;
6. whether the result survives independent qualification;
7. whether the implementation can be stabilized into a typed, testable
   candidate.

Each branch was a bounded mission with its own freeze/evaluator. Later branches
inherit earlier docs/receipts but do not retroactively make earlier data an
independent qualification.

## 2. Branch map

All bases are relative to the previous tip:

| Branch | Tip | Base | Status |
|---|---|---|---|
| `work/rag-requal-20261115` | published `f93d0499cccb450065302f0def9ca87ea1096983`; local ref `1d8f57a3…` | `27b81c0` | Discovery not qualified |
| `work/rag-s1-20261115` | `1d8f57a3b06834b8beb3afaea78ae37ac2c6dcf4` | `f93d0499` | Selection NO_GO |
| `work/rag-s1b-20261115` | `ef2a7667dfb0eacb80abf82166b437ab42db344e` | `1d8f57a3` | Signal NO_GO |
| `work/rag-refint-20261115` | `89c182e78e4ca62fd9963e55062d2b5bd5f7354f` | `ef2a7667` | Reference partial |
| `work/rag-evidence-20261115` | `382116824c62d56d9b1908d79d8e0d9d1c2703bd` | `89c182e7` | Grounding partial |
| `work/rag-gq-20261115` | `0843ae5cf093b4696ddc600f1c154c2f80a981bc` | `3821168` | NO_GO |
| `work/rag-gqr-20261115` | `39e75d484c1d347b95c0cb69f40128568e873b6f` | `0843ae5c` | Gate A PASS; GQ4 NO_GO |
| `work/rag-gs-20261115` | `102f214c71884712ce8d4f7ee2dd341257ec3fb2` | `39e75d48` | Readiness only; GQ5 not run |

`work/rag-s1` and the local `work/rag-requal` ref both point to `1d8f57a` (the
post-S1 docs tip); the published `work/rag-requal` ref remains `f93d0499`.
The S1 commit `7d912c1` is in that history. This is a local branch-label/ref
artifact, not a separate implementation; use the remote SHA for publication
identity and the local SHA for content inspection.

## 3. Requalification — Discovery on E8/v3

**Verdict:** `DISCOVERY NOT QUALIFIED`.

- Candidate union: **18/25** (not Recall@10).
- Fixed FTS top-10: **0.333**; hybrid: **0.417**; oracle best-of-six: **0.597**.
- Best-call strict recall: **0.125**; max strict 0.292.
- 7 missing spans are cross-file/SQL or semantic: `CoreDBContext`/`Employee`,
  Org/SQL, `CoreDBContext`, D12 SQL.
- Corpus 818 files; external v3 index, Potion; no live workspace indexed.
- Measured on E8/v3 foundation; does not authorize production/Selection.

Why it matters: the old “9/25 retrievable” was a **union**, not top-10
Recall@10; the candidate pool improved, but top-k evidence did not qualify.

Evidence: `rust/docs/rag-discovery-handoff-20261115.md`,
`rag-discovery-requalification-20261115.md` at `f93d0499`.

## 4. S1 — Selection feasibility

**Verdict:** `NO_GO`; Selection remains frozen.

| Policy | Dev spans | Recall@10 | Strict | Tokens mean/max |
|---|---:|---:|---:|---:|
| Fixed FTS | 5/25 | 0.333 | 0.292 | 8,049 / 28,426 |
| Exact-symbol | 6/25 | 0.361 | 0.319 | 766 / 3,389 |
| Structural member/block | 6/25 | 0.361 | 0.319 | 2,691 / 3,674 |
| Oracle | 15/25 | 0.736 | 0.472 | 1,473 / 3,501 |

- Gate: Recall@10 ≥0.60 FAIL; selected spans ≥16/25 FAIL; strict/tokens PASS.
- Held-out exact: 7/20, 0.531/0.406; structural 9/20, 0.625/0.500.
- Candidate ceiling 16/25; runtime only 6/25; oracle same budget 15/25.
- Exact-symbol is precise but too sparse; structural expansion adds cost without
  dev recall. Held-out does not rescue dev failure and was not used for tuning.

Evidence: `rust/docs/rag-selection-s1-handoff-20261115.md` at
`work/rag-s1b`/`work/rag-gs` history.

## 5. S1b — query-to-evidence signals

**Verdict:** `NO_GO`; dev improves but does not generalize; false-edge rate is
decisive.

| Metric | Dev | Held-out |
|---|---:|---:|
| Recall@10 | **0.569 FAIL** | 0.500 |
| Strict | 0.486 PASS | **0.250 FAIL** |
| Selected spans | 10/25 FAIL | 9/20 |
| Tokens mean/max | 3,151.7 / 3,711 | 3,228.4 / 3,588 |
| False-edge rate | **75%** (2 true / 6 false) | **86%** (3/18) |

- Dev gained 5 spans and lost D07; held-out strict regressed 0.500→0.250.
- 47/57 selected dev units and 32/37 held-out units were non-gold.
- The remaining misses require cross-file DbContext/SQL binding, not another
  structural edge.

Reusable element: exact-symbol fallback only; not the full signal layer.

Evidence: `rust/docs/rag-selection-s1b-handoff-20261115.md` at `ef2a7667`.

## 6. RefInt — reference intelligence

**Verdict:** `MISSION_PARTIAL`; coverage improved, selection/relevance did not.

- Reference index: 1,370 types, 3,382 methods, 8,602 properties, 2 SQL files,
  30,323 edges, 8,666 resolved.
- Candidate union: **22/25 dev**, **20/20 held-out**.
- Selected: **10/25 dev**, **8/20 held-out**.
- Recall@10: 0.500 dev / 0.563 held-out; strict 0.458 / 0.375.
- Non-gold selected units: 91/101 dev, 59/64 held-out (>90%).
- Token mean/max: 2,955/3,602 dev; 2,182/3,105 held-out.
- Resource: 25.28 s / 133.6 MB dev; 8.72 s / 110.7 MB held-out.
- Only one explicit `exec sp_*` edge; SQL binding incomplete.
- Held-out was already observed by S1/S1b; not independent.

Why no promotion: structural reachability is not task relevance; false evidence
dominates selection.

Evidence: `rust/docs/rag-refint-handoff-20261115.md` at `89c182e7`.

## 6b. Discovery side experiments — H-A/H-B/H-C

These bounded experiments were run during requalification/S1 and are preserved
because they explain **why not to retry** certain “obvious” refinements.

| ID | Hypothesis / gate | Result |
|---|---|---|
| H-A | Split >200-line containers to best-matching declaration: recall loss ≤2 pp, strict ≥0.30, lines −30% | **FAIL** |
| H-B | Exact-symbol channel for query identifiers: +≥2 gold spans in union or strict +≥0.10 | **FAIL** |
| H-C | Cross-file/SQL structural closure: bounded reads, ≤64 MiB, candidate gain, deterministic | **DEFERRED** |

H-A detail: refined top-10 recall **0.5556 → 0.3750**, strict unchanged 0.2917,
mean lines **2,542.5 → 995.5** (−61%), with 76 files/2.17 MB audit reads. It is a
cost-only transformation; density/query-token overlap did not reliably retain the
gold declaration.

H-B detail: symbol channel covered **5/25** gold spans (D04, D08×2, D10, D11),
but marginal candidate-union gain over search was **0 spans**; all five were
already retrievable. Some exact names returned 100/51 items, so it needs a
selector. It is a precision signal for Selection, not a standalone discovery
channel.

H-C detail: the remaining seven precise spans are D02×3 (two
`CoreDBContext` ranges + `Employee`), D07×2 (`OrganizationCommand` + SQL),
D10×1 (`CoreDBContext`) and D12×1 (SQL). Structural closure would
require a production reference graph or SQL procedure binding; the E1
typed-reference feature is non-default and its resource gates failed
(67.8/72.3 MiB > 64 MiB). No H-C run/resource claim exists. Do not implement it
as a “quick” Discovery fix while Selection/Expansion are frozen.

Evidence: `rust/docs/rag-discovery-experiments-20261115.md` at the S1b/GS tips.

## 7. Evidence Grounding — binding + roles

**Verdict:** `MISSION_PARTIAL`; small, manually authored evaluation; no
production/generalization claim.

Implemented:

- `binding_20261115.py`: method → wrapper → SQL, `VERIFIED` / `AMBIGUOUS` /
  `UNRESOLVED`;
- `grounding_20261115.py`: exact-symbol/binding-aware evidence + abstention;
- baseline, evaluator, independent verifier.

Results on authored fixtures:

| Metric | Dev | Held-out | Adversarial |
|---|---:|---:|---:|
| Cases | 6 | 6 | 3 |
| Required groups | 9/9 | 9/9 | — |
| Role precision | 1.00 | 1.00 | — |
| Misleading | 0 | 0 | 0 |
| Correct abstention | 6/6 | 6/6 | 3/3 |
| Tokens mean | 754 | 439 | — |
| Wall/RSS | 0.70 s/50.7 MB | 0.73 s/50.6 MB | — |

SQL binding: 15 VERIFIED, 2 AMBIGUOUS, 0 UNRESOLVED; 1 direct, 14 wrapper,
2 name-only (never promoted). The CRCRLF reader fix corrected procedure lines
from 205 to 102/103. Verifiers PASS (256/235/130 checks) and sanitized runs were
identical.

Limits: 6+6 cases, authored by implementer, not independent source adjudication.

Evidence: `rust/docs/rag-evidence-handoff-20261115.md` at `3821168`; evaluation
contract `rag-evidence-evaluation-contract-20261115.md`.

## 8. GQ — independent grounding qualification

**Verdict:** `NO_GO` for candidate `3821168`.

The independent GQ set found a real correctness failure:

- commented `ChangeWorkProcess` declaration/call was treated as live;
- false `VERIFIED` SQL binding through that comment;
- commented-only code selected as evidence;
- negative claims unsafe;
- `DbSet<EmployeeChangeRequestDetail>` property/type missed;
- `CONTROL` and `WRAPPER` conflated;
- dev required-group recall **8/10**, role precision **0.533**, misleading
  admissions **6**, correct abstention **4/6 (0.667)**, negative-claim safety
  **0/1**; held-out groups 3/3 and role precision 1.000, but only four held-out
  cases.
- claim-level dev: 3/6 SUPPORTED, 2 unsafe, 1 insufficient; held-out 4/4
  correct. The dev safety failure is decisive even though group recall passes.
- two false `VERIFIED` chains were found through commented code; one chain
  produced an unsafe `REFUTED` negative claim.

This is the most important post-R4 lesson: the prior manual evaluation passed
because it did not exercise commented/inactive code. GQ made the failure visible;
it does not prove no other failures.

Evidence: `rust/docs/rag-gq-handoff-20261115.md` at `0843ae5c`.

## 9. GQ-R — repair and re-qualification

**Verdicts:** Gate A PASS; GQ4 NO_GO; later GS readiness.

Repairs:

- lexical liveness scanner (comments, strings, raw/verbatim/interpolated,
  `#if false`, unknown → fail closed);
- binding only through live code; no name-only VERIFIED;
- `CONTROL` vs `WRAPPER` provenance;
- exact DbSet property/type mapping;
- negative claims require an explicit local/closed-world basis.

Gate A:

- 96 checks PASS;
- 13 VERIFIED / 2 AMBIGUOUS / 0 UNRESOLVED;
- no false commented binding;
- previous GQ failure cases no longer selected.

GQ4 found the remaining quality gap:

- dev required-group recall 2/4 (0.50), role precision 0.667;
- property query selected a view (`EmployeeBaseViews`) instead of property/type;
- unknown SQL near-name selected SQL/wrappers;
- negative query admitted SQL/wrappers.

Final GQ-R verdict was `GQ2_NO_GO` for the then-current candidate; Gate A was
PASS.

Evidence: `rust/docs/rag-gqr-gate-a-20261115.md`,
`rag-gqr-gate-b-gq4-20261115.md`, `rag-gqr-handoff-20261115.md` at
`39e75d48`.

## 10. GS — typed grounding stabilization

**Verdict:** `READY_FOR_INDEPENDENT_QUALIFICATION` for `typed-grounding-v1`;
not production-qualified; GQ5 not run.

Added:

- exact type/property/method/SQL/view resolution;
- `DbSet<T>` mapping by type relation;
- no substring view/type equivalence;
- primary method selection (binding/service over controller);
- multi-role `roles` union with provenance/liveness/ambiguity;
- evaluation contract v2 (UNKNOWN vs MISLEADING, WRONG_ENTITY_KIND, negative
  basis);
- regression + metamorphic suite;
- independent verifier.

GQ4 v2 (still an observed regression set, not independent):

| Metric | Dev | Held-out |
|---|---:|---:|
| Cases | 3 | 3 |
| Required-group recall | 1.000 | 1.000 |
| Role precision | 1.000 | 1.000 |
| Wrong kind / misleading | 0 / 0 | 0 / 0 |
| Negative safety | 1.000 | n/a |
| Strict completeness | 1.000 | 1.000 |

Readiness evidence:

- Gate A 96 PASS;
- typed regression 19/19 PASS;
- evaluation contract 5/5 PASS;
- independent verifier 124/124 PASS;
- GQ4 dev 1.17 s / 50.38 MiB; held-out 1.07 s / 50.85 MiB;
- token counter absent → tokens **not measured**;
- freeze `8a26c91e…`; tool identity `bafa862e…`;
- branch `102f214`.

Reusable capability: typed resolver, liveness/binding safety, role contract,
regression suite, evaluator v2. Not yet reusable as production because GQ5,
public integration, and human-independent adjudication are missing.

## 11. Why the chain produced repeated NO-GO

| Failure mode | Consequence |
|---|---|
| Candidate union vs top-k confusion | false sense of coverage |
| Structural reachability treated as relevance | >90% non-gold selections |
| Held-out observed/reused | not independent generalization |
| Tiny/manual evaluations | false confidence; GQ caught one real bug |
| Route/harness contamination | E3/E4 numbers invalid until E5/E6 |
| Selection tuning without new retrieval | candidate ceiling 16–18/25; cannot reach target |
| No full agent loop/CHECK | cannot recover from a bad seed set |
| R1 library not exposed | no external-agent MVP |
| Plan not updated after R4 | governance gap, repeated micro-missions |

## 12. What survives

Safe/reusable:

- R1 evidence pack contract and tests;
- R2 C# facts bounded parser;
- R3 SQL source facts/binding model;
- liveness-aware binding and fail-closed abstractions;
- typed entity/role resolver;
- exact source/range/hash provenance;
- evaluation v2 semantics and regression fixtures.

Not reusable as product claim:

- R4 expander;
- S1/S1b signal/selection;
- RefInt selection;
- any “qualified” claim from small/manual sets;
- GS before GQ5/public integration.

## 13. Evidence anchors

- Branch docs at the exact tips in §2: `rag-discovery-handoff-20261115.md`,
  `rag-selection-s1-handoff-20261115.md`,
  `rag-selection-s1b-handoff-20261115.md`,
  `rag-refint-handoff-20261115.md`, `rag-evidence-handoff-20261115.md`,
  `rag-gq-handoff-20261115.md`, `rag-gqr-handoff-20261115.md`,
  `rag-gs-handoff-20261115.md`.
- `PROJECT-STATE.md` for runtime vs branch identities.
- External receipts under `~/.cache/zvec-grep/rag-*` (not copied into this
  portable KB).
