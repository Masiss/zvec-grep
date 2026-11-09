# One-Index capability audit (RAG v1.0)

Status: **audit only; no index format, storage or default change.**
Date: 2026-11-09. Base `9444eed` (`work/zg-recovered`), branch `work/onindex`.
Companion: `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md` and
`rust/docs/rag-onindex-discovery-results.md`.

Evidence: source inspection of `rust/crates/zg-engine/src/{storage,api,pipelines,extraction}`,
plus live read-only probes against the frozen B0 workspace index (818/818 files,
33 MB, copied to the experiment OUT):

```text
zg --status                     100% 818/818 files; FTS tokenizer=jieba filters=lowercase
zg --fts "employee"             code entities with ranges
zg --fts "EmployeeDetail" --prefer-symbol
                                declaration entity recalled by exact symbol name
zg --fts X --symbol-type class  "No matches" for every type value
in-process probe (release)      first query ~570 ms cold, then ~140 ms; ~398 MB HWM
CLI query (any route)           ~297 MB peak RSS per process
B0 server receipt               idle PSS 571,800 KiB, peak 769,113 KiB
```

## 1. What the one index stores

| Object | Contents | Query surface |
|---|---|---|
| `entities` collection | one canonical record per entity: id, `file_id`, source range, content (Text/Code/Image), fragment selectors, JSON `metadata` | vector search, fetch by id |
| `fragments_<model>` collections | one record per fragment: `file_id`, `entity_id`, `document_id`, `text` with a full-text index, `embedding`, metadata scalars | FTS (BM25), vector (cosine), scalar filters |
| `files` collection | path, file id, size/snapshot/status | path/file-id resolution |
| graph SQLite (`VERSION 1`) | `edges` table: contains/calls/imports/extends/implements, with pending references and resolution provenance | direct SQL through `IndexStore` |
| workspace manifest | index state/descriptor, embedding model, FTS config, generation | validation, freshness |

Indexed metadata scalars today: `file_id`, `entity_id`, `document_id`,
`symbol_name` (hashed with a fixed salt), `symbol_type`. `CodeMetadata` also
carries `scope`, `signature`, `documentation`, `visibility`, `language`, and
`MarkdownMetadata` carries `heading`, `scope`, `level`, but those are stored in
the entity payload, not as filterable scalars.

## 2. Capabilities that exist

1. **Exact file/range addressing.** Every entity and fragment carries a source
   range; `ContextItem` exposes path, range, content, entity id and container.
2. **Exact symbol recall.** `CodeMetadata::SYMBOL_NAME` is indexed and hashed.
   `--prefer-symbol` adds a symbol-filtered FTS recall route, and the resolver
   (`IndexStore::find_resolution_candidates`) enumerates *every* exact-name
   definition without a search cap.
3. **Lexical (BM25) search.** Full-text over fragment text, jieba tokenizer,
   lowercase filter; multiple model tables merge by reciprocal rank.
4. **Vector search and hybrid fusion.** Cosine over pinned fp16 vectors, RRF
   fusion across routes (`fts`, `vector`, `fts+vector`), adaptive recall depth.
5. **Path/file filters.** Glob rules, formats/categories, file-id sets,
   `rg_paths`, per-query `QueryFilter`.
6. **Structural summaries on demand.** `code_outline` derives bounded member/call
   summaries from stored content for class/interface/module/function entities.
7. **Embedded lexical scan.** The engine embeds ripgrep (`grep` crates) with
   ignore/glob/type handling; usable for exhaustive bounded selection.
8. **C# fact extraction (side parser).** `csharp_facts` extracts types, fields,
   DbSets, methods, assignments, invocations, saves and resolves scopes inside
   one project; `candidate.rs` implements the typed closure used by H7.

## 3. Capabilities that are missing or thin

| Gap | Evidence | Consequence |
|---|---|---|
| Reference/declaration edges are not produced by indexing | `ExtractionOutput.graph` is `None` in every production extractor path; `edges` is written only by tests | The resolver/graph is scaffolding, not a usable discovery source |
| No type/scope model in the index | `scope` is a string in `CodeMetadata`; no receiver types, no field types, no DbSet model | Receiver/type resolution needs the side parser or a parse |
| No public exact-symbol enumeration | `find_resolution_candidates` is `pub(crate)`; public `ContextOptions` cannot filter by `symbol_name` | Public callers get ranked top-k, not all declaration sites |
| `symbol_type` filter returns no matches | live probe: every type value returns 0 hits for names that do exist | Cannot restrict declaration recall to class/interface/module |
| No structural document model | Markdown has heading/scope/level only; JSON/CSV/log have no section/key/event metadata | Document discovery must group at read time |
| Process residency is large | ~297 MB per CLI query, 398 MB in-process, 571 MB idle server | Absolute RSS gates measure the resident service, not per-case discovery |

## 4. Prior-art comparison (mechanism level)

| System | Mechanism | What zg already has | What zg adds / lacks |
|---|---|---|---|
| ripgrep | exhaustive lexical scan, no index, no structure | embedded `grep` crates, globs/types/ignore | Exhaustive and cheap per file, but no cross-file identity; used as bounded file selection in arm B |
| ast-grep | tree-sitter AST pattern matching with structural scopes | tree-sitter grammars, outline, C# fact walk | No pattern engine; the fact walk already resolves scopes inside a project |
| Kensaku | hybrid code search surface (lexical + semantic) | FTS + vector + RRF fusion + path filters | Comparable recall stack; no evaluation evidence reused here |
| sweet-search | agent-facing search surface over code/docs | one index for code and documents, provenance-carrying candidates | zg's gap is declaration/reference enumeration, not retrieval |

Only mechanism classes are compared; no external benchmark result is reused as
evidence. The concrete gap that matters for discovery is **exact declaration
enumeration**, which the store already implements internally.

## 5. What the audit implies for discovery

- The index is sufficient to *seed* discovery (FTS/vector/path) and to locate
  declarations **if** exact symbol enumeration is exposed; it is not sufficient
  to answer "which types/fields/DbSets does this anchor need" without either the
  side parser or a bounded parse.
- Document discovery can reuse the same envelope and index ranges; document
  structure has to be derived at read time (headings, JSON key path, CSV header,
  log event).
- A process-resident index cannot satisfy an absolute 64 MiB peak; the gate has
  to be applied to the discovery worker's incremental cost, with the total
  process reported separately (the pre-registration does exactly this).
