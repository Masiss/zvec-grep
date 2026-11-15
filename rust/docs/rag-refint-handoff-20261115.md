# RAG reference intelligence — handoff 2026-11-15

Status: **`MISSION_PARTIAL`.** A prototype reference layer closes most candidate
coverage gaps with provenance, but task-relevant selection and false-evidence
control remain unqualified. No production change.

## 1. Branch and state

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-refint-20261115` |
| Branch | `work/rag-refint-20261115` |
| Base | `work/rag-s1b-20261115` @ `ef2a7667dfb0eacb80abf82166b437ab42db344e` |
| External root | `/home/masis/.cache/zvec-grep/rag-refint-20261115/` |
| Corpus | `/home/masis/.cache/zvec-grep/rag-requal-20261115/corpus` |
| Manifest | `c706c8dd…6359cdb`; 822 files / 5,400,637 B |
| Tokenizer | `107bbd…f3d45` |
| Dev probe | `84b7593a…9188e27` |
| Held-out probe | `74a83698…255c4c51` |

## 2. Deliverables

Tools:

- `benchmarks/rag-impact/tools/refint_20261115.py` — reference index, bounded
  expansion, intent scoring, selection.
- `benchmarks/rag-impact/tools/refint_index_20261115.py` — standalone
  provenance-bearing reference index export.
- `benchmarks/rag-impact/tools/verify_refint_20261115.py` — independent verifier.

Docs:

- `rust/docs/rag-refint-architecture-20261115.md`
- `rust/docs/rag-refint-failure-matrix-20261115.md`
- `rust/docs/rag-refint-evaluation-20261115.md`
- this handoff.

## 3. Results

| Metric | Dev | Held-out |
|---|---:|---:|
| Candidate union | **22/25 (0.88)** | **20/20 (1.00)** |
| Initial union | 18/25 | not remeasured |
| Selected spans | 10/25 | 8/20 |
| Macro Recall@10 | 0.500 | 0.563 |
| Macro strict | 0.458 | 0.375 |
| Tokens mean / max | 2,955 / 3,602 | 2,182 / 3,105 |
| Selected non-gold units | 91/101 | 59/64 |

Reference index: 1,370 types, 3,382 methods, 8,602 properties, 2 SQL files,
30,323 edges, 8,666 resolved. Cost: dev 25.28 s / 133.6 MB peak RSS; held-out
8.72 s / 110.7 MB.

Gate: dev Recall@10 0.500 < 0.60 and selected spans 10 < 16 → **FAIL**. Strict
0.458 passes, tokens pass, determinism/pass.

## 4. Interpretation

- Typed property/call expansion reliably discovers cross-file declarations:
  `CoreDBContext`, `Employee`, `EmployeeDetail`, `OrganizationCommand`.
- Structural reachability is not task relevance. The selector still chooses
  broad headers/entities; non-gold rate >90%.
- SQL coverage is incomplete: only one explicit `exec sp_*` edge. D07/D12 SQL
  need method-name→procedure binding.
- Held-out was already observed by S1/S1b; this run is evaluation, not an
  independent generalization lock.

## 5. Verification

- Dev verifier PASS: **119,394 checks**.
- Held-out verifier PASS: **119,155 checks**.
- Dev determinism: same receipt hash across rerun (`7c75cd6c…`).
- Sanitized dev (no gold/source-ref fields) selected units identical.
- Fabricated edge target (`NO_SUCH.cs`) rejected with exit 1.
- Token counts recomputed with frozen counter.
- `coverage-refint.json` records candidate union separately from Recall@10.

## 6. Artifacts

| Artifact | SHA-256 |
|---|---|
| `reference-index.json` | `55e955c6…a926d` |
| `refint-dev.json` | `7c75cd6c…4403` |
| `refint-heldout.json` | `7f563f50…eb24c` |
| `refint-dev-sanitized.json` | `8d124042…515f` |
| `verify-refint-dev.json` | `f048db1e…f1f6` |
| `verify-refint-heldout.json` | `7ee77629…92c` |
| `coverage-refint.json` | `d1c13e99…1626` |

## 7. Production implications

### Migration

None. The reference index is an external JSON sidecar, built from source. No
v3 index schema change, daemon update, binary replacement or production index
mutation.

### Rollback

Delete the branch/worktree and the external `reference-index.json`; retrieval
continues on the existing v3 index. No data migration or index rebuild is
required.

### If pursued

A production candidate needs engine-side typed extraction, an additive
versioned sidecar, incremental invalidation/reuse, and a separate relevance
model. The core v3 index need not change for the sidecar experiment, but
production integration would be an owner-level schema/feature decision.

## 8. Decision ledger

| ID | Verdict |
|---|---|
| H-R0 candidate coverage | **PASS** (22/25 dev, 20/20 held-out) |
| H-R1 task-relevant selection | **FAIL** (10/25; 90% non-gold) |
| H-R2 held-out generalization | **INCONCLUSIVE** (strict improves, spans fall; contaminated) |
| H-R3 SQL name binding | **OPEN** (only 1 explicit edge; D07/D12 SQL missing) |
| H-R4 resource cost | **PASS for prototype** (25 s, 134 MB, 12 MB artifact) |
| H-R5 no migration | **PASS** (external sidecar) |

## 9. Owner decisions

| ID | Decision |
|---|---|
| **D-RI-1** | Accept `MISSION_PARTIAL`; keep Selection/Expansion and production frozen. |
| **D-RI-2** | Authorize a separate bounded SQL method→procedure binding + relevance experiment, with a fresh held-out fixture. Do not continue scoring the current metadata. |
| **D-RI-3** | Decide later whether any reference sidecar belongs in the engine; it is not approved now. |

## 10. Cleanup state

- Worktree committed and pushed before removal.
- External Cargo target removed after verification; receipts, reference index,
  probes, logs and coverage retained (~? MB).
- No installed binary, daemon, production index/config or shared history was
  modified.
- Only main worktree remains after cleanup.

## 11. Exact next authorized action

If D-RI-2 is approved: build **method-name→SQL-procedure** edges, add a fresh
development fixture, freeze a new independent held-out set, and test whether
task relevance improves without increasing false evidence. Do not merge this
branch.
