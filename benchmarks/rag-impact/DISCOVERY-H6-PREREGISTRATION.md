# Discovery H6 — structural cross-file reference resolution

Status: **PROPOSED, NOT RUN. Pre-registration only.**
Date: 2026-11-08.

## Evidence

Remaining misses D02 C4/C5, D10 C4, D12 S1 have no observed reference path in
H4; H4 used generic observed symbols and added only one incidental span.

## Hypothesis

Using the existing C#/SQL structural facts to resolve only explicit references
observed in cited bytes (`Type.Method`, `new Type`, `DbSet<T>`/entity types,
`sp_`/proc names) will add the missing definitions without generic frequency
heuristics.

## Policy

- Start from H1/H2 pool; read only observed bytes.
- Extract explicit structural references with R2/R3 facts:
  - declared/used type names and `new Type`;
  - DbSet/entity element types;
  - procedure/view names from SQL execution strings.
- Resolve each reference with exact search, bounded to 6 refs/case; no gold,
  no hardcoded HRM names, no new index/model.
- Cap candidate pool; evaluate old + hard independent; preserve H1/H2 evidence.

## Gate

Micro old ≥20/25 and hard independent ≥4/5, no regressions, resource bounds
(p95 wall ≤5 s/case, PSS ≤64 MiB) and provenance for every added span. If no
observed reference path exists, leave as file/query-discovery gap; do not force
a relation.
