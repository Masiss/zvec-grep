# Indexed Semantic Retrieval foundations: Phase A embedding audit

**Status:** source audit complete; one exposed-case direct embedding diagnostic; same-source extraction comparison completed across four locked C# files; full-file method-span mapping cross-checked on those files. Independent embedding benchmark remains in progress.
**Scope:** maintained checkout at `663566546f03768b9b021c65eb3accc4aa0720a1`, plus the separately pinned R4 v3 research snapshot at `/tmp/r4-evidence-projection-v3-20261124/rust` and public corpus receipts. No index was rebuilt or changed.

## Reproduction identity

| Item | Evidence |
|---|---|
| Model | `local/potion-code-16m-v2`, backend `model2vec`, catalog reference pinned to Hugging Face revision `e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`; ModelScope mirror revision `3e922fde18f43b8db69f3381b6d468738d3dd2d7`. |
| Weights | Catalog size 32,490,072 bytes, SHA-256 `75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c`; the cached file has the same hash. |
| Tokenizer | `tokenizer.json`, 1,024,340 bytes, SHA-256 `107bbdcbad4bff1d299b7a4c3a2fb17c52890688b7dd0e4c9deab79d3c4f3d45`; `tokenizer_config.json` SHA-256 `81336c4a13050c6487eee83879338202a16638fbf49b5b0231a9deee1231b0c1`. The latter is not a substitute for the tokenizer hash. |
| Model shape and metric | 256 dimensions, cosine, L2-normalized output, maximum 1,024 tokenizer tokens. Catalog and runtime implementation agree. |
| Runtime/backend | R4 public-source receipt records CPU inference, engine source commit `e7d0a89c27e199489ea4ad786c619211bad5ac2d`, and R4 adapter binary SHA-256 `cd7b66620a49b771c05f21ec2c74f3a3dfa960c92854717fb2dde5d59aaf15c2`. Root checkout is a separate source version; do not claim that binary was built from current HEAD. |
| Current local cache | The pinned revision's weight and tokenizer files exist under `/home/masis/.zvec-grep/models/...`; their hashes match the maintained catalog. No network access is needed for the planned local probe. |

## Exact current embedding path

### Document input

1. In the matched R4 v3 source used for the RAG traces, C# extraction parses source with the existing Tree-sitter C# grammar, forms an entity from a recognized code node, and stores its source content/range plus symbol metadata. **Important source-version difference:** root HEAD `6635665` has no C# grammar/dependency and falls back to plain text for `.cs`; it cannot reproduce C# symbol metadata. Use the pinned R4 v3 source for C# RAG experiments, and treat root HEAD as a different implementation until reconciled.
2. Fragment creation slices entity content at entity-relative byte ranges. For code, default nominal chunking is 3,600 characters/540 overlap, but indexing supplies a model-aware budget. Potion's 1,024-token limit yields an estimated 1,894 UTF-16-character chunk budget (`1024 * 185 / 100`) and 15% overlap, reduced by metadata space. Token-dense inputs can use 1,024 characters/15% overlap.
3. Document text is `vector_metadata_text + "\\n" + fragment_text` when metadata exists. For code the prefix can include `symbol: <kind> <name>`, `scope: ...`, `signature: ...`, and collapsed `doc: ...`. Metadata is capped at one quarter of the nominal chunk-character budget; fragment content budget is reduced accordingly. The model then applies the 1,024-token limit and records which inputs were truncated. **R4 v3 C# special case:** scope is deliberately omitted; the A02 diagnostic passed `max_chars=1,894`, so its metadata cap was 473 UTF-16 code units.
4. Potion has **no query prefix and no document prefix**. Model2Vec tokenizes, takes the first 1,024 IDs, removes unknown-token IDs, averages static token vectors, L2-normalizes, and narrows to `f32`.

### Query input

The resolved vector-route query is passed as a single text input with purpose `Query`. Potion's query prefix is `None`, so it is tokenized with the same backend algorithm and token limit as documents, without document metadata. Route construction or query-group expansion can alter which query string reaches this boundary; experiments must log that resolved string and test the intended verbatim-query case separately.

### Source locations

- Model catalog and backend contract: `rust/crates/zg-engine/src/models/catalog/entries.rs`, `rust/crates/zg-engine/src/models/backends/model2vec/model.rs`.
- Query embedding: `rust/crates/zg-engine/src/pipelines/indexed_search/pipeline.rs::SearchEmbeddingRuntime`.
- Document projection/chunking: `rust/crates/zg-engine/src/extraction/service.rs`, `rust/crates/zg-engine/src/pipelines/indexing/input_budget.rs`, and `rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs::prepare_fragments`.
- Structural extraction: `rust/crates/zg-engine/src/extraction/code.rs::walk_code_node` and `append_entity`.
- The R4 v3 C# implementation additionally lives at `/tmp/r4-evidence-projection-v3-20261124/rust/crates/zg-engine/src/extraction/code.rs` and `.../extraction/code/adapter.rs`; its pinned C# grammar is `tree-sitter-c-sharp`.

## Verified observations and open checks

- **Confirmed:** metadata is included in every code fragment's vector text. It may help identify symbols but also repeats documentation/signature in every fragment; representation ablations must isolate this instead of silently stripping it.
- **Confirmed:** exact truncation behavior is prefix truncation at 1,024 token IDs after full projected text is assembled. Current chunk heuristics estimate token count by characters and do not prove that each input avoids truncation.
- **Confirmed:** indexing pipeline receives truncated input indices. **Not yet established:** whether the R4 receipt/index stores per-fragment truncation details and whether the historical public-source index had any truncated fragments. Check status/receipts before attributing quality issues to truncation.
- **Confirmed:** code path writes vectors and supports vector search; the search storage interface loads hit content and metadata, not arbitrary stored vectors. **Not yet established:** whether a low-level scratch-only API can export vectors without modifying/rebuilding the protected index. Use local re-embedding for the controlled study unless that is proven.
- **Confirmed:** actual cached weight and tokenizer hashes match the current catalog; R4 evaluation receipt independently records the weight hash. The existing R4 search binary's model hash provenance is strong, but the report should preserve its source commit/binary digest separation.
- **Not tested yet:** whether exact cosine calculations over re-embedded vectors reproduce R4 vector ranks. This requires re-embedding the exact stored document inputs and comparing the resulting same-index vector ordering; do not assume matching weights alone proves this.
- **Not tested yet:** where long source fragments lose key behavior under token truncation, whether metadata consumes a material share of the 1,024-token input, and whether an alternative representation improves the hard-negative cases.

## Next experiments (pre-result)

1. Produce a frozen, source-range-labeled hard-negative dataset with held-out files/entities. Keep known `a02`/`a03` and previously exposed cases as diagnostics only; do not open the sealed RAG validation tasks.
2. Re-embed exact R0 current inputs with the cached model; report truncation, pairwise wins/ties/losses and margins by hard-negative type, plus first-relevant rank on shared candidate pools.
3. Hold query embeddings fixed for R1–R5 representation ablations. Record source ranges, character/token lengths, truncation and embedding cost.
4. Prototype fragment-to-method mapping with existing Tree-sitter grammars on full entity content; validate partial/cross-method ranges and document range limitations before considering any retrieval use.
5. The design contract has received a fresh-context GPT-6.1 Sol review. Sol has not yet reviewed actual task/range/label artifacts or any score results. Submit the completed score-blind annotations and pools for review before freezing or scoring.

## Prior evidence retained as diagnostics, not a model verdict

- RAG `a02`: within one `BufferedTokenStream` entity, the implementation-bearing fragment at lines 146–199 had vector rank 17; nearby `SetTokenSource` documentation at lines 339–388 had vector rank 26. A lexical substitution nevertheless selected the documentation fragment.
- RAG `a03`: the target sibling had vector rank 11 versus baseline sibling rank 39.
- These are a few exposed RAG queries with different retrieval competition and ranks; they are not pairwise-accuracy or margin estimates.
- Indexed semantic qualification recorded 0/9 searchable gold spans in vector and hybrid top 10 over three tasks. That run remained `INCONCLUSIVE` due to the v3 verifier mismatch and small task slice, so it cannot isolate an embedding limitation.

For the public C# A02 source file (21,550 bytes), the existing token-dense detector chooses the ordinary 1,894 UTF-16-character window and 284-character overlap; the document metadata cap is 473 UTF-16 units. The exact positive R0 input used in the direct test has SHA-256 `48a3b972d680eec9e6d0764566cfaed184acf44738cf12895919e9cda666b7d8`, is 377 Potion tokenizer tokens, and is included in `potion-diagnostic-a02-r4-v3.json`.

## Direct exposed-case diagnostic: `a02`

`potion-diagnostic-a02-r4-v3.rs` ran against the matched R4 v3 extractor and locked public C# source. It rebuilt current R0 metadata-plus-fragment inputs and computed cosine directly from local query/document vectors. No index was opened or modified; no input was truncated.

Five sibling fragments from one `BufferedTokenStream` entity were compared with the exact R4 v3 metadata cap and input builder. The query tokenized to 17 tokens; the positive document example tokenized to 377. All five inputs were below the 1,024-token limit. Across six positive/negative comparisons, there were **3 positive wins and 3 negative wins**. The implementation fragment at lines 146–200 scored `0.43232`; the nearby `SetTokenSource` documentation/body fragment at lines 339–388 scored `0.42164` (margin `0.01069`). But an `AdjustSeekIndex` documentation plus `LT` fragment at lines 307–346 scored `0.45399`, above both positive fragments. The best positive ranks second in this five-fragment candidate pool. The exact generated positive document input and its SHA-256 are stored in the JSON artifact.

This is mixed evidence: Potion gives a small favorable signal for the exact same-entity `SetTokenSource`-documentation-vs-`Reset`-implementation pair, but a neighboring incomplete behavior fragment defeats it. This final score supersedes the first diagnostic attempt, which incorrectly passed uncapped metadata; the source-input cap is now aligned with R4 `prepare_fragments`. The case was exposed before this diagnostic and cannot estimate general pairwise accuracy. Machine-readable result: `potion-diagnostic-a02-r4-v3.json`.

The same run also exercises a temporary query-time Tree-sitter span mapper, detailed in `STRUCTURE-MAPPING-DIAGNOSTIC-v1.md`. It maps methods, bodies and preceding XML docs from the stored class entity, without new parser or index fields. Two focused tests passed. This supports **STRUCTURE_MAPPING_FEASIBLE** for the matched R4 v3 C# path; it is not integrated into retrieval and does not apply to root HEAD's plain-text C# fallback.

## Maintained checkout versus R4 v3, same locked source

The current checkout is `work/source-discovery-autonomous-20261122` at `663566546f03768b9b021c65eb3accc4aa0720a1`. The pinned R4 v3 source is `work/r4-evidence-representation-20261119` at `e7d0a89c27e199489ea4ad786c619211bad5ac2d`. The C# Tree-sitter grammar and indexing adapter first appear in commit `986ab51`; that commit is an ancestor of R4 v3 but not of the current checkout. The R4 grammar is `tree-sitter-c-sharp 0.23.5`. The R4 line also has `csharp_facts::MethodFact`, but this side parser stores identity/signature and line start/end; it is distinct from the Tree-sitter indexing adapter and is not persisted in the indexed entity schema.

A deterministic scratch comparison ran both extraction paths over the same four files from the locked public source tree. Source hashes matched between runs. Chunk options were held at 1,894 UTF-16 units with 284 overlap; no index or model inference was used. The tokenizer was used only to count generated R0 inputs.

I audited the R4 scratch source against the clean `e7d0a89` worktree after an initial path-check command falsely reported broad differences. Correct-path comparison confirms byte-identical manifests, lockfile, C# adapter, embedding input builder, indexing pipeline, and Model2Vec backend. `code.rs` differs only by scratch mapper re-export and test functions; the extraction implementation is otherwise identical. The scratch RAG context has separate experiment-flag changes, but was not called by these measurements. The comparison is therefore tied to the R4 v3 extraction/input path used for index generation.

| File | Current checkout entities / fragments / symbol metadata | R4 v3 entities / fragments / symbol metadata | Source-byte coverage, current / R4 | Sum of R0 tokenizer tokens, current / R4 |
|---|---:|---:|---:|---:|
| `BufferedTokenStream.cs` | 14 / 14 / 0 | 45 / 63 / 43 | 100.00% / 99.90% | 5,698 / 12,690 |
| `TokenStreamRewriter.cs` | 19 / 19 / 0 | 64 / 94 / 62 | 100.00% / 99.93% | 7,582 / 16,647 |
| `UnbufferedTokenStream.cs` | 8 / 8 / 0 | 32 / 39 / 31 | 100.00% / 99.82% | 3,332 / 6,363 |
| `JsonTextReader.cs` | 66 / 66 / 0 | 122 / 159 / 105 | 100.00% / 99.18% | 20,459 / 24,728 |

The current checkout falls back to plain-text chunks with no symbol metadata. R4 v3 emits class/member/value entities, qualified scopes, signatures and attached documentation metadata. Its finer candidate inventory is 2.4–4.9x as many fragments on these files; total tokenized candidate input is 1.2–2.2x. This is an indexing and cost difference, not evidence of better relevance: candidate counts, overlaps and metadata all changed together. The emitted ranges cover 99.18–99.93% of these files in R4; the remaining bytes are not necessarily semantically important, but full-file coverage is not exact. Full measurements and per-file hashes are in `csharp-source-version-comparison.json`.

Historical run identity is not uniform. The public R4 development/projection runs cite source commit `e7d0a89`, adapter SHA-256 `cd7b6662…`, source-lock `214298e7…`, index generation `ed3d5c63…`, and manifest `bc9fef25…`. The 2026-11-22 indexed-semantic qualification used a different v3 index generation `cea13512…` and cached route adapter SHA-256 `bdd6f620…`; its receipt explicitly says cached-binary source linkage was not independently established. Do not merge those scores into a single source-version-controlled benchmark. The A02 direct cosine diagnostic did not query any index; it re-embedded the exact R0 input locally with the pinned Potion weights/tokenizer.

## Expanded method mapping check

The entity-local mapper exactly joined 33/33, 53/53, and 17/17 method spans to extractor method entities in the three ANTLR files. In `JsonTextReader.cs`, class-entity outline reparsing found 6 of 73 methods because the stored class entity ends at an error-recovery boundary; the C# side-facts parser reports 73 methods but marks the file parse-error. A scratch full-file parse plus span walk finds all 73 method nodes and joins 73/73 exact byte ranges to the method entities, including despite the parse-error flag. This distinguishes a scope limitation of entity-local outline from loss of method candidates.

Warm full-file parse-plus-map medians were 7.99 ms (21.6 KB), 10.11 ms (29.0 KB), 3.36 ms (12.3 KB), and 32.22 ms (101.7 KB). This measures a local debug unit-test path, not end-to-end request latency or cold service cost. The full-file parser was run separately from indexing; the scratch mapper itself does not persist ranges. The test covers methods and documentation comments present in these four files, not the complete C# syntax matrix.

## Candidate geometry and C# side facts

On the 48 candidate-curation files (24 ANTLR, 12 Dapper, 12 Newtonsoft), R4 v3 extraction produced 995 symbol entities; the side-facts parser produced 548 MethodFacts. These totals are not the same unit: extraction includes classes, interfaces, properties/values and methods, while MethodFact is method-only. Concrete evidence of the distinction: `IWritableToken.cs` and `IRecognizer.cs` have zero MethodFacts but eight indexed structured entities each, mostly interface/property declarations. Interface declaration source is therefore available to the indexing/extraction path even where method facts are absent.

Across these files, full-file Tree-sitter identified 548 method spans; 80 documented methods have at least one stored R4 fragment that intersects their docs without intersecting their body and at least one fragment that intersects the body without docs, across 21 files. This shows the existing R4 fragment geometry can provide genuinely separable documentation/body candidates for annotation in some files, while many other cases have only mixed fragments or no docs. This is a deterministic geometry count, not a relevance judgment or a benchmark score. Four of the 48 files have a Tree-sitter parse-error flag; their extractor/facts output remains represented and must be manually checked before a task uses a range. Candidate inventory and geometry receipts are `csharp-benchmark-candidate-inventory-draft.json` and `csharp-fragment-doc-body-geometry-draft.json`.

The R4 v3 scratch export initially validated 48 files and has now expanded to all 51 files used by the behavior-study candidate inventory, including the Dapper interface implementation example and Newtonsoft base converter declaration. All 51 hashes match the authoritative public source lock. Extraction emitted 1,178 entities and 1,591 fragments; the mapper produced 699 owner projections and 659 unique method ranges, exactly matching the 659 structured function entities. There are 40 repeated nested-owner projections and no mismatched ranges or source slices. Four parse-error files remain in the distractor pool; carry their status into transformation receipts. The 51-file extraction-plus-map test took 0.68 seconds in this local debug run, including source loading, so it is not a request-latency estimate. See `csharp-r4-v3-51-file-mapping-validation.json` and `STRUCTURE-MAPPING-DIAGNOSTIC-v1.md`.

This validates the mapping substrate, not Potion relevance. The current 40-group query and annotation files are still drafts; Sol's first score-blind review found material query/range/role defects, and a corrected second review is required. No similarity scores or model outputs have been computed. Primary benchmark scoring stays closed until candidate-specific support/partial/non-support labels and metadata-documentation provenance are adjudicated and the design review passes.

## Remaining Phase A/B checks

- Reproduce vector-route ordering from direct vectors on a controlled same-entity pool; do not assume stored route scores are cosine values until the metric transform is verified.
- Freeze the 30–50 group benchmark with file/entity-disjoint held-out sources. Include mixed neighboring fragments because the `a02` negative that outranks the positive is not merely documentation.
- Run truncation and metadata-budget diagnostics across the frozen benchmark, then keep R0 input fixed while testing representations.
- Reconcile the absence of C# extraction on root HEAD with the R4 v3 parser path before selecting a product architecture target. The four-file comparison now quantifies the divergence; broaden to more source structures only if it changes the benchmark conclusion.
- Freeze the independent query groups, ranges, and scoring before any primary similarity run; use the separate model/index identities above when describing existing results.
- Then send the frozen methodology to fresh-context GPT-6.1 Sol High for an independent challenge before primary evaluation.

Structure mapping is no longer an open feasibility question for the matched R4 v3 C# class entity: the scratch mapper and its two focused tests establish a no-schema path. Mapping accuracy across the requested edge cases, query-time service cost, and value to ranking remain unmeasured.
