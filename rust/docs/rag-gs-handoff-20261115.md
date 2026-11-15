# RAG-GS — Typed Grounding Stabilization Handoff

Status: **`READY_FOR_INDEPENDENT_QUALIFICATION`** for the experimental
`typed-grounding-v1` candidate. Not production-qualified. No GQ5 campaign was
opened.

## 1. Git / environment

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-gs-20261115` |
| Branch | `work/rag-gs-20261115` |
| Base | `39e75d484c1d347b95c0cb69f40128568e873b6f` |
| Commit | `1125d4094e03a37856c134bf407fe553c1c39a49` |
| Remote | `1125d4094e03a37856c134bf407fe553c1c39a49` |
| Main worktree | `/home/masis/src/zvec-grep` @ `27b81c0` |
| External root | `/home/masis/.cache/zvec-grep/rag-gs-20261115` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Corpus manifest | `d7052d3f…fc4a` |

One experiment worktree only; no production binaries, daemon, indexes, model,
schema or ranking changed.

## 2. Candidate freeze

Singleton: `typed-grounding-v1`

| Item | Hash |
|---|---|
| Freeze manifest | `8a26c91e67732707ef09c3a47cfd1b472e734d0885bdc88daa3ed09fc939660f` |
| Tool identity | `bafa862ee17c84fae2b9bf617ca690eb9e95b6de7e19ed41dc125ec703091e9f` |
| Bindings | `d8b40b0b…a8203` |
| Regression | recorded in freeze |
| GQ4 dev/held-out receipts | recorded in freeze |
| Eval contract test | `PASS` 5/5 |

Frozen tools:

- `typed_resolution_20261115.py`
- `grounding_20261115.py`
- `evaluate_gq_v2_20261115.py`
- `typed_resolution_regression_20261115.py`
- `verify_gs_20261115.py`
- `test_eval_contract_20261115.py`
- `liveness_20261115.py`
- `refint_20261115.py`

## 3. What changed

### Typed resolver

- exact type/property/method/view identity;
- `DbSet<T>` mapping by type relation;
- exact SQL `sp_` normalization only;
- no substring view/type equivalence;
- primary method selection by binding/service evidence;
- constructors not admitted as property/method evidence;
- unknown SQL abstention;
- property intent takes precedence over SQL/view substring matches.

### Evidence roles

- `CONTROL`, `WRAPPER`, `TYPE`, `PROPERTY`, `SQL`, `VIEW`, `SUPPORTING`,
  `UNKNOWN`;
- units carry `roles` union, `entity_kind`, `resolved`, `provenance`, `liveness`,
  `ambiguity`;
- no order-dependent role overwrite;
- non-gold selection is `UNKNOWN`, not misleading.

### Evaluation v2

- separate REQUIRED/SUPPORTING/OPTIONAL/IRRELEVANT/WRONG_ENTITY_KIND/MISLEADING/
  UNKNOWN;
- explicit coverage and strict thresholds;
- separate candidate coverage, role accuracy, wrong-kind, misleading,
  abstention, negative safety, token/resource metrics;
- negative claims require explicit `method_body_complete` or `closed_world`
  basis.

### Regression / verifier

- 19 typed resolution/metamorphic checks, all PASS;
- 5 evaluation contract checks, all PASS;
- 124 independent verifier checks, 0 failures;
- Gate A 96 checks, PASS;
- liveness adversarial tests PASS.

## 4. Before/after

| Metric | v5 before | typed candidate |
|---|---:|---:|
| GQ4 dev group recall | 0.500 | **1.000** |
| GQ4 dev role precision | 0.667 | **1.000** |
| GQ4 dev unknown | 1 | **0** |
| GQ4 dev unsupported | 1 | **0** |
| GQ4 dev negative safety | unproven | **1.000** |
| GQ4 held-out group recall | 1.000 | **1.000** |
| GQ4 held-out role precision | 1.000 | **1.000** |

GQ4/GQ/GQ2/GQ3 are observed sets, not independent generalization evidence.

## 5. Resource

| Split | Wall | Peak RSS |
|---|---:|---:|
| GQ4 dev | 1.17 s | 50.38 MiB |
| GQ4 held-out | 1.07 s | 50.85 MiB |

Token counter binary absent; token counts are **not measured**, not estimated.

## 6. Artifacts

| Artifact | Path |
|---|---|
| Freeze | `receipts/candidate-freeze.json` |
| Regression | `receipts/regression.json` |
| Eval contract test | `receipts/eval-contract-test.json` |
| GQ4 dev receipt | `receipts/gs-gq4-dev.json` |
| GQ4 held-out receipt | `receipts/gs-gq4-heldout.json` |
| Eval v2 dev | `receipts/eval-v2-gq4-dev.json` |
| Eval v2 held-out | `receipts/eval-v2-gq4-heldout.json` |
| Verifier | `receipts/verify-gs.json` |
| Gate A | `receipts/verify-gate-a.json` |
| Logs/time | `logs/` |

Docs:

- `rust/docs/rag-gs-architecture-20261115.md`
- `rust/docs/rag-gs-verification-20261115.md`
- this handoff.

## 7. Readiness limitations

- No independent GQ5; no generalization claim.
- Evaluation is tiny and machine-assisted.
- SQL is the only non-C# positive tested.
- Dynamic SQL and global absence proofs are unsupported.
- Historical GQ/GQ2/GQ3 receipts lack the new negative-basis field; do not use
  them as negative-safety qualification.
- No human-independent adjudication.

## 8. Proposed GQ5 plan

Do not execute without owner authorization. Use fresh, source-first,
unobserved cases: ≥8 dev + ≥8 held-out; coverage includes property/DbSet,
SQL proc/view, liveness strings/comments/preprocessor, wrong-kind collisions,
unknown/no-answer, closed-world and open-world negatives, source drift,
multi-role overlap, Vietnamese and English. Freeze candidate/cases/labels
before candidate execution. Use the gates in
`rag-gs-verification-20261115.md`; do not weaken Gate A or eval v2.

## 9. Owner decisions

| ID | Decision |
|---|---|
| **D-GS-1** | Accept readiness; no production promotion. |
| **D-GS-2** | Authorize future independent GQ5. |
| **D-GS-3** | Keep production Selection/Expansion, ranking, model, schema, binary and daemon frozen. |
| **D-GS-4** | On GQ5 failure, attribute and repair; do not loosen gates. |

## 10. Cleanup

After commit/push: verify remote SHA, remove experiment worktree with Git,
remove external Cargo target if present, retain receipts, regression fixtures,
freeze, logs and docs. Do not remove historical GQ/GQ2/GQ3/GQ4 assets.
