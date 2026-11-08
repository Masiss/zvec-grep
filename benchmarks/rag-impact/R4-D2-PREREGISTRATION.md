# R4-D2 pre-registration — file-hit to source-range local retrieval

Status: **FROZEN BEFORE RUN. D2 only; D1 follows separately.**
Date: 2026-11-08. Owner approved D2 before D1. Frozen gold/corpus/historical
receipts/targets/held-out remain unchanged.

## Fixed inputs

- B0 receipt: `results/b0-official.json` (six search calls, top-10 units each).
- Development cases: `cases/dev.json`; 12 real cases only. Held-out untouched.
- Source: HRM2.0 `68a53a6865b81d05745c4aa47967204e8e128996`, read-only.
- Baselines: fixed deployable `fts_code_tokens` **0.3750**; B0 oracle **0.4722**
  historical only.

## Policy (deployable, no gold)

1. Run the six frozen B0 calls; take the union of their top-10 units.
2. Open at most 8 distinct files represented by those units, read each once.
3. Extract C# method ranges locally (namespace/class/method, brace-bounded).
   Score each method by query-token overlap in method name and body, plus a
   bonus if a seed unit overlaps the method.
4. Keep the search seed units as candidates and add method ranges.
5. Select at most 3 files by maximum candidate score; pack up to 10 ranges,
   preserving seed units and the method ranges; deduplicate exact ranges.
6. No model, LSP, graph store, second index, network or DB. No gold identifiers
   are inputs; gold is used only for measurement.

## Metrics

- **Primary:** Evidence Recall@10 on the 12 development cases.
- Per-case recall and regressions vs fixed `fts_code_tokens` / B0 oracle.
- Retrieval cost: files opened, source bytes read, method candidates scored,
  wall time.
- **Additional reads** (defined): distinct files opened after the six search
  calls to construct the pack, plus files the agent would open after receiving
  the pack. D2 reports the local-retrieval file opens; post-pack reads are
  reported separately and not mixed with pack file diversity.

## Gate

- Promotion only if mean Recall@10 is **above the fixed deployable baseline
  0.3750** and per-case regressions are explicitly acceptable. The original
  blind targets remain unchanged; this arm does not redefine them.
- If below 0.3750, report NO-GO.
- Do not adjust gold, corpus, targets or receipts after the run.
