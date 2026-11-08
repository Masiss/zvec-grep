# Discovery H5 — deterministic budget-aware ordering

Status: **PROPOSED, NOT RUN. Pre-registration only.**
Date: 2026-11-08.

## Evidence

H2 full-prefix 500 vs bounded 500 varies with Python set/range order (D05/D06/
D07/D12 coverage changes across hash seeds). 19/25 is an order artifact; the
bounded policy is 15/25 at 500.

## Hypothesis

Sorting H2 ranges deterministically by **query/code relevance and observed seed
rank**, not gold, will retain more of the 19 full-pool spans at the 500 cap.

## Policy

- H1 pool units first, in original call/rank order.
- H2 method/type/SQL ranges sorted by:
  - overlap with an H1 seed unit;
  - query-token hits in method/type name, then body;
  - file seed rank; shorter range tie-break; path/line stable tie-break.
- Cap 500; no top-10 selection; no gold features; no model/index.
- Evaluate on old 12 and hard independent set with same cap and resource gates.

## Gate

Micro old ≥20/25, hard independent ≥4/5, no H1 regression, p95 wall ≤5 s/case,
peak PSS ≤64 MiB, CPU/I/O recorded. If fail, report NO-GO; no H6 unless a
different evidence path exists.

## Amendment — 2026-11-08 (append-only; original preserved)

The H5 ceiling is **19/25**: H5 only reorders the existing H2 pool, so it cannot
create the 20th span. H5 success is therefore **19/25 at cap 500, deterministic,
within resource budget**; it cannot satisfy the overall 20/25 Discovery gate by
itself. H6 is required for any span outside the H2 pool.
