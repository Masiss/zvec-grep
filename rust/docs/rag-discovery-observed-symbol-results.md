# Discovery H3 — observed-symbol expansion result

Status: **NO GAIN; Discovery remains 19/25 (76%).**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/DISCOVERY-OBSERVED-SYMBOL-PREREGISTRATION.md`.
Dev-only; no held-out.

## Result

- Started from H2 pool (19/25).
- Extracted rare observed symbols from pool files and ran exact FTS/rg probes.
- Coverage remained **19/25**; no new gold span. H2 coverage preserved.
- Selected identifiers were generic/rare symbols (`AKKA`, `AccountManagement`,
  `ActiveCode`, etc.), not the missing `CoreDBContext`, `Employee`,
  `EmployeeDetail`, or `sp_UpdateOrgTotalEmployee`.
- This is the third independent Discovery hypothesis. No further Discovery
  tuning is authorized; report NO-GO.

## Artifact

- `benchmarks/rag-impact/results/discovery-observed-symbol-probe.json`
- Tool: `benchmarks/rag-impact/tools/discovery_observed_symbol_probe.py`
