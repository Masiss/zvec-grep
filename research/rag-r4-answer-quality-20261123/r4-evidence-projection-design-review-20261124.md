# R4 evidence projection: architecture audit and design review

## Current path, verified in current source

The current working tree is commit `663566546f03768b9b021c65eb3accc4aa0720a1`. The exact implementation path is:

1. `rust/crates/zg-engine/src/pipelines/indexed_search/pipeline.rs::add_recall_hits` collects each route's `StorageSearchHit` into a candidate keyed by `EntityId`. Every internal evidence row retains route mode, route ID, and route-local rank.
2. `fuse_candidates` assigns entity score as the sum of reciprocal route ranks using `RRF_K=60`. A subsequent deterministic code ranker adds symbol, definition-intent, path-affinity, and support signals.
3. Entity loading sorts evidence by route-local rank and deterministic route/document tie-breaks, then constructs `SearchEvidence { fragment }`. Route mode, route ID, and route-local rank are discarded at this conversion.
4. `rust/crates/zg-engine/src/pipelines/indexed_search/context.rs::context_item_target` projects only `hit.evidence.first()` into an excerpt. `search_plan_to_context_items` creates one context item per entity hit.
5. `select_and_rank_context_items` deduplicates by `entity:<id>`, merges query-group metadata, then applies group coverage and global ranking. Its default output limit is seven items per small query set and thirty across larger query sets.
6. `ContextItem` already carries `entity_id`, entity `range`, `excerpt_range`, and exact `content_range`. The R4 adapter preserves content coordinates, byte offsets, and hashes. Source freshness is checked and marked.

The strongest observed failure is therefore after entity ranking. For `BufferedTokenStream`, the first evidence fragment in the rank-1 entity omitted the target lines while a recalled sibling fragment in that same entity contained them. For `val-03`, two recalled siblings covered the two required `GridReader` ranges, but only the first fragment was projected. These are direct reproduction facts. `val-04` and `val-10` combine multi-branch requirements with incomplete joins for some non-final evidence. `val-08` remains an acquisition/gold-scope uncertainty.

Other supported issues are distinct from fragment projection:

- Some target files or ranges never appear in saved results. The direct FTS/vector top-50 files are proxies, not the exact adaptive hybrid candidate pool.
- A target file ranked early is not necessarily an actionable method entry point.
- Sufficient-pack answers still fail required-step/citation dimensions on `a04`, `d06`, and `n11`.
- Indexed evaluation reports higher process latency and RSS than lexical `rg` on the measured workload. Warm serving cost was not measured.
- Trace data cannot consistently join every non-final entity to source ranges or first actionable evidence.

## Competing designs

| Design | Mechanism | Expected gain | Falsifier / regression risk |
|---|---|---|---|
| A. Select one sibling by fragment-level RRF | Preserve route-local ranks through evidence loading. For each recalled fragment compute `sum(1 / (60 + route_rank))`, then project the highest-scoring fragment per entity. Keep entity ranking and one item per entity. | May fix cases where a useful sibling is recalled but the currently first sibling is not. It preserves context size and entity diversity. | Fails if the useful sibling has weaker route ranks, if one required answer needs multiple fragments, or if route recall did not include the target. Route-rank presence is not semantic relevance. |
| B. Pack multiple fragments per entity | Preserve fragment provenance, deduplicate identical/overlapping source ranges, then let non-overlapping fragment items compete under the existing item and byte caps. Retain entity diversity by rank and allocate no entity an unlimited share. | Can provide multiple branches already recalled, directly addressing `GridReader`'s two disjoint ranges. | Can crowd out other entities, return duplicate/noisy excerpts, inflate tokens, or still fail when the needed fragment was never recalled. Public `ContextItem` already has entity and excerpt coordinates, but downstream consumers may assume unique entity IDs. |
| C. Expand one selected fragment to bounded source structure | Keep the baseline fragment and expand to its containing method/declaration or immediate neighboring indexed fragments where boundaries are reliable. Never cross nonlocal gaps. | Adds local context when a selected anchor is relevant but too narrow. | Does not recover nonlocal branches, can add irrelevant bytes, and adjacent-neighbor Candidate C previously failed task-level validation. Repeating the same neighbor policy is not justified. |

At the time this design memo was written, no design had been approved. The subsequent adjudication is recorded in `projection-prototype-v1/design-review-a-v1.json` and `projection-prototype-v1/final-sol-review-v1.json`: bounded sibling packing (B) was attempted first but had no free item slots; one-slot fragment substitution (A) was then reviewed and tested, but its improvement hypothesis was rejected on the frozen development split. See the canonical `HANDOFF.md` for the final corrected overlap-guard replay and status.

## Required reproductions and controls

- Same-entity wrong first fragment: two fragments share one entity. The first by route order misses the target. The selected output must contain the target-bearing source.
- Two separated siblings: both are required and fit the budget. Test whether the chosen design can expose both ranges without bridging unrelated source.
- Overlapping siblings: merge or retain one range deterministically without duplicate source bytes.
- Multiple entities compete for a small item/byte budget. A fragment-rich entity must not crowd all other entities out.
- Missing route metadata, empty evidence, Full-range fragment, byte-range fragment, CRLF and multibyte source coordinates.
- Input route/evidence order permutations must produce the same selected output when ranks and IDs are unchanged.
- Re-run existing entity ranking fixtures to ensure baseline-complete `dev-04` and other known successful cases survive.

Gold must not influence candidate selection. Use source spans only to score a frozen candidate after output selection. Enforce 10 items and 24,576 source bytes at the pack boundary. Do not compare raw FTS and vector scores as if they share a calibrated scale.

## Review request to GPT-6.1 Sol High

Choose one design to prototype, or reject all three. Challenge whether the alleged loss belongs to this layer, the route ranks provide a meaningful signal, fragment-level output breaks API assumptions, and the proposed budget/diversity policy preserves baseline successes. State the minimum source-only tests and development gate needed before any answer-model calls. Do not use exposed validation to tune or claim generalization.
