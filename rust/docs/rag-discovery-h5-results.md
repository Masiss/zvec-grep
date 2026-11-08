# Discovery H5 — deterministic relevance-aware ordering (cap 500)

Status: **19/25 = ceiling; deterministic; resource cost high. NO-GO as a
standalone Discovery gate.**
Date: 2026-11-08. Pre-registration + amendment:
`DISCOVERY-H5-PREREGISTRATION.md`.
Receipt: `results/discovery-h5-ordering.json`.

## Result

| Measure | Value |
|---|---:|
| Coverage | **19/25 (76%)** |
| Macro | 0.8611 |
| Cap | 500/case |
| Deterministic selected hashes | **identical across `PYTHONHASHSEED=1/2`** |
| p95 wall/case | 4.9–6.5 s (run variance) |
| CPU user+sys | 7.2–9.5 s total |
| Peak RSS (VmHWM) | ~22.7 MiB |
| Source bytes | 19.6 MB |
| Search output | 6.9 MB |
| rchar | 733.8 MB (does not reconcile with source+search; treat as unreliable I/O attribution) |

- H5 preserves all H1 evidence and reaches the H2 reorder ceiling **19/25**.
- H5 cannot reach 20/25 by construction: it only reorders the H2 pool.
- Missing: D02 C4/C5, D10 C4, D12 S1 (5 spans).
- Cost is above the lightweight target; p95 > 5 s in one run and CPU total is
  high for 12 cases. No Selection/top-10 in this pool.

## Artifact

- `benchmarks/rag-impact/tools/discovery_h5_ordering.py`
- `benchmarks/rag-impact/results/discovery-h5-ordering.json`
