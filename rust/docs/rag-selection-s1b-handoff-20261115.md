# RAG-S1b signal feasibility — handoff 2026-11-15

Status: **NO_GO.** Query-to-evidence relationship signals improve dev recall but
fail the gate, do not generalize to held-out, and produce too many false
evidence edges. Selection/Expansion remain frozen.

## 1. Branch and artifacts

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-s1b-20261115` |
| Branch | `work/rag-s1b-20261115` |
| Base | `1d8f57a3b06834b8beb3afaea78ae37ac2c6dcf4` (S1) |
| External root | `/home/masis/.cache/zvec-grep/rag-s1b-20261115/` |
| Dev probe | `84b7593a…9188e27` |
| Held-out probe | `74a83698…255c4c51` |
| Dev cases | `27626109…a4a9c311` |
| Held-out cases | `2d81048e…e38f6d` |
| Corpus manifest | `c706c8dd…6359cdb` |
| Index manifest | `d7052d3f…473fc4a` (v3) |
| Tokenizer | `107bbd…f3d45` |

Receipts:

| Receipt | SHA-256 |
|---|---|
| `signal-dev.json` | `c1dcd9b6…c4f5f1` |
| `signal-dev-rerun.json` | `c1dcd9b6…c4f5f1` (deterministic) |
| `signal-dev-sanitized.json` | `bc84606d…77f1` (units identical) |
| `signal-heldout.json` | `a54ec030…583c6a` |

## 2. Gate result

| Gate | Target | Dev | Held-out |
|---|---:|---:|---:|
| Recall@10 | ≥0.60 | **0.5694 FAIL** | 0.5000 |
| Strict | ≥0.30 | **0.4861 PASS** | **0.2500 FAIL** |
| Selected spans | ≥16/25 | **10/25 FAIL** | 9/20 |
| Tokens mean/max | ≤3,716 | 3,151.7 / **3,711** PASS | 3,228.4 / 3,588 PASS |
| Deterministic | required | PASS | PASS |
| Gold leakage | none | PASS | — |
| Provenance | required | PASS | PASS |

S1 comparison: dev 6→10 spans, Recall@10 0.361→0.569, strict 0.319→0.486,
tokens 2,691→3,152. Held-out: 9→9 spans, strict 0.500→0.250 (regression).

## 3. What was recovered / lost

Recovered 5 spans: D01 controller + service, D03 prefix, D06 first write path,
D10 `CoreService` header/property group.

Lost 1 span: D07 `RestoreEmployeeById` was displaced by higher-scoring
headers/prefixes. Net +4.

Still missing: D02 (0/6), D05 (0/1), D07 (0/3), D12 (0/2); D06 remains 1/3.
Held-out: H06 gains 4 spans but no strict; H02/H08 lost (4 spans); strict 7→3.

## 4. Signal / false-edge finding

- Direct call/SQL edges are real in source but often point to shared helpers or
  sibling operations, not the requested evidence.
- Dev: 2 true edges / 6 false edges; 47 of 57 selected units non-gold.
- Held-out: 3 true / 18 false; 32 of 37 selected units non-gold.
- False-edge rate 75% dev, 86% held-out. This is the decisive failure.
- Dev-specific identifier/header/prefix signals do not transfer to English
  held-out wording.

Remaining 7 required spans are cross-file/SQL or semantic:
D02 `CoreDBContext`/`Employee`, D07 Org/SQL, D10 `CoreDBContext`, D12 SQL.
They are not in the engine candidate pool; S1b correctly did not add them.

## 5. Ceiling reconciliation

- Requal precise candidate union: **18/25** (not Recall@10).
- S1 fixed pool ceiling: **16/25**.
- Budgeted oracle: **15/25**.
- S1 runtime: **6/25**.
- S1b runtime: **10/25**.

S1b cannot exceed 16/25 by construction. It realizes 10/25; the remaining 6
are ranking/selection failures or `NOT_SELECTABLE`.

## 6. Verification

- Independent verifier PASS for dev and held-out: identity, pool containment,
  source SHA-256, token counts, metric arithmetic, edge-source existence,
  determinism, D/H isolation.
- Negative controls rejected: fabricated range, duplicate, outside-pool unit,
  fabricated edge path/name, identity/token tamper.
- Sanitized dev run yields identical units.
- Dev rerun hash identical.
- Resource: 45.5 s wall / 45.1 s CPU / 112,636 KiB peak RSS for 12 cases;
  249 files / 2.995 MB dev; 301 files / 3.498 MB held-out.

## 7. Dependency map

### Selection

Do not unfreeze. The signal layer is not reliable enough: held-out strict
regresses and false edges dominate. Exact-symbol fallback remains the only
safe component from S1/S1b.

### Expansion / Discovery

The unsolved spans require cross-file DbContext/SQL binding or semantic
translation. The current index has no reference graph or SQL procedure binding;
E1 is experimental/excluded. Do not reopen E1R without a resource gate.

### R1

Unchanged; no Evidence Delivery or release claim. Previous R1 correctness
evidence remains (`api::evidence` 10/10, `r1_public_api` 2/2).

## 8. Architectural implication / next unit

If this direction is revisited, the minimum viable next unit is a **bounded,
reference-aware Discovery experiment** with:

- typed call/reference and SQL binding provenance, not name-similarity alone;
- explicit false-edge accounting and a ≤64 MiB resource target;
- query-to-identifier alias evidence that works on both Vietnamese dev and
  English held-out wording;
- held-out frozen before tuning.

Do not implement that architecture from S1b; it needs a separate owner-authorized
proposal. Do not continue ranking heuristics on the same metadata.

## 9. Scope / cleanup

- No production Selection/Expansion, index, schema, model, daemon, binary or
  production data was changed.
- Prototype files are two Python tools (`selection_s1b_20261115.py`,
  `verify_selection_s1b_20261115.py`); no engine source change.
- External Cargo target (~958 MB) is removed after verification; receipts,
  probes, corpus/index and logs retained.
- Worktree is committed/pushed, then removed; only the main worktree remains.

## 10. Owner decisions

| ID | Decision |
|---|---|
| **D-S1b-1** | Accept `NO_GO`; keep Selection/Expansion frozen and stop tuning on this metadata. |
| **D-S1b-2** | Optionally authorize a separate reference-aware Discovery proposal with false-edge/resource gates; do not open it here. |
| **D-S1b-3** | Keep installed `0.0.1`/v2; no release/install unit. |

Implementation/docs commit: recorded in the branch tip and remote ref after the
documentation commit.
