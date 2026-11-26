# Complete Fragment Ranking & Selection — report

Status: `DONE / research-only / NO-GO for promotion`. Branch pushed:
`work/recover-rag-evidence-20261126` through `b6429b2`. Start point `0ca0090`.
Artifacts: `/home/masis/.cache/zvec-grep/fragment-ranking-20261126`.

## 1. Implementation

New RAG-only flow:

`Fragment Fusion → Fragment Relevance Ranking → Budget-driven Selection → Verified Evidence Pack`

Source changes:

- `rust/crates/zg-engine/src/pipelines/indexed_search/ranking/mod.rs`
  - `rank_fragments()` ranks each fragment independently using normalized RRF,
    FTS route score, Potion vector score, fragment-content query coverage and
    a bounded structural metadata bonus.
  - No file diversity, no delivery cutoff inside ranking.
  - Modes: mixed weights (`ZVEC_RAG_FRAGMENT_RELEVANCE=1`), RRF-dominant
    (`ZVEC_RAG_RANK_RRF_ONLY=1`), optional bounded structural support
    (`ZVEC_RAG_RANK_RRF_SUPPORT=1`).
- `rust/crates/zg-engine/src/pipelines/indexed_search/pipeline.rs`
  - fragment flow calls `rank_fragments()` instead of entity-driven `rank()`;
    ranked stage now records fragment id.
- `rust/crates/zg-engine/src/pipelines/indexed_search/context.rs`
  - `fragment_budget_select()` and `fragment_budget_select_v2()` select by
    relevance, query coverage, proportional source-range overlap and context
    cost under the 24 KiB / 10 item budget.
  - Verified pack assembly no longer applies a second pruning pass in fragment
    relevance mode.
- Normal search paths are unchanged when all RAG env switches are absent.

Binaries:

- G0 baseline `5a4c8201…`
- G1 previous fragment candidate `6d05ed17…`
- G2/G3 ranking candidate `39238bd0…`
- G4/G5 candidate `a5e43884…`
- H1 candidate `6fa0b013…`
- I1 candidate `80419444…`
- J1 final candidate `0c470f22…`

## 2. Arms

| Arm | Description |
|---|---|
| G0 | entity flow baseline |
| G1 | previous fragment flow + old budget selection |
| G2/G3 | mixed fragment relevance + ratio/cost selection, rank 50/200 |
| G4/G5 | mixed relevance + score-first selection, rank 50/200 |
| H1 | mixed relevance with RRF-dominant fallback, score-first selection |
| I1 | minimal RRF relevance + proven rank-ordered budget selection |
| J1 | I1 + bounded structural metadata support |

## 3. Quality and cost at 24 KiB / 10 items

Macro over 12 Rust and 12 C# development cases. No held-out.

| Arm | Rust exact | Rust precision | Rust tokens | C# exact | C# precision | C# tokens |
|---|---:|---:|---:|---:|---:|---:|
| G0 | **0.250** | 0.049 | 4,213 | 0.083 | 0.007 | 4,624 |
| G1 | 0.153 | 0.042 | 1,302 | **0.167** | **0.030** | **946** |
| G2/G3 | 0.069 | 0.011 | 1,608 | 0.083 | 0.024 | 1,676 |
| G4/G5 | 0.069 | 0.017 | 1,529 | 0.083 | 0.019 | 1,426 |
| H1 | 0.153 | 0.051 | 1,415 | 0.083 | 0.019 | 1,083 |
| I1/J1 | 0.181 | **0.068** | 1,508 | 0.083 | 0.025 | 1,691 |

Runtime summary (p95, CPU sum, peak RSS):

| Arm | Rust | C# |
|---|---|---|
| G0 | 1.77 s / 19.49 s / 412,288 KiB | 2.30 s / 24.51 s / 446,196 KiB |
| G1 | 1.93 s / 22.13 s / 414,976 KiB | 2.80 s / 30.40 s / 454,444 KiB |
| I1 | 2.83 s / 31.35 s / 416,964 KiB | 8.62 s / 82.74 s / 457,092 KiB |
| J1 | 2.79 s / 30.58 s / 416,580 KiB | 8.42 s / 82.42 s / 457,012 KiB |

Oracle ceilings differ by candidate pool and stage parsing; G2+ loaded pools
show much higher ceilings than delivered packs, confirming that the remaining
loss is selection/ranking, not discovery.

### Gate result

Preregistered gate required exact coverage gain over G1 and G0 on both
languages with no precision regression, <=25% token growth vs G1 and <=2x p95:

- I1/J1 improve Rust exact from `0.153 → 0.181` and precision from `0.042 →
  0.068`, but still below G0 exact `0.250`, and regress C# from `0.167 →
  0.083`.
- H1 is neutral on Rust exact and regresses C#.
- G2/G4 and their rank-200 variants lose coverage substantially.
- All new-rank arms have higher latency; C# p95 is roughly `3x` G1.

**No arm passes the both-language gate. NO-GO for promotion.**

## 4. Task correctness

Tool-free Pi `hy4-flash-free`, concise bounded answer, deterministic
keyword/abstention grading.

| Split | G0 | G1 | I1 |
|---|---:|---:|---:|
| Rust correct /12 | **3** | 2 | 1 |
| C# correct /12 | 0 | 0 | 0 |
| Rust total tokens | 50,743 | 20,849 | 23,405 |
| C# total tokens | 66,495 | 18,367 | 27,444 |

The evidence-precision gain in I1 did not improve answer correctness and was
worse than G1 on Rust cases. C# grading remains weak/high-abstention.

## 5. Regression

- Normal-search parity: **6/6 sampled Rust/C# cases byte-identical** between G0
  baseline and final candidate with all RAG switches unset.
- Focused engine tests: `cargo test -p zg-engine --lib -- pipelines::indexed_search`
  → **49 passed, 0 failed, 1 ignored**.
- No production binary, daemon, index, config, model or held-out changed.

## 6. Failure taxonomy

- **Relevance-weight defect (observed)**: mixed FTS/vector/coverage weights
  reorder fragments away from the gold-bearing RRF pool; G2/G4 lose exact
  coverage to `0.069`.
- **Selection-cost defect (observed)**: ratio-by-cost utility prefers small but
  weakly relevant fragments; score-first G4 improved precision but not
  coverage.
- **Structural-support limit (tested)**: bounded symbol/definition support (J1)
  produced no measurable change over I1; entity metadata cannot repair the
  fragment ranking gap in this setup.
- **Latency regression (observed)**: fragment relevance mode computes extra
  per-fragment signals and keeps a wider ranked pool; C# p95 rises from `2.8 s`
  (G1) to `8.4–8.6 s`.
- **Measurement limits**: one run per arm, 12 dev cases per language, heuristic
  oracle parsing, weak C# grader, different rank pools across arms.

## 7. Decision

The requested fragment relevance ranking and budget-driven selection are
implemented end-to-end and verified, including a verified pack with no second
pruning. They do **not** improve both languages: Rust precision improves in I1,
but C# coverage/precision/latency and Rust task correctness regress versus G1.

Do not promote. The remaining bottleneck is now isolated to fragment-level
relevance estimation under a fixed context budget; discovery, fusion and
fragment identity preservation are no longer the limiting stages.
