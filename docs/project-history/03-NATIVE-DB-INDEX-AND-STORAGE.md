# 03 — Native DB, Index and Storage

## 1. Storage stack

The maintained Rust line uses:

- **native zvec** (`zvec-rust` / native `libzvec_c_api.so`) for persistent
  collections and search;
- **FTS** collections for lexical retrieval;
- **FP16 vector** collections for embeddings;
- a **SQLite graph sidecar** for directed code relationships/pending
  references;
- workspace metadata, manifests and optional per-file reconciliation sidecars
  under `.zvec-grep/`.

The engine does not expose raw zvec as its public contract. `ZvecGrep` wraps
typed workspace/index/search operations. `rust/ARCHITECTURE.md:26-48` describes
the scanner → extraction → model runtime → zvec flow and the workspace layer.

## 2. Persisted workspace shape

Observed layout under a workspace:

```
.zvec-grep/
  manifest.json                 settings, embedding identity, index version,
                                storage generation
  generations/<uuid>/storage/   active native storage generation
  index/                        legacy/older index directory (can remain large)
  locks/
```

`rust/crates/zg-engine/src/workspace/README.md` documents manifest/paths,
recoverable publication and optional reconciliation sidecars. The active
generation is a unit of publication; a new generation is not made visible until
finalization succeeds.

### Current observed index

From the read-only audit at `2026-11-15`:

- active generation `6e3c3a4d-4690-4868-8f9e-1023bbb7577c`;
- index **version 2**;
- embedding **Potion Code 16M v2**, 256d, cosine;
- FTS tokenizer `jieba`, lowercase filter;
- `zg --status`: “Workspace index status is unknown”;
- `zg --verify`: **PASS_COMPLETE**, native ABI 1;
- verify coverage: proxima covered, RocksDB covered, IPC partial, sidecars
  partial, WAL covered; sidecar/IPC content-checksum limitations reported.

The old `.zvec-grep/index/` directory is ~2.4 GB while the active generation
storage is ~216 MB. Do not infer the active index size from `index/`.

## 3. Core records

The engine composes:

| Record/tables | Role |
|---|---|
| file | canonical path, file identity, source snapshot/hash/status |
| directory | directory identities and parent relationships |
| entity | extracted symbol/entity metadata and source range |
| fragment | search projection text, symbol metadata, chunk/source range |
| FTS | lexical index per collection |
| vector | embedding per fragment/chunk, FP16 in the maintained line |
| graph | SQLite edges/pending refs for code relationships |
| idmap | live path ↔ file ID allocation inside a storage generation |
| reconciliation sidecar | optional embedding reuse metadata bound to stored vector |

Fragments are one zvec collection per embedding model, with FTS and FP16 vector
projections (`rust/crates/zg-engine/src/storage/fragments/mod.rs:1-5`).

## 4. Index v2 → v3

Index v3 is a **C# structural-index and FTS/scope** change, not a new storage
engine.

### What v3 changed

- C# structural entities: classes, properties, methods, namespaces/scopes.
- Exact-symbol support and additive `ZvecGrep::symbols` API.
- FTS/vector metadata no longer includes `scope` for C# member entities; this
  prevents every member of a class matching a namespace/class query and
  crowding top-k. `CodeMetadata` still carries scope for output/filtering.
- FTS multi-term query changed from `match_string` (which returned arbitrary
  top-k when no real match) to an explicit quoted `OR` query, capped at 64
  terms, with boolean stopwords filtered and escapes handled.
- Cursor/source-range/coverage work for exact symbols.
- No-vector/schema migration was not added.

### Costs/gates

Index v3 qualification used synthetic mixed/dense/duplicate/quality corpora
with a mock embedding server; it measures relative retrieval behavior, not
semantic model quality. Results:

- quality R@10 0.833; R@50 1.0 FTS/hybrid, 0.983 vector;
- MRR@10 1.0; nDCG@10 1.0;
- mixed `Type0` @50: 12 items/8 same-file → 6 items/2 same-file, 4 files;
- mixed index 15.51 → 15.26 MB apparent (−1.6%);
- dense index 55.78 → 54.42 MB (−2.4%);
- dense index full/peak cost still high; v3 is **CONDITIONAL GO** with FTS/scope
  fixes, not a blanket performance win.

The doc explicitly defers selective/leaf-only embedding and scalar optimize
deferral to separate format/maintenance work
(`rust/docs/index-v3-promotion-qualification.md:106-158`).

### Current compatibility

Source `work/zg-recovered` has `CURRENT_INDEX_VERSION = 3`
(`rust/crates/zg-engine/src/workspace/mod.rs:13`). A version-2 index is not
silently migrated. The installed binary/index remains v2.

## 5. FTS and vector search

### FTS

- lexical, native zvec FTS collection;
- tokenizer/filter per workspace (active: jieba/lowercase);
- query terms and filters;
- explicit OR construction for multi-term queries in v3;
- model-independent when the fragment table is identical (E5/E6).

### Vector

- per-chunk/entity embedding, FP16 in the maintained line;
- cosine metric for the current Potion index;
- dimension/model identity persisted in the manifest/schema;
- active Potion is 256d; Gemma experimental is 768d and a different model;
- vector results are not interchangeable across model or device identity;
- CPU and accelerator indexes have explicit device identity and unsafe
  cross-device writes fail closed.

### HNSW wording

The project uses native zvec/Proxima vector search. A concrete HNSW parameter
choice is not re-invented by this knowledge base; exact native ANN internals are
behind `zvec-rust` 0.8.0. The repository does record HNSW defaults alignment
work in historical commits and the native backend is not replaced by a
hand-written ANN index. Treat “HNSW tuning” as native backend behavior unless a
specific receipt proves otherwise.

## 6. Publication, recovery and crash safety

- Index publication is generation-based; finalization is after successful
  write.
- A lock/ownership model prevents two writers from publishing the same
  generation.
- Crash tests cover staged build failure, reopen, torn/missing sidecars,
  concurrent readers/writers and rollback.
- E2 sidecars are optional cache metadata; a stale sidecar cannot authorize a
  replaced vector. Failure to read a sidecar is a cache miss, not index failure.
- Graph storage uses SQLite WAL (`storage/graph/mod.rs`).
- `--verify` reports coverage for native collections/sidecars and can expose
  incomplete content-checksum coverage.

## 7. Freshness and snapshot semantics

- Workspace manifest tracks generation and updated time.
- Files carry source snapshots: size, modified time, content hash.
- Query results may be served from the current index while background refresh
  runs; freshness/background-refresh are part of result metadata.
- Index rebuilding/publication is explicit; a query does not silently rewrite
  the workspace scan policy.
- `--status` can report “unknown” while `--verify` is complete; freshness is not
  the same as integrity/coverage.

## 8. Locks and known limitations

- Native `optimize()` does not permit concurrent readers/writers; some
  maintenance runs under the state lock.
- No-op fast path avoids unnecessary work but still has scan/lock/validation
  cost.
- E1 deferred-maintenance experiment shows bounded gains but is not merged; it
  needs a maintenance policy.
- Native 0.8 handle-based lifecycle limits were documented as a blocker for
  fully outside-lock scalar maintenance.
- Sidecar/IPC checksum coverage is partial; verify says so.
- Index v3 dense C# cost is substantial (disk/wall/user CPU); conditional
  promotion, not universal win.

## 9. Confirmed vs proposed

**Confirmed:**

- zvec 0.8 native backend, FTS/FP16 vectors, generation publication, manifest,
  SQLite graph, source hash/range, recovery tests, versioned index.
- v3 has C# structural metadata and FTS/scope fixes in source.

**Proposed/deferred:**

- selective/leaf-only embeddings;
- nullable-vector schema;
- scalar optimize deferral;
- public exact-symbol CLI/MCP surface;
- same-millisecond snapshot token hardening (not reproduced);
- native lock/handle redesign.

Do not present deferred design as implemented.

## 10. Evidence anchors

- `rust/ARCHITECTURE.md:26-48` — retrieval/indexing/storage.
- `rust/crates/zg-engine/src/workspace/README.md:1-50` — workspace layout,
  generation, reconciliation.
- `rust/crates/zg-engine/src/storage/mod.rs:1-16` — storage modules.
- `rust/crates/zg-engine/src/storage/fragments/mod.rs:1-5` — FTS/FP16.
- `rust/crates/zg-engine/src/workspace/mod.rs:13` — index version 3.
- `rust/docs/index-v3-promotion-qualification.md:1-160`.
- `rust/docs/index-v3-deep-audit.md` — F6/F9 and limits.
- `rust/docs/e2-integration-20261112.md:20-75` — sidecar/identity/crash.
- `rust/docs/e8p2-integration-20261115.md:13-40` — v3/v3 guards.
- Runtime: `zg --verify` read-only, 2026-11-15.
