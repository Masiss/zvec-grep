# Discovery H2 — file-to-range expansion

Status: **FROZEN BEFORE RUN. Discovery coverage only.**
Date: 2026-11-08. Dev-only; no held-out; no Selection/Expansion.

## Hypothesis

The H1 pool already contains files but misses method/type/SQL ranges inside them.
Expanding each pool file to its local method/type/procedure/view ranges will
recover range-coverage misses without new search queries or a new index.

## Policy (gold-free)

- Start from the H1 candidate pool (depth-50 base + question-symbol probes).
- For each distinct file in that pool, read it once and add:
  - C# method and type ranges using bounded structural/lexical extraction;
  - SQL procedure/view ranges using the existing line-form SQL parser;
- Do not add files outside the H1 pool; no repository-wide scan.
- Candidate pool only; no top-10 selection, no ranking, no Expansion.

## Metrics

- Micro and macro candidate coverage (gold spans in pool).
- File coverage and range coverage separately.
- Pool size, new unique ranges, marginal gain over H1, files/bytes read, wall time.
- Regressions are measured only as previously covered spans loss (should be zero;
  H1 pool is preserved).

## Gate

- Freeze the pool only if micro coverage reaches **20/25 (80%)**. If below,
  audit the remaining spans and use H3 (observed-identifier expansion) before
  stop; do not tune this hypothesis after seeing gold.
