# C# structural indexing and exact-symbol lookup

Status: **CONDITIONAL GO** after hardening; continuation checkpoint
`index-v3-handoff-20261109.md`. See `index-v3-hardening-results.md`,
`index-v3-deep-audit.md` and `index-v3-promotion-qualification.md`. RAG
discovery, selection, expansion, held-out data and product defaults are
unchanged.
Date: 2026-11-09. Base `9444eed` (`work/zg-recovered`); implementation branch
`work/csharp-symbols-20261109`, hardening branch
`work/index-v3-hardening-20261109`, final HEAD `894b71e`.

## 1. Root cause (audited, not inferred)

`extraction/code.rs::grammar()` and `extraction/code/adapter.rs::resolve_adapter()`
had no `CSharp` arm even though `FileFormat::CSharp` existed and the workspace
already depended on `tree-sitter-c-sharp`. `.cs` therefore fell through to the
plain-text fallback:

- no `CodeMetadata` was written, so fragment documents had no `symbol_name` or
  `symbol_type` scalar;
- `symbol_type` filters were correct but matched no C# data;
- the internal exact-name resolver returned zero candidates for every C# name;
- the public API had no exact-symbol enumeration at all.

The audit receipt is `benchmarks/rag-impact/results/onindex-exact-symbol-audit.json`
on `work/onindex`; the corresponding note is `rust/docs/rag-onindex-index-audit.md`.
This branch implements the minimal fix proposed there.

## 2. What changed

### 2.1 Extraction

- `Language::CSharp` added to the domain language enum (`"csharp"`).
- C# grammar wired through `tree-sitter-c-sharp` `0.23.5`.
- `LanguageAdapter` covers:
  - `namespace_declaration` and `file_scoped_namespace_declaration` → `Module`;
  - `class_declaration`, `struct_declaration`, `record_declaration`,
    `delegate_declaration` → `Class`;
  - `interface_declaration` → `Interface`; `enum_declaration` → `Enum`;
  - methods, constructors, destructors, operators, conversion operators and
    top-level local functions → `Function`;
  - properties, indexers, events, event fields, fields and enum members → `Value`.
- Fields with multiple declarators (`public int A = 1, B = 2;`) emit one
  declaration per declarator.
- Scope uses the existing `::` breadcrumb: `Acme.Core::Outer::Inner`. File-scoped
  namespaces apply to their following compilation-unit siblings, matching C#
  semantics; block namespaces scope their children.
- Signatures stop before bodies, accessor lists, arrow expressions, constructor
  initializers and attribute lists. `init`, `const`, `event`, generics and
  parameter defaults remain in the signature.
- XML doc comments (`///`) are captured through the existing comment path.
- Method bodies are not walked, so local variables and local functions inside
  methods are not emitted as declarations.
- Non-declaration source outside every declaration (using directives, comments,
  global attributes, top-level statements) is emitted as one text gap entity per
  contiguous non-whitespace run. This restores v2 text-fallback recall for
  mixed declaration/statement files without changing other adapters. For block
  namespaces the namespace entity covers only its header; comments/usings inside
  the block become gap entities. Punctuation-only gaps are skipped.
- C# documentation is taken from `///` and `/** */` preceding comments only; an
  ordinary `//` comment is not promoted to declaration documentation (it remains
  searchable through its gap entity).

### 2.2 Exact-symbol API

`ZvecGrep::symbols(SymbolsOptions)` returns `SymbolsResult`.

- Lookup is by **exact, case-sensitive declaration name** over the existing
  `symbol_name` metadata projection. It never uses FTS top-k, BM25, vectors or
  workspace file reads.
- Optional filters: `scope` (exact breadcrumb), `symbol_types`, `path` (exact
  workspace-relative path with `/`).
- `limit` defaults to 50 and is capped at 500; `total` reports all matches and
  `truncated`/`next_cursor` describe continuation. `cursor` resumes strictly
  after the last returned declaration.
- Ordering is deterministic and metadata-only: relative path, scope, symbol
  type, signature, entity id. Canonical payloads are read only for the returned
  page to resolve source ranges.
- Cursors are checksummed and bound to the query parameters plus the workspace
  generation and manifest update time. Malformed, cross-query and stale-snapshot
  cursors are rejected with `INVALID_ARGUMENT`; they never restart silently.
- `coverage` reports indexed, unfinished and failed workspace files for the
  snapshot; `complete` is false when any file is not indexed.
- Each item carries `entity_id`, `name`, `symbol_type`, `scope`, `signature`,
  `language`, `relative_path`, `absolute_path`, `range` (source line/byte
  coordinates), `role` and source `status` (`fresh` / `possibly_stale`).
- `role` is always `definition`. Reference occurrences are not declarations;
  they are never promoted into this result and no type resolution or call graph
  is attempted.
- Source range is read from the canonical entity record for the matching
  declarations. No content is returned and no workspace source file is opened.
  Freshness uses the existing stored-snapshot check, only for returned items.
- Exact-name candidate enumeration is bounded by the stored name cardinality,
  not by the page size; paging is correct but O(candidates) per page.

### 2.3 Schema and migration

No metadata or storage schema field was added. `CodeMetadata` already carried
`symbol_name`, `symbol_type`, `scope`, `signature`, `language`, `documentation`
and `visibility`; C# now populates it.

Existing indexes built before this change contain `.cs` files as text chunks.
Returning zero C# symbols from those indexes would be silently incomplete, so
`CURRENT_INDEX_VERSION` was bumped **2 → 3**. The existing version gate fails
closed with `INDEX_INCOMPATIBLE` and the standard
`zg --index --rebuild` guidance. A rebuild re-extracts every file, so all C#
metadata and gap entities are regenerated. Incremental refresh of a version-2
index is refused by the same gate; there is no partial or guessed upgrade path.
v3 was not released; the hardening branch is the first v3 definition. Any v3
index built from the earlier experimental commit must be rebuilt.

Measured v2→v3 rebuild on the mixed corpus: median 3.03 s, 15.51 MB, 346 MB peak
RSS. Non-C# workspaces rebuild too because the version is workspace-wide, but
their entity/fragment counts and index bytes are unchanged.

## 3. Verification

### 3.1 Tests

- `extraction::code` unit tests: declaration kinds, scopes, signatures, docs,
  source ranges, field declarator expansion, overload separation, nested types,
  file-scoped namespaces, and no method-body walking.
- `tests/csharp_symbols.rs` (real indexing through the public engine API):
  - parallel partial classes across files;
  - overloads as distinct declarations;
  - same simple name in different namespaces/scopes;
  - nested types and interface members;
  - namespace declarations repeated per file;
  - deterministic ordering, bound and truncation;
  - `scope`, `symbol_types` and `path` filters;
  - references inside method bodies not returned;
  - exact-name miss is empty, with no lexical fallback;
  - `symbol_type` filter over C# through the indexed context path;
  - file rename and delete with incremental refresh;
  - restart from persisted storage;
  - stale metadata marked, then repaired by `RefreshPolicy::Wait`;
  - fail-closed behaviour without an index and invalid bounds.
- `cargo test -p zg-engine --lib`: 745 passed, 0 failed, 24 ignored.
- `cargo test -p zg-engine`: all binaries passed (unit, concurrent queries,
  C# symbols, engine storage, file selection, index version, scan failures,
  storage crash).
- Existing language-adapter tests are unchanged; the mixed corpus below indexes
  Rust, Python and Markdown identically before and after.

### 3.2 Measurements (hardening protocol)

Release build, mock OpenAI-compatible embedding server, deterministic corpora;
three runs per condition, median reported. Baseline is the same corpus at
`9444eed` with `.cs` indexed as text. Full per-corpus tables, disk attribution
and caveats are in `index-v3-hardening-results.md`.

| Metric | Baseline (2) | After (3) | Delta |
|---|---:|---:|---:|
| Full index wall | 2.45 s | 3.07 s | +25% |
| Full index peak RSS | 315 MB | 347 MB | +10% |
| Allocated index bytes | 8.93 MB | 18.04 MB | +102% |
| Incremental (1 modified file) wall | 1.35 s | 1.46 s | +8% |
| Incremental peak RSS | 376 MB | 389 MB | +4% |
| FTS cold/warm p50 | 392–437 / 139–146 ms | 441–449 / 117–155 ms | parity |
| Exact `Widget` (500 declarations, limit 500) | n/a | 153–165 ms p50 | new |
| Exact paging 10 × 50 | n/a | 1.49–1.92 s total | new |

Exact-symbol cold queries were 423–634 ms; warm single-name queries 149–194 ms.
The mixed footprint doubles; a declaration-dense corpus (300 files × ~40
declarations) grows 6.7× (8.56 → 57.45 MB) and needs ~20× user CPU, dominated by
one 1024-d f16 vector per declaration. Parent/child body overlap and FTS
metadata prefixes account for the rest. High-symbol-density C# is therefore a
real capacity cost, not a rounding error; no dedup was implemented because it
changes retrieval semantics. Non-C# corpus bytes and results are identical.

Raw receipts: `~/.cache/zvec-grep/out-csharp/bench/`
(`baseline-*.json`, `after-*.json`, `*.time`).

## 4. Limitations (explicit)

- No type resolution, receiver inference, call graph or reference enumeration.
- `role: reference` is not produced by this metadata-only path; a future graph
  consumer must not reuse this API to claim reference coverage.
- Structs, records and delegates share the existing `Class` category because
  `SymbolType` has no distinct variants; signatures retain the real keyword.
- Field declarations are located at their declarator, not at the shared field
  statement keyword. Member scope and signature include the declared type and
  modifiers.
- Source ranges are exact declarations; surrounding comments/attributes are not
  part of the entity range.
- Namespaces are emitted once per written declaration, so a partial class split
  across two files yields two `Module` entries for the same namespace name.
- C# FTS/vector metadata omits `scope` (scope remains in `CodeMetadata` and
  exact-symbol filters). This prevents a class-name query from recalling every
  member through scope text; class/namespace declarations still represent their
  scopes. Requires reindex for a clean index; see
  `index-v3-promotion-qualification.md`.
- CLI/MCP/daemon do not expose exact-symbol enumeration or cursors; only the
  engine API does (interface gap, not a correctness failure).
