# RAG-S1 Selection baseline and metric validity — 2026-11-15

Status: **READ-ONLY/EXPERIMENT BASELINE.** Selection and Expansion remain
frozen in production. No engine, index, model, daemon, installed binary or
production data was changed.

Worktree: `work/rag-s1-20261115` (from `work/rag-requal-20261115` @ `f93d049`,
base `27b81c0`). External root:
`/home/masis/.cache/zvec-grep/rag-s1-20261115/`.

## 1. Frozen inputs

| Input | SHA-256 / value |
|---|---|
| Dev cases (D01–D12) | `27626109…a4a9c311`; 12 cases / 25 spans |
| Held-out cases (H01–H08) | `2d81048e…e38f6d`; 8 cases / 20 spans |
| Corpus manifest | `c706c8dd…6359cdb`; 822 files / 5,400,637 bytes |
| Corpus tree | `2b761e66…ab874a3` |
| Index manifest | `d7052d3f…473fc4a`; v3, 818 files, 0 failed/pending |
| Requal receipt | `17ae8f32…8ce8e0` |
| Dev candidate probe | `84b7593a…9188e27` (7 calls × 50 units) |
| Held-out candidate probe | `74a83698…255c4c51` (5 FTS/vector/hybrid calls × 50; no rg) |
| Tokenizer | `107bbd…f3d45` (Potion code tokenizer) |
| Token counter | `74941efc…f7d6a` (`rag_token_count`, truncation disabled) |

Held-out evaluation uses `source_evidence` (`start_line`/`end_line` or
`spans`), not the dev `source_refs` schema. The verifier checks the per-file
`sha256` in both schema variants against the frozen corpus.

## 2. Definitions (corrected)

- **Candidate union**: any retrieved/derived unit overlapping the gold span,
  regardless of rank. This is not Recall@10.
- **Recall@10**: first 10 selected units, span overlap, macro over cases.
- **Strict evidence**: one selected unit covers ≥50% of the gold span and is
  no longer than `max(100, 3 × gold length)`.
- **Selectable ceiling**: exact maximum gold spans coverable by ≤10 units,
  evaluation-only, using gold masks; never a runtime policy.
- **Tokens**: tokenizer count of the packed path headers + source lines, not a
  `chars/4` proxy. A pack is rejected if it exceeds 3,716 tokens.
- **Gold leakage**: policy output must be identical when all gold/label fields
  are removed from the case file.

## 3. Candidate pool and baseline

The prior requalification's engine-only precise candidate ceiling is **18/25**
before Selection filters. Selection's structural pool (engine declarations,
direct members, blank-line blocks, SQL, no rg-line guessing) has a **16/25**
selectable ceiling on dev.

| Policy | Dev spans | Dev Recall@10 macro | Dev strict macro | Mean tokens | Max tokens | Ceiling |
|---|---:|---:|---:|---:|---:|---:|
| Fixed `fts_code_tokens` top-10 (B0 policy) | 5/25 | 0.3333 | 0.2917 | 8,048.8 | 28,426 | 16/25 |
| Exact-symbol Selection | 6/25 | 0.3611 | 0.3194 | 766.3 | 3,389 | 16/25 |
| Structural Selection | 6/25 | 0.3611 | 0.3194 | 2,691.0 | 3,674 | 16/25 |
| Oracle (evaluation only, budgeted) | 15/25 | 0.7361 | 0.4722 | 1,472.8 | 3,501 | 16/25 |

Baseline fixed FTS exceeds the token budget on 6/12 dev cases. Both Selection
prototypes enforce the budget; none reaches the Recall@10 gate.

Held-out, with the policy frozen before running:

| Policy | Held-out spans | Recall@10 | Strict | Mean tokens | Max tokens |
|---|---:|---:|---:|---:|---:|
| Fixed `fts_code_tokens` top-10 | 9/20 | 0.5000 | 0.2500 | 16,410 | > budget |
| Exact-symbol | 7/20 | 0.5313 | 0.4063 | 1,052.9 | 3,360 |
| Structural | 9/20 | 0.6250 | 0.5000 | 2,954.1 | 3,675 |
| Oracle (evaluation only) | 14/20 | 0.8375 | 0.6250 | 871.8 | 3,501 |

## 4. Metric validity matrix

| Dimension | Check | Result |
|---|---|---|
| Denominator | fixed 25 dev / 20 held-out spans | PASS |
| Candidate union vs Recall@10 | reported separately | PASS |
| Strict definition | coverage ≥0.5, size cap | implemented in verifier |
| Token accounting | real tokenizer, no truncation | PASS; max 3,674 dev, 3,675 held-out |
| Pack budget | per-case ≤3,716 | PASS for exact/structural; baseline fails |
| Determinism | rerun identical trace hash | PASS (`65bf94e2…`) |
| Gold leakage | sanitized cases produce identical units | PASS |
| Source identity | per-file SHA-256 in cases vs corpus | PASS |
| Range provenance | selected range inside frozen pool range | PASS |
| Fabricated range | verifier negative control | rejected |
| Duplicate unit | verifier negative control | rejected |
| Wrong path | verifier negative control | rejected |
| Held-out isolation | no D/H mixing; separate receipt | PASS |

## 5. Baseline interpretation

The exact-symbol policy improves the fixed FTS baseline slightly:
+1 span, +0.028 Recall@10, +0.028 strict, at ~10× lower token cost. Structural
expansion adds no dev recall over exact and uses ~3.5× the tokens. Neither
reaches 0.60 Recall@10 or 6 additional selected spans.

The candidate pool can support **16/25** spans, but the runtime policies select
only **6/25**. The gap is Selection signal, not context budget.
