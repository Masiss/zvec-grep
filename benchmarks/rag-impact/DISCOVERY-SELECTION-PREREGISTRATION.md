# Discovery Selection experiment — pre-registration

Status: **FROZEN BEFORE RUN. Selection after H1 pool improvement.**
Date: 2026-11-08. Dev-only; no held-out; no target/corpus/index change.

## Context

H1 found a candidate-pool ceiling of 14/25 (macro 0.6528) with depth-50 + question
symbols, but the deployable content-score top-10 selected only 6/25 (0.4167).
Selection is now the bottleneck.

## Policies (deployable, no gold)

A. **RRF across calls**: each pool unit receives `sum(1/(60+rank))` over distinct
query calls that returned it; top 10 by score, tie by first call/rank.
B. **Symbol + RRF**: first prioritize units whose cited source contains a
question-derived code symbol; within that, RRF; then remaining RRF units.

Both use only question text, search results and cited source; no gold spans or
gold identifiers. Source reads are counted.

## Metrics

- Pool ceiling and optimal selectable@10 (H1 reference).
- Deployable Recall@10 for A/B; per-case regressions vs fixed FTS 0.3750 and
  depth-50 pool.
- Queries, files/bytes read, wall time.
- No held-out, no target change, no candidate promotion.

## Gate

- Selection improvement requires deployable Recall@10 above fixed FTS and above
  the H1 deployable 0.4167 without losing pool evidence. If neither policy
  reaches the target 0.5722, report Selection NO-GO and do not proceed to
  Expansion; keep the pool evidence for a later retrieval approach.
