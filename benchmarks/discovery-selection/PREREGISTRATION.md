# Discovery & Selection Bottleneck — preregistration

Frozen before instrumented runs.

## Corpora and cases

- Rust: `clap-rs/clap@4201754a` with frozen 12 cases from the Rust RAG mission
  (`cases.json` SHA-256 `c6e1a7e7…`).
- C#: read-only HRM2.0 development cases D01–D12 and frozen B0 receipts. The old
  held-out file is not opened.
- Potion stays unchanged. No graph extension, no production change.

## Instrumented stages

One isolated binary, env-gated JSONL trace, no behavior change when disabled:

1. `raw`: per-route raw recall results before entity aggregation.
2. `fused`: candidates after RRF fusion, before entity load.
3. `loaded`: all fully loaded entities with per-route trace.
4. `ranked`: after rule ranking with diversity penalty and cutoff.
5. `context_group_items` / `context_selected`: after dedupe, coverage
   priority, global fill, item cutoff.
6. CLI pack text: final rendered items; graded by the harness.

## Controlled comparisons on the same candidate pool

- Ranking: pure cosine, pure RRF, current rule ranking, rule ranking with
  `FILE_DECAY=0` (no diversity penalty).
- Cutoffs: limit/top-K `10`, `25`, `50`, `100`.
- Budgets: item count `5/10/20` and pack bytes `8/16/24/32 KiB`.

## Metrics

- File recall: gold file appears in stage output.
- Entity recall: gold entity id appears in stage output.
- Range overlap: any stage range intersects a gold span.
- Exact evidence coverage: gold span fully contained in a delivered range.
- Noise: delivered bytes/tokens not overlapping any gold span.
- Latency, user+sys CPU, peak RSS, Potion tokens per pack.

## Preregistered questions

- If `raw` range recall is high but `context_selected` is low, discovery is not
  the bottleneck; pruning/selection is.
- If `raw` range recall is low, focus on discovery/query formulation.
- A ranking variant is considered useful only if it raises exact coverage or
  range recall without increasing delivered bytes >25% versus current rank.
