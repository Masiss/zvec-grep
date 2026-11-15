# Index v3 hardening — pre-registration

Continuation checkpoint: `index-v3-handoff-20261109.md`.

Date: 2026-11-09. Branch `work/index-v3-hardening-20261109`, base `986ab51`.
This document freezes the audit findings, the bounded change set and the gates
before implementation. RAG, Selection, Expansion, held-out data and historical
receipts are out of scope. No index format change is proposed.

## 1. Audit evidence (measured, not inferred)

### 1.1 Disk attribution (525-file mixed corpus, 1024-d embedding)

Read-only harness `examples/index_forensics.rs`; raw results in
`~/.cache/zvec-grep/out-csharp/audit/forensics-{baseline,after}.json`.

| Item | v2 baseline | v3 | Delta |
|---|---:|---:|---:|
| Allocated index bytes | 9,244,672 | 20,983,808 | +11,739,136 |
| `entities` collection | 831,488 | 2,813,952 | +1,982,464 |
| `fragments_<model>` | 5,332,992 | 15,089,664 | +9,756,672 |
| `files` / `directories` / reconciliation | unchanged | unchanged | 0 |
| Entity docs | 750 | 3,151 | +2,401 |
| Fragment/vector docs | 750 | 3,151 | +2,401 |
| Embedding payload (1024 f16) | 1,536,000 | 6,453,248 | +4,917,248 |
| FTS text bytes | 166,238 | 413,916 | +247,678 |
| Entity payload bytes | 405,481 | 1,313,677 | +908,196 |
| Entity metadata JSON bytes | 67,098 | 565,062 | +497,964 |
| Fragment scalar bytes | ~80 KB | ~443 KB | +363 KB |
| Entity content / source bytes | 1.04× | 1.84× | duplication |
| FTS text / source bytes | 1.12× | 2.80× | duplication |

Attribution: 42% of the growth is one embedding per declaration; 13% entity
payload/metadata; 3% fragment scalars; the rest is RocksDB/scalar-index overhead
from 4.2× more fragment rows. C# contributes 2,701 of 3,151 docs. Parent class
content and child declarations overlap in both entity payload and FTS, as they
already do for Rust/Java/TypeScript.

### 1.2 C# coverage regression (proven)

Structural extraction starts only at declarations, so content outside any
declaration is no longer indexed when a file also has declarations. A file with
only `using` directives still falls back to text and is searchable.

Audit test: `Program.cs` = using + comment + top-level statement + class;
`OnlyUsings.cs` = using + comment only. After v3:
- `toplevelalpha` (top-level statement) → 0 hits;
- `jsonbeta` (using directive in Program.cs) → 0 hits;
- `commentgamma` (comment in Program.cs) → 0 hits;
- `usingsdelta` (OnlyUsings fallback) → 1 hit.

v2 text fallback indexed all four. This is a real recall loss, not a storage
optimization. Fix must add searchable gap entities for C# only, without touching
other adapters.

### 1.3 Exact-symbol API gaps

- No continuation: `total > 500` cannot be enumerated beyond the cap.
- No cursor validation: callers cannot distinguish "empty page" from "index
  changed between pages".
- Range resolution fetches the canonical payload for **every** exact-name match,
  even though only the returned page needs ranges. `Add` with 600 declarations
  costs ~127 ms p50 / 144 ms p95 warm and ~4.19 s user CPU over 31 queries.
- Ordering currently depends on source offsets read from payloads; it is
  deterministic but forces full-candidate payload reads.

### 1.4 Version/migration

v2→v3 is an extraction-capability change, not a storage-schema change. The
manifest version gate already fails closed for info/context/index/watch and now
symbols. v3 is not released; any v3 index is experimental, so hardening v3
semantics does not require v4. Rollback to a v2 binary requires rebuild.

### 1.5 Other adapters

Rust/Python/TypeScript already use declaration entities only; their pre-existing
behaviour is unchanged. The gap fix is C#-scoped because C# is the new adapter
and the regression is measured.

## 2. Bounded change set

### A. C# coverage entities (recall restoration)

After structured C# extraction, compute the union of declaration byte ranges
(using the whole declaration node for fields/namespaces), then emit a text
entity for each non-whitespace gap outside that union. Gaps are `Content::Code`
with `metadata: None`, chunked with the existing text fragmenter. No other
adapter changes. No new index field.

Gate: all four audit queries above must be retrievable; union coverage of
non-whitespace source bytes must be ≥99% for the C# fixture; index growth on the
mixed corpus ≤5%; non-C# query parity unchanged.

### B. Exact-symbol continuation + snapshot safety

- Add `cursor: Option<String>` to `SymbolsOptions` and
  `next_cursor: Option<String>` to `SymbolsResult`.
- Cursor is self-describing: format version, workspace storage generation,
  query hash (name/scope/type/path), and last ordering key. Encoded with a
  checksum; malformed, cross-query and stale-generation cursors are rejected
  with `INVALID_ARGUMENT`, never silently restarted.
- Ordering becomes fully metadata-derived and deterministic:
  `(relative_path, scope, symbol_type, signature, entity_id)`. Range payload
  reads happen only for the requested page.
- `total` remains all matches; `next_cursor` is present iff more matches exist;
  `truncated` equals `next_cursor.is_some()`.
- Single call is snapshot-safe: workspace read lock is held from manifest
  generation through storage read. Across pages, generation must match.

Gate: 600 declarations, limit 250 → pages 250/250/100; concatenation equals the
full unpaginated set exactly; no duplicates/gaps; stale cursor after refresh is
rejected; cursor from another name/scope is rejected; forged/malformed cursor is
rejected.

### C. Completeness signal

`SymbolsResult.coverage` reports workspace files indexed, unfinished and failed,
so a failed file cannot make an exact-symbol answer look exhaustive. Read from
the same store snapshot as the symbol query.

Gate: a workspace with one failed file reports `complete=false`; a clean
workspace reports `complete=true`.

## 3. Non-goals

- No storage dedup of parent/child bodies; attribution exists, but any change
  alters retrieval semantics and needs a separate quality qualification.
- No embedding-dimension, chunk-size, FTS or ranking change.
- No schema field, no v4, no second index, no graph/LSP/model.
- No CLI/MCP surface change in this hardening slice.

## 4. Measurement protocol

Same self-contained harness, release build, mock embedding server, one process
per phase. Corpora: existing 525-file mixed corpus, a high-symbol-density corpus
(300 C# files × ~40 declarations), and a duplicate-name corpus (500 files with
the same type/method names). Three runs per condition; report median and range.
v2 baseline uses the 9444eed worktree; v3 uses this branch. Metrics: full and
incremental wall/user CPU/peak RSS/PSS/I/O, index bytes (apparent + allocated),
collection/field breakdown, cold/warm FTS/vector/hybrid/exact-symbol latency,
hit@k parity on deterministic token queries, declaration completeness, stale
detection and migration/rebuild cost.

## 5. Verdict rule

GO only if A+B+C gates pass, no new regression in engine suites, and exact-symbol
correctness is unchanged or stronger. If C# recall cannot be restored without a
format/retrieval change, the result is NO-GO for promotion, not a silent
trade-off.
