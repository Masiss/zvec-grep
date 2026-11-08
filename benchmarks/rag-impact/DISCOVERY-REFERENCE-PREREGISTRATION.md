# Discovery H4 — reference-aware expansion

Status: **FROZEN BEFORE RUN. Discovery coverage only.**
Date: 2026-11-08. Dev-only; no held-out; no Selection/Expansion.

## Context

H2 reaches 19/25 (76%). The remaining D02 C4/C5/C6, D10 C4 and D12 S1 are
file/definition discovery misses; H3 generic rare symbols added nothing.

## Hypothesis

References observed inside H2 pool source (types, `new Type`, procedure names,
quoted SQL execution targets) identify the missing definitions. Resolving only
those observed references with bounded exact searches should add the missing
files/ranges without gold or repository-wide scanning.

## Policy (gold-free)

- Start from the H2 pool.
- Read only pool files/ranges already observed.
- Extract at most 6 references from those observed bytes:
  - `sp_`/`fn_`/`func_` procedure names;
  - `new Type` and CamelCase type tokens;
  - quoted SQL execution targets.
- Rank by reference kind and observed frequency; no gold identifiers.
- Run one exact FTS and one exact rg probe per reference, depth 50; merge.
- No selection/top-10; no new model/index; no hardcoded HRM names.

## Metrics / attribution

- Micro/macro candidate coverage, file/range coverage, new spans/files.
- For each missing span: whether an observed reference path existed, the
  reference, and whether exact resolution found it.
- Pool size, references, queries, files/bytes, wall/CPU/PSS if measurable.
- H2-covered spans must remain covered.

## Gate

- Freeze if micro coverage reaches **20/25 (80%)** without H2 regression and
  within resource budget. If a missing span has no observed reference path, mark
  it a file-discovery gap and do not force traversal. Otherwise NO-GO.
