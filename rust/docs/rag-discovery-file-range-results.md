# Discovery H2 — file-to-range expansion result

Status: **19/25 micro (76%), below the 80% Discovery gate.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-FILE-RANGE-PREREGISTRATION.md`.
Dev-only; no held-out; no Selection/Expansion.

## Result

| Measure | H1 | H2 |
|---|---:|---:|
| Range coverage | 14/25 | **19/25** |
| File coverage | — | 19/25 |
| Macro coverage | 0.6528 | **0.8611** |
| Pool units | — | ~10–20k total |

- Recovered D05, D06 (2), D07, D12 C7 by expanding pool files to local
  method/type/SQL ranges.
- No H1 span was lost; H2 is monotonic over H1.
- Remaining 6: D02 (4), D10 (1), D12 S1 (1).
- Cost: 1,266 files read, ~19.6 MB; same search set as H1 plus local source
  parsing; no model/index change.
- 19/25 = 76%, one span short of the 20/25 (80%) gate.

## Artifact

- `benchmarks/rag-impact/results/discovery-file-range-probe.json`
- Tool: `benchmarks/rag-impact/tools/discovery_file_range_probe.py`
