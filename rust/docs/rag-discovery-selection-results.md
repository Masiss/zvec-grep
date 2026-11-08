# Discovery Selection — A/B result

Status: **NO-GO. Pool ceiling 14/25; best deployable selection 6/25.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-SELECTION-PREREGISTRATION.md`.
Dev-only; no held-out.

## Result

| Policy | Selected gold | Macro |
|---|---:|---:|
| Pool ceiling (optimal) | 14/25 | 0.6528 |
| A: RRF across calls | 4/25 | 0.2500 |
| B: symbol-first + RRF | 6/25 | 0.3750 |
| Fixed FTS baseline | 6/25 | 0.3750 |

- A lost D01 one span, D08 one, D11 one; B retained D08/D11 but still lost D01,
  D02, D03, D06, D07, D10.
- The pool contains gold, but simple call-rank/symbol heuristics cannot separate
  target ranges from unrelated ranges.
- Cost: 1,266 files read, ~19.6 MB, ~36.5 s total; no model/index change.

## Disposition

Selection NO-GO. Do not proceed to Expansion on this pool. The next Selection
work would need a different, evidence-grounded feature (e.g. method/range
semantics or agent-chosen gap), not more RRF tuning. No candidate promotion.

## Artifact

- `benchmarks/rag-impact/results/discovery-selection-probe.json`
- Tool: `benchmarks/rag-impact/tools/discovery_selection_probe.py`
