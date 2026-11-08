# H4 reference-aware Discovery result

Status: **20/25 micro (80%) but NO-GO on resource/generalization gate.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-REFERENCE-PREREGISTRATION.md`.
Receipt: `benchmarks/rag-impact/results/discovery-reference-probe.json`.

## Coverage

- H2: 19/25 (76%).
- H4: **20/25 (80%)**, macro 0.875; file coverage 21/25.
- Only one new span (D02 `EmployeeDetail.cs`), likely incidental to generic
  observed `func_Employee*` references.
- Still missing (5):
  - D02 C4 17–20 — query formulation; exact `AddFromAssembly`/context probe can
    reach it, but no observed reference path selected it.
  - D02 C4 41–55, D02 C5 — file discovery; files remain absent from the pool.
  - D10 C4 — query formulation; `CoreDBContext` is not selected from observed
    references.
  - D12 S1 — query formulation; `sp_UpdateOrgTotalEmployee` is not selected.
  H4 did not invent a relation for these; no observed reference path was found
  that both starts in cited bytes and reaches the target.
- H2-covered spans preserved.

## Cost / provenance

- 144 exact queries total (~12/case), **97.3 s** wall; H2 files/bytes reused.
- Observed references were mostly generic `func_Employee*` names; no direct
  evidence path to the missing `CoreDBContext`, `Employee`, or
  `sp_UpdateOrgTotalEmployee` definitions.
- CPU/PSS not separately qualified for H4; wall alone is already ~8.1 s/case,
  above the lightweight Discovery resource expectation.
- 80% is reached by one incidental span with high query cost; this is not a
  robust reference-aware path.

## Disposition

H4 micro gate number is met, but the **resource/generalization gate fails**.
Do not freeze the pool, do not proceed to Selection/Expansion. Missing spans
without an observed reference path remain file/query-discovery gaps; do not
force traversal.

No model/index/held-out/target change.
