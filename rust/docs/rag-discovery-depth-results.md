# Discovery depth probe — result

Status: **CEILING IMPROVED, TARGET STILL ABOVE CEILING.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-DEPTH-PREREGISTRATION.md`.
Development split only; held-out untouched.

## Result

| Policy | Retrievable gold | Selectable@10 | Macro recall |
|---|---:|---:|---:|
| B0 six calls, top-10 | 9/25 | 9/25 | 0.4722 |
| Same six calls, 50 results/call | **12/25** | **12/25** | **0.5417** |
| Frozen blind target | — | — | 0.5722 |

Per case (retrievable / gold): D01 2/2, D02 2/6, D03 0/1, D04 1/1,
D05 0/1, D06 1/3, D07 1/3, D08 2/2, D09 1/1, D10 1/2, D11 1/1, D12 0/2.

- Depth 50 recovered D01's second span and one additional D02 span.
- D03, D05 and D12 remain fully undiscovered; D02 still has four misses;
  D06/D07 still miss two each.
- Search cost: six calls/case, 78–196 distinct units/case; no model, index or
  corpus change.
- The target is still above the depth-50 ceiling. Further selection/ranking
  changes cannot reach it from this candidate set.

## Interpretation

Depth is a real Discovery lever but insufficient. The remaining misses are
query/formulation and discovery, not top-10 cutoff. The next Discovery work
should test general-purpose query/symbol expansion and code-aware retrieval,
not another pack-ranking policy. No candidate GO and no target change.

## Artifact

- `benchmarks/rag-impact/results/discovery-depth-probe.json`
- Tool: `benchmarks/rag-impact/tools/discovery_depth_probe.py`
