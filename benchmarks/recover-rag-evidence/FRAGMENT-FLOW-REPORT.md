# Fix RAG Evidence Flow — fragment-centric pipeline report

Status: `DONE / research-only / PARTIAL qualification`. No production promotion.
Branch: `work/recover-rag-evidence-20261126`, advanced from `5eab0b5` to
`905c699`. Artifacts: `/home/masis/.cache/zvec-grep/fragment-flow-20261126`.

## 1. What changed

The RAG-only pipeline is now:

`Fragment Discovery → Fragment Fusion → Fragment Ranking → Fragment Selection → Evidence Pack`

Implementation is env-gated by `ZVEC_RAG_FRAGMENT_FLOW=1`; normal search is
unchanged. Code references:

- `rust/crates/zg-engine/src/pipelines/indexed_search/pipeline.rs`
  - new `FragmentCandidate`
  - `fuse_fragment_candidates()` keys by `document_id`, computes RRF from
    fragment-level route traces
  - `load_fragment_candidates()` produces one `SearchHit` per fragment with
    entity/file as metadata and one `SearchEvidence`
  - stage dump adds `fragment_fused`, `fragment_id`, `entity_range`,
    `fragment_range`
- `rust/crates/zg-engine/src/pipelines/indexed_search/ranking/mod.rs`
  - fragment flow forces effective `FILE_DECAY=0`
- `rust/crates/zg-engine/src/pipelines/indexed_search/context.rs`
  - RAG fragment dedupe key is `fragment:<fragment_id>`; multiple fragments
    from the same entity/file can coexist

Unchanged: chunking, Potion, tokenizer, index schema, storage, FTS/vector
discovery, RRF constant, normal CLI search behavior.

Binary SHA-256 values:

- F0 baseline `5a4c8201…`
- F1 previous candidate `eda2619b…`
- F2/F3/F4 fragment candidate `6d05ed17…`

## 2. Arms

| Arm | Meaning |
|---|---|
| F0 | baseline, no switches |
| F1 | previous best A1 no-diversity |
| F2 | fragment flow + budget selection + R1 pack |
| F3 | F2 + rank limit 200 |
| F4 | fragment flow only |

Fixed budget for all arms: 24 KiB / 10 items, 12 Rust cases and 12 C#
development cases. No held-out opened.

## 3. Evidence quality and cost

| Arm | Rust exact | Rust precision | Rust tokens | C# exact | C# precision | C# tokens |
|---|---:|---:|---:|---:|---:|---:|
| F0 | 0.250 | 0.049 | 4,213 | 0.083 | 0.007 | 4,624 |
| F1 | **0.333** | 0.065 | 4,768 | 0.083 | 0.006 | 4,672 |
| F2 | 0.153 | 0.042 | 1,302 | **0.167** | 0.030 | 946 |
| F3 | 0.153 | 0.042 | 1,302 | **0.167** | 0.029 | 1,495 |
| F4 | 0.250 | **0.088** | 4,417 | 0.083 | **0.029** | 2,146 |

Runtime (p95 / CPU sum / peak RSS):

| Arm | Rust | C# |
|---|---|---|
| F0 | 1.59 s / 19.15 s / 412,092 KiB | 2.17 s / 24.13 s / 445,820 KiB |
| F2 | 1.78 s / 22.xx s / 415,xxx KiB | 2.31 s / 28.xx s / 453,xxx KiB |
| F4 | 1.94 s / 21.80 s / 415,420 KiB | 2.69 s / 26.95 s / 454,148 KiB |

Oracle ceilings were computed per arm from the loaded candidate pool; F3 uses
a 200-candidate pool, so it is not directly comparable with the 50-candidate
arms. Values ranged Rust `0.528–0.708`, C# `0.347–0.542`. No arm closes the
gap to oracle.

### Gate result

Preregistered gate required `>=+0.05` exact on both languages with `<=25%`
token growth and `<=2x` p95:

- F4 improves precision substantially (Rust `+0.039`, C# `+0.022`) and cuts C#
  tokens by `53.5%`, but exact coverage is unchanged on both languages.
- F2/F3 improve C# exact by `+0.083` but regress Rust exact by `−0.097`.
- No arm passes the both-language evidence gate.

Fragment identity goal is met: a F2/R01 pack contains 7 fragments, including
3 from `clap_builder/src/builder/arg.rs`; duplicates are now at source-range
level rather than file/entity level.

## 4. Task correctness

Tool-free Pi `opencode-go/hy4-flash-free`, concise bounded answer, no repo
tools, deterministic keyword/abstention grading.

| Split | F0 | F2 | F4 |
|---|---:|---:|---:|
| Rust correct /12 | 2 | 1 | 2 |
| C# correct /12 | 0 | 0 | 0 |
| Rust total tokens | 50,782 | 20,704 | 53,208 |
| C# total tokens | 66,695 | 18,382 | 34,431 |

The precision/token gains did not translate into more correct answers on this
small grader. C# keyword grading remains weak (high abstention). This is a
limitation, not a proof of answer-level equivalence.

## 5. Regression

- Normal-search parity: **6/6 sampled Rust/C# cases byte-identical** between
  baseline `5a4c8201…` and fragment candidate `6d05ed17…` with all RAG env
  switches unset.
- Focused engine tests:
  `cargo test -p zg-engine --lib -- pipelines::indexed_search`
  → **49 passed, 0 failed, 1 ignored**.
- No production binary, daemon, index, config, model or held-out changed.

## 6. Failure taxonomy and limits

- **Unit-resolution failure (fixed)**: `evidence.first()` discarded useful
  fragments; fragment flow now keeps each fragment as a first-class unit.
- **Selection failure (not fixed)**: F2/F4 still deliver only 0.15–0.25 Rust
  exact coverage; oracle is much higher. Better units alone do not solve
  query-to-evidence matching.
- **Budget/delivery failure**: R1 pack with 24 KiB still drops useful fragments;
  C# gains but Rust regresses.
- **Measurement limits**: one run per arm, 12 dev cases per language, heuristic
  oracle parsing, weak C# answer grader, different rank pools for F3.

## 7. Decision

Fragment-centric flow is implemented and verified, with clear evidence that
same-file/same-entity fragments can coexist and that precision improves. It is
**not promoted** because exact coverage and task correctness did not improve
on both languages. If pursuing further work, the bottleneck is now isolated to
fragment-level ranking/selection and context budget, not entity aggregation or
`evidence.first()`.
