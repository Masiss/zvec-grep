# R4 discovery failure attribution — D01–D12 (read-only forensics)

Status: **ANALYSIS ONLY. No implementation, no production change.**
Branch `work/r4-workflow-20261115` @ `711a4f7`. Date 2026-11-15.

Reproduce:

```bash
cargo run -q -p zg-engine --example r4_discovery_forensics      # 154 s
cargo run -q -p zg-engine --example r4_symbol_reachability     # 25 s
```

Receipts:

- `benchmarks/rag-impact/results/r4-discovery-forensics-20261115.json`
- `benchmarks/rag-impact/results/r4-symbol-reachability-20261115.json`

Both probes are read-only against the frozen corpus; the measured configuration
is the one used by the committed R4 receipt (24 tool calls, 4 scopes, 250
results/call, 96 files, extension and 1 MiB size bounds).

## 1. Correction to the previous result

`r4-workflow-results-20261115.md` claimed pool coverage was 7/25 and that
"selection converted 7/7". The receipt shows **pool 7, selected 6**. D10's
`CoreService.cs:15-22` was pooled (rank 77 of 250) and then dropped by the
**10-item pack cap** (`budget_items`), not by a per-file cap. The results doc is
corrected accordingly. Pool coverage is unchanged at 7/25.

## 2. Per-span attribution (25 gold spans)

| case | file | span | attribution | question handles | full-scan rank | symbol route file rank | enclosing declaration | symbol route top-10 |
|---|---|---|---|---:|---:|---:|---|---|
| D01 | EmployeeController.cs | 459-620 | pooled file, span missed | 1 | — | — | `QuickEdit` (miss) | no |
| D01 | EmployeeCommand.cs | 215-285 | no question handle | 0 | — | 8 | `EmployeeEditThongTinCaNhan` | yes |
| D02 | EmployeeCommand.cs | 215-285 | no question handle | 0 | — | 8 | `EmployeeEditThongTinCaNhan` | yes |
| D02 | EmployeeCommand.cs | 287-425 | no question handle | 0 | — | 8 | `EmployeeEditThongTinCaNhan` | no |
| D02 | CoreDBContext.cs | 17-20 | no question handle | 0 | — | 1 | `OnModelCreating` | yes |
| D02 | CoreDBContext.cs | 41-55 | no question handle | 0 | — | 1 | `EmployeeDetails` | yes |
| D02 | Employee.cs | 1-65 | no question handle | 0 | — | 1 | (file stem) | yes |
| D02 | EmployeeDetail.cs | 1-100 | no question handle | 0 | — | 4 | (file stem) | yes |
| D03 | EmployeeCommand.cs | 215-285 | starved by result limit | 2 | 3312 | 8 | `EmployeeEditThongTinCaNhan` | yes |
| D04 | EmployeeCommand.cs | 562-573 | selected | 1 | 2 | 2 | `EmployeeUpdateInfo` | yes |
| D05 | EmployeeCommand.cs | 215-425 | no question handle | 0 | — | 8 | `EmployeeEditThongTinCaNhan` | yes |
| D06 | EmployeeCommand.cs | 215-285 | no question handle | 0 | — | 8 | `EmployeeEditThongTinCaNhan` | yes |
| D06 | EmployeeCommand.cs | 427-457 | no question handle | 0 | — | 0 | — | yes |
| D06 | OrganizationCommand.cs | 1057-1074 | no question handle | 0 | — | — | `UpdateOrgTotalEmployee` | no |
| D07 | EmployeeCommand.cs | 539-560 | selected | 2 | 1399 | 4 | `DeleteEmployeeByIds` | yes |
| D07 | OrganizationCommand.cs | 1057-1074 | no question handle | 0 | — | — | `UpdateOrgTotalEmployee` | no |
| D07 | sp_UpdateOrgTotalEmployee.sql | 58-95 | starved by result limit | 1 | — | — | — | no |
| D08 | EmployeeController.cs | 624-634 | selected | 1 | 5 | 5 | `DeleteEmployeeByIds` | yes |
| D08 | EmployeeCommand.cs | 539-549 | selected | 1 | 4 | 4 | `DeleteEmployeeByIds` | yes |
| D09 | EmployeeBaseViews.sql | 1-10 | selected | 2 | — | — | — | no |
| D10 | CoreService.cs | 15-22 | pooled / item cap | 1 | 77 | 2 | — | yes |
| D10 | CoreDBContext.cs | 9-20 | no question handle | 0 | — | 1 | `DbContext` | yes |
| D11 | EmployeeCommand.cs | 866-887 | selected | 1 | 0 | 0 | `UpdateEmployeeAttr` | yes |
| D12 | OrganizationCommand.cs | 1057-1074 | no question handle | 0 | — | — | `UpdateOrgTotalEmployee` | no |
| D12 | sp_UpdateOrgTotalEmployee.sql | 1-95 | starved by result limit | 1 | — | — | — | no |

Counts: **selected 6 · no question handle 14 · starved by result limit 3 ·
pooled file but span missed 1 · pooled but item cap 1**.

`question handles` = how many of the case's DEFINE queries occur literally in
the span. `full-scan rank` = rank of the span's file when the same query is run
with limit 5000 (— means it never appeared). `symbol route` uses the enclosing
declaration name, the file stem, or identifiers found **inside the span**.

## 3. Root causes by category

### 3.1 No lexical handle (14/25) — missing retrieval, not ranking

The question's tokens never occur in the span or its declaration. Examples: D02
asks about "personal" update, and the evidence is `Employee.cs`,
`EmployeeDetail.cs`, `CoreDBContext.cs`, `EmployeeCommand.cs`; D05/D06/D12
similar. Vietnamese prose + English identifiers means lexical discovery cannot
reach these spans **at any limit**. This is the dominant cause and no amount of
ranking, budget or scan depth fixes it.

All 14 have a *reachable symbol*: exact declaration name or file stem puts the
file in the top-10 results (D02 `EmployeeCommand` 8, `CoreDBContext` 1,
`Employee` 1, `EmployeeDetail` 4; D05/D06 `EmployeeEditThongTinCaNhan` 8; D10
`CoreService` 2). D06/D07/D12's `OrganizationCommand:1057-1074` is reachable only
through `UpdateOrgTotalEmployee` when the query is the *declaration* rather than
the file stem — and that name comes from the SQL proc, i.e. from typed facts, not
from the question.

### 3.2 Scan-order starvation (3/25) — caps, amplified by post-filtering

D03's first match in `EmployeeCommand.cs` is at **rank 3312** of a 5000-limit
scan; the measured run only saw 250. Two SQL spans likewise never surfaced.
Cause: the corpus mixes source with generated dumps (`Database/**.json`,
`Database/**.sql`), the backend limit is applied **before** the extension filter,
so admitted source files are starved by matches in files the workflow then
discards. Evidence: 96 distinct files entered the pool while the JSON filter
rejected thousands of units, and file budget gaps fire on every case.

This is a defect in *how the filter is applied*, not in ranking: passing globs /
file types to the backend removes the starvation.

### 3.3 Pooled file, span missed (1/25)

D01 `EmployeeController.cs:459-620`: the file is in the pool (via `Form`), but no
pooled unit overlaps the span. The declaration detector picked `QuickEdit`, and
the submitted units sat in other methods. Widening chosen from a match line is
not declaration-aware enough; the enclosing declaration of the *gold* span is
`EmployeeEdit(EmployeeEditModel)`.

### 3.4 Pooled but item cap (1/25)

D10 `CoreService.cs:15-22` was pooled at rank 77/250 and lost when the 10-item
pack filled with higher-scored units from other files. This is a **selection/cap**
loss, the only one of its kind; it does not change the bottleneck.

### 3.5 Not the causes (measured, not assumed)

- **Indexing/scope**: all 25 gold files are in the frozen corpus manifest, all
  pass the extension and size bounds, and all exist. No gold span is missing
  because of index coverage or file exclusion.
- **Cross-file/SQL acquisition**: the SQL spans (D07, D09, D12) were partly
  reached by expansion (D09 selected), but the two proc-body spans were starved
  by the same result limit; this is not a separate SQL-parsing failure.
- **Ranking**: only one loss (D10) is a ranking/cap loss. Ranking is not the
  current bottleneck.

## 4. Oracle reachability evidence (why a symbol route is worth building)

Queries derived **from the gold span itself** (declaration name, file stem, in-span
identifiers) reach **17/25 spans inside the top 10** of a 50-result search
(18/25 files). With perfect selection over those candidates the macro Recall@10
ceiling would be **0.6944** — above the frozen R0 target 0.5722 and well above the
deployable FTS baseline 0.3750.

This is an **oracle ceiling**, exactly like B0's oracle best-call: the queries
were built from gold. It proves headroom, not a deployable policy. The
deployable question is whether the same names can be derived from the question
plus first-pass evidence — which is what R2/R3 typed facts and the R2 invocation
targets are for.

## 5. Limits of this evidence

- Single run per probe; host load 2.4–4.3 during the forensics run.
- The workflow probe reproduces the *measured* configuration; it is not a new
  measurement of Recall@10.
- `full-scan rank` uses limit 5000 with no extension filter, so it measures
  match volume including generated dumps; that is the point of the starvation
  finding, not a ranking claim.
- Symbol reachability is lexical name search, not `index v3` symbol lookup: no
  corpus index was built (a build is ~70 min CPU, E4 evidence). Index-backed
  lookup would return declaration ranges directly and cannot be starved, but it
  is not measured here.
- No held-out data was read.
