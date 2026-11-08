# Hard independent development evaluation

Status: **frozen before probe; H1/H2 4/5, H4 5/5; still not a GO gate.**
Date: 2026-11-08. Cases:
`benchmarks/rag-impact/cases/dev-independent-hard.json`.
Receipt: `results/discovery-independent-hard.json`.

## Set

Five cases with natural-language queries that do not contain the exact target
identifiers; sources include AddressCommand, EmployeeDetailCommand and
EmployeeCommand. Disjoint from D01–D12; held-out untouched.

## Result (cap 500/case)

| Arm | Coverage | Macro |
|---|---:|---:|
| H1 | 4/5 | 0.80 |
| H2 bounded | 4/5 | 0.80 |
| H4 reference | 5/5 | 1.00 |

- Missing for H1/H2: H4 `MapCountryFromApiToDatabase` (AddressCommand 352–370).
  H4 recovers it via observed `Address`/mapping references, showing some
  cross-file generalization.
- H4 still costs ~7.3 s p95 wall on this set and has no CPU/PSS qualification.
- The easy independent set (`dev-independent.json`) was 5/5 for H1 too and is
  not discriminating.

## Interpretation

Hard independent set is more useful than the easy one, but 5 cases cannot
establish the 80% gate. H4’s 5/5 is not sufficient because old-dev coverage was
only 20/25 with one incidental span and resource cost is high. Independent eval
must grow and be frozen before any pool freeze.
