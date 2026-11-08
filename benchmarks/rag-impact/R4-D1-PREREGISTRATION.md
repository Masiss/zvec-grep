# R4-D1 pre-registration — observed-identifier query expansion

Status: **FROZEN BEFORE RUN. D1 after D2.**
Date: 2026-11-08. Frozen gold/corpus/historical receipts/targets/held-out remain
unchanged.

## Fixed inputs and baselines

- B0 receipt: six search calls, top-10 units each.
- Development cases: 12 real cases only; held-out untouched.
- Deployable baseline: fixed `fts_code_tokens` **0.3750**.
- Historical oracle: B0 **0.4722**, not a deployable policy.

## Policy (deployable, no gold)

1. Use the six frozen B0 calls as the initial evidence set.
2. From the observed seed files only, extract up to 2 new identifiers: method,
   class/type or file-stem names that occur in observed source, ranked by
   generic frequency and query-token overlap. Do not use gold spans, gold
   identifiers, held-out data or candidate output.
3. Run at most 2 bounded exact/lexical searches over the existing corpus/index
   for those observed identifiers.
4. Merge the new units with the initial evidence; keep at most 5 initial seed
   units and 5 new units (or fewer) in a 10-range pack; deduplicate ranges.
5. No model, LSP, persistent graph, second index, network or DB.

## Metrics

- Primary: Evidence Recall@10 on the 12 development cases.
- Per-case regressions vs fixed `fts_code_tokens`.
- Retrieval cost: extra search calls, units returned, wall time.
- Additional reads: files the agent opens after receiving the pack; report
  separately from pack file diversity.

## Gate

- Promotion only if mean Recall@10 exceeds the fixed deployable baseline
  **0.3750** and regressions are explicitly acceptable. The original blind
  targets remain unchanged.
- If below 0.3750, report NO-GO.
