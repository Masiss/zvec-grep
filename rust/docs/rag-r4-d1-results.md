# R4-D1 result — observed-identifier query expansion

Status: **NO-GO. Mean Recall@10 is below the fixed deployable baseline.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/R4-D1-PREREGISTRATION.md`.
Frozen inputs/targets/held-out unchanged.

## Result

| Metric | Fixed `fts_code` | D1 | B0 oracle (historical) |
|---|---:|---:|---:|
| Mean Recall@10 | 0.3750 | **0.3472** | 0.4722 |

- D1 did not beat the fixed deployable baseline; gate FAIL.
- Gain: D02 0.0 → 0.167 (one span).
- Regressions: D01 0.5 → 0.0; D10 0.5 → 0.0.
- Unchanged: D03/D05/D06/D07/D12 at 0; D04/D08/D09/D11 at 1.0.
- Cost: up to 2 extra FTS probes/case, ~16.3 extra units, ~2.44 s extra
  search/case, 8 seed files opened, ~193,847 bytes read, pack ~8.9 files/case.

## Interpretation

The observed-identifier extractor selected generic names (`ChangeCompare`,
`CoreService`) rather than the target method/procedure. D02 gained one span, but
the pack displaced D01/D10 evidence. The extra search cost is high relative to
the gain. D1 remains an experiment receipt only.

## Artifact

- `benchmarks/rag-impact/results/candidate-r4d1.json`
- Tool: `benchmarks/rag-impact/tools/r4d1_policy.py`

No candidate promotion, no target change, no held-out run.
