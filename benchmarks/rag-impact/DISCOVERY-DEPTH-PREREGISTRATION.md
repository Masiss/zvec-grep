# Discovery depth experiment — pre-registration

Status: **FROZEN BEFORE RUN. Discovery-research probe only.**
Date: 2026-11-08. No candidate promotion, no target change, no held-out.

## Question

The frozen B0 six-call search only retained top-10 per call. Does increasing
per-call result depth to 50 raise the retrievable-gold ceiling and the optimal
10-item selectable ceiling on the same 12 development cases?

## Inputs

- Frozen B0 receipt, dev cases, corpus manifest.
- Existing corpus/index only; no model, LSP, graph, second index or network.
- Same six query constructions as B0 (`hybrid`, FTS question, first phrase,
  last phrase, code tokens, rg code tokens), with `--limit 50` / `-m 50`.
- Gold used only for measurement, never as query input.

## Measurements

- Per case: union distinct units across six calls; gold spans covered by union
  (retrievable ceiling); optimal coverage of any 10-unit subset of that union
  (selectable Recall@10 ceiling).
- Compare with frozen B0 top-10 union: 9/25 gold, macro 0.4722.
- Search cost: queries/case, returned units, wall time; no index write.
- Report per-case counts and exact unit lists in the receipt.

## Gate / interpretation

- This is a ceiling probe, not a candidate. If the ceiling stays below the frozen
  blind target 0.5722, selection/ranking cannot reach the target with these
  queries; a query-formulation/retrieval track is required.
- If the ceiling rises, it only authorizes a later deployable selection policy;
  it does not promote a candidate.
- No held-out access, no target change, no corpus/index change, no release.
