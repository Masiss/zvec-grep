# R4-D2 result — file-hit local range retrieval

Status: **NO-GO. Mean Recall@10 did not exceed the fixed deployable baseline.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/R4-D2-PREREGISTRATION.md`.
Frozen inputs/targets/held-out unchanged.

## Result

| Metric | Fixed `fts_code` | D2 | B0 oracle (historical) |
|---|---:|---:|---:|
| Mean Recall@10 | 0.3750 | **0.3750** | 0.4722 |

- D2 did not beat the deployable fixed baseline; gate FAIL.
- Gains: D01 0.5 → 1.0; D10 0.0 → 0.5.
- Regression: D09 1.0 → 0.0.
- Unchanged misses: D02/D03/D05/D06/D07/D12 at 0.
- Cost: 8 seed files opened/case, ~193,847 bytes read, ~88.6 method
  candidates scored, ~237 ms/case, pack at most 3 files.
- Pack files are not post-pack agent reads; both are reported separately.

## Interpretation

The local method scorer admitted methods from up to 8 files but the 3-file
packing selected the wrong files for D03/D05/D06/D07/D12; D09 lost a previously
packed range. The policy is not safe to promote. D2 remains an experiment
receipt only.

## Artifact

- `benchmarks/rag-impact/results/candidate-r4d2.json`
- Tool: `benchmarks/rag-impact/tools/r4d2_policy.py`

No candidate promotion, no target change, no held-out run.
