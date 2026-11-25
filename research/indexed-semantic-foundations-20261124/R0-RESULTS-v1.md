# Potion Code 16M v2 — R0 independent-source results

> **Historical provenance notice:** this score run used the tokenizer's undeclared built-in 512-token cap. Eighteen candidate inputs had additional tokens up to the specified 1,024-token R4 cap. The corrected R0 output is in `corrected-r0-v2/`; aggregate pairwise and annotated-range summary values in this historical report happen to be unchanged, but its old score/token hashes are superseded. Use `INDEXED-SEMANTIC-FOUNDATIONS-HANDOFF-v1.md` for current conclusions.

**Scope:** local R0 direct-cosine measurement only. This is not a product, ANN, or answer-quality trial. The pairwise diagnostic has 11 groups and 15 candidate-input pairs; the 40-group required-range ranking set is not exhaustively relevance-labeled.

## Frozen inputs and execution

- 40 behavior groups, 120 paraphrased queries, 51 locked C# files, and 1,591 curated R4 candidate fragments: 657 ANTLR, 373 Dapper, 561 Newtonsoft.Json.
- 119 annotated source-range segments: 83 required positive, 33 negative-control, and 3 partial ranges. Pairwise labels cover 11 groups and 15 explicit candidate-input pairs.
- Potion Code 16M v2: 256 dimensions, cosine, normalized, 1,024 token limit, no query/document prefix. Model and tokenizer hashes are pinned in the R0 freeze manifest.
- All 120 queries and 1,591 candidates fit under the cap. Maximum lengths were 35 query tokens and 512 candidate tokens. All 228 range/candidate input mappings survived truncation and unknown-token filtering.
- The score-blind Rust scorer generated 190,920 query/candidate cosine scores. Replaying it produced a byte-identical score file. End-to-end replay took 3.02 seconds and peaked at 97,644 KiB RSS on this machine.

## Hard-negative discrimination

Across 15 candidate-input pairs and three paraphrases per group, Potion favored the adjudicated supporting input in **33 of 45 comparisons**, with 12 losses and no ties. The macro mean of the 11 group scores was **0.773**; 8 groups favored the positive, 3 favored the hard negative. The mean cosine margin was **+0.0908** (median +0.0751; observed range −0.2641 to +0.4864).

The group results were:

| Group | Positive win fraction |
|---|---:|
| `dev-c4-02` | 0.833 |
| `dev-c5-01` | 0.333 |
| `dev-c5-02` | 1.000 |
| `dev-c5-03` | 1.000 |
| `dev-c8-01` | 1.000 |
| `held-dapper-c3` | 1.000 |
| `held-dapper-c5` | 1.000 |
| `held-newton-c2` | 0.000 |
| `held-newton-c3` | 1.000 |
| `held-newton-c5` | 0.333 |
| `held-newton-c6` | 1.000 |

Three groups showed paraphrase-dependent pairwise scores: `dev-c4-02` had a tied query after two positive wins; `dev-c5-01` won only one of three phrasings; and `held-newton-c5` won one of three. Two of these were group-level losses. `held-newton-c2` lost all three comparisons, with small but consistent negative margins (−0.0062 to −0.0291): the implementation input outranked the requested abstract declaration.

The held-out subset has only six pairwise groups across two repositories. Its family-cluster bootstrap is descriptive: equal-repository mean 0.792, 95% interval 0.583–1.000. The category and repository cells are too small for reliability claims.

## Required-range completion under competition

This measures rank of the frozen required source ranges, not exhaustive semantic recall. A range is found when an R0 candidate input contains its exact physical bytes or its provenance-verified metadata bytes; jointly required segments complete only when every range in the frozen set is found.

| Candidate pool | Mean size | Completion@1 | Completion@5 | Completion@10 | Completion@20 | Median completion rank |
|---|---:|---:|---:|---:|---:|---:|
| Primary file | 27.2 | 30.0% | 75.8% | 89.2% | 98.3% | 3 |
| Linked evidence files | 43.2 | 24.2% | 64.2% | 83.3% | 97.5% | 4 |
| Curated repository slice | 581 | 15.8% | 32.5% | 53.3% | 68.3% | 9.5 |
| Curated split slice | 768 | 15.0% | 32.5% | 50.8% | 67.5% | 10 |
| All 51 selected files | 1,591 | 14.2% | 30.8% | 45.8% | 60.0% | 14 |

The numbers are macro-averaged by group over three paraphrases. At the full curated-pool size, at least one paraphrase completed the source set for 8/40 groups by rank 1, 22/40 by rank 5, 27/40 by rank 10, and 34/40 by rank 20. All three phrasings completed by rank 5 for only 5/40 groups.

The Dapper repository slice has 373 candidates, so its repository-level k=500 result is underfilled. These selected-file pools are not full repositories and do not exercise ANN retrieval or the production entity cutoff.

For category 6, jointly required evidence completed at ranks 5/6/7 for `dev-c6-01`; 16/32/4 for `dev-c6-02`; 26/14/54 for `dev-c6-03`; 2/3/2 for `held-dapper-c6`; and 1/6/1 for `held-newton-c6`. These five groups are descriptive only.

## Secondary lexical controls

After the R0 vectors were frozen, a score-blind secondary control ranked the same 1,591 `r0_input` candidates using pool-specific BM25, identifier-component overlap, and equal-weight RRF of BM25 and R0 cosine rank. The lexical tokenizer and BM25 parameters were fixed before those scores were computed, but after the vector output existed; treat this as exploratory, not preregistered. The 11-group/15-pair adjudicated subset and frozen required ranges were joined only after raw rankings were written. The initial v1 run was superseded after audit found that its tokenizer split adjacent letters and digits contrary to protocol; v2 follows the protocol and uses exact tie comparisons. The correction did not change the 45 pair outcomes; it changed some full-pool completion ranks slightly.

| Method | Pair outcomes (45 paraphrase comparisons) | Macro group outcome | Group results |
|---|---:|---:|---:|
| R0 cosine | 33 wins / 0 ties / 12 losses | 0.773 | 8 wins / 0 ties / 3 losses |
| BM25 | 37 wins / 0 ties / 8 losses | 0.864 | 10 wins / 1 tie / 0 losses |
| Equal-weight RRF | 37 wins / 1 tie / 7 losses | 0.856 | 9 wins / 0 ties / 2 losses |
| Entity/scope token overlap | 9 wins / 29 ties / 7 losses | 0.530 | 3 wins / 5 ties / 3 losses |

These are repeated paraphrases of only 11 task groups, not 45 independent task samples. BM25 favored the adjudicated supporting input in more comparisons than cosine, including moving `held-newton-c2` from a 0.000 group score under cosine to 0.667; it did not establish broad superiority. RRF retained both signals and had two group losses (`dev-c5-01`, `held-newton-c2`). Identifier overlap is a weak control: all 120 queries had at least one match somewhere in the split pool, and common component matches make its many tied candidate scores poor evidence of useful exact-symbol alignment.

Across the six held-out pairwise groups from Dapper and Newtonsoft, macro group outcomes were 0.722 for cosine, 0.889 for BM25, and 0.847 for RRF. This small two-repository slice shows no demonstrated vector advantage over lexical and does not pass a vector-plus-lexical complementarity gate; it is not a reliable estimate of performance on other repositories.

For the full 1,591-candidate curated pool, required-range-set completion was:

| Method | @1 | @5 | @10 | @20 | Median completion rank |
|---|---:|---:|---:|---:|---:|
| R0 cosine | 14.2% | 30.8% | 45.8% | 60.0% | 14 |
| BM25 | 18.3% | 41.7% | 53.3% | 65.0% | 9 |
| Equal-weight RRF | 20.0% | 45.0% | 61.7% | 73.3% | 6 |

The RRF gains are evidence that lexical rank contributes useful signal for these frozen source ranges on this curated corpus; they do not isolate which component caused the gain, measure exhaustive relevance recall, or establish gains in ANN, R4 acquisition, entity cutoff, or evidence packing. Candidate inputs and ranking units remain R4 fragments, so this control does not test method-aware representation.

## Interpretation and limits

Potion has measurable discriminative signal on these controls, but 3 of 11 pairwise groups favored the hard negative, Three groups showed paraphrase-dependent pairwise scores, including two group-level losses: `dev-c4-02` had a tied query after two positive wins, while `dev-c5-01` and `held-newton-c5` switched between wins and losses across phrasings. The secondary lexical comparison shows that BM25 scored better than cosine on this small adjudicated subset and that RRF improved frozen required-range completion in the curated pool (@10: 61.7% vs. 45.8% for cosine; @20: 73.3% vs. 60.0%). This strengthens the case for lexical signal in retrieval, while leaving representation, candidate acquisition, ANN, entity cutoff, and evidence packing unresolved. In the broader curated competition pools, cosine ranks fell as the pool grew: median required-range completion moved from rank 3 in the primary file to rank 14 across all selected files.

The rank results use frozen required source ranges, not exhaustive candidate relevance judgments. The 29 groups outside the pairwise subset are rank-only; pending whole-query labels were not treated as negative. Cosine values are similarities, not relevance probabilities. Category-7 alternative-set discrimination was not measured because the frozen category-7 groups have jointly required ranges only.

No R1–R5 representation comparison, query-vector reuse trial, RAG development trial, or answer trial has been run yet. The next experiment is the frozen representation variants under a common transformed-range survival mask, with lexical/RRF retained as exploratory retrieval controls. No product/API/index changes were made.

## Reproduction artifacts

- Frozen score-blind inputs and labels: `frozen-r0-v1/`
- Token IDs, offsets and range survival: `frozen-r0-v1/tokenization-r0-v1/`
- Raw cosine output: `frozen-r0-v1/scores-r0-v1.jsonl`
- Raw analysis: `frozen-r0-v1/analysis_labels/R0-analysis-v1.json`
- Lexical protocol: `R0-LEXICAL-CONTROL-PROTOCOL-v1.md` (SHA-256 `9e9686dfe2582341f7447a5028f5e09ae9e3c1d7b0ae0eb9cbcde625e66b8f9d`)
- Corrected raw BM25, identifier-overlap and RRF rankings: `frozen-r0-v1/lexical-control-v2/raw-lexical-ranks.jsonl` (SHA-256 `6111872f9abb4a3655795be644bb52538716c6da538f21bc121ad840cdca60b2`)
- Corrected lexical analysis: `frozen-r0-v1/analysis_labels/R0-lexical-analysis-v2.json` (SHA-256 `23eda2114306f83017cbf78df78f8e9d4be61e65de690397cceb32ec7693a189`)
- Lexical output manifest and receipts: `frozen-r0-v1/lexical-control-v2/manifest.json`, `R0-LEXICAL-CONTROL-RECEIPT-v2.json` (v1 artifacts retained for audit and marked superseded)
- Reproduction scripts: `scripts/compute_r0_lexical_controls_v2.py`, `scripts/analyze_r0_lexical_controls_v2.py`
- Independent Sol High lexical review: `SOL-R0-LEXICAL-REVIEW.md`
- Score-output manifest: `frozen-r0-v1/R0-score-output-manifest.json`
- Frozen R0 manifest SHA-256: `c73ca8b107751d59e3a4dd78b9aa19e33008e43de96d3d41c98407a3b7f333e4`
- Raw score SHA-256: `fd75bf620aa7eb6c84ccfc5ca40e861a5f57e83f0d6f5eca6e0722b1e4772d3b`
