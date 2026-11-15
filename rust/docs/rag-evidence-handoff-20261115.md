# RAG evidence relevance and grounding — handoff 2026-11-15

Status: **`MISSION_PARTIAL`.** Binding-aware grounding works on the authored
fixtures and passes independent verification, but the evaluation is small and
manually authored; no production promotion or generalization claim.

## 1. Branch and base

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-evidence-20261115` |
| Branch | `work/rag-evidence-20261115` |
| Base | `work/rag-refint-20261115` @ `89c182e78e4ca62fd9963e55062d2b5bd5f7354f` |
| External root | `/home/masis/.cache/zvec-grep/rag-evidence-20261115/` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Corpus manifest | `d7052d3f…fc4a` |
| Tokenizer | `107bbd…f3d45` |

## 2. Implemented

| Tool | Purpose |
|---|---|
| `binding_20261115.py` | Bounded method → wrapper → SQL binding with `VERIFIED`/`AMBIGUOUS`/`UNRESOLVED` |
| `grounding_20261115.py` | Exact-symbol, binding-aware evidence/role selection + abstention |
| `baseline_evidence_20261115.py` | Naive exact-symbol baseline |
| `evaluate_evidence_20261115.py` | Required-group, role, misleading, abstention metrics |
| `verify_evidence_20261115.py` | Independent source/binding/evaluation verifier |

Docs:

- `rust/docs/rag-evidence-evaluation-contract-20261115.md`
- `rust/docs/rag-evidence-architecture-20261115.md`
- `rust/docs/rag-evidence-evaluation-20261115.md`
- this handoff.

## 3. Results

| Metric | Dev | Held-out | Adversarial |
|---|---:|---:|---:|
| Cases | 6 | 6 | 3 |
| Required groups hit | **9/9** | **9/9** | — |
| Required units hit | **9/9** | **9/9** | — |
| Role precision | **1.00** | **1.00** | — |
| Misleading admissions | **0** | **0** | 0 |
| Correct abstention | 6/6 | 6/6 | **3/3** |
| False abstention | 0 | 0 | 0 |
| Tokens total/mean | 4,525/754 | 2,634/439 | 0 |
| Wall/RSS | 0.70 s/50.7 MB | 0.73 s/50.6 MB | — |

Naive baseline: required-group recall **0.444** dev / **0.556** held-out;
role precision **0.667/0.714**; false abstention 1 dev.

SQL binding: **15 VERIFIED**, **2 AMBIGUOUS**, **0 UNRESOLVED**; direct explicit
1, wrapper 14, name-only 2 (never promoted).

## 4. Verification

- Dev verifier **PASS** (256 checks).
- Held-out verifier **PASS** (235 checks).
- Adversarial verifier **PASS** (130 checks).
- Sanitized cases (roles/groups removed) produce identical units.
- Determinism receipts identical across rerun.
- Mutated name-only binding→VERIFIED rejected.
- Fabricated source path rejected.
- Frozen tokenizer token counts PASS.
- Measurement fix: SQL `CR CR LF` lines are normalized from bytes; procedure is
  102/103 lines, not 205. This is an experimental source-reader fix.

## 5. Artifacts

| Artifact | SHA-256 |
|---|---|
| `sql-bindings.json` | `4862a694…89643` |
| `grounding-dev.json` | `2227ca12…c87b7` |
| `grounding-heldout.json` | `c2a5e776…21ae5` |
| `grounding-adversarial.json` | `d3d3d44d…017634` |
| `eval-dev.json` | `1416419e…f97de0` |
| `eval-heldout.json` | `e2bf87a1…fdde0b` |
| `eval-adversarial.json` | `1d85bcd9…8f8d8de` |
| `verify-dev.json` | `3500670d…e1b35` |
| `verify-heldout.json` | `f5bc8a9a…70110` |
| `verify-adversarial.json` | `0a96aa53…693a6` |
| `dataset-manifest.json` | `d0028b19…b05c4` |

Fixture hashes:

- dev `f1b6815e…54073`
- held-out `bc8adafd…e866f`
- adversarial `c31237e0…ac025`

## 6. Qualification limits

1. Six dev + six held-out cases; authored by the implementer, not independently
   human-annotated.
2. All C#/SQL; no non-C#, dynamic SQL or conflicting deployment authority.
3. D02 second required group reaches the declared ≥50% coverage threshold, not
   100%.
4. Held-out is independent of S1/S1b but not of this implementer.
5. No claim generation; claim support/false acceptance not measured.
6. The 100% fixture result is not production generalization.

## 7. Production / rollback

- No production files, daemon, binary, index, model or config changed.
- SQL bindings and grounding are an external experimental sidecar.
- Rollback: delete branch/worktree and external artifacts; v3 retrieval is
  unaffected.
- Any production integration would need an owner-approved versioned sidecar,
  invalidation/recovery, and a larger independently annotated evaluation.

## 8. Owner decisions

| ID | Decision |
|---|---|
| **D-ER-1** | Accept `MISSION_PARTIAL`; keep production Selection/Expansion frozen. |
| **D-ER-2** | Authorize a larger independent annotation set incl. non-C#, dynamic SQL, conflicting evidence, no-answer before qualification. |
| **D-ER-3** | Decide whether binding sidecar/abstention becomes an engine feature; not approved now. |

## 9. Cleanup state

Worktree is committed/pushed, then removed. Cargo target is removed; receipts,
fixtures, bindings, logs and reports retained. Only the main worktree remains.

## 10. Exact next authorized action

If D-ER-2 is approved: build an independent annotation set with at least hard
negatives, dynamic SQL, conflicting definitions and non-C# cases; freeze it
before changing policy; then re-evaluate this same binding/grounding candidate.
Do not merge the branch.
