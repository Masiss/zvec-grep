# H2 bounded cap — qualification result

Status: **CAP 500 FAILS COVERAGE; H2 full pool is over-expanded.**
Date: 2026-11-08. Tool:
`benchmarks/rag-impact/tools/discovery_h2_bounded.py`.
Receipt: `results/discovery-h2-bounded.json`.

## Result

| Measure | Full H2 | Cap 500 |
|---|---:|---:|
| Coverage | 19/25 (76%) | **15/25 (60%)** |
| Pool units | 15,683 | 6,000 (500/case) |
| Added ranges | 13,525 | 3,842 |
| Provenance | method 11,632; type 1,893 | method 3,275; type 567 |
| p95 wall/case | 5,314 ms | **4,052 ms** |
| p95 CPU/case | 1.00 s | **0.44 s** |
| Peak PSS | 13,367 KiB | **11,955 KiB** |
| I/O rchar | 736.8 MB | 722.9 MB |

- The cap reduces cost but **loses D05, one D06/D07 span, and both D12
  spans**; 15/25 is below H2 full and far below the 80% gate.
- The earlier 19/25 “coverage@500” from the full-pool insertion order is **not
  a deployable cap policy**: it relied on the order of the full pool. The
  bounded policy is the correct 500-range result.
- No gold in the cap policy; no Selection/top-10.

## Interpretation

H2 needs either a larger pool or a coverage-aware ordering; a naive 500-range
cap is not enough. H2 is **NO-GO** as a bounded Discovery policy.
