# GQ-R repair and qualification — handoff 2026-11-15

Status: **Gate A PASS; Gate B/GQ4 `GQ2_NO_GO`.** Repaired candidate v5 fixes
comment/liveness and false SQL binding, but fails the independent GQ4 quality
gates. No integration.

## 1. Branch / base

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-gqr-20261115` |
| Branch | `work/rag-gqr-20261115` |
| Base | `work/rag-gq-20261115` @ `0843ae5c` |
| External root | `/home/masis/.cache/zvec-grep/rag-gqr-20261115/` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Corpus manifest | `d7052d3f…fc4a` |
| Tokenizer | `107bbd…f3d45` |

## 2. Gate A — correctness repair

Implemented `liveness_20261115.py`:

- comments/block comments;
- regular, verbatim, raw and interpolated strings;
- escaped strings and SQL literals;
- `#if false`, `#else`, `#endif`; unknown symbols are `UNKNOWN`;
- inactive/commented code excluded from the index, strings retained for SQL.

Repaired `RefIndex`:

- methods/types/properties parsed from live code only;
- calls from live code only;
- SQL procedure names from string literals inside method ranges;
- `DbSet` property metadata (`is_dbset`);
- exact ranges + liveness.

Repaired binding/grounding:

- no commented wrapper chain;
- `CONTROL` not overwritten by `WRAPPER`;
- exact property/type grounding;
- negative questions do not add unrelated SQL;
- liveness + role provenance.

### Gate A verification

| Check | Result |
|---|---|
| Adversarial liveness tests | **PASS** |
| Gate A verifier | **PASS**, 96 checks |
| Prior false `ChangeWorkProcess` chains | **gone** |
| Binding states | 13 VERIFIED / 2 AMBIGUOUS / 0 UNRESOLVED |
| Direct/wrapper/name-only | 1 / 12 / 2 |

Regression on old GQ: GQ-03 no commented SQL, GQ-04 abstains, GQ-01 property
found. Old GQ is development evidence only.

## 3. Candidate freeze v5

| Item | Value |
|---|---|
| Candidate | `liveness-binding-grounding-v5` |
| Freeze | `82cdccd6…52472` |
| Grounding | `a30397c5…4711e3` |
| Liveness | `cc456eaf…0ca0c` |
| Reference index | `d2da4719…22aec4` |
| Binding policy | `e1ccc705…4f9c4` |
| Frozen bindings | `d8b40b0b…a8203` |
| Gates | `ad2e21b3…a239c9` |

No candidate change after GQ4 formal run.

## 4. Gate B — GQ4

GQ2/GQ3 were used during repair and invalidated as final evidence. GQ4 was
authored after the v5 freeze.

| Split | Cases | Required groups |
|---|---:|---:|
| Dev | 3 | 4 |
| Held-out | 3 | 1 |

| Arm | Dev groups | Dev role precision | Dev unknown | Dev unsupported | Held-out groups | Held-out role |
|---|---:|---:|---:|---:|---:|---:|
| Exact-symbol baseline | 2/4 (0.50) | 1.000 | 0 | 0 | 1/1 | 1.000 |
| Reference-aware | 2/4 (0.50) | 0.333 | 4 | 0 | 1/1 | 1.000 |
| **Candidate v5** | **2/4 (0.50)** | **0.667** | **1** | **1** | **1/1** | **1.000** |

Candidate dev claims 2/3 sufficient; held-out 3/3. Safety: no false VERIFIED
binding, no non-live evidence, no unsafe negative claim. Quality gates fail:
required-group recall 0.50 < 0.80; role precision 0.667 < 0.85; one unsupported
acceptance.

### GQ4-01 failure

Query asks for the `EmployeeBaseViews` DbSet property and `EmployeeBaseView`
declaration. Candidate selected `DB/Views/EmployeeBaseViews.sql` 1–10 (VIEW)
because `EmployeeBaseView` is a substring of the view name; required property
`CoreDBContext.cs:42` and type `Employee.cs:64–81` were missed.

This is a query-intent/property-selection failure, not a liveness/binding safety
failure. GQ4 labels did not mark the view as misleading, so the evaluator
reports unknown rather than misleading; the missing required groups still fail.

## 5. Verification

| Check | Result |
|---|---|
| Gate A verifier | PASS |
| GQ4 dev verifier | **FAIL** — role precision + required group gates |
| GQ4 held-out verifier | PASS |
| GQ4 sanitized input | Selected units identical |
| False commented binding | none |
| Fabricated source | none |
| Candidate/tool/dataset hashes | verified |
| Claim/metric arithmetic | verified |

## 6. Artifacts

| Artifact | SHA-256 |
|---|---|
| `candidate-freeze-v5.json` | `82cdccd6…52472` |
| `sql-bindings-repaired.json` | `d8b40b0b…a8203` |
| `candidate-v5-gq4-dev.json` | `c0ab3c6b…3c42` |
| `candidate-v5-gq4-heldout.json` | `b50ab5d0…0b26` |
| `eval-gq4-v5-dev.json` | `d8ebacc6…d4f6` |
| `eval-gq4-v5-heldout.json` | `ab2025a9…9fca` |
| `verify-gq4-dev.json` | `63269ec6…7296` |
| `verify-gq4-heldout.json` | `303a8c2b…b4f1` |
| `gq4-manifest.json` | `7389f1bb…e132` |
| `verify-gate-a.json` | recorded in external receipts |

Docs:

- `rust/docs/rag-gqr-gate-a-20261115.md`
- `rust/docs/rag-gqr-gate-b-gq4-20261115.md`
- this handoff.

## 7. Decision

- **Gate A: PASS.**
- **Gate B/GQ4: `GQ2_NO_GO`.**
- Do not integrate, merge, promote, replace binary/index/daemon, or unfreeze
  production Selection/Expansion.

### Targeted remediation

1. Give property/DbSet intent priority over SQL/view for property-mapping
   queries.
2. Exact-match view names; do not use substring matching for `EmployeeBaseView`
   versus `EmployeeBaseViews`.
3. Keep liveness/Gate A regression contract unchanged.
4. Freeze a new candidate identity and a **new** GQ5 set; do not reuse GQ4 as
   final evidence for the fixed candidate.

## 8. Owner decisions

| ID | Decision |
|---|---|
| **D-GQR-1** | Accept Gate A PASS and `GQ2_NO_GO` for v5. |
| **D-GQR-2** | Optionally authorize v6 property/view-intent repair + fresh GQ5. |
| **D-GQR-3** | Defer production sidecar until a qualified candidate and stronger human-independent evaluation exist. |

## 9. Cleanup

Worktree committed/pushed then removed; Cargo target removed; GQ/GQ2/GQ3/GQ4
datasets, gates, receipts, hashes and logs retained. Only main worktree remains.
