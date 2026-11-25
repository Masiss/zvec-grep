# R4 evidence pipeline audit — 2026-11-24

## Scope and method

This audit joins the matched R4 v3 Rust implementation from `/home/masis/src/zvec-grep-r4-evidence-20261119` (commit `e7d0a89c27e199489ea4ad786c619211bad5ac2d`) with the saved development/validation traces and adjudication artifacts. The root checkout was at `663566546f03768b9b021c65eb3accc4aa0720a1` and has index schema v2; it is not the executable source for the frozen v3 index. The isolated matched-source snapshot is `/tmp/r4-evidence-projection-v3-20261124/rust`. This audit does not alter an index or use gold to choose evidence. The workspace semantic search index currently fails to open its FTS index, so code locations were verified with local source inspection. Indexed validation prompts are already exposed and are used only as diagnostic examples.

## Pipeline map from current source

1. `indexed_search/pipeline.rs` executes route calls and records `StorageSearchHit`s as internal evidence attached to candidates keyed by entity ID.
2. `fuse_candidates` computes entity RRF from route ranks. The later deterministic entity ranker adds query-derived symbol, definition, path and cross-hit support signals.
3. Loading sorts internal evidence by route rank, route order, route ID and document ID. Conversion to `SearchEvidence` retains the fragment object but drops the route rank, route mode and route ID from each evidence item.
4. `context_item_target` uses only `hit.evidence.first()` to produce the source excerpt. If an entity has no evidence, it falls back to the entity's full content range.
5. Context selection deduplicates items by entity ID, prioritizes query-group coverage, then fills by global entity rank. The result is capped by item limits. Source-range mapping, freshness status, source hashes and citation coordinates are separately handled downstream.

## Findings

| Layer | Finding | Evidence strength | Impact / limit |
|---|---|---|---|
| Fragment selection | The first recalled sibling is always projected. Route-level order survives only as list position, while its route identity and rank are discarded from `SearchEvidence`. | **Direct reproduction.** In the Indexed validation `BufferedTokenStream` case, ordinal 7 did not cover the `SetTokenSource` range at lines 367–375; recalled ordinal 8 did. This is distinct from RAG dev task `a02`, whose target is the `Reset()` implementation at lines 146–149 and whose candidate A output selected nearby `SetTokenSource` documentation instead. | A route rank is not calibrated fragment relevance. Both observations are task-specific and must not be conflated; neither quantifies corpus-wide frequency. |
| Multiple evidence ranges per entity | Context deduplication key is `entity:<id>`. A single entity cannot contribute two independent context items, even when its sibling fragments cover disjoint methods. | **Direct code evidence and task reproduction.** `val-03` required `GridReader` lines 208–236 and 438–458. Both sibling ranges were present in recalled evidence; the delivered fragment covered neither. | Prevents multi-branch answers when one entity owns separated source regions. It may be correct for duplicate hits across query groups, so a fix must deduplicate overlapping ranges while retaining disjoint useful ranges. |
| Local structure expansion | Candidate C expands the `val-03` GridReader item from lines 70–100 to 44–130; the answer requires lines 208–236 and 438–458. | **Direct saved validation artifacts.** The expanded item still misses both required ranges. | Local adjacency does not recover nonlocal branches. No raw task/trace maps the prose label `C-NEW-03`, so its exact mapping to this candidate is unverified. |
| Route acquisition | Exact target source is absent from some delivered packs. `val-08` has no target-file hit in the direct FTS/vector top-50 pools and R4 returned helper evidence. | **Observed delivery miss, root layer unresolved.** Direct top-50 pools are proxies, and the target may be outside the adaptive hybrid candidate map. Gold scope also has uncertainty. | Query vocabulary, route acquisition and source-range mapping remain competing explanations. Do not label this as a proven vector failure. |
| Fusion / entity ranking | RRF and deterministic code ranking choose an entity before fragment projection. Target files appear early on some tasks, but early file rank is not an actionable method entry point. Relevant source can be present in a low-ranked sibling or a different entity. | **Mixed.** Final ranks and some non-final candidate traces exist. Several non-final source-range joins are missing. | Rank fusion may contribute, but the saved evidence does not justify changing RRF or comparing raw FTS and vector scores directly. |
| Context budget and diversity | The benchmark caps output at 10 items and 24,576 source bytes. Context selection prioritizes one item per coverage group, then fills by global rank. | **Direct code and protocol evidence.** No controlled ablation isolates which items are displaced by the selection policy. | An improved fragment selector can still regress when multiple fragments consume budget or reduce entity diversity. Measure byte displacement and preserve baseline-complete tasks. |
| Answer reasoning / citation use | Sufficient packs still produced answer-side failures on `a04`, `d06` and `n11`. The overnight prompt revisions scored 8/12 and 9/12 versus the original sufficient-pack 9/12. The latter gained `a04` but lost `d05`. | **Direct graded evidence.** | Retrieval changes alone cannot fix these failures. Current evidence does not support further broad prompt tuning. |
| Source integrity / freshness | The context path validates fragment-to-source coordinate mapping and reports `PossiblyStale` when the file snapshot disagrees. | **Protective mechanisms confirmed in code.** No stale-source failure was found in the frozen runs. | Keep these checks as invariants, not as diagnosed defects. |
| Measurement / observability | Normal trace lacks full pre-ranking source-range joins for every candidate. Direct top-50 routes are not the hybrid's adaptive candidate pool. Task-level adjudication also lacks item IDs for some aggregate usefulness counts. | **Direct artifact limitations.** | This limits root-cause attribution and rank/byte efficiency claims. It is an evidence gap, not itself a retrieval quality defect. |
| Runtime cost | On the frozen Indexed validation measurements, R4 median process time/RSS was 1.11 s / 426,918 KiB versus `rg` 0.191 s / 76,416 KiB. RAG development retrieval also measured R4 at 1.2925 s mean versus lexical at 0.1404 s. | **Measured, workload-specific.** | No warm serving comparison was collected. The values justify a cost gate, not a universal performance claim. |

## Root-cause summary

The strongest confirmed defect is **fragment projection after entity retrieval**. There are two concrete mechanisms: selection of the first sibling fragment and entity-level deduplication that blocks a second disjoint range. Other incompleteness includes route/acquisition misses and cases where exact non-final candidate provenance is insufficient to distinguish acquisition from ranking. Separately, sufficient evidence still leaves answer completeness and citation failures.

The evidence does not establish one universal cause. Current task-level Indexed results show R4 and `rg` both useful on 8/9 answerable tasks and complete on 3/9. Candidate C did not improve those task outcomes and engineering remains `HOLD`. The recent RAG prompt candidates also failed their development gate. No validation result is claimed for this new audit.

## Design implications

- Preserve route provenance and fragment-level score/rank through evidence projection. Never treat FTS rank and vector rank as directly comparable without an explicit contract.
- Evaluate a fragment-level selection/packing method that can return multiple non-overlapping ranges from one entity when they add distinct source support. Keep source-range deduplication, global entity diversity and byte accounting explicit.
- Do not spend a second prototype on fusion or query expansion unless an exact adaptive trace identifies a failure that those mechanisms can repair.
- Instrument the actual adaptive candidate path in an isolated build before claiming that a target file or fragment was never acquired.
- Keep answer-agent failures and retrieval failures in separate score columns.

## Evidence references

- `docs/research/indexed-source-delivery-design-evidence-20261123.md`
- `research/public-source-evaluation-20261123/product-reality-analysis-20261123.json`
- `research/public-source-evaluation-20261123/runs/validation-final-20261123/r4/diagnostic-trace.jsonl`
- `research/rag-r4-answer-quality-20261123/development-failure-diagnosis-v1.json`
- `research/rag-r4-answer-quality-20261123/development-answer-report-v3.json`
- `research/rag-r4-answer-quality-20261123/overnight-rag-prompt-report-v4.json`
