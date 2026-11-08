# Independent development evaluation — result

Status: **5/5 coverage on an easy independent set; not a discriminating gate.**
Date: 2026-11-08. Cases:
`benchmarks/rag-impact/cases/dev-independent.json` (frozen before probe).
Receipt: `results/discovery-independent-probe.json`.

## Set

Five cases disjoint from D01–D12, sourced from:
- EmployeeCommand `EmployeeCreate` and `UpdateTrangThaiCongViecEmployee`;
- AddressCommand `NationCommand` and `MapNationFromApiToDatabase`;
- EmployeeDetailCommand `EmployeeDetailCommand`.

Queries are natural-language; no gold identifiers are used by policies. Held-out
is untouched. Cases were frozen before the probe; this note records the protocol
after the run (the probe was a checkpoint run).

## Result (cap 500 ranges/case)

| Arm | Coverage | Macro |
|---|---:|---:|
| H1 depth-50 + symbols | 5/5 | 1.000 |
| H2 bounded | 5/5 | 1.000 |
| H4 references | 5/5 | 1.000 |

Resource: peak PSS 11,966 KiB; CPU 3.32 s user+sys; p95 case wall 7,055 ms;
I/O rchar 478.4 MB.

## Interpretation

The set is independent but too easy: H1 already covers all five spans, so H2/H4
have no marginal signal. It does **not** demonstrate generalization to the 80%
gate or to diverse query formulation. A harder independent set is still needed;
the current result cannot promote Discovery.
