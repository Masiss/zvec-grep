# Index v3 handoff — 2026-11-09

## Current checkpoint

- Canonical source remains `work/zg-recovered` at `9444eed`. **Nothing is merged,
  pushed or released.**
- Index v3 worktree:
  `/home/masis/src/worktrees/zvec-grep/index-v3-hardening-20261109`
- Branch: `work/index-v3-hardening-20261109`
- **HEAD: `894b71e`** (clean working tree)
- External build cache: `~/.cache/zvec-grep/rust-target-recovered-20261105`
- Protected `rust/target`, main tree, RAG, Selection/Expansion, held-out data and
  historical receipts were not modified.
- No owner binary/daemon/index was installed, restarted or migrated.

Read in this order before resuming:

1. `index-v3-promotion-qualification.md` — final gates/results and verdict.
2. `index-v3-deep-audit.md` — independent findings F1–F11 and dispositions.
3. `csharp-symbol-index.md` — extraction/API contracts.
4. `index-v3-hardening-results.md` — earlier resource/disk protocol.
5. This handoff — continuation state and exact next action.

Do not treat any older handoff or stop decision as authorization. The active task
must come from the current owner request.

## Scope delivered

### Extraction

- C# `tree-sitter-c-sharp` adapter: namespaces (file-scoped/block), class,
  struct, record, interface, enum, delegate, methods, constructors, destructors,
  operators, conversion operators, properties, indexers, events, fields, enum
  members, top-level local functions.
- Source-backed declaration ranges/signatures/scopes; field declarator expansion;
  overload/partial/nested/Unicode/CRLF/generic/operator/indexer coverage.
- Gap entities cover non-declaration source (usings, comments, global attributes,
  top-level statements) without duplication; punctuation-only gaps skipped.
- C# documentation comes only from `///` and `/** */`; ordinary comments remain
  searchable through gaps.
- Block namespace coverage ends at the body start (does not hide comments/usings).

### Exact-symbol API

`ZvecGrep::symbols(SymbolsOptions) -> SymbolsResult`:

- exact, case-sensitive, declaration-only lookup over existing `symbol_name`
  metadata; no FTS/vector ranking and no type resolution/call graph;
- deterministic metadata ordering `(path, scope, type, signature, entity_id)`;
- bounded page (default 50, max 500) with opaque `cursor`/`next_cursor`;
- cursor is checksummed, query-bound, generation/`updatedTime`-bound and capped
  at 16 KiB; stale/cross-query/malformed/oversize cursors are rejected;
- canonical payload read only for the returned page; per-item freshness;
- workspace file coverage (`indexed/unfinished/failed`) so a failed file never
  looks exhaustive.

### Retrieval correctness

- **F11 blocker fixed**: zvec `Fts::set_match_string` returned arbitrary top-k for
  multi-term no-match queries. FTS now builds an explicit quoted `A OR B`
  `query_string` from lexical identifier terms; no-answer returns empty, partial
  known/unknown keeps OR recall, CJK/punctuation/boolean-stopword cases covered.
  **Query-only; no reindex needed.**
- **F5 fixed**: C# FTS/vector metadata omits `scope`; `CodeMetadata.scope` remains
  for output/filters. Class/namespace entities still represent scopes. This cuts
  same-file crowding and 1.6–2.4% disk; requires reindex for a clean index.

## Commits

| Commit | Summary |
|---|---|
| `986ab51` | C# structural adapter + public exact-symbol API; v3 version bump |
| `e9a9824` | C# gap coverage, cursor/coverage, snapshot checks, benchmarks/docs |
| `928f49f` | block-namespace gaps, ordinary-comment docs, cursor cap, adversarial tests |
| `894b71e` | multi-term FTS `query_string` fix, C# scope omission, quality gates/receipts |

## Verification (at `894b71e`)

- `cargo test -p zg-engine --tests`: lib **745 passed / 24 ignored**; integration
  binaries **8+1+4+17+8+1+2+2+3+2 passed, 0 failed**.
- `cargo test -p zg-cli -p zg-daemon-protocol -p zg-transport-mcp`:
  **180+5+7+60 passed, 0 failed**.
- `cargo check --workspace --all-targets` and release `zg` build pass.
- `cargo fmt --all -- --check` passes.
- New code has no clippy warnings. Workspace `-D warnings` still reports
  pre-existing `extraction/candidate.rs` lints present at the baseline.
- FTS tests: `tests/fts_query.rs`; extraction: `csharp_adversarial...`;
  coverage: `tests/csharp_coverage.rs`; pagination/coverage:
  `tests/csharp_symbols.rs`; exact-symbol API edge cases.
- Version rollback fail-closed checked both directions (`expected 2`/`expected 3`).

## Final measurements (mock embedding, synthetic corpora)

| Corpus | v2 allocated | v3 final allocated | v2 full | v3 final full | v3 peak RSS |
|---|---:|---:|---:|---:|---:|
| mixed | 8.93 MB | 17.37 MB | 2.45 s | 2.82 s | 344 MB |
| dense | 8.56 MB | 54.15 MB | 2.09 s | 6.59 s | 447 MB |
| duplicate | 8.21 MB | 12.56 MB | 2.27 s | 3.11 s | 328 MB |
| quality | n/a | 5.75 MB | n/a | 1.59 s | 315 MB |
| non-C# | 5.479 MB | 5.479 MB | 1.72 s | 1.87 s | 311 MB |

- Dense: one 1024-d f16 vector per declaration dominates (25.8 MB/54.15 MB);
  container vectors are only ~1.1% (dense) to ~8.5% (mixed). User CPU dense
  ~1.1 s → ~21 s; this is **not lightweight** for declaration-dense C#.
- Quality corpus: R@10 **0.833** (maximum for 12 relevant files in 10 slots),
  R@50 **1.0** FTS/hybrid, **0.983** vector, MRR/nDCG@10 **1.0**.
- No-answer query: old FTS returned 50 arbitrary docs; final returns 0.
- Mixed `Type0` @50: 12 items/8 same-file → 6 items/2 same-file, 4 files.
- FTS/vector/hybrid `prefer-symbol` expected-path probes remain hit.
- Exact-symbol paging remains complete and cursor-safe; O(candidates)/page and
  workspace coverage scan are accepted limitations.

## Migration / reindex / rollback

- v2→v3 is an extraction-capability change, not a storage schema change. The
  manifest gate fails closed and requires `zg --index --rebuild`.
- FTS `query_string` fix is query-only; no reindex.
- C# scope omission changes FTS/vector text; rebuild v3 for a clean index.
  There is no automatic detection between pre-`894b71e` v3 indexes and the final
  definition. Treat `894b71e` as the first promotable v3 definition.
- v3→v2 rollback requires rebuild with a v2 binary; no partial migration.
- Non-C# workspaces also rebuild because the version is workspace-wide; bytes and
  results are otherwise unchanged.
- Targeted per-file reindex/capability markers are design-only, not implemented.

## Verdict and open conditions

**CONDITIONAL GO for Index v3.**

Promotion conditions:

1. Promote the FTS `query_string` fix (blocker-grade, query-only) regardless of
   the scope decision.
2. Rebuild v3 for scope change before use; do not reuse pre-`894b71e` v3 indexes.
3. Owner must accept dense C# cost (~6.3× disk, ~3× full wall, ~20× user CPU vs
   text fallback) or fund a separate selective/leaf-embedding track.
4. Exact-symbol consumers must use cursors; a 500-item page is not exhaustive.
5. No reference/call-graph claims from `symbols`.
6. Synthetic/mock quality evidence does not qualify a production embedding model.

## Deferred / unknown (do not silently promote)

- **Selective/leaf embeddings / nullable-vector schema**: material dense savings
  possible, but requires index-format/schema change and recall qualification.
- **Scalar optimize deferral**: prototype incremental mixed 1.66→1.39 s (−16%),
  FS outputs −33%, but leaves scalar debt/fragmentation; needs maintenance policy.
- **CLI/MCP/daemon exact-symbol surface**: engine API only today; interface gap.
- **F6 same-ms `updatedTime` snapshot collision**: not reproduced (~900 ms per
  incremental tick); low-risk UNKNOWN. A monotonic timestamp would harden it.
- **Exact-symbol O(candidates) pagination and O(files) coverage**: complete and
  cursor-safe, but not optimized.
- **Parent/child duplicate context** remains for class content plus members; not
  removed because it changes retrieval semantics.
- **Identifier prefix/stemming** (`Property` vs `Property0`) is whole-token BM25
  behavior in both v2/v3; no tokenizer change was made.

## Exact next authorized action

No further work is authorized by this handoff alone. If the owner asks to
proceed, first choose one bounded action:

- **Promote**: prepare a merge/release plan for `894b71e` after owner accepts the
  dense cost and reindex; do not push/merge without explicit request.
- **Efficiency track**: implement and qualify selective/leaf embeddings with a
  new index-format/schema design and a recall gate; keep it separate from v3.
- **Interface track**: add CLI/MCP/daemon `symbols` surface with cursor/coverage
  error signaling and backward-compatibility tests.
- **Snapshot track**: reproduce F6 with a controlled clock, then implement
  monotonic `updatedTime` if required.

Before any action: verify HEAD/worktree, read this handoff and the four docs
above, preserve protected `rust/target`, and use an isolated worktree plus
external `CARGO_TARGET_DIR`.
