# H7 E1 forensics — resource and correctness attribution

Status: **E1 remains NO-GO. Forensics only; no optimization implemented, no
gate changed, no E2.**
Date: 2026-11-08. Base `f62b830`, worktree `h7-e1-20261108`.
Receipt: `benchmarks/rag-impact/results/discovery-h7-e1-forensics.json`.
Historical `discovery-h7-e1.json` is unchanged.

## 1. Phase profile (one process, warm page cache)

| Phase | Time | PSS |
|---|---:|---:|
| Parse P1/P2/P3 facts (817 C# files, 5.40 MB) | **5,844 ms** | **346,941 KiB** |
| ProjectFacts merge/clone | **78 ms** | **+39,060 KiB** (386,001 KiB) |
| Per-case expansion + closure (12 cases) | ~54 ms/case incremental | end 389,497 KiB |
| Peak RSS | — | **393,368 KiB (~384 MiB)** |

- The 383 MiB is **not closure**. The baseline fact layer is ~347 MiB after
  parsing; merging full ProjectFacts adds ~38 MiB; closure adds ~3.2 MiB.
- `parse_build_ms` 5.844 s; total phase 11.36 s. Per-case wall off p50 195 ms,
  p95 248 ms; on p50 255 ms, p95 315 ms.
- Cold/warm separation is limited: three process runs gave 5.83–7.04 s parse;
  source cache was not dropped. Do not claim a cold-cache number.

## 2. I/O and search scope

- Build rchar **5,407,282 bytes**; manifest source bytes **5,400,637**;
  difference **6,645 bytes (~0.12%)**. This reconciles.
- Per-case closure rchar is ~104 bytes (page-cache reads only).
- E1 test performs **no search**; the 6/case anchor calls are upstream B0/H1.
  Wall time covers project parse + merge + refs-off/on expansion, not search.

## 3. Closure diagnostics (code-level)

Across 12 cases:

- closure items emitted: **1,498**
- resolved attempts: **8,454**
- unresolved:
  - `invocation_receiver_type_missing`: **6,947**
  - `type_decl_missing`: **461**
  - `sql_binding_not_resolved`: **105**

The large `invocation_receiver_type_missing` count is the dominant unresolved
reason; it is not visible in coverage because most do not touch gold spans.

## 4. Coverage attribution @500

| Case | Gold | On | Off | On ranks per gold span | Note |
|---|---:|---:|---:|---|---|
| D01 | 2 | 2 | 2 | 19, 2 | unchanged |
| D02 | 6 | 5 | 2 | 166, 166, 40, 40, 45, **None** | C4 17–20/41–55 + C5; **C6 EmployeeDetail missing** |
| D03 | 1 | 1 | 1 | 196 | pushed down |
| D04 | 1 | 1 | 1 | 4 | unchanged |
| D05 | 1 | 1 | 1 | 238 | pushed down |
| D06 | 3 | 3 | 3 | 3, 3, 55 | @50 drops; recovered @500 |
| D07 | 3 | 3 | 3 | 175, 313, 460 | @100/250 regressions; recovered @500 |
| D08 | 2 | 2 | 2 | 0, 3 | unchanged |
| D09 | 1 | 1 | 1 | **367** (off **44**) | **ranking displacement, not missing path** |
| D10 | 2 | 2 | 2 | 51, 36 | C4 recovered |
| D11 | 1 | 1 | 1 | 1 | unchanged |
| D12 | 2 | 1 | 1 | 43, **None** | **S1 `sp_UpdateOrgTotalEmployee.sql` missing** |

- Totals @500: on 23/25, off 20/25.
- D02 gain: 2→5; C4/C5 found; C6 not.
- D12 S1: no selected SQL binding from the anchor, so not emitted.
- D09: off rank 44, on rank 367; closure inserts 323 receiver-type items above
  it. At @100 and @250 the gold is lost; at @500 it is recovered.
- D07 uses 338 receiver-type closure items; its ranks move from 119/67/122 to
  175/313/460, causing @100/@250 regressions.
- D06 third span moves rank 40→55, causing the @50 regression.

## 5. Cost-reduction options (not implemented)

### Option A — seed-file full parse + declaration-only reference index

- Full method/assignment/invocation/save facts only for anchor files in P1.
- P2/P3 get a compact declaration index: types, fields, DbSets, selected SQL
  definitions; no full FileFacts/method graph.
- Predicted: remove most of the 347 MiB parse baseline and the 38 MiB full
  ProjectFacts merge; target materially below 64 MiB (must be measured).
- Coverage prediction: 23/25 if declaration coverage is complete; D02 C6 and
  D12 S1 still depend on observed references, so may remain missing.
- Tradeoff: no method traversal in reference projects; requires a compact
  scope/candidate API. This is the only option that addresses the resource gate.

### Option B — anchor-bounded closure with receiver-type cap

- Top 2 anchors; max ~8 receiver types/anchor; dedupe; keep observed DbSet
  element and selected SQL.
- Predicted: restore D09 rank 44, remove D07/D06 budget regressions; coverage
  likely 22–23/25.
- Resource: saves only ~3 MiB PSS and ~54 ms/case; **does not fix the 347 MiB
  baseline**. It is a correctness/pool-churn fix, not a resource fix.
- Tradeoff: may lose D02 +3 if the cap excludes a needed receiver type.

No option is implemented; gates unchanged.

## 6. Verdict

- E1 coverage/independent/determinism evidence stands.
- **E1 resource gate still FAIL**; p95/cold/I/O partially measured, allocations
  not instrumented.
- **E1 NO-GO; E2 not run.** Selection/Expansion frozen. R1 qualification
  continues independently; no RAG v1.0 GO.
