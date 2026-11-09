# Index v3 deep audit — independent findings (2026-11-09)

Base `e9a9824`; this audit was executed in the same isolated worktree with
external `CARGO_TARGET_DIR`. It does not reuse the previous report as authority;
each finding below has a reproduction and a disposition.

Receipts: `~/.cache/zvec-grep/out-csharp/index-v3/receipts/`.
New harness: `examples/audit_query.rs` (flexible FTS probe).

## Findings

### F1. Block-namespace coverage hid source and produced false completeness (FIXED)

`namespace N { ... }` is parsed with the whole block as its declaration range.
The resolved namespace entity carries only its name, but gap coverage used the
whole block, so comments/usings inside the block were neither in the namespace
entity nor in gap entities. A block-namespace fixture with
`using ... // marker` and `// marker` inside the block returned neither marker.
This was introduced by the gap-coverage hardening and is a real recall defect.

Fix: block namespace coverage now ends at the body start; gap coverage indexes
the remaining non-whitespace bytes. Punctuation-only gaps (`{}`, `;`, `,`) are
skipped. Regression: `tests/csharp_coverage.rs` block-namespace fixture plus
`extraction::code::csharp_adversarial...`.

### F2. Ordinary `//` comments became C# documentation (FIXED)

`extract_preceding_doc` accepted any preceding comment, so a plain `//` above a
C# declaration became `CodeMetadata.documentation` and was prepended to FTS and
vector text. Reproduction: class `BlockThing` matched a query containing only
the ordinary comment marker (`qzxqwerty`). This inflated false positives and
made comment text appear twice (gap entity + declaration documentation).

Fix: C# uses only `///` and `/** */` preceding comments for documentation;
ordinary comments remain searchable through gap entities. XML docs such as
`/// <summary>` still populate documentation.

### F3. Dense benchmark queried a token absent from the corpus (MEASUREMENT FIXED)

The previous dense `Property0` miss was not a production defect. The benchmark
`query_cases` had no `dense` arm and fell through to `Type0`, which does not exist
in the dense corpus; FTS/vector/hybrid therefore reported zero. Direct probes:

- `v3_audit_query --query Property0 --limit 10`: 10 items / 10 files;
- `v3_audit_query --query Dense0`: 10 items / 10 files;
- v2 and v3 both return 10; parity holds.

`Property` (without suffix) returns 0 in both versions because the tokenizer
matches whole identifier tokens, not prefixes. That is a tokenizer limitation,
not a `Property0` regression. The benchmark harness now uses `Dense0` for the
dense corpus and records the expected path.

### F4. Exact-symbol cursor length was unbounded (FIXED)

A syntactically invalid but huge cursor was parsed before rejection. Cursors are
now capped at 16 KiB with `INVALID_ARGUMENT`; regression added to the pagination
test. This is a bounded-input safety fix, not a format change.

### F5. Scope-prefix member crowding (QUALITY REGRESSION, DEFERRED)

`lexical_text` and vector metadata prepend `scope` to every declaration. Query
`Type0` on the mixed corpus returns 8–12 matches from the same file because every
member has `scope: Bench.N0::Type0`; v2 text fallback returned 1 chunk from that
file (5 items total, 4 files).

Prototype (not committed): omit `scope` from C# FTS and vector metadata only.
- mixed `Type0` @50: 12 items / 8 same-file → 6 items / 2 same-file, still 4
  distinct files; expected path hit remains true;
- dense and duplicate results unchanged;
- index bytes mixed 15.51 → 15.26 MB (−1.6%), dense 55.78 → 54.42 MB (−2.4%);
- vector/hybrid distinct-file counts unchanged in the measured corpora.

Disposition: **DEFERRED**. It changes retrieval text and scope-search semantics
for C#. It needs a dedicated retrieval-quality gate and owner approval; it is not
part of the promoted v3 change. The prototype files were reverted.

### F6. Same-millisecond `updatedTime` snapshot collision (HYPOTHESIS, NOT REPRODUCED)

Exact-symbol cursors bind to `storageGeneration` + manifest `updatedTime` (ms).
Incremental writes keep the generation; two writes in the same millisecond would
share a snapshot token. A 20-tick in-process incremental loop took ~900 ms per
tick (manifest `updatedTime` strictly increased every tick), so no natural
collision was reproduced. A monotonic `updatedTime` hardening is possible but
was not applied without a reproduction. Classified **UNKNOWN/low-risk
limitation**, not a confirmed defect.

### F7. Parent/container cost was overstated in the earlier note (CORRECTION)

Container entities (classes/namespaces) duplicate member content, but their
vectors are not the dominant cost:
- dense: 301 class vectors ≈ 0.62 MB of 57.45 MB (~1.1%);
- mixed: 751 class vectors ≈ 1.54 MB of 18.04 MB (~8.5%);
- duplicate: 501 class vectors ≈ 1.03 MB of 13.01 MB (~7.9%).
Dropping container embeddings could save ~1–8.5%, not the earlier 10–30%
hypothesis. The dominant cost remains one vector per declaration (25.8 MB of
57.45 MB dense).

### F8. Write amplification and reconciliation overhead (ACCEPTED PRE-EXISTING COST)

`/usr/bin/time` file-system outputs (512-byte blocks): mixed full v2 269,280 →
v3 299,920 (+11%); dense full v2 187,696 → v3 436,728 (+133%); one-file
incremental mixed v2 116,104 → v3 133,080. These are writes by the native
storage/optimize path, not just user content; one-file incremental still rewrites
tens of MB. Reconciliation sidecars are ~4 KiB per file (525 files ≈ 2.15 MB),
pre-existing and unchanged by v3. Reported as a storage/lifecycle cost; no
format change was attempted.

### F9. Interfaces (LIMITATION)

`ZvecGrep::symbols` is public and tested, but CLI, MCP and daemon protocol expose
only context/search with `preferSymbol`/symbol filters, not exact-symbol
enumeration or cursors. Backward compatibility is intact (additive types and
workspace version gate), but agent-facing callers cannot use the exact-symbol
API yet. This is an interface gap, not a correctness failure.

### F10. Coverage granularity (LIMITATION)

`SymbolsResult.coverage` is workspace-wide. A failed or unfinished non-symbol
file makes `complete=false` for a symbol query, which is conservative but noisy.
No false-positive completeness was found; failed files are not silently ignored.
The coverage check also lists all files on every page, so paging cost is O(files).

## Cross-layer checks performed

- Extraction: adversarial C# records/structs, attributes, generics, explicit
  interfaces, operators, indexers, Unicode identifiers, CRLF; source-backed
  ranges and signatures.
- Exact symbols: 500-declaration pagination, stale/cross-query/malformed/oversize
  cursors, failed-file coverage, restart, incremental refresh.
- Retrieval: FTS/vector/hybrid and `preferSymbol` expected-path parity on mixed,
  dense, duplicate and non-C# corpora; top-50 diversity inspected (F5).
- Lifecycle: rename/delete/refresh/restart/crash suites; read-session cache
  retirement verified from source and existing concurrency tests.
- Versioning: v2→v3 fail-closed, rebuild cost, non-C# rebuild cost; rollback is
  rebuild-only.
- Storage: allocated/apparent bytes, per-type/language bytes, vector/text/
  metadata attribution, write amplification, reconciliation overhead.

## Dispositions

| Finding | Disposition |
|---|---|
| F1 block namespace coverage | FIXED |
| F2 ordinary comments as docs | FIXED |
| F3 dense benchmark query | FIXED (measurement) |
| F4 cursor length | FIXED |
| F5 scope-prefix crowding | DEFERRED (retrieval design) |
| F6 same-ms token collision | UNKNOWN / low-risk; not reproduced |
| F7 container cost claim | CORRECTED (1–8.5%) |
| F8 write amplification | ACCEPTED TRADE-OFF (pre-existing) |
| F9 CLI/MCP symbols surface | DEFERRED |
| F10 coverage granularity | ACCEPTED LIMITATION |

## Verdict

**CONDITIONAL GO for Index v3.** F1/F2/F4 are merged on the hardening branch;
F5/F6/F9/F10 are promotion conditions or separate tracks. The v3 storage,
CPU and dense-disk costs remain as recorded; no storage dedup or retrieval
semantics change was promoted.
