# H7 E1R — compact declaration index result

Status: **NO-GO. Resource gate failed; E2 not run.**
Date: 2026-11-08. Base `e7eac77`, branch `work/h7-resource`.
Pre-registration: `benchmarks/rag-impact/DISCOVERY-H7-RESOURCE-PREREGISTRATION.md`.
Pre-profile: `results/h7-resource-preprofile.json`.
Receipt: `results/h7-e1r-compact.json`.
Historical E1 receipt (`discovery-h7-e1.json`) unchanged.

## 1. Pre-change profile (full path)

| Project | Files | Types | Methods | Fields | DbSets | Assignments | Invocations | Saves | Parse | PSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P3 | 164 | 254 | 3 | 4,492 | **38,610** | 0 | 3 | 0 | 404 ms | 69,006 KiB |
| P2 | 426 | 832 | 1,216 | 3,561 | **99,684** | 3,141 | 15,193 | 525 | 3,181 ms | 188,474 KiB |
| P1 | 227 | 286 | 2,051 | 636 | **53,118** | 2,026 | 12,883 | 108 | 2,625 ms | 345,678 KiB |

- `DbSetFact` counts are duplicated: `parse_file_with_scope` clones the whole
  project scope into every `FileFacts`; aggregation multiplies it per file.
  P3/P2/P1 have ~38k/99k/53k entries instead of ~234 unique.
- This is the main full-path memory driver, not closure.

## 2. Refactor

- Added `ProjectDeclarations` + `build_declaration_index` (types, fields, unique
  DbSets) parsed one file at a time.
- Added `parse_anchor_facts`: full methods/assignments/invocations/saves only
  for anchor files; project-scoped declarations are passed separately via
  `expand_with_declarations`.
- Removed full `ProjectFacts` merge and per-case declaration clones.
- Existing closure/provenance semantics retained; no new parser/index/model.
- Declaration index: 1,372 types, 8,689 fields, **234 DbSets**.

## 3. Coverage

| Budget | E1R on | refs-off |
|---|---:|---:|
| @10 | 8/25 | 8/25 |
| @50 | 14/25 | 12/25 |
| @100 | 15/25 | 14/25 |
| @250 | 20/25 | 16/25 |
| @500 | **23/25** | 16/25 |

- Independent easy **5/5**, hard **5/5**.
- Determinism: two runs have identical per-case hashes and coverage.
- D02: 5/6; C6 `EmployeeDetail.cs` still missing.
- D12: 1/2; S1 `sp_UpdateOrgTotalEmployee.sql` still missing.
- D09: off rank 41, on rank 364; 323 closure items; @100/@250 lost; @500
  recovered. Pool churn remains.
- D07 still loses @100/@250, recovers @500.

## 4. Resource

| Measure | Value |
|---|---:|
| Declaration build | 2,628 ms |
| Declaration PSS | 53,247 KiB |
| Anchor parse mean / p95 | 285 ms / 354 ms |
| Total wall | 6.46 s |
| Per-case on p50 / p95 | 15.7 ms / 24.7 ms |
| Peak RSS | **74,028 KiB (~72.3 MiB)** |
| PSS end | **69,387 KiB (~67.8 MiB)** |
| Gate | **FAIL (65,536 KiB / 64 MiB)** |

- Improvement from E1: peak RSS 393,368 → 74,028 KiB; declaration duplication
  removed (234 DbSets instead of ~100k).
- Still over by ~8.5 MiB peak RSS. Remaining cost is declaration index/parser
  retention plus anchor full facts.
- I/O: declaration phase rchar 982,030 B; initial 5,400,637 B source load
  happened before the phase counter, so total I/O is **not reconciled**; gate
  remains unmet.
- Allocation count not instrumented.

## 5. Gate

| Gate | Result |
|---|---|
| Coverage @500 ≥23 | PASS 23/25 |
| Easy independent 5/5 | PASS |
| Hard independent ≥4/5 | PASS 5/5 |
| Determinism | PASS |
| Provenance/status | PASS (closure reason template; unresolved histogram emitted) |
| Peak PSS/RSS ≤64 MiB | **FAIL 67.8/72.3 MiB** |
| I/O reconciled | **FAIL/partial** |
| p95 ≤5 s/case | PASS per-case (parse is one-time) |

**E1R: NO-GO.** E2 not run; no cap tuning; Selection/Expansion frozen.
