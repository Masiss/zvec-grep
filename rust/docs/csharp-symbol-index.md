# C# structural indexing and exact-symbol lookup

Status: **Index Track GO** for C# extraction and bounded exact-symbol
enumeration. RAG discovery, selection, expansion, held-out data and product
defaults are unchanged.
Date: 2026-11-09. Base `9444eed` (`work/zg-recovered`), branch
`work/csharp-symbols-20261109`.

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

### 2.2 Exact-symbol API

`ZvecGrep::symbols(SymbolsOptions)` returns `SymbolsResult`.

- Lookup is by **exact, case-sensitive declaration name** over the existing
  `symbol_name` metadata projection. It never uses FTS top-k, BM25, vectors or
  workspace file reads.
- Optional filters: `scope` (exact breadcrumb), `symbol_types`, `path` (exact
  workspace-relative path with `/`).
- `limit` defaults to 50 and is capped at 500; `total` reports matches before the
  limit and `truncated` reports whether the page is partial.
- Ordering is deterministic: relative path, source start offset, source end
  offset, entity id.
- Each item carries `entity_id`, `name`, `symbol_type`, `scope`, `signature`,
  `language`, `relative_path`, `absolute_path`, `range` (source line/byte
  coordinates), `role` and source `status` (`fresh` / `possibly_stale`).
- `role` is always `definition`. Reference occurrences are not declarations;
  they are never promoted into this result and no type resolution or call graph
  is attempted.
- Source range is read from the canonical entity record for the matching
  declarations. No content is returned and no workspace source file is opened.
  Freshness uses the existing stored-snapshot check, only for returned items.

### 2.3 Schema and migration

No metadata or storage schema field was added. `CodeMetadata` already carried
`symbol_name`, `symbol_type`, `scope`, `signature`, `language`, `documentation`
and `visibility`; C# now populates it.

Existing indexes built before this change contain `.cs` files as text chunks.
Returning zero C# symbols from those indexes would be silently incomplete, so
`CURRENT_INDEX_VERSION` was bumped **2 → 3**. The existing version gate fails
closed with `INDEX_INCOMPATIBLE` and the standard
`zg --index --rebuild` guidance. A rebuild re-extracts every file, so all C#
metadata is regenerated. Incremental refresh of a version-2 index is refused by
the same gate; there is no partial or guessed upgrade path.

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
- `cargo test -p zg-engine --lib`: 743 passed, 0 failed, 24 ignored.
- `cargo test -p zg-engine`: all binaries passed (unit, concurrent queries,
  C# symbols, engine storage, file selection, index version, scan failures,
  storage crash).
- Existing language-adapter tests are unchanged; the mixed corpus below indexes
  Rust, Python and Markdown identically before and after.

### 3.2 Measurements

Release build, mock OpenAI-compatible embedding server, deterministic corpus:
300 C# files + 75 Rust + 75 Python + 75 Markdown = 525 files. One process per
phase; peak RSS/PSS sampled in-process. Baseline is the same corpus at
`9444eed` with `.cs` indexed as text.

| Metric | Baseline (2) | After (3) | Delta |
|---|---:|---:|---:|
| Full index wall | 2.42 s | 2.90 s | +20% |
| Full index peak RSS | 322,432 KiB | 355,288 KiB | +10% |
| Full index PSS | 306,219 KiB | 332,867 KiB | +9% |
| Index bytes | 6.72 MB | 18.44 MB | +174% |
| Incremental (1 modified file) wall | 1.25 s | 1.35 s | +8% |
| Incremental peak RSS | 376,564 KiB | 389,848 KiB | +4% |
| Query process peak RSS | 284,716 KiB | 295,380 KiB | +4% |
| FTS `Method0` + `symbol_type=function` | 0 items, 352 ms | 1 item, 115 ms | fixed |
| Exact `Add` (600 declarations) | n/a | 127 ms p50 / 144 ms p95 | new |

Exact-symbol first query (cold, includes workspace open) was 368 ms; warm
single-name queries were ~118–136 ms. The 18.4 MB footprint is the structural
model's declaration bodies: a class entity contains its members and each member
is also stored separately, exactly as existing Rust/Java/TypeScript adapters
already behave. The baseline text fallback stored each file once as chunks.
This is a measured storage cost, not a correctness regression; no existing
adapter changed.

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
