# Discovery H1 — question symbols + code-aware pool

Status: **POOL CEILING IMPROVED; DEPLOYABLE SELECTION FAILED.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-SYMBOL-PREREGISTRATION.md`.
Dev-only; held-out untouched.

## Result

| Measure | B0 top-10 | Depth 50 | H1 symbol pool | H1 selected |
|---|---:|---:|---:|---:|
| Gold spans | 9/25 | 12/25 | **14/25** | 6/25 |
| Macro | 0.4722 | 0.5417 | **0.6528** | 0.4167 |

- H1 pool exceeded the blind target 0.5722 macro; **selection did not**.
- New pool recoveries: D03, D07; D01/D02 pool 2; D06/D10 pool 1.
- D05/D12 remain 0; D02 still 2/6.
- Cost: 1,266 source files read, ~19.6 MB, ~38.3 s total across 12 cases;
  six base calls plus up to eight symbol probes/case. No model/index change.
- H1 selected top-10 was worse than fixed FTS because the content score chose
  long unrelated ranges and symbol probes from generic question words.

## Artifact

- `benchmarks/rag-impact/results/discovery-symbol-probe.json`
- Tool: `benchmarks/rag-impact/tools/discovery_symbol_probe.py`

The pool is usable evidence for Selection research; it is not a candidate.
