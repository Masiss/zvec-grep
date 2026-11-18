# SQL symbol index audit & R4 acquisition impact

Branch `work/r4-sql-symbols-20261118` (from `3ab7b90`). Isolated corpus copy +
index rebuild only; production untouched. 45 extraction tests + 25 workflow
tests pass; clippy clean.

## Gate 1 — audit reconciliation (errata to the previous audit)

The prior report's per-span counts included probe artifacts that are now fixed
and reconciled:

| Item | Reported before | Reconciled |
|---|---|---|
| Gold spans | "20 C# OK + 3 SQL" | **22 C# persisted (all verified, 1 with a ranking-limited probe FTS check), 3 SQL not persisted** = 25 |
| Sample | "97/102 OK + 4 SQL failures" | **100/102: 1 SQL design gap + 1 ranking-limited probe check; the other 3 "failures" were probe naming artifacts (inheritance tails: Feature, Holiday, EmployeeOrganizationView)** |

The reconciliation found **two more probe defects** (class-name extraction
searched a space-stripped string; SQL names were lowercased by the probe) and
fixed them; the engine itself had no C# defect in either audit. The previous
report's headline conclusion (C# INDEX_CORRECT, SQL design gap) stands.

## Gate 2 — SQL symbol extraction (prototype, no schema change)

Root cause: `resolve_adapter` covers tree-sitter grammars C/C++/C++/Go/Java/JS/
Python/Rust only; `.sql` fell to plain-text fallback, so no `symbol_name`
metadata was ever persisted.

Choice made (with trade-off reasoning):
- **Not** tree-sitter-sql: a new external dependency, grammar quality varies,
  and the mission's stop rule requires an owner decision for dependency-level
  changes.
- **Chosen**: reuse the R3 `sql_facts` parser (already PASS-gated in the R2/R3
  mission) inside `extract_code`: a `FileFormat::Sql` branch emits one
  `Function`-type entity per CREATE PROCEDURE/VIEW/FUNCTION with the exact
  declaration name, conservative R3 line range, and the whole-file text
  fallback **unchanged** so FTS/vector coverage cannot regress. `language` is
  left `None` because the persisted `Language` enum has no SQL variant — no
  index-schema change, no migration, `_old` variants stay out per R3 rules, and
  a repository definition is never treated as evidence of a live deployment.

Regression tests: definition entities with names/ranges/metadata, fallback
preservation, `_old` variant non-persistence; plus negative coverage via the
existing R3 fact tests.

## Gate 3 — isolated index verification

New isolated corpus copy at
`/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`, rebuilt with the
branch's engine (`--embedding local/potion-code-16m-v2 --device cpu`):
31 s wall / 57 s CPU / 822 files / 15,041 entities; index size 82 MB — identical
to the old index size (2 SQL files, negligible growth).

- `symbols('sp_UpdateOrgTotalEmployee')` → 1 hit, range 1–102, signature
  `CREATE PROCEDURE [dbo].[sp_UpdateOrgTotalEmployee]` ✓
- `symbols('EmployeeBaseViews')` → range 1–9, overlapping the gold span ✓
- **All 25/25 gold spans now persisted** (authoritative-name read-back in the
  receipt); all previously passing C# checks unchanged; C# parity verified
  cross-index (`EmployeeEditThongTinCaNhan` → same file, range 215–459, same
  score 2.1700).
- Independent sample: 100/102 OK; residuals are 1 probe-lowercase artifact and
  2 ranking-limited probe FTS checks (Glone, DeleteOrgChart — persisted ✓).

## Gate 4 — R4 impact (paired, same contract)

| Arm | Old index | New index (SQL symbols) |
|---|---:|---:|
| hybrid raw (reference) | 0.4167 | **0.5000** |
| hybrid snippet ≤24 KiB (baseline) | 0.1667 | **0.2500** |
| R4b no plan | 0.3194 | 0.3194 |
| **R4 plan arm (best)** | **0.4861** | **0.4861** |

Citation 1.000, budgets within contract, single run per arm.

**Honest result: SQL symbols improved the deployable hybrid baseline
(+8.3 pp bounded, +8.3 pp raw) and did NOT change either R4 arm.** The SQL
definition entities rank well in the hybrid question route, but the R4
workflow's ≤6-call budget still spends its single typed call on a C# name, and
its lexical line-units pool the SQL file without selecting the definition
entity (D07 pooled-not-selected; D12 not pooled).

## Verdict

**PARTIAL.**
- Gate 1: reconciliation complete, errata issued, no engine C# defect.
- Gate 2/3: **PASS** — SQL symbols persisted correctly, exact-lookupable,
  C# non-regression verified; no schema/dependency change.
- Gate 4: **R4 unchanged (0.4861); baseline improved** — SQL symbols close the
  index gap and help the deployable route, but they do not improve R4 under its
  current call allocation. This is reported as-is; no ranking or call-allocation
  tuning was attempted to force an R4 gain.

## Next step (recommendation, not started)

The R4 plan arm's typed call already names SQL procedures (the R3 traversal
derives `sp_UpdateOrgTotalEmployee` from the pooled OrganizationCommand), and
that name is now symbol-lookupable — converting it requires only the call
allocation that Gate 4 of the previous mission already showed is
cannibalization-risky within ≤6 calls. If an owner wants that conversion, the
cheapest experiment is: plan symbols routed *before* plan queries (1 call),
evaluated against the baseline on the new index; otherwise the standing result
(0.4861 plan arm, 0.2500 baseline on the SQL-symbol index) is the honest state.

## Reproduce

```bash
# isolated rebuild (31 s, CPU)
W=/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118
cd $W/corpus && zg --index --embedding local/potion-code-16m-v2 --device cpu
cargo run -q -p zg-engine --example r4_index_truth -- $W/corpus
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- extraction::code rag::workflow
```

Receipts: `benchmarks/rag-impact/results/r4-index-truth-20261118.json`
(re-generated against the new index),
`r4b-paired-20261116.json` (new-index run).
