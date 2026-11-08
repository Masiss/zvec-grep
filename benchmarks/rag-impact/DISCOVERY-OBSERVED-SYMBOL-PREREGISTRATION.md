# Discovery H3 — observed-symbol expansion

Status: **FROZEN BEFORE RUN. Discovery coverage only.**
Date: 2026-11-08. Dev-only; no held-out; no Selection/Expansion.

## Context

H2 file-to-range expansion reaches 19/25 micro (76%), short of the 20/25 (80%)
gate. The remaining spans are D02 C4/C5/C6, D10 C4 and D12 S1.

## Hypothesis

The H2 pool contains files that reference type/procedure symbols not otherwise
queried. Extracting rare observed symbols from those files and running bounded
exact FTS/rg probes will add the missing type/procedure files without gold
identifiers or repository-wide scanning.

## Policy (gold-free)

- Start from the H2 pool (H1 pool + local method/type/SQL ranges).
- From the source files already in that pool, extract observed identifiers:
  CamelCase type names, method names, `sp_`/underscore procedure names, and
  quoted SQL execution names.
- Exclude identifiers already present in the question; rank by low observed
  frequency (rare symbols first) and bound to 4 identifiers.
- Run at most one exact FTS and one exact rg probe per identifier, depth 50;
  merge results into the candidate pool. No new index/model.
- No top-10 selection; candidate coverage only.

## Metrics

- Micro/macro candidate coverage, file/range coverage, new spans/files.
- Pool size, files/bytes read, extra queries, wall time.
- Regressions: H2-covered spans must remain covered.

## Gate

- Freeze Discovery pool only if micro coverage reaches **20/25 (80%)** without
  losing H2 coverage and without unbounded depth/repository scan. If below,
  report NO-GO for Discovery and stop; no Selection/Expansion.
