# Potion Code 16M v2 — capability and extensibility investigation

Status: `DONE / research-only`. No production change, no model promotion, no
engine/ranking change, no old held-out opened. All probes ran on copied model
files and frozen development evidence. Artifacts:
`/home/masis/.cache/zvec-grep/potion-capability-20261126`. Branch pushed:
`work/potion-capability-20261126`.

## 1. What Potion actually is

Sources:

- HF model card `minishlab/potion-code-16M-v2` README: distilled from
  `nomic-ai/CodeRankEmbed`, vocabulary mined from CornStack (+43k tokens),
  Model2Vec PCA distillation to 256 dims, Tokenlearn on 1.2M query/document
  pairs, contrastive fine-tuning (MNRL) on 1.2M pairs.
- `config.json`: `normalize=true`, `embedding_dtype=float16`.
- `modules.json`: `StaticEmbedding` followed by `Normalize`; no transformer,
  attention, pooling module or task head beyond static embedding + normalize.
- Rust backend `models/backends/model2vec/model.rs:415-485`: tokenize, drop
  `[UNK]`, mean-pool token rows in f64, L2-normalize, narrow to f32.
- Rust loader `safetensors.rs:97-110`: F16 weights expanded to f32 in memory.
- Catalog `models/catalog/entries.rs:422-450`: dimension 256, cosine,
  `normalize=true`, `max_input_tokens=1024`, no query/document prefix.

Measured weights:

| Property | Value |
|---|---:|
| Tensor | `embeddings` |
| Shape | `[63457, 256]` |
| Parameters | **16,244,992** |
| Dtype on disk | F16 |
| Weight bytes | 32,489,984 |
| File bytes | 32,490,072 (30.98 MiB) |
| Tokenizer | WordPiece, 63,457 vocab, BertNormalizer lowercase, BertPreTokenizer |
| Added tokens | `[PAD]` id 0, `[UNK]` id 1 |
| Tokenizer truncation | right, `max_length=512`, `LongestFirst` |
| Padding | right, BatchLongest |

Important mismatch: the engine catalog declares 1,024 max input tokens, but the
tokenizer config truncates at 512 and Rust calls `Tokenizer::encode` without
disabling truncation. Direct probe on `clap_builder/src/parser/parser.rs`
produced exactly 512 token ids. The model card's “no practical limit” refers to
static embedding arithmetic, not to this tokenizer/runtime configuration.

## 2. Representation capacity and redundancy

Weight analysis (`results/model-audit.json`):

| Measure | Result | Interpretation |
|---|---:|---|
| Row norm p01 / p50 / p99 | 3.55 / 8.96 / 14.13 | Healthy, near-uniform token scale. |
| Near-zero rows `<0.05` | 1 | Only `[PAD]` (norm 0.0072). |
| Sampled duplicate rows | 0 / 21,153 | No obvious duplicated token capacity. |
| Dead output columns | 0 | No unused dimensions by norm. |
| Singular values, energy rank 90/95/99% | 194 / 222 / 249 of 256 | Output space is almost full-rank; little low-rank redundancy to exploit. |
| SVD cost | 3.94 s, peak RSS 353,832 KiB for audit | Analysis cost only, not runtime. |

Corpus token coverage:

| Corpus | Files | Tokens | Unique ids used | % of 63,457 vocab | Files at 512-token cap |
|---|---:|---:|---:|---:|---:|
| Rust clap | 337 | 123,326 | 2,426 | 3.82% | 189/337 (56.1%) |
| C# HRM2.0 | 4,631 | 1,781,832 | 5,838 | 9.20% | 2,323/4,631 (50.2%) |
| Union | — | — | 7,120 | 11.22% | — |

Conclusion: the 16M parameters are mostly used in a high-rank table. There is
no strong evidence of “unused capacity” inside weights. The mismatch is not
parameter count but **what a single static bag-of-token vector can represent**.
Low corpus-specific token coverage is a domain/vocabulary issue, not dead rows.

## 3. Capability probes

All probes used the frozen 12 Rust dev cases and the baseline 50-candidate pool
from the Rust RAG investigation. Gold was used only for grading. C# used D01–D12
development cases and the union of B0 call units, not held-out.

| Probe | Isolated capability | Rust macro recall | C# macro recall | Verdict |
|---|---|---:|---:|---|
| P1 | Pure cosine rerank of mean-pooled vectors | **0.3403** | 0.2917 | Best simple reuse of existing embeddings. |
| P2 | Multi-view: 0.6 metadata + 0.4 body pooled vectors | 0.2570 | — | NO-GO, worse than P1. |
| P3 | Late-interaction token MaxSim with static token vectors | 0.3056 | — | NO-GO, worse than P1. |
| P4 | MMR coverage selection with diversity 0.3 | 0.2361 | — | NO-GO, worse than P1. |
| P5 | Self-supervised 256x256 projection body→query view | 0.1042 | 0.2083 | NO-GO; hurts in both languages. |
| P6 | Semantic chunking at low adjacent-window cosine (<0.75) | **0.3681** | — | Directional gain +0.028 over P1, below preregistered +0.05 threshold. |

Reference points: engine baseline delivered recall on the same Rust cases was
0.2500; evaluation-only oracle over the same pool was 0.7292. P1 shows the
Potion vector itself can rerank better than the current rule rank, but it is
still far from the pool ceiling. P6's chunk boundaries are derived only from
Potion window similarities, no gold guidance.

Resource/cost:

- Probe harness: 30.65 s wall, peak RSS 204,516 KiB; 29.0 s spent in tokenizer
  encode subprocesses.
- Projection head: 3,000 self-supervised pairs, 0.097 s train, 65,536 params,
  256 KiB f32 / 128 KiB f16. It did not improve quality.
- Mean selected pack bytes: P1 8,531; P2 8,772; P3 17,920; P4 6,193; P5 12,049;
  P6 8,759. P3's token interaction gained no quality while producing the
  largest packs.
- Index cost from existing receipts (not re-run): Rust clap 335 files/4,810
  entities in 13.37 s wall, 20.90 s user, peak RSS 558,024 KiB; C# B0 818 files,
  median cold index 6.31 s, max RSS 524,200 KiB.

## 4. Capability matrix

| Task | Potion can do? | Evidence / limit |
|---|---|---|
| Semantic code/text retrieval | **Yes, baseline capability** | P1 0.3403 Rust rerank; model card CoIR 39.08, hybrid 43.36. |
| Query grounding to exact symbols | **Weak** | No exact identifier semantics; prior Rust H2 expansion hurt; Potion vector cannot invent missing source facts. |
| Multi-view embedding | **Technically yes, not useful as tested** | P2 below P1; separate metadata/body pooling only reuses the same static token rows. |
| Semantic chunking | **Partial** | P6 0.3681, below threshold; token-window cosine can find some boundaries but lacks structural awareness. |
| Sub-unit reranking | **Partial** | P1 improves over engine rank; P6 slightly more; still far from oracle. |
| Late interaction | **Shallow only** | P3 implemented static token MaxSim, worse than P1; no contextual token states to interact. |
| Evidence selection / coverage | **Weak** | MMR P4 worse than P1; diversity by pooled vector loses relevance signal. |
| Compression / summarization | **No** | Static pooling cannot generate or preserve multi-fact content; can only score/prune existing spans. |
| Graph/typed relations | **No** | No edge encoder, no type states, no call direction; graph must be external. |
| Order/scope/negation sensitivity | **No** | Mean pooling is permutation-invariant; one token vector regardless context. |
| Long-context reasoning | **No in this runtime** | Tokenizer truncates at 512; engine catalog's 1024 is not applied. |
| Fine-tuning / adapters | **Technically yes** | Frozen base + projection head is feasible; tested linear head hurt, so no evidence for this simple adapter. |
| Distillation into richer model | **External task** | Potion itself is already a distilled static model; further distillation requires a new teacher/labels. |

## 5. What can be reused

Reusable without changing base embeddings:

1. **Token row table** (32.5 MiB): token-level semantic similarity, MaxSim,
   multi-view pooling, window-boundary scoring.
2. **Pooled vector**: query/candidate rerank, dedupe, lightweight clustering.
3. **Frozen table + task head**: a small projection/adapter can be trained and
   versioned separately so current embeddings remain byte-identical. Our tested
   linear projection did not help; a supervised head with real relevance labels
   is a different experiment.
4. **Tokenizer vocabulary**: code-specific tokens mined from CornStack are the
   main artifact of v2 and are useful for lexical-semantic features.

Not reusable from Potion because they do not exist:

- contextual hidden states, attention maps, token positions, layer outputs,
  cross-attention query-document features.
- source identity, line range, declaration structure, call graph or types.

## 6. Extension options and trade-offs

| Option | Expected benefit | Cost/risk | Verdict from evidence |
|---|---|---|---|
| Keep Potion frozen, use P1 rerank | +0.09 recall on Rust dev over current rule rank; no new weights | Additional pooling/Rerank latency; still far from oracle | Best supported immediate reuse, research-only. |
| Semantic chunking prototype (P6) | +0.028 over P1, best observed | Token-window similarity is structure-blind; needs careful calibration | Not enough evidence to adopt; keep as hypothesis. |
| Projection/adapter head | Could align query and code views | 256 KiB per 256x256 head; training labels needed; drift risk if base updated | Tested self-supervised head hurt; do not adopt. |
| Fine-tune full embedding table | Could specialize to domain | Changes existing embeddings, breaks stored vector compatibility; needs reindex | Not done; high risk, not justified. |
| Add contextual/graph encoder | Handles symbol/scope/call relations | Larger model, CPU cost, architecture change | Outside Potion; needed for structural intelligence. |
| Raise tokenizer limit by disabling truncation | Restores long code tokens | More tokens processed; mean pooling dilutes further; not tested here | Cannot fix representation by itself. |
| Potion + BM25 hybrid | Model card shows 39.08→43.36 CoIR | Already in engine as FTS+vector routes | Keep as baseline strategy. |

## 7. Proven static-architecture limits

The following are not implementation bugs but consequences of static embedding:

- A token has one vector regardless of scope, position, type or occurrence.
- Mean pooling cannot preserve order, condition/branch semantics, negation or
  caller/callee direction.
- There is no query-conditioned representation; query-document interaction must
  be external cosine/MaxSim.
- There is no hidden state to reuse for type inference or graph traversal.
- Long files are truncated at 512 by this tokenizer/runtime.
- Compression of multiple distinct facts into one 256-d vector loses evidence;
  an evidence pack must keep source spans.
- Multi-hop retrieval and selection require external graph/search/ranking.

## 8. Reproduce

```bash
W=/home/masis/src/zvec-grep-rust-rag-20261126
A=/home/masis/.cache/zvec-grep/potion-capability-20261126
M=$A/model/e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b
python3 "$W/benchmarks/potion-capability/tools/audit_model.py" --model-dir "$M" --out "$A/results"
python3 "$W/benchmarks/potion-capability/tools/capability_probe.py" \
  --model-dir "$M" --encoder "$A/build/target/debug/examples/potion_encode_lines" \
  --rust-root "$A/../rust-rag-20261126/corpus/clap" \
  --rust-receipt "$A/../rust-rag-20261126/results/arm-receipt.json" \
  --rust-cases "$A/results/rust-cases.json" \
  --csharp-root /home/masis/itc/Ricons/HRM2.0 \
  --csharp-cases /home/masis/src/zvec-grep/benchmarks/rag-impact/cases/dev.json \
  --b0-receipt /home/masis/src/zvec-grep/benchmarks/rag-impact/results/b0-official.json \
  --artifacts "$A"
```

## 9. Limits

- One run per probe; 12 Rust and 12 C# development cases; no held-out.
- Static token vectors were computed by a Python prototype mirroring the Rust
  mean-pool/normalize logic, not by promoting a new Rust implementation.
- P5 used self-supervised symbol/body pairs, not human relevance labels; failure
  does not refute supervised adapters trained with real labels.
- P6 threshold was preregistered as +0.05; observed +0.028 is directional only.
- The source model card's CoIR numbers are upstream benchmark results, not
  measurements on these corpora.
