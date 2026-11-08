# RAG R3 — EF6/SQL facts and selected-variant authority

Status: **R3 SQL FACT SLICE PASS on synthetic + real selected anchors; full
cross-file expansion and candidate promotion remain pending.**
Date: 2026-11-08. Follows `docs/rag/implementation-plan.md` §5 and
[`rag-r2-csharp-facts.md`](rag-r2-csharp-facts.md).

## Scope

Added `crates/zg-engine/src/extraction/sql_facts.rs`. It is a bounded SQL fact
reader, not a SQL engine and not a deployment oracle:

- selected `CREATE/ALTER PROCEDURE` and `CREATE/ALTER VIEW` definitions;
- source-visible `UPDATE`/`INSERT`/`DELETE` writes and `FROM`/`JOIN` reads;
- view columns (including `FullName` in `EmployeeBaseViews`);
- selected/excluded authority per definition;
- EF `ExecuteSqlCommand`/`SqlQuery` literal SQL strings;
- dynamic values/tables stay candidates with a reason;
- no SQL execution, no DB connection, no live-authority claim.

## Resolution rules

- A proc call binds only to a **selected** definition. If variants exist but
  none is selected, the binding is `candidate` with `no selected variant`.
- `sp_UpdateOrgTotalEmployee` binds to the selected S1; `_old`/`_old1` remain
  excluded and are never promoted.
- A raw `update Employees ...` with concatenated values records the source-visible
  table write but stays `candidate` because the predicate/value is dynamic.
- A dynamic table name (`"update " + tableName`) is a candidate; no table is
  guessed from proximity.
- A view is read-only in these facts; `SELECT` never becomes a write.

## Tests

- Focused SQL tests: **3 passed, 0 failed, 1 ignored**.
  - selected proc writes `Organizations.TotalEmployee`, reads `Employees`;
  - selected view exposes `FullName`, writes nothing;
  - excluded `_old` variant is not selected;
  - C# proc call binds to S1; raw update/dynamic table stay candidate;
  - view query is a read.
- Real HRM2.0 ignored gate: **PASS**. It parses S1/S2 and both excluded variants,
  binds `UpdateOrgTotalEmployee` to S1, finds the `Employees` raw update in
  `DeleteEmployeeByIds`, and validates the `EmployeeBaseViews` column read.
- Full engine lib under bwrap containment: **741 passed, 0 failed, 18 ignored**
  (includes R3 and R4 tests). Workspace clippy/fmt: PASS.

## Gate status

| R3 gate item | Status |
|---|---|
| restore → helper → proc definition → Organizations update | **PASS for source facts** |
| personal-update branches/two save boundaries | **PASS in R2** |
| view read vs table write (`EmployeeBaseViews`) | **PASS** |
| selected-variant authority / no `_old` authority | **PASS** |
| dynamic SQL target stays candidate | **PASS** |
| reverse expansion / full cross-file loop | **NOT IMPLEMENTED** |
| deployment/live DB authority | **NOT CLAIMED** |

## Limits

- The SQL reader is a bounded line/token scanner, not a T-SQL parser. Unsupported
  control flow, dynamic SQL and multi-statement templates remain gaps.
- It records source-visible facts only; it cannot prove a proc was deployed or
  executed.
- The selected catalog is supplied by the caller; R3 does not decide project
  inclusion.
- Reverse/forward graph expansion beyond the bounded candidate helper is R4.

## Next

R4 uses these facts in a bounded evidence-expansion pilot. See
[`rag-r4-candidate-pilot.md`](rag-r4-candidate-pilot.md); the pilot did **not**
meet the frozen benefit targets, so the R1 lightweight mode remains the safe
fallback.
