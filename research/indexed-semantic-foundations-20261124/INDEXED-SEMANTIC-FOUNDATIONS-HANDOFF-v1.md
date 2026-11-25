# Indexed Semantic Foundations — research handoff

**Scope.** Offline, local CPU measurements on the locked 51-file C# corpus. No index was rebuilt, no source/API/default changed, and no external model or answer-generation call was made. These measurements do not establish ANN retrieval, production acquisition, B2 selection, evidence packing, or answer quality.

## R0 model signal and lexical controls

The corrected R0 uses Potion Code 16M v2 weights SHA-256 `75cf7a6c…227623c`, tokenizer SHA-256 `107bbdcb…c3f3d45`, 256 dimensions, mean static-token embeddings, L2 normalization, cosine, no prefix, and the R4 cap of 1,024 tokens. The original probe accidentally honored tokenizer.json's built-in 512-token truncation. Eighteen candidates had 512 tokens in the old receipt but more than 512 and at most 592 tokens when encoded without that hidden cap. Their first 512 token IDs match exactly. The old R0 vector scores and any RRF derived from them are superseded; the 18 score vectors changed, but the reported pairwise and required-range rank summaries happened to remain unchanged.

Corrected R0 still favors adjudicated supporting inputs in **33/45** paraphrase comparisons across 11 groups/15 pairs (12 losses, no ties; macro group score 0.773; 8 group wins, 3 losses). Its held-out macro group score is 0.722 across six groups in Dapper and Newtonsoft, with the repositories diverging (Dapper 1.0; Newtonsoft 0.583).

BM25 over the same full R0 strings favors support in **37/45** comparisons (macro 0.864; 10 group wins, 1 tie, no losses). Corrected equal-weight RRF has 37 wins, 1 tie and 7 losses (macro 0.856; 9 group wins, 2 losses). Held-out macro group outcomes are cosine 0.722, BM25 0.889 and RRF 0.847. This sample shows no vector advantage over lexical and no vector-plus-lexical complementarity-gate pass.

On the protocol's split-specific curated pool (mean 768 candidates), corrected cosine required-range-set completion is @10 50.8% and @20 67.5%; BM25 is 53.3% and 67.5%; corrected RRF is 62.5% and 74.2%. RRF improves this annotated-range measure, but its held-out pairwise result trails BM25. A separate all-51-file, 1,591-candidate pooled stress result is secondary and is not used for split evaluation. These are not exhaustive semantic recall rates.

## Frozen R0–R4 representation comparison

The R0–R4 inputs follow frozen `CONTRACT-v1.md`, preserve the same 1,591 fragment IDs and source anchors, and use the same query text. R1–R3 mark non-applicable fragments instead of substituting unrelated R0 text. Tokenization uses the exact tokenizer with embedded truncation disabled, then applies the first-1,024-token cap. No R0–R3 candidate is truncated; R4 truncates 11 candidates at 1,024. Their appended structure suffixes are incomplete, but none of those candidate IDs supplies a positive range or pair input in the common mask. The common source-survival mask was frozen before corrected scoring: 34/83 positive ranges survive individually across all representations; **24** of those ranges contribute to sufficient sets in **15/40** included range groups. **7/15** pair inputs survive across five pair groups; two `dev-c4-02` pairs are outside the 15 range groups. These separate denominators are preserved below.

| Representation | Applicable candidates | Pairwise group score | Common-mask split-pool completion@10 | Common-mask split-pool completion@20 | Unique-source-byte completion ≤24,576 |
|---|---:|---:|---:|---:|---:|
| R0 current fragment | 1,591 | 0.833 (4 wins/1 loss) | 48.9% | 71.1% | 82.2% |
| R1 body intersections | 1,060 | 0.800 (4/1) | 48.9% | 57.8% | 73.3% |
| R2 signature + body | 1,095 | 0.800 (4/1) | 53.3% | 68.9% | 80.0% |
| R3 qualified owner + body | 1,095 | 0.733 (4/1) | 53.3% | 68.9% | 86.7% |
| R4 fragment + structure tags | 1,591 | 0.833 (4/1) | 51.1% | 64.4% | 77.8% |

The pairwise denominator is only seven candidate pairs and 21 query comparisons. All five variants retain the same held-out pair pattern: Dapper wins its one common pair group; Newtonsoft loses its one common pair group. R3 improves split-pool completion@10 by 4.4 percentage points and unique-source-byte completion by 4.4 points, but its pairwise score is lower than R0 and it does not meet the frozen held-out improvement gate. R4 gains 2.2 points at @10 but falls behind at @20 and under the source-byte budget; it does not improve pairwise discrimination. The 24,576-byte measure counts unique original fragment anchors and verified metadata source ranges after deduplication; it measures evidence exposure, not transformed-input token count or the frozen equal-token-budget ablation. No representation is selected for integration.

R5 is a separate method-unit architecture with **718 units from 659 methods**, including 41 methods split into 1,421 UTF-16-unit windows with 284-unit overlap. No R5 input is truncated; one candidate has an unknown token, in a file with no positive ranges. R5 covers 67/83 unique positive source ranges and at least one sufficient set in 36/40 groups. On its own covered set, split-pool completion@10 is 57.4%, @20 69.4%, and macro group completion within 24,576 unique source bytes is 80.6%. These are separate-track diagnostics; differing unit count and coverage prevent a raw rank@k comparison with R0–R4.

## Structure mapping and architecture evidence

The R4 v3 C# Tree-sitter 0.23.5 mapper checked 51 locked files: 1,178 entities, 1,591 physical fragments, and 659 unique method ranges matching all 659 function entities exactly. Mapping validation is consistency with the same extraction path, not independent parser accuracy. File-wide geometry finds 288 fragments intersecting multiple method bodies and 29 methods whose bodies are split across fragments; 610 method bodies are fully contained by at least one fragment. These facts can represent multi-owner fragments and method/body/documentation overlap. The metadata-only R4 vector trial did not show a consistent gain: R4 rose at split-pool @10 but fell at @20 and under the byte budget.

The R4 v3 line is commit `e7d0a89…`; it has a C# Tree-sitter grammar. The maintained checkout at `6635665…` falls back to plain text for `.cs`; its C# facts parser is not the same capability as structural C# extraction. Do not generalize the R4 v3 measurements to the maintained line without an equivalent parser path.

Full-file extraction plus mapping took 0.68 seconds for the 51 selected files in the existing debug scratch validation. That is not a service-latency estimate. Existing outline generation reparses stored entities; reusing one parse for outline and ranges remains a plausible implementation seam, but no production integration was measured.

## Decision and limits

- Potion has measurable signal, but it does not beat BM25 on this independent C# sample. Persistent failures include `held-newton-c2` on the original hard-pair set and the common held-out Newtonsoft pair group.
- R3 is a candidate for a narrowly scoped development follow-up only if new independent evidence confirms its source-byte-budget gain. It fails the current representation gate and is not a product candidate.
- The mapper can expose method ownership, declaration/body/documentation relations and complete/partial overlaps from R4 v3 source facts. Those structural facts are not proof that sibling methods are query-relevant.
- Candidate acquisition, ANN, entity cutoff, fragment projection, B2 selection and answer quality remain unisolated. No integrated prototype or answer trial was run because no representation cleared the existing gate.
- The preregistered nested 25/100/500 candidate pools and equal-token R0m–R4m ablation were not run. The primary results use full local/file/repository/split pools and the corrected split-specific selected corpus; the equal-source-byte diagnostic is reported separately.
- The R0 lexical protocol is an exploratory secondary control (settings fixed after vector scores existed). Pairwise groups are not independent paraphrase samples; pools are curated selected-file slices; there are only two held-out repositories.

## Reproduction and review artifacts

- Corrected R0 outputs: `corrected-r0-v2/`.
- Contract-compliant R0–R5 inputs, token IDs/offsets, common mask, score outputs and analysis: `contract-r0-r5-v1/`.
- Corrected lexical controls and analysis: `frozen-r0-v1/lexical-control-v3/` and `frozen-r0-v1/analysis_labels/R0-lexical-analysis-v3.json`.
- Corrected lexical control receipt: `R0-LEXICAL-CONTROL-RECEIPT-v3.json`.
- Corrected representation analysis: `frozen-r0-v1/analysis_labels/R0-R5-contract-analysis-v1.json`.
- Mapper geometry: `r4-v3-51-file-fragment-method-geometry-v2.json`.
- Preliminary representation run is explicitly superseded in `REPRESENTATION-EXECUTION-FAULT.md`.

All benchmark build/scoring temporary files, Cargo target cache, and subprocess temp files are under workspace `.tmp`; `/tmp` usage is about 745 MB with roughly 7 GB free. No answer trials ran.
