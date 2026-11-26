# Potion capability investigation — preregistration

Frozen before capability probes.

## Model and corpora

- Model: `minishlab/potion-code-16M-v2` revision
  `e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`, one F16 tensor `embeddings`
  `[63457, 256]`. Rust runtime uses mean pooling over token rows and L2
  normalization; tokenizer config truncates at 512 tokens while the engine
  catalog declares `max_input_tokens=1024`.
- Rust corpus: frozen `clap-rs/clap@4201754a` and the 12 dev cases from the
  prior Rust RAG mission. No held-out used.
- C# corpus: read-only HRM2.0 source and frozen D01–D12 development cases in
  `benchmarks/rag-impact/cases/dev.json`. Old `heldout.json` is not opened.

## Probes

| ID | Probe | Isolated variable | Success criterion |
|---|---|---|---|
| P0 | Weight/tokenizer audit | none | Descriptive; no threshold |
| P1 | Vector rerank over existing candidate pool | Cosine of mean-pooled question vs candidate | Establish ceiling of current static embedding |
| P2 | Multi-view embedding | Separate metadata prefix vs body pooling, fixed 0.6/0.4 blend | Selected recall >= P1 + 0.05 |
| P3 | Late interaction token MaxSim | Query-token x candidate-token max similarity, mean over query tokens | Selected recall >= P1 + 0.05 |
| P4 | MMR coverage selection | Diversity term 0.3 over pooled vectors | Selected recall >= P1 + 0.05 |
| P5 | Self-supervised projection head | Ridge map from body pooled vector to symbol/doc pooled vector, 256x256 | Selected recall >= P1 + 0.05 with <=256 KiB extra weights |
| P6 | Semantic chunking prototype | Split large ranges at low adjacent-window cosine (<0.75) | Selected recall >= P1 + 0.05 without gold guidance |

All probes reuse the same frozen candidate pools. Gold is used only by the
grader. No production model, index, ranking or engine file is changed.

## Measurements

For every probe: selected/pool span recall, pack bytes, Potion tokens,
embedding wall time, peak RSS, and parameter/weight overhead. Single run per
probe; no repeated tuning run.
