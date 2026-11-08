# Discovery symbol/query experiment — pre-registration

Status: **FROZEN BEFORE RUN. Discovery H1.**
Date: 2026-11-08. Dev-only; held-out untouched. No candidate promotion, no
corpus/target/index change.

## Hypothesis

If the six depth-50 B0 calls miss code identifiers, adding exact searches for
code-like symbols already present in the question, followed by a deterministic
code-aware top-10 selection, will raise both the candidate-pool ceiling and
deployable Recall@10 above the depth-50 baseline (12/25, macro 0.5417) and fixed
FTS (0.3750).

## Policy (deployable, no gold)

1. Run the six B0 query constructions with 50 results per call.
2. Extract at most four code-like symbols from the question (CamelCase, `_`,
   `sp_`, quoted/uppercase identifiers). Do not use gold spans/identifiers.
3. Run one exact FTS probe and one managed-`rg` exact probe per symbol; cap
   results at 50.
4. Build a candidate pool from all base and symbol units, deduplicated.
5. Selection: read each unit's cited source range, score by query-term overlap
   in path/content plus exact symbol occurrence; rank, break ties by original
   call/unit order, and take 10. No gold used for ranking.
6. Report pool ceiling, optimal selectable@10, and deployable top-10 separately.

## Metrics

- Candidate-pool retrievable gold / selectable@10 ceiling.
- Deployable Recall@10; per-case regressions vs fixed FTS and depth-50.
- Queries/case, returned units, source files/bytes read, wall time.
- No held-out, no target change, no index/model.

## Gate

- Discovery improvement requires pool ceiling and deployable recall to rise
  above their respective baselines. If the pool rises but deployable recall does
  not, Selection is the next layer; if neither rises, report NO-GO and use the
  second hypothesis (observed-symbol expansion from candidate files).
- No promotion of a candidate beyond Discovery research.
