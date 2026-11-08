# Discovery H6 — structural reference resolution (cap 500)

Status: **20/25 (80%) but NO-GO on resource/generalization. Implementation was
frequency-ranked observed-name expansion, not typed structural resolution; the
original label is corrected below.**
Date: 2026-11-08. Pre-registration:
`DISCOVERY-H6-PREREGISTRATION.md`.
Receipt: `results/discovery-h6-reference.json`.

## Result

| Measure | Value |
|---|---:|
| Coverage | **20/25 (80%)** |
| Macro | 0.875 |
| Cap | 500/case |
| New span | D02 C6 (`EmployeeDetail.cs`) |
| Still missing | D02 C4/C5, D10 C4, D12 S1 |
| p95 wall/case | **10.97 s** |
| CPU user+sys | 15.3 s |
| Peak RSS | 19.2 MiB |
| rchar | 1.248 GB (unreliable attribution; source/search not separately reconciled) |

- The +1 span is again incidental to generic observed `func_Employee*`
  references, not a direct `CoreDBContext`/`sp_UpdateOrgTotalEmployee` path.
- No H5 regression: H2/H5 evidence preserved.
- Hard independent evaluation was not run for H6; only H1/H2/H4 were measured
  on the hard set.
- Resource and generalization gates fail; no freeze.

## Artifact

- `benchmarks/rag-impact/tools/discovery_h6_reference.py`
- `benchmarks/rag-impact/results/discovery-h6-reference.json`

## Implementation correction (2026-11-08)

The committed H6 receipt was produced by `tools/discovery_h6_reference.py`,
which does **not** resolve typed structural references. It extracts candidate
names from observed source with regexes, ranks procedure-like names first, then
frequency, and takes the top six. It then runs exact search. This is a
frequency-ranked observed-name expansion, not a `ReferenceExpr` /
`DeclarationResolver` closure.

Consequences:

- The one new D02 C6 span is incidental to generic `func_Employee*` names.
- `CoreDBContext` / `sp_UpdateOrgTotalEmployee` were not resolved from typed
  receiver or selected SQL binding.
- The 20/25 result remains a valid measurement of the implementation, but it
  must not be cited as evidence that structural reference resolution works.

Historical receipts and pre-registration are unchanged.
