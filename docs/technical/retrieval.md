# Retrieval, extraction, and ranking

[Engineering guide](README.md) · [Runtime flows](runtime-flows.md) · [Storage](storage-consistency.md)

zvec-grep has two complementary product search paths: ranked retrieval from a persisted index, and managed ripgrep directly over workspace files. The indexed path is not exhaustive; managed ripgrep is the route for literal or regular-expression coverage.

## Workspace discovery and admission

Both implementations apply workspace scope, ignore/glob/type rules, and file-size/binary admission before extraction, but their concrete types and defaults are implementation-specific. For TypeScript, begin at `src/engine/utils/file-selection.ts`, `src/engine/pipeline/indexing/root-paths.ts`, scanner modules, and `src/engine/file-size-policy.ts`. For Rust, inspect `zg-engine/src/file_selection/`, `pipelines/indexing/`, and host scanner APIs. Do not assume that matching CLI flag names imply identical edge-case behavior; compatibility fixtures are the check.

## Extraction and index representation

TypeScript extraction routes through `src/engine/extraction/`: structural Tree-sitter code extraction, Markdown section extraction, text chunking, and image extraction. Extracted content is prepared for vectorization in `vector-content.ts`; file/fragment identities are managed in that subsystem. Model input limits and truncation are part of retrieval quality and must be evaluated alongside the extractor, not treated as a cosmetic detail.

Rust has an independent extraction and file-selection implementation under `rust/crates/zg-engine/src/extraction/` and `file_selection/`. It has its own supported grammar/runtime dependencies declared in `rust/Cargo.toml`; parity should be tested per fixture/format rather than inferred from shared terminology.

The indexed unit may represent a file as multiple fragments (for example code symbols or document sections). Search therefore retrieves fragment candidates and later hydrates them into file-level results, previews, and source ranges. Preserve fragment→file mappings and the source snapshot during both ranking and hydration.

## Indexed retrieval stages

```text
normalized query and filters
  ├─ lexical route (FTS/BM25)
  ├─ vector route (embedding + nearest-neighbor)
  └─ hybrid route (candidate retrieval → rank fusion)
       → candidate/filter processing → final ranking/limit
       → file grouping and source hydration
```

In the TypeScript implementation, the route/pipeline is under `src/engine/pipeline/search/` and `src/engine/service/lexical.ts`; embedding and ranking backends are under `src/engine/models/`. In Rust, inspect `pipelines/indexed_search/`, the model runtime, and its storage read ports. Search docs describe hybrid lexical/vector retrieval and reciprocal-rank fusion (RRF); exact candidate limits, scoring weights, tie-breaks, and filter order are implementation details that must be confirmed in the current source and route-specific tests before being documented as stable.

Key distinctions:

- **Lexical/FTS:** ranks indexed text using native lexical index structures. It is not a promise of exact whole-workspace coverage.
- **Vector:** embeds the query and searches semantically similar indexed fragments; model, endpoint, and index compatibility matter.
- **Hybrid:** combines lexical and vector candidate lists/ranks. The fusion mechanism is not equivalent to adding raw lexical and vector scores.
- **Multiple query groups:** may remain separate or be fused depending on the explicit API/CLI option; check that caller contract.
- **Filters and glob/type selection:** determine eligible scope/candidates. Verify whether a given filter is applied at discovery, native retrieval, or post-retrieval in the owning route.

## Managed ripgrep

The managed-rg path scans source files directly using bundled ripgrep support in TypeScript or the embedded `grep`/`ignore` crates in Rust. It does not require an index or embedding model. zg owns result formatting, so some ripgrep output modes are intentionally rejected while common matching/context/discovery controls are supported. The TypeScript adapter is `src/cli/managed-rg.ts`; Rust CLI parsing and execution are in `zg-cli` and `zg-engine` direct-search modules. Treat this as a separate route, not a fallback that indexed search silently becomes.

## Embedding and model lifecycle

Model selection, artifact resolution, worker/concurrency limits, remote authorization, and cache lifetime can affect index contents, latency, and privacy. TypeScript implementation is in `src/engine/models/` plus `src/authorization/`; Rust is under `zg-engine/src/models/` and `authorization/`. Consult [Embedding models](../07-embedding.md), and Rust authorization docs, before changing endpoint resolution or consent. A local MCP/daemon token protects the local service boundary; it does not grant permission to transmit workspace content to a remote model.

## Evaluation and regression strategy

A retrieval change should identify which behavior it targets and use corresponding tests:

- extraction/symbol/section fixtures and source-location correctness;
- file-selection and ignored/generated-file cases;
- lexical exact-term recall and ranking;
- semantic recall and model/output shape;
- hybrid fusion, duplicate handling, filters, candidate refill, and stable tie-breaking;
- result hydration, previews, paths, and output ordering;
- direct managed-rg parity for its supported flags;
- TypeScript↔Rust compatibility cases when the Rust implementation is in scope.

Offline retrieval reports and benchmark datasets are scoped evidence, not blanket product quality claims. Preserve dataset, split, query set, model, candidate limits, and metric definitions in the report; do not conflate latency results with relevance results.