# RAG R2 — bounded C# facts (side parser)

Status: **R2 REAL-CASE GATE PASS for the bounded C# fact subset; R3/R4 and
SQL-dependent cases remain pending.**
Date: 2026-11-08. Follows `docs/rag/implementation-plan.md` §4 and
`rust/docs/rag-r0-r1.md`.

## Scope

Implement the recommended R2-A side parser without changing ordinary `.cs`
indexing/chunking or the index format. The parser is a private engine module:
`crates/zg-engine/src/extraction/csharp_facts.rs`.

Owner authorized the new grammar dependency. It is the only lockfile change:

- `tree-sitter-c-sharp = "0.23.5"` in `rust/Cargo.toml` and `zg-engine`.

No production defaults, storage format, zvec pin, model, concurrency or cache
settings changed. `.cs` files still use the existing text/fallback extraction
unless a later, explicitly authorized integration wires facts into indexing.

## What is extracted

- Project identity from `.csproj` as XML data: `AssemblyName`, `RootNamespace`.
- Namespace, type kind, `partial`, line spans.
- Methods: signature, parameters, namespace/type identity, line spans.
- `DbSet<T>`/`IDbSet<T>` properties and their element types.
- Field/property types, local and parameter types, `var` inference for
  `new X()`, `Context.DbSet.FirstOrDefault`, `Type.GetType/GetMethod`.
- Assignments: receiver, inferred receiver type, field, value, line, guard.
- Invocations: receiver, inferred receiver type, method, arguments, line,
  guard, resolution status/target/reason.
- `SaveChanges` boundaries: receiver, context type, line, guard.
- Branch guards from `if`/`else` and switch sections.
- Parse-error flag; a broken file does not panic or lose the lexical fallback.

## Resolution rules

- Resolution is project-scoped. Same FQN/method in two assemblies is not merged.
- Receiver-typed calls resolve only when exactly one matching method exists in
  that project/type. Overload/ambiguity stays `candidate`.
- Unqualified calls resolve only when unique in the current type, then the
  project; otherwise `candidate`.
- Reflection (`Type.GetType`, `GetMethod`, `Invoke`), dynamic SQL
  (`ExecuteSqlCommand`, `SqlQuery`), EF `SaveChanges`/LINQ, and external writes
  stay `candidate` with a reason. They are never promoted to resolved edges.
- `expand_targets` deduplicates visited targets, stops at a hop cap and returns
  explicit gaps for cycles/cap. It is a bounded helper, not a graph store.

## Tests and verification

- New focused tests: **8 passed, 0 failed, 1 ignored** (the ignored test is the
  external HRM2.0 real-case gate; it passes when run with `--ignored`).
  (`cargo test -p zg-engine --lib extraction::csharp_facts -- --test-threads=1`).
  - D13: same FQN/method in ProjectA/ProjectB resolves inside each project.
  - D14: reflection/dynamic SQL invocations remain candidates; zero resolved.
  - D16: cycle edges resolve; expansion is capped/deduplicated.
  - R2 personal-update fixture: Employee/EmployeeDetail receiver inference, two
    `SaveChanges`, `changeType` guard around `UpdateOrgTotalEmployee`.
  - Project identity from `.csproj`.
  - Cross-file partial-class and cross-project referenced-DbSet resolution.
  - Broken source sets `parse_error` without panicking.
  - Real HRM2.0 gate (`--ignored`): D01 dispatch, D02 receiver types and two
    saves, D03 Keyword expression, D04 no Keyword write, D05 save ordering,
    D06 changeType guard, D08 raw-SQL candidate, D10 CoreDBContext saves, D11
    Avatar branch.
- Full engine lib under the qualified bwrap containment:
  **735 passed, 0 failed, 16 ignored** before the final D03/D05 assertion-only
  edit; the real ignored gate was rerun after that edit and passed.
- `cargo clippy --workspace --all-targets -- -D warnings`: **PASS**.
- `cargo fmt --all -- --check`: **PASS**.

The focused tests ran with an external `TMPDIR`; the full lib suite ran with the
bwrap read-only host/repo and private writable OUT, as required after the earlier
test-isolation incident.

## Gate status

| R2 gate item | Status |
|---|---|
| Minimal fixtures for project/partial/receiver/branch/commit boundaries | **PASS on synthetic fixtures** |
| Zero false resolved edges on D13/D14 critical negatives | **PASS** |
| Source comments/strings are not executed calls | **PASS** (D14/D16 parser tests) |
| Parse failure keeps lexical fallback | **PASS** (`parse_error` flag; no indexing change) |
| Existing-language extraction tests | **PASS** (full engine lib) |
| Real HRM2.0 D01–D12 parser facts vs frozen gold | **PASS for C# facts used by D01–D06/D08/D10/D11**; D07/D09/D12 SQL facts are R3 |
| D15 stale/mutation behavior | **PENDING R4 revalidation** |
| R3 SQL/EF/cross-file expansion | **NOT STARTED** |

## Limits

- The parser handles a bounded C# subset. Unsupported constructs must remain
  candidates/gaps; do not infer a resolved target from a name or nearby file.
- The real-case gate was a read-only fact extraction over the frozen corpus; it
  does not prove answer quality or complete relation coverage. D07/D09/D12
  require the R3 SQL adapter. D15 requires R4 revalidation.
- The side parser is not yet exposed to the MCP/CLI response shape.
- D15 mutation/revalidation and multi-hop loop budgets belong to R4; D07/D09/D12
  SQL facts belong to R3.

## Next

1. Real-case R2 gate passed for the C# fact subset; keep the ignored test as the
   regression harness for the external HRM2.0 checkout.
2. Start R3: EF6/SQL facts, selected-variant authority, reverse expansion, with
   the same candidate/gap discipline.
3. Then R4/R5: bounded loop, evidence assembly, and candidate measurement
   against the frozen B0.
