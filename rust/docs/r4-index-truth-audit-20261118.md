# Index truth audit — source-to-retrieval verification

Branch `work/r4-index-audit-20261118` (from `d934f92`). Read-only audit of the
**isolated R4 index** at
`/home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus/.zvec-grep`
(manifest: **index v3, storage generation `8d17678e-67ae-4e07-9d92-a27051f9c854`,
embedding local/potion-code-16m-v2, dimension 256, CPU**). The production v2
index was not touched and is a different environment.

Method: an independent extractor (regex + brace matching, deliberately NOT the
engine's R2/tree-sitter parser) re-derived 4,611 declarations from the 822-file
frozen corpus and read them back through the **real API routes** (exact `symbols`
lookup, FTS, vector). Gold was used only as a diagnostic input; a gold-free
sample (every 8th file, 102 declarations) ran the same checks. The audit itself
found and fixed three probe artifacts (class-name extraction picking inheritance
words, SQL bracket handling, gold spans starting at attribute lines one row above
the method) — receipt iterations kept in git history.

## Gate 1 — source → index integrity

- 822 files scanned (816 `.cs`, 2 `.sql` in scope, plus config/docs); all 25
  gold-span files present in the index manifest.
- C# declarations: persisted ranges match the independent brace-matched
  extraction (e.g. `CoreDBContext` 9–256, `CoreService` 17–223, method-level
  declarations all matched on the exact-name lookup).
- No stale records, wrong offsets, duplicate fragments or silent omissions were
  found for C# in either the gold or the sample audit.

## Gate 2 — read-back through real routes

| Route | Verdict |
|---|---|
| Exact symbol lookup (`symbols(name)`) | C# classes and methods: correct file, range and scope; returns ≤20 hits, so common class names need pagination, not a defect |
| FTS over an in-body identifier | reaches the gold span's file and range for every C# case checked |
| Vector (declaration name as query) | hits in most cases; weaker for property-only declarations — relevance, not truth |
| **SQL symbols** | **`sp_UpdateOrgTotalEmployee` → 0 hits; `EmployeeBaseViews` → no covering range. Cause: `zg-engine/Cargo.toml` registers tree-sitter grammars for C/C++/C++/Go/Java/JS/Python only — there is no SQL grammar, so SQL declarations are never extracted as symbols.** SQL content IS FTS/vector indexed (D09's view was retrieved and selected by the lexical route in R4 runs) |
| R2/R3 facts | **not persisted** — the workflow parses source files at expansion time (`csharp_facts::parse_file`, `sql_facts::parse_sql_file`); the index contributes symbol/FTS/vector metadata only |

## Gate 3 — 25-span ledger (diagnostic, offline gold)

For every gold span: SCANNED ✓ → EXTRACTED ✓ (independent extractor) →
PERSISTED ✓ → SEARCHABLE ✓ — **for all 20 C# spans**, including every span that
R4 currently misses (the never-pooled CoreDBContext, Employee, EmployeeDetail
declarations are all in the index, symbol-lookupable with correct ranges:
`CoreDBContext` → 2 hits, range 9–256 covering both D02 and D10 gold).

The 3 SQL spans (D07 58–95, D09 1–10, D12 1–95) are persisted as
**FTS/vector content but not as symbols** (design limitation above). D09 was
retrieved and selected via lexical; D07 pooled via lexical; D12 was never pooled
because no lexical call remained — a budget consequence of a route that *can*
find it.

**Conclusion for R4 attribution:** the current R4 misses are **not** index
defects. Never-pooled C# spans are fully retrievable through the exact-symbol
route the workflow already has; SQL spans are reachable through FTS/lexical.
The failures measured earlier (wrong-sub-unit, unexamined, never-pooled) originate
in call allocation, ordering and semantic ranking — confirmed against a
verified index, not assumed.

## Gate 4 — independent verification

- Manifest identity recorded above; corpus hash set frozen (822 files verified
  against the manifest in the R4 mission).
- Independent sample audit (gold-free): 97/102 OK; 4 SQL-name lookups not
  persisted (same design limitation), 1 FTS miss from probe-noise tokens — no C#
  failure found outside the dev cases.
- Two probe defects were found in the audit instrumentation itself and fixed
  before drawing conclusions (documented above); no benchmark or engine
  measurement was changed.

## Verdict

**INDEX_CORRECT for C# (verified independently, including method-level
granularity), with one documented DESIGN LIMITATION: no SQL symbol extraction
(no tree-sitter SQL grammar) — SQL files are FTS/vector-indexed but exact symbol
lookup over procedures/views returns nothing.** This is a capability gap, not
corruption. It directly affects R4's typed acquisition: the typed symbol route
can never reach SQL procedures; SQL evidence must be budgeted through
lexical/FTS.

## Root-cause map (index layer)

| Failure class | Count | Evidence |
|---|---:|---|
| NOT_SCANNED / NOT_EXTRACTED / NOT_PERSISTED (C#) | 0 | gold + sample audits |
| NOT_PERSISTED (SQL symbols) | 3 gold + sample | no SQL grammar in the extractor |
| NOT_SEARCHABLE (C#) | 0 | FTS/vector probes |
| R4's remaining misses | — | all originate above the index (call budget, ordering, semantic sub-unit choice) |

## Next step (recommendation, not started)

1. Add a **tree-sitter SQL grammar** (or a lightweight CREATE PROCEDURE/VIEW
   extractor) so procedures/views become symbol-lookupable — small, local,
   schema-compatible (new symbol rows only), and it unlocks the typed route for
   SQL gold. Owner approval for an index-format-affecting dependency change is
   required.
2. Independent of that, R4's allocation problem stands as measured: within the
   ≤6-call budget, symbol acquisition of CoreDBContext/Employee/EmployeeDetail
   (all verified present) is a call-allocation choice, not an index fix.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4_index_truth -- $W/corpus
# receipt: benchmarks/rag-impact/results/r4-index-truth-20261118.json
```
