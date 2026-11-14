# E4 — embedding model selection and quantization

**Status:** DONE — addendum complete (experiment only; no merge/promotion)
**Branch:** `work/e4-embedding-selection-20261113`
**Base:** `work/e3-02a-attribution-20261112` @ `95fd0f3`
**Main baseline:** `work/zg-recovered` @ `6d5b523` (untouched)
**Worktree:** `/home/masis/src/zvec-grep-e4`
**External artifacts/receipts:** `/home/masis/.cache/zvec-grep/e4-20261113/`

No production default, installed binary, daemon, index schema, ranking, selection
/expansion, structural cardinality, or E1/E2-C/E3-02B work was changed. Engine
changes on this branch are experiment-only: the two new catalog entries are
behind the non-default `e4-experiment` feature, and the workload/sanity probes
are ignored manual tests.

## 1. Preflight

| Item | Value |
|---|---|
| Host | 12th Gen Intel i7-12700H, 20 logical CPUs |
| RAM | 15 GiB total, 8.5 GiB available at start |
| Disk | 57 GB free on `/` at start |
| Git | main worktree `work/zg-recovered` @ `6d5b523`; one experiment worktree |
| Backend | `llama-cpp-embedding-v3`, pinned llama.cpp `5f55650a78f92aff4d48d671423e888fac0469ff` |
| Frozen gold | `e3-01-v2`, queries `a2d2210e…`, corpus `ac8c5d80…` (verified) |
| Corpus | 392 C# files, 98,666 lines, 4.6 MB |
| Cargo target | `/home/masis/.cache/zvec-grep/e4-20261113/target` |

The reference index from E3-02A had been cleaned up, so the EmbeddingGemma Q8
reference was re-indexed under this task's conditions. Its metrics reproduce
E3-02A exactly (see §5), which validates the rebuild and the experiment-only
backend changes.

## 2. Compatibility matrix

Artifact identity, GGUF metadata, tensor lists and hashes:
`receipts/compatibility-gguf-meta.json`, `receipts/artifact-sha256.txt`,
`receipts/qat-projection-evidence.json`.

| Candidate | Artifact / revision | SHA-256 | Bytes | Arch | Dim | Context | Pool | Prefix | Verdict |
|---|---|---|---:|---|---:|---:|---|---|---|
| EmbeddingGemma 300M Q8 | `ggml-org/embeddinggemma-300M-GGUF` @ `0f741b5` | `b5ce9d77…` | 333,590,944 | gemma-embedding | 768 | 2048 | mean | Gemma task prompts | **COMPATIBLE — reference** |
| EmbeddingGemma QAT Q4_0 | `ggml-org/embeddinggemma-300M-qat-q4_0-GGUF` @ `8dd0ca2` | `50d28e22…` | 277,852,192 | gemma-embedding | 768 | 2048 | mean | Gemma task prompts | **BLOCKED — projection head omitted** |
| EmbeddingGemma QAT Q4_0 (alt) | `lmstudio-community/embeddinggemma-300m-qat-GGUF` @ `a81b371` | `5a9e0645…` | 229,093,184 | gemma-embedding | 768 | 2048 | mean | Gemma task prompts | **BLOCKED — same omission** |
| EmbeddingGemma QAT Q8_0 (control) | `ggml-org/embeddinggemma-300m-qat-q8_0-GGUF` @ `66f974f` | `6fa0c02a…` | 328,577,056 | gemma-embedding | 768 | 2048 | mean | Gemma task prompts | **BLOCKED — same omission** |
| Jina v2 Base Code Q5_K_M | `second-state/jina-embeddings-v2-base-code-GGUF` @ `5a9d2a5` | `4fb1c5f1…` | 121,395,616 | jina-bert-v2 | 768 | 8192 | mean | none | **COMPATIBLE (community GGUF)** |
| CodeRankEmbed Q8 | `brandtcormorant/CodeRankEmbed-Q8_0-GGUF` @ `33bd774` | `666db8df…` | 146,029,792 | nomic-bert | 768 | 2048* | CLS | query instruction | **COMPATIBLE (community GGUF)** |
| Potion Code 16M v2 | catalog pin | catalog | 32.5 MB | model2vec | 256 | 1024 | n/a | none | **COMPATIBLE** |
| BGE Small EN v1.5 | `onnx-community/bge-small-en-v1.5-ONNX` | catalog | ~61 MB | BERT/ONNX q4 | 384 | 512 | CLS | query instruction | **COMPATIBLE** |

\* Upstream CodeRankEmbed advertises 8192 tokens; the published GGUF metadata
and conversion use the 2048-token nomic-bert context.

Licenses: Gemma Terms (`gemma`) for both EmbeddingGemma families; Apache-2.0 for
Jina; MIT for CodeRankEmbed, Potion Code and BGE Small.

### QAT Q4_0 blocker evidence

All inspected QAT GGUFs contain 314 tensors and only `token_embd.weight` +
`output_norm.weight` outside the transformer blocks. The reference Q8 GGUF has
316 tensors and additionally carries `dense_2.weight` (768→3072) and
`dense_3.weight` (3072→768), the SentenceTransformers projection head declared
by the upstream `modules.json`. The upstream QAT checkpoint ships separate
`2_Dense/model.safetensors` and `3_Dense/model.safetensors` (9,437,272 bytes
each, non-identity weights). llama.cpp applies `dense_2`/`dense_3` only when
present (`llama-graph.cpp: build_dense_out`), so the published QAT GGUFs compute
a different embedding function — not a lower-precision version of the reference.
A retrieval comparison against Q8 would therefore not be a quantization
comparison. QAT Q4_0 was not advanced to full-index benchmarking.

Quantization headroom is also limited for this family: `token_embd.weight` is
201,326,592 parameters (vocabulary 262,144 × 768). In the Q4_0 artifact this
tensor is still Q8_0, so the file shrinks only 16.7% versus Q8 (277.9 MB vs
333.6 MB). The "300M" footprint is dominated by the vocabulary table, not the
transformer blocks.

### Backend compatibility work (experiment branch only)

- Two `e4-experiment`-gated llama.cpp catalog entries (Jina Q5_K_M,
  CodeRankEmbed Q8). With the feature off, the default catalog and its
  TypeScript parity oracle are unchanged and tested.
- `none` and `coderank` prompt formats. The existing embeddinggemma/qwen3 paths
  are unchanged and unit-tested.
- Non-causal encoder `n_ubatch` rule generalized behind `e4-experiment`: the
  product build keeps the gemma-embedding-only special case; the experiment also
  covers bert/nomic-bert/jina-bert-v2/modern-bert/neo-bert/eurobert/t5encoder.
  Without this, inputs above 256 tokens abort llama.cpp with
  `GGML_ASSERT(cparams.n_ubatch >= n_tokens)`; the first Jina/CodeRank probe
  reproduced that abort (logs under `receipts/inference/`).

## 3. Semantic sanity

Clear-contrast 3-pair probe (unrelated distractor); tokenizer, dimension, finite
values, non-zero norms and expected ordering:

| Model | Margins (positive − negative) | Min |
|---|---|---:|
| EmbeddingGemma Q8 | 0.515, 0.494, 0.280 | 0.280 |
| Jina code Q5_K_M | 0.770, 0.741, 0.561 | 0.561 |
| CodeRankEmbed Q8 | 0.683, 0.610, 0.319 | 0.319 |

An earlier probe with plausible-but-related distractors was discarded as a gate
because the reference model also failed one pair; both probes and raw margins
are retained under `receipts/sanity/`. This is a smoke check, not a parity claim
for community GGUF conversions.

## 4. Inference benchmark

Fixed workload: 32 frozen queries + 256 deterministic 800-char C# chunks from
the E3 corpus; 3 rounds; CPU; batch 1 and a larger batch. Token counts use the
matching HF/SentencePiece tokenizer (approximate ±special tokens). Raw logs:
`receipts/inference/`, summary: `receipts/inference-summary.json`.

| Model | Batch | Doc tok/s | Doc vec/s | Query tok/s | Doc p50 call | Loaded PSS | Peak RSS | Artifact |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EmbeddingGemma Q8 | 1 | 484 | 2.62 | 498 | 369 ms | 402 MB | 880 MB | 334 MB |
| EmbeddingGemma Q8 | 16 | 524 | 2.84 | 571 | 5,531 ms (16) | 401 MB | 873 MB | 334 MB |
| Jina code Q5_K_M | 1 | 353 | 2.11 | 407 | 458 ms | 171 MB | 278 MB | 121 MB |
| Jina code Q5_K_M | 8 | 368 | 2.20 | 644 | 3,594 ms (8) | 172 MB | 274 MB | 121 MB |
| CodeRankEmbed Q8 | 1 | 552 | 2.77 | 463 | 355 ms | 181 MB | 267 MB | 146 MB |
| CodeRankEmbed Q8 | 8 | 579 | 2.90 | 359 | 2,728 ms (8) | 181 MB | 287 MB | 146 MB |
| BGE Small EN v1.5 | 1 | 3,617 | 18.13 | 2,989 | 47 ms | 85 MB | 160 MB | ~61 MB |
| BGE Small EN v1.5 | 4 | 3,381 | 16.95 | 2,364 | 229 ms (4) | 85 MB | 259 MB | ~61 MB |
| Potion Code 16M v2 | 1 | 808,017 | 4,491 | 445,861 | 0.22 ms | 116 MB | 131 MB | 33 MB |
| Potion Code 16M v2 | 8 | 866,581 | 4,817 | 374,688 | 1.65 ms (8) | 116 MB | 132 MB | 33 MB |

Observations:

- Batching returns only 4–8% for the transformer encoders here; the workload is
  short-document and CPU-bound. BGE q4 ONNX shows no batch gain at batch 4.
- Jina is **slower per token** than the 300M Q8 reference despite being smaller
  and Q5-quantized, while using 57% less resident memory.
- CodeRankEmbed is 10–14% faster than the reference at batch 1 and uses 55% less
  resident memory.
- Potion is three orders of magnitude faster (model2vec is a token-embedding
  lookup + pooling) and uses 116 MB.
- Cold-load numbers cover model construction + `prepare` only; llama.cpp uses
  mmap, so the first inference brings pages in. They are not full cold-start
  latency.

## 5. Retrieval benchmark

All arms use frozen `e3-01-v2`, an identical corpus copy, `*.cs` scan rule,
`limit=50`, `auto_update=false`, CPU, and three repeats per route. Evaluation
reuses the frozen anchors, absence regexes and line-overlap relevance. Raw runs:
`receipts/matrix/<arm>/`; aggregation: `summary.json`, `attribution.json`,
`paired-*.json`.

Reference reproduction: the EmbeddingGemma Q8 arm matches E3-02A exactly
(R@10 0.5494/0.5741/0.6142 and solved@10 0.4074/0.3704/0.4444 for
FTS/vector/hybrid). Top-10 hit sets were identical across the three repeats of
every route.

| Arm | Route | R@5 | R@10 | R@50 | Solved@10 | Solved@50 | MRR@10 | nDCG@10 | p50 / p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Gemma Q8 | FTS | 0.4599 | 0.5494 | 0.7469 | 0.4074 | 0.5926 | 0.5660 | 0.4782 | 414 / 565 ms |
| Gemma Q8 | Vector | 0.5370 | **0.5741** | 0.8179 | 0.3704 | **0.7037** | **0.5733** | **0.5117** | 488 / 691 ms |
| Gemma Q8 | Hybrid | 0.5463 | **0.6142** | 0.8025 | **0.4444** | 0.6667 | **0.5963** | **0.5387** | 410 / 497 ms |
| CodeRank Q8 | FTS | 0.4321 | 0.5062 | 0.6790 | 0.2963 | 0.4444 | 0.5436 | 0.4453 | 453 / 630 ms |
| CodeRank Q8 | Vector | 0.3827 | 0.4969 | 0.7963 | 0.2963 | 0.5926 | 0.4932 | 0.4254 | 471 / 652 ms |
| CodeRank Q8 | Hybrid | 0.4167 | 0.5463 | 0.7407 | 0.3333 | 0.4815 | 0.5372 | 0.4581 | 428 / 525 ms |
| Jina Q5_K_M | FTS | 0.4537 | 0.5802 | 0.7963 | 0.3333 | 0.5926 | 0.5461 | 0.4610 | 398 / 509 ms |
| Jina Q5_K_M | Vector | 0.4537 | 0.5710 | **0.8580** | 0.3704 | 0.6667 | 0.4942 | 0.4176 | 406 / 599 ms |
| Jina Q5_K_M | Hybrid | 0.4660 | 0.6019 | **0.8333** | 0.3704 | 0.6296 | 0.5448 | 0.4821 | 415 / 573 ms |
| Jina Q5_K_M @2048* | FTS | 0.3951 | 0.4969 | 0.7315 | 0.2963 | 0.5185 | 0.5183 | 0.4334 | 344 / 420 ms |
| Jina Q5_K_M @2048* | Vector | 0.4259 | **0.5957** | 0.8241 | 0.3704 | 0.6296 | 0.5111 | 0.4583 | 504 / 712 ms |
| Jina Q5_K_M @2048* | Hybrid | 0.4259 | 0.5864 | 0.7685 | 0.3704 | 0.5185 | 0.5437 | 0.4747 | 443 / 599 ms |
| BGE Small | FTS | 0.2685 | 0.3735 | 0.5556 | 0.1852 | 0.3333 | 0.3904 | 0.3038 | 404 / 541 ms |
| BGE Small | Vector | 0.2932 | 0.3889 | 0.6111 | 0.1852 | 0.4074 | 0.3738 | 0.3157 | 529 / 821 ms |
| BGE Small | Hybrid | 0.2685 | 0.4043 | 0.5185 | 0.2222 | 0.2963 | 0.3710 | 0.3187 | 409 / 572 ms |
| Potion 16M | FTS | 0.4444 | 0.4660 | 0.6049 | 0.2222 | 0.4444 | 0.5566 | 0.4173 | 420 / 584 ms |
| Potion 16M | Vector | 0.3395 | 0.4877 | 0.6235 | 0.2963 | 0.4444 | 0.5449 | 0.4185 | 382 / 522 ms |
| Potion 16M | Hybrid | 0.3519 | 0.4877 | 0.6358 | 0.2963 | 0.4815 | 0.5407 | 0.4184 | 319 / 450 ms |

No-answer trap-FP@10: 0.0 for Gemma, CodeRank, Potion; **0.2** for Jina
(`newtonsoft-12-no-yaml` returns `XmlNodeConverter`) and BGE. Any-result@10
remains 1.0 for all arms (not correctness).

### Full-index cost and layout

| Arm | Context | Fragments | Mean frag chars | Index wall | User CPU | Peak RSS | Index disk |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemma Q8 | 2048 | 9,223 | 943 | 1:10:36 | 16,619 s | 2.99 GiB | 63.2 MB |
| CodeRank Q8 | 2048 | 9,223 | 943 | 1:10:59 | 16,872 s | 1.01 GiB | 63.2 MB |
| Jina Q5_K_M | 8192 | 8,326 | 957 | 1:44:59 | 24,959 s | 2.11 GiB | 60.4 MB |
| Jina Q5_K_M @2048 | 2048 | 9,223 | 943 | 1:32:34 | 22,011 s | 1.10 GiB | 60.4 MB |
| BGE Small | 512 | 15,531 | 745 | 13:10 | 10,700 s | 0.96 GiB | 74.4 MB |
| Potion 16M | 1024 | 10,905 | 881 | 0:19 | 24 s | 0.68 GiB | 58.9 MB |

### Controlled-chunking Jina experiment

The 8192-token Jina arm builds 8,326 fragments, while the 2048-token reference
builds 9,223, so its native result mixes model quality with chunking. A
feature-gated alias (`...-ctx2048`, identical artifact, prompts and pooling,
`context_size=2048`) produced exactly the reference layout: **9,223 fragments,
mean 942.95 chars, p95 3,742, max 13,385** — identical to Gemma and CodeRank.
Index cost 1:32:34, 22,011 s user CPU, 1.10 GiB peak RSS, 60 MB disk.

At matched layout the vector comparison is clean:

| Vector metric | Gemma Q8 | Jina @2048 | Delta |
|---|---:|---:|---:|
| R@5 | **0.5370** | 0.4259 | **−0.1111** |
| R@10 | 0.5741 | **0.5957** | +0.0216 |
| R@50 | 0.8179 | 0.8241 | +0.0062 |
| Solved@10 | 0.3704 | 0.3704 | 0.0000 |
| Solved@50 | **0.7037** | 0.6296 | −0.0741 |
| MRR@10 | **0.5733** | 0.5111 | −0.0622 |
| nDCG@10 | **0.5117** | 0.4583 | −0.0533 |
| No-answer trap-FP@10 | **0.0** | 0.2 | +0.2 |

Paired vector deltas: R@10 mean **+0.0216** (4 wins / 4 losses / 19 ties),
solved@10 0.0000, MRR −0.0622 (3/8/16), nDCG −0.0533 (5/9/13). Hybrid is worse:
R@10 −0.0278, solved@10 −0.0741, MRR −0.0526, nDCG −0.0639. The 2048-token
chunking improves Jina over its own 8192 arm (vector R@10 0.5710 → 0.5957,
nDCG 0.4176 → 0.4583) but it still trades top-5 precision and rank quality for
a small recall@10 gain.

### FTS is not model-independent

Two distinct effects were found:

1. **Chunking is model-context-dependent.** `IndexingPipeline` calls
   `index_chunk_options(model.info().max_input_tokens(), …)`. A 512-token model
   produces 15,531 shorter fragments; a 2048-token model produces 9,223 longer
   ones. FTS and vector results therefore include a chunking-policy effect, not
   just embedding quality.
2. **Even with byte-identical fragments, BM25 scores shift across indexes.**
   Gemma and CodeRank have identical 9,223 fragment rows: `scalar.0.ipc` is
   byte-identical (`4e854852…`), document ids and row order are identical, and
   `manifest.3` is identical. A tiny-index probe with identical source produced
   BM25 1.41455 on the Gemma index vs 1.42262 on the CodeRank index; on the full
   corpus this moved some top-10 orderings. With matched layouts the FTS R@10
   spread is **0.5494 (Gemma), 0.5062 (CodeRank), 0.4969 (Jina @2048)** and the
   MRR spread is **0.5660 / 0.5436 / 0.5183** — a material 5.2 pp lexical
   difference with byte-identical documents. The
   query model does not affect FTS: querying the CodeRank index with the Gemma
   model produced the same 32/32 FTS results. The difference originates in the
   native collection, whose name is hashed from the embedding model identity,
   not in query preprocessing.

Consequence: **vector-only on matched layouts is the clean model-quality
comparison.** Gemma vs CodeRank and Gemma vs Jina @2048 are matched-layout
comparisons (identical 9,223 fragments). Jina's native 8192-token layout
(8,326 fragments) is a separate deployment arm whose numbers include the
chunking effect. BGE/Potion are inherently limited to 512/1024-token chunks;
their numbers describe deployment under their native context, not a
chunking-controlled comparison.

### Paired per-query deltas vs Gemma (three-run mean)

| Candidate | Route | R@10 Δ | Solved@10 Δ | MRR@10 Δ | nDCG@10 Δ |
|---|---|---:|---:|---:|---:|
| CodeRank | Vector | −0.0772 | −0.0741 | −0.0800 | −0.0862 |
| CodeRank | Hybrid | −0.0679 | −0.1111 | −0.0591 | −0.0805 |
| Jina | Vector | −0.0031 | 0.0000 | −0.0790 | −0.0940 |
| Jina | Hybrid | −0.0123 | −0.0741 | −0.0515 | −0.0566 |
| Jina @2048 | Vector | +0.0216 | 0.0000 | −0.0622 | −0.0533 |
| Jina @2048 | Hybrid | −0.0278 | −0.0741 | −0.0526 | −0.0639 |
| BGE | Vector | −0.1852 | −0.1852 | −0.1994 | −0.1960 |
| BGE | Hybrid | −0.2099 | −0.2222 | −0.2253 | −0.2200 |
| Potion | Vector | −0.0864 | −0.0741 | −0.0284 | −0.0932 |
| Potion | Hybrid | −0.1265 | −0.1481 | −0.0556 | −0.1203 |

Wins/losses/ties and per-query rows are in `receipts/matrix/<arm>/paired-*.json`.
Concentrated losses: `newtonsoft-07-linq-property-case`,
`newtonsoft-08-iso-date-format`, `antlr-05-atn-deserialize`,
`dapper-05-type-map-selection`, `newtonsoft-11-property-name-flow`. Concentrated
gains: `antlr-06-interval-complement`, `newtonsoft-04-settings-to-contract`,
`newtonsoft-09-preserve-references`.

### Declaration-type attribution (K=10)

| Route | Gemma container/method/value | CodeRank | Jina | BGE | Potion |
|---|---|---|---|---|---|
| FTS | 22 / 4 / 4 | 22 / 3 / 3 | 18 / 7 / 6 | 11 / 5 / 3 | 15 / 5 / 4 |
| Vector | 19 / 6 / 3 | 21 / 4 / 3 | 11 / 11 / 6 | 10 / 6 / 4 | 15 / 6 / 4 |
| Hybrid | 22 / 6 / 3 | 21 / 4 / 3 | 17 / 8 / 7 | 12 / 6 / 3 | 15 / 6 / 4 |

Jina retrieves more method/value entities and fewer containers; CodeRank/BGE/
Potion move in the same direction but with lower recall. Container dominance for
Gemma is partly overlap: a class entity can contain several anchors.

### Non-C# generalization pilot (directional)

A frozen, source-grounded pilot (`e4-noncsharp-pilot-v1`) was built outside the
repository under `receipts/pilot/`: 8 files / 131 entities across Rust, SQL,
Markdown, TOML config and a system-package log; 10 answerable + 2 no-answer
queries, anchors chosen from the source before any retrieval run. Gemma,
Jina @2048 and CodeRank all used the same corpus copy, globs and 2048-token
layout.

| Arm | Vector R@5/10/50 | Vector solved@10 | Vector MRR | Vector nDCG |
|---|---|---:|---:|---:|
| Gemma Q8 | 1.0 / 1.0 / 1.0 | 1.0 | **0.9500** | **0.9631** |
| Jina @2048 | 1.0 / 1.0 / 1.0 | 1.0 | 0.9250 | 0.9431 |
| CodeRank Q8 | 1.0 / 1.0 / 1.0 | 1.0 | 0.7900 | 0.8405 |

No-answer trap-FP@10 was 0.0 for all three. Ordering is Gemma ≥ Jina >
CodeRank, consistent with the C# result; no reversal. **The pilot is not
decision-grade:** recall saturates at 1.0 because non-code entities are whole
sections/files and the anchors lie inside them; n=10; the ranking differences
are therefore the only informative signal. It is directional evidence, not a
production benchmark and not comparable to `e3-01-v2`.

Two operational findings from the pilot: `logs/` is a default-ignored
directory (the domain had to be renamed to `journal/`), and indexing a full
614 KB log was superlinear (1,000 lines ≈ 126–195 s; 3,000 lines exceeded a
300 s timeout and the full file was skipped/killed). The frozen pilot uses a
1,000-line slice.

## 6. Decision

| Candidate | Verdict | Reason |
|---|---|---|
| EmbeddingGemma 300M Q8 | **PROVISIONAL REFERENCE (unchanged)** | Best hybrid R@10/solved@10/MRR/nDCG on the frozen corpus; no tested candidate dominates its quality–cost trade-off. |
| EmbeddingGemma QAT Q4_0 | **BLOCKED** | Published GGUFs omit the `dense_2`/`dense_3` projection head; not a faithful quantization. Tokens dominate the file, so Q4_0 only saves 16.7% anyway. |
| CodeRankEmbed Q8 | **NO_GO** | Matched-layout comparison: −7.7 pp vector R@10, −11.1 pp hybrid solved@10, −8.6 pp vector nDCG. Memory halved, CPU roughly equal, but the quality loss is material. |
| Jina v2 Base Code Q5_K_M | **NO_GO** | Matched-layout vector test: R@10 +2.2 pp but R@5 −11.1 pp, MRR −6.2 pp, nDCG −5.3 pp, solved@10 equal, no-answer trap-FP 0.2; hybrid solved@10 −7.4 pp. 57% less RAM, but 1.5× index time (1:45 native, 1:33 at 2048) and ~30% lower document throughput. The 8192 context remains a qualitative long-document advantage this corpus cannot price in. |
| Potion Code 16M v2 | **NO_GO for quality parity; resource champion** | 227× faster index, 1,650× model-layer throughput, 116 MB PSS, but −8.6 pp vector R@10, −14.8 pp hybrid solved@10, −9.3 pp vector nDCG. Only viable if a lower quality floor is explicitly accepted. |
| BGE Small EN v1.5 | **NO_GO** | −18.5 pp vector R@10, −22.2 pp hybrid solved@10, −19.6 pp nDCG, plus a no-answer FP. |

No model is called a production default: the evidence is 27 answerable C#
queries plus a directional non-C# pilot, one corpus family and one host.
EmbeddingGemma Q8 stays the provisional reference for this repository; the
decision does not generalise to Rust, SQL, Markdown, logs or non-code text.

**No integration was made.** The experiment-only catalog entries and prompt
formats stay behind the non-default `e4-experiment` feature; the default catalog,
production index, daemon and binaries are untouched. There is no promotion-ready
candidate to package.

### Owner promotion checklist (if a candidate is revisited)

1. Freeze a held-out, independently annotated non-C# dataset with tight anchors
   (needle-level, not whole-file) and enough queries for CIs.
2. Resolve the native FTS collection-identity dependence described above, or
   exclude FTS/hybrid from cross-model comparisons until it is fixed.
3. Repair the QAT GGUF projection head, then run a matched-layout Q8-vs-Q4
   comparison.
4. For Jina, define a chunking policy first (native 8192 vs fixed 2048); the
   current data show the 2048 layout improves Jina's ranking but still loses
   top-5 precision to Gemma.
5. Require a no-regression gate on solved@10/MRR/nDCG and no-answer trap-FP
   before changing the default.

### What is not proven

- No decision-grade non-C# / documentation / SQL / configuration / log evidence.
  The pilot is directional only (recall saturates, n=10).
- No independent second annotation pass (inherited E3 limitation).
- Community GGUF fidelity is unverified for CodeRankEmbed (llama.cpp
  `nomic-bert` implementation vs upstream custom model) and Jina Q5_K_M
  (community conversion). The compatibility gate was load + semantic sanity, not
  vector parity with a reference implementation.
- Jina's isolated embedding quality was resolved by the matched-layout 2048
  experiment; its 8192 long-document behaviour remains unmeasured.
- No GPU, ANN-concurrency or cold-start-under-page-cache study.
- The exact CPU seconds for query processes were not captured; index CPU/RSS are
  exact from `/usr/bin/time -v`.

### Minimal next steps

1. **If long context matters:** evaluate Jina's native 8192-token layout on a
   long-document corpus; the 2048 controlled run already separates its embedding
   quality from chunking and shows lower top-5 precision than Gemma.
2. **If quantization remains a goal:** repair the QAT GGUF conversion
   (`dense_2`/`dense_3`) or convert the base model Q4_0 with the projection
   preserved, then rerun the matched-layout comparison.
3. **Before any default change:** add an independent held-out benchmark for Rust,
   SQL, Markdown and non-code text; today's evidence cannot support a
   cross-domain default.
4. **If a fast mode is desired:** define an explicit quality floor first; Potion
   is the only candidate with a three-order-of-magnitude resource win.

## 7. Reproducibility

```sh
# Build (experiment feature)
cd /home/masis/src/zvec-grep-e4/benchmarks/retrieval-evidence/harness
CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/e4-20261113/target cargo build --release

# Per-arm full index (one arm at a time; records time/RSS receipts)
cd /home/masis/src/zvec-grep-e4/benchmarks/retrieval-evidence
CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/e4-20261113/target \
  ./e4/index-arm.sh <root>/corpus <cache> <model-reference> <name> <receipts>/index

# Retrieval matrix (3 repeats x 3 routes) and summary
CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/e4-20261113/target \
PYTHON=/home/masis/.cache/zvec-grep/e4-20261113/venv/bin/python \
  ./e4/run-arm.sh <root>/corpus <cache> <receipts>/matrix/<name> <model-reference> <name> 3
/home/masis/.cache/zvec-grep/e4-20261113/venv/bin/python \
  ./e4/summarize.py --matrix-dir <receipts>/matrix/<name> --prefix <name>

# Model-layer fixed workload / semantic sanity
cd /home/masis/src/zvec-grep-e4/rust
ZG_MODEL_BENCH_WORKLOAD=/home/masis/.cache/zvec-grep/e4-20261113/workload/fixed-workload.jsonl \
ZG_MODEL_BENCH_MODEL=<model-reference> \
ZG_MODEL_BENCH_CACHE=/home/masis/.cache/zvec-grep/e4-20261113/model-cache \
CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/e4-20261113/target \
  cargo test -p zg-engine --release --features e4-experiment model_layer_fixed_workload -- --ignored --nocapture

# Unit checks (default feature parity + experiment feature)
cargo test -p zg-engine --release --lib -- models::catalog models::backends::llama_cpp
cargo test -p zg-engine --release --features e4-experiment --lib -- models::catalog models::backends::llama_cpp models::tests::model_layer_bench
```

### Controlled-chunking alias

`local/jina-embeddings-v2-base-code-q5-k-m-ctx2048` is a feature-gated catalog
alias of the same Jina artifact with `context_size=2048`; it exists only to hold
the fragment layout fixed. It is not a promotion candidate.

### Non-C# pilot

The pilot corpus and queries live outside Git under
`/home/masis/.cache/zvec-grep/e4-20261113/pilot/` (proprietary/system files are
not redistributed). Frozen hashes: `queries.json`
`f0dca2c1dac0d25af217621ea3de5c16221ca2b484bdca495925bdebb33721e6`,
`corpus-sha256.txt`, `manifest.json`. Rebuild rooted at
`pilot/arms/<arm>/corpus` with globs
`*.rs,*.sql,*.md,*.toml,*.log` and the same harness.

### Independent verification

`pilot/independent_check.py` re-implements R@10, solved@10, MRR@10 and nDCG@10
without importing the repository evaluator. For the Jina @2048 vector run
(27 answerable queries) and the pilot Gemma vector run it matched `evaluate.py`
to 0.0 delta; `INDEPENDENT_MATCH_OK`. Three repeats of every Jina @2048 route
had identical top-10 hit sets; macro metric stdev was 0.0.

Frozen dataset hashes: `queries.json` `a2d2210e…`, `corpus.json` `ac8c5d80…`.
Workload hash: `70dea2014773c96360ce19ce64c25de9a770554085f75aad6ee19626f54f9c40`.
Combined summary: `receipts/e4-final-summary.json`; addendum summary:
`receipts/e4-addendum-summary.json`.


## 8. Autonomous completion addendum

Completed after the first E4 pass without re-running valid earlier measurements:

| Item | Result |
|---|---|
| Jina chunking confound | **Resolved.** Feature-gated `...-ctx2048` alias produced the exact reference fragment layout; matched-layout vector test shows R@10 +2.2 pp but R@5 −11.1 pp, MRR −6.2 pp, nDCG −5.3 pp, solved@10 equal, trap-FP 0.2. Jina verdict is now **NO_GO**, not INCONCLUSIVE. |
| Non-C# pilot | **Directional only.** 8 files / 131 entities / 12 queries across Rust, SQL, Markdown, TOML and a log slice; Gemma ≥ Jina > CodeRank on MRR/nDCG, recall saturated at 1.0. No reversal; not decision-grade. |
| Independent verification | A separate metric implementation matched `evaluate.py` to 0.0 delta on the Jina @2048 vector run and the pilot Gemma vector run; three repeats had identical top-10 sets. |
| Regression / rollback | Default-feature catalog oracle and prompt-format tests pass; experiment entries are absent with the feature off; the non-causal ubatch change is feature-gated and tested in both modes; invalid-GGUF removal test passes. |
| Integration | **None.** No candidate is promotion-ready; default catalog, production index, daemon and binaries are untouched. |
| FTS finding | With byte-identical 9,223-fragment layouts, FTS R@10 spans 0.4969–0.5494 and MRR 0.5183–0.5660 purely by collection identity. This is an engine-level reproducibility issue for cross-model lexical/hybrid comparisons and needs a separate bounded investigation. |

The final owner decision is unchanged: **NO-GO for replacing EmbeddingGemma Q8**
on the current evidence; keep it as the provisional reference and do not call it
production-qualified.
