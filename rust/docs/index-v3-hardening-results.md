# Index v3 hardening — audit and qualification results

Date: 2026-11-09. Branch `work/index-v3-hardening-20261109`, base `986ab51`.
Pre-registration: `index-v3-hardening-prereg.md`. Independent deep audit:
`index-v3-deep-audit.md`; promotion gates and final FTS/scope numbers:
`index-v3-promotion-qualification.md` (block-namespace gap, C# docs, cursor bound, dense
benchmark correction, deferred scope experiment).
Scope: C# structural indexing, exact-symbol API, v3 promotion evidence. RAG,
Selection, Expansion, held-out data and historical receipts are untouched.

Receipts: `~/.cache/zvec-grep/out-csharp/index-v3/receipts/`
(`*-full.json`, `*-incremental.json`, `*-query*.json`, `*-symbol*.json`,
`forensics-*.json`, `migrate2-*.json`, `*.time`).
Harnesses: `examples/index_bench.rs`, `examples/symbol_bench.rs`,
`examples/query_check.rs`, `examples/index_forensics.rs`.

## 1. Pipeline and data placement

`source` → `extraction` (`CodeMetadata` + content) → canonical `entities`
(payload content + metadata JSON, one record per entity) → `fragments`
(one row per entity fragment: metadata scalars, FTS text, f16 embedding) →
native FTS/scalar/vector indexes → `files`/`directories` → optional SQLite
graph. Exact symbol lookup reads `entities.metadata` + `entities.payload`
(range only for the returned page); context reads FTS/vector candidates and
then canonical entities.

Three duplication sources exist and are pre-existing except for the new C#
volume:

1. A class entity contains all member source, and each member is also an
   entity. Parent and child content overlap in `payload`, FTS text and
   embeddings.
2. FTS text prepends symbol name/scope/signature/documentation to the content.
3. Every fragment gets its own 1024-d f16 vector (2 KiB).

Gap entities (new) cover only source bytes outside every declaration, so they
add no overlap with declarations.

## 2. Disk attribution

Read-only `index_forensics` against r1 roots. Allocated = `du` block bytes;
collection values are allocated block totals.

| Corpus | Total v2 | Total v3 | Entities v2/v3 | Fragments v2/v3 |
|---|---:|---:|---:|---:|
| mixed (525 files) | 8.93 MB | 18.04 MB | 0.70 / 2.64 MB | 5.14 / 12.32 MB |
| dense (300 × ~40 decls) | 8.56 MB | 57.45 MB | 0.92 / 10.22 MB | 5.64 / 45.22 MB |
| duplicate (500 × 3 decls) | 8.21 MB | 13.01 MB | 0.52 / 1.74 MB | 4.73 / 8.30 MB |

Mixed v3 composition: 6.15 MB vector payload, 0.36 MB FTS text, 1.21 MB entity
payload, 0.54 MB metadata JSON, 2.15 MB reconciliation sidecars, remainder
RocksDB/scalar indexes and fixed tables. Dense v3: 25.81 MB vectors, 1.69 MB FTS
text, 5.14 MB payload, 2.43 MB metadata, 1.23 MB reconciliation.

Attribution of the mixed growth (+9.11 MB): **+4.91 MB embeddings** (one per new
declaration), +0.91 MB entity payload, +0.50 MB metadata JSON, +0.36 MB fragment
scalars, +0.25 MB FTS text, remainder index overhead from 5× more rows.
Per entity: C# content 77 B, metadata 184 B, payload ~400 B, one 2 KiB vector.
Per declaration the dominant cost is the vector, not source text.

No storage dedup was implemented. Removing parent embeddings or parent content
would change retrieval semantics and needs a separate quality qualification.

## 3. Indexing resources (median of 3, one process per phase)

| Corpus | v2 full | v3 full | v2 incr. | v3 incr. | v2 peak RSS | v3 peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| mixed | 2.45 s | 3.07 s | 1.35 s | 1.46 s | 315 MB | 347 MB |
| dense | 2.09 s | 6.18 s | 1.36 s | 1.88 s | 314 MB | 470 MB |
| duplicate | 2.27 s | 2.76 s | 1.28 s | 1.40 s | 311 MB | 328 MB |
| non-C# | 1.72 s | 1.87 s | 1.39 s | 1.29 s | 311 MB | 311 MB |
| top-level gap (300 files) | 2.37 s | 2.06 s | n/a | n/a | 310 MB | 315 MB |

User CPU: mixed 1.33 → 3.41 s; dense 1.1 → 21.4 s; duplicate 1.1 → 2.2 s.
The extra CPU is parsing plus one embedding/vector write per declaration.
Incremental cost is proportional to the changed file only. Parser lifecycle is
one tree-sitter `Parser` per file in `extract_code`; no parser pool.

## 4. Retrieval parity

`query_check` on r1 roots, FTS and FTS+`prefer_symbol`; expected-path hit@10:

| Corpus | v2 | v3 |
|---|---|---|
| mixed `Type0` | hit, 5 items | hit, 10 items |
| dense `Dense0` | hit, 10 | hit, 10 (previous `Property0`/`Type0` dense receipt was invalid; see deep audit F3) |
| duplicate `Widget` | hit, 10 | hit, 10 |

All modes returned distinct entity IDs; no duplicate entity rows. v3 returns
more declaration-level items for the same file because parent and child entities
are separate (mixed `Type0`: 10 distinct entities, 4 files). Cold/warm context
latency stayed in the same 120–170 ms band; no measured retrieval regression.
Non-C# corpus index bytes, entity counts and query results are identical
(5.479 MB both versions), so the disk/CPU delta comes from C# declarations only.

## 5. Exact-symbol hardening

- Cursor pagination: 500 duplicate `Widget` declarations, limit 50 → 10 pages,
  500 unique IDs, no gaps/duplicates; concatenation equals the 500-limit run.
- Cursor is bound to query hash + workspace generation + manifest
  `updatedTime`. Stale cursor after incremental refresh is rejected; wrong-name
  and malformed cursors are rejected.
- Ordering is metadata-only: `(path, scope, symbol_type, signature, entity_id)`.
  Only the returned page fetches canonical payload ranges.
- `coverage` reports indexed/unfinished/failed workspace files; an injected
  failed file makes `complete=false`.
- Latency on the same 500-declaration index: limit 500 warm p50 153–165 ms
  (pre-hardening 217–225 ms, −30%); limit 50 warm p50 167–189 ms vs
  145–160 ms pre-hardening (noise/first-page file scan; no correctness change).
  Cold 430–520 ms. Ten pages total 1.49–1.92 s.
- Residual: each page still enumerates all exact-name candidates and lists all
  files for coverage, so paging a name with very high cardinality is O(n) per
  page. No silent truncation; `total` is always the full count.

## 6. C# recall restoration

Before hardening, a file with declarations plus `using`, comments or top-level
statements indexed only declarations. After gap coverage, all four audit
markers are retrievable from `Program.cs`/`OnlyUsings.cs`, while structured
`Marker`/`Inside` symbols remain exact. The mixed/dense/duplicate corpora have no
non-declaration gaps, so their index size is unchanged by this fix. A dedicated
300-file top-level corpus measures the gap cost in isolation: pre-hardening v3
5.839 MB → final v3 6.214 MB (+6.4%) and 1.95 s → 2.06 s (+5.6%) while v2 text
fallback was 5.455 MB. The pre-registered 5% gate was stated for the mixed
corpus (0% gap, passed); the gap-dense deviation is recall-restoring content,
not duplicated declaration bodies.

## 7. Migration and versioning

v2→v3 is an extraction-capability change, not a storage schema change. The
manifest gate fails closed for info/context/symbols/index/watch. v3→v2 rollback
also requires a rebuild. Measured v2→v3 rebuild on the mixed corpus: median
3.03 s, 15.51 MB index, 346 MB peak RSS — same as a fresh v3 build.

Non-C# repositories get no content benefit but still rebuild because the version
is workspace-wide. A future per-file extraction capability marker could avoid
that; it would be a manifest/schema change and is not implemented.

v3 was never released. This hardening branch redefines v3 to include gap
coverage. Any v3 index built from the earlier experimental commit must be
rebuilt; there is no automatic detection between the two v3 definitions.

## 8. Facts vs hypotheses / open issues

Measured facts: disk attribution, indexing resources, retrieval expected-path
parity, cursor completeness and rejection, coverage signal, gap recall fix,
migration cost and non-C# parity.

Hypotheses not proven: (a) parent/child embeddings may raise near-duplicate
ranked results on real repositories; (b) dropping container embeddings could save
~1–8.5% of v3 disk (corrected attribution in the deep audit; dense class vectors
are ~1.1%, mixed ~8.5%), not the earlier 10–30% estimate; (c) per-file extraction capability
markers would make non-C# migration free. None was implemented without a
quality/reindex design.

Open issues: exact-symbol page cost at very high name cardinality; one full-suite
run showed four timing-sensitive storage optimize tests failing under load, all
passed on isolated and full reruns (baseline flakiness, not reproduced as a new
regression). The previous `Property0` FTS=0 claim was a benchmark-query defect,
not a tokenizer regression: direct probes return 10/10 files in v2 and v3; only
the unsuffixed `Property` query returns 0 in both (whole-token BM25). See
`index-v3-deep-audit.md` F3.

## 8b. Verification

- `cargo test -p zg-engine --tests`: lib 745 passed / 24 ignored; integration
  binaries 8+1+4+17+8+1+2+2+3+2 passed, 0 failed. `cargo fmt --check` passes.
- Downstream `zg-cli`, `zg-daemon-protocol`, `zg-transport-mcp`: 180+5+7+60
  tests passed; workspace `cargo check --all-targets` and release `zg` build
  pass.
- Clippy reports no warnings in the new production/example files. The workspace
  `-D warnings` run still fails on pre-existing `extraction/candidate.rs`
  lints (also present at the base commit), not on this change.
- One full-suite run produced four timing-sensitive storage optimize test
  failures under load; the same tests passed individually and in the next two
  full runs. Treated as baseline flakiness, not attributed to this change.
- Deep-audit fixes: block-namespace gap coverage, C# ordinary-comment doc
  filtering, punctuation-only gap suppression, cursor size cap; adversarial
  Unicode/CRLF/generic/operator/indexer extraction test and block-namespace
  coverage test pass.

## 8c. Post-hardening promotion changes

The deep audit's F11 (multi-term FTS returned arbitrary documents) and F5
(C# scope-prefix crowding) were fixed after this table was measured. Final
allocated disk is mixed 17.37 MB, dense 54.15 MB, duplicate 12.56 MB; quality
topic retrieval is R@10 0.833 (maximum for 12 relevant files), R@50 1.0
(FTS/hybrid), MRR/nDCG 1.0. No-answer queries now return empty instead of
arbitrary top-k. See `index-v3-promotion-qualification.md`.

## 9. Verdict

**CONDITIONAL GO for Index v3.**

Promotion conditions:

1. Treat the final hardening commit as the first v3 definition; rebuild any
   pre-hardening v3 experiment index (no mixed-version detection exists).
2. Accept the measured cost: mixed disk ~2×, dense disk ~6.7×, dense full-index
   user CPU ~20× versus text fallback, driven by one embedding per declaration.
3. Keep exact-symbol consumers on cursor paging; do not assume a 500-item result
   is exhaustive.
4. Do not claim reference/graph coverage from `symbols`; it returns declarations
   only.
5. Any future dedup of parent/child embeddings is a separate retrieval-quality
   change, not an Index v3 patch.
