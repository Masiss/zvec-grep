# GQ independent grounding qualification — handoff 2026-11-15

Status: **`NO_GO`** for frozen candidate `3821168`. Independent GQ data found a
false SQL binding through commented-out code, dev role/misleading/abstention/
negative-safety gate failures, and a false answer. The evaluation itself is
machine-assisted and small; that is an additional limitation, not a rescue.

## 1. Branch / base

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-gq-20261115` |
| Branch | `work/rag-gq-20261115` |
| Base | `382116824c62d56d9b1908d79d8e0d9d1c2703bd` |
| External root | `/home/masis/.cache/zvec-grep/rag-gq-20261115/` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Corpus manifest | `d7052d3f…fc4a` |
| Tokenizer | `107bbd…f3d45` |

## 2. Evaluation

| Split | Cases | Required groups |
|---|---:|---:|
| Dev | 6 | 10 |
| Held-out | 4 | 3 |

Coverage: C# cross-file/wrapper/SQL, inline SQL, property/DbSet, view, ambiguous
same-name method, commented-out hard negatives, negative claims, unknown
procedure, Vietnamese/English, one Rust-query corpus-boundary abstention.

Rust/non-C# capability is **not** established: GQ-09 is a no-answer case because
the frozen corpus has no Rust source.

## 3. Three-arm result

| Arm | Dev groups | Dev role precision | Dev misleading | Dev negative safety | Held-out groups | Held-out role precision |
|---|---:|---:|---:|---:|---:|---:|
| Exact-symbol baseline | 4/10 | 0.800 | 1 | 1/1 | 3/3 | 0.600 |
| Reference-aware, no grounding | 4/10 | 0.286 | 2 | 0/1 | 3/3 | 0.375 |
| **Frozen candidate** | **8/10** | **0.533** | **6** | **0/1** | **3/3** | **1.000** |

Candidate dev claim evidence: 3/6 sufficient; 2 unsafe; 1 insufficient.
Held-out claims: 4/4 sufficient. Held-out is too small to offset dev safety.

## 4. Frozen candidate identity

| Item | Value |
|---|---|
| Candidate commit | `3821168` |
| Grounding hash | `6aa30d7b…ec3a60` |
| Binding hash | `e1ccc705…4f9c4` |
| Frozen binding artifact | `4862a694…89643` |
| Gate hash | `133163b0…a20c` |
| Candidate freeze hash | `c4ecc8e8…fa322` |
| Qualification manifest | `67edc4fd…f31395` |

No candidate file was modified after GQ was authored or run.

## 5. Gate result

| Gate | Target | Result |
|---|---:|---:|
| Dev required-group recall | ≥0.80 | 0.80 PASS |
| Role precision | ≥0.85 | 0.533 FAIL |
| Misleading admissions | ≤1 | 6 FAIL |
| Correct abstention | ≥0.80 | 4/6 FAIL |
| Negative-claim safety | 1.00 | 0.00 FAIL |
| False verified bindings | 0 | 2 chains FAIL |
| Wall/RSS | ≤5 s / ≤200 MiB | PASS |
| Tokens/case | ≤3000 | PASS |

## 6. Root-cause failure

`WorkProcessCommand.cs:164` is a **commented-out** declaration. `EmployeeCommand.cs:472`
is a **commented-out** `ChangeWorkProcess(...)` call. The candidate's reference
index/binding artifact still records them as live and emits:

```
EditEmployeeJobAndGroup → ChangeWorkProcess → UpdateOrgTotalEmployee
ChangeWorkProcess → UpdateOrgTotalEmployee
```

as `VERIFIED`. Consequently GQ-03 selects the SQL/wrapper/commented method and
makes the negative claim unsafe. GQ-04 selects commented blocks instead of
abstaining. GQ-01 misses a property/DbSet query. These are source-level
correctness failures, not ranking preferences.

## 7. Artifacts

| Artifact | SHA-256 |
|---|---|
| `candidate-dev.json` | `7d95dc4e…b842b` |
| `candidate-heldout.json` | `24cce640…dfdcca` |
| `gqe-candidate-dev.json` | `77a39337…c04913a8` |
| `gqe-candidate-heldout.json` | `81bc269a…508e8e` |
| `verify-candidate-dev.json` | `e38f61a7…271b4a6` |
| `verify-candidate-heldout.json` | `72f49d15…64c535` |
| `dev-gq.json` | `821efc10…0a1bc` |
| `heldout-gq.json` | `9807749e…5a07bc` |
| `qualification-manifest.json` | `67edc4fd…f31395` |

Tooling added on this branch:

- `reference_arm_20261115.py` — reference-aware no-grounding arm.
- `evaluate_gq_20261115.py` — roles, claims, abstention, failures.
- `verify_gq_20261115.py` — independent dataset/candidate/binding verifier.

## 8. Verification result

| Check | Result |
|---|---|
| Dev verifier | FAILS candidate; 174 checks |
| Held-out verifier | FAILS candidate; 114 checks |
| Sanitized labels removed | Selected units identical |
| Frozen hashes | Match |
| False commented binding | Detected in both splits |
| Source/ranges | No fabricated selected path found |
| Claim/metric arithmetic | Verified |

Verifier FAIL is a candidate finding, not an evaluator crash. It does not make
the machine-assisted labels human-independent.

## 9. Decision / remediation

- **Candidate: `NO_GO`.**
- Evaluation quality is limited but sufficient to establish the concrete false
  binding. It is not sufficient for `QUALIFIED_CANDIDATE`.
- No merge, no production sidecar, no binary/index/daemon changes.

Remediation, if owner wants another candidate:

1. Strip comments before declaration/call extraction.
2. Exclude comment-only methods from `CONTROL` evidence and require a live body.
3. Add property/DbSet exact-symbol grounding.
4. Preserve `CONTROL` vs `WRAPPER` roles.
5. Freeze a new candidate identity and a **new** independent dataset; do not use
   GQ results as final evidence for the fixed candidate.

## 10. Cleanup

Worktree will be committed/pushed, then removed. Cargo target removed; GQ
dataset, gates, receipts, hashes, logs and reports retained. Only the main
worktree remains.

## 11. Owner decision

| ID | Decision |
|---|---|
| **D-GQ-1** | Accept `NO_GO` for `3821168`; do not integrate. |
| **D-GQ-2** | Optionally authorize a comment-aware candidate + fresh independent GQ2 dataset. |
| **D-GQ-3** | Defer any production sidecar decision until a qualified candidate and human-independent evaluation exist. |
