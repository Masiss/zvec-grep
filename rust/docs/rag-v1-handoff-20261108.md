# RAG v1.0 handoff — 2026-11-08 (after H7 E1R)

Branch: `work/zg-recovered`
HEAD: `9328766` (docs); experiment code `416a312` (`feat(rag): H7 compact declaration index resource refactor`)
Worktrees:
- main: `/home/masis/src/zvec-grep`
- E1: `/home/masis/src/worktrees/zvec-grep/h7-e1-20261108`
- E1R: `/home/masis/src/worktrees/zvec-grep/h7-resource-20261108`

External build target: `/home/masis/.cache/zvec-grep/rust-target-h7-resource-20261108`
No push/release. Protected `rust/target` was not touched.

## 1. Status

| Layer | Status |
|---|---|
| H7 E1 typed reference closure | **historical NO-GO**, receipt unchanged |
| H7 E1R compact declaration index | **NO-GO (resource)**; coverage/independent PASS |
| Discovery | **NO-GO** |
| Selection | **FROZEN** |
| Expansion | **FROZEN** (gap prototype archived only) |
| R1 delivery | **PARTIAL**; independent qualification continues |
| RAG v1.0 | **NOT GO** |

H7 E2 is **not run**. No cap tuning, no default promotion, no corpus/gold/
held-out/target changes.

## 2. H7 E1R result

Pre-registration:
`benchmarks/rag-impact/DISCOVERY-H7-RESOURCE-PREREGISTRATION.md`
Receipt: `results/h7-e1r-compact.json`
Pre-profile: `results/h7-resource-preprofile.json`
Results note: `rust/docs/rag-discovery-h7-resource-results.md`

Coverage:

| Budget | E1R on | refs-off |
|---|---:|---:|
| @10 | 8/25 | 8/25 |
| @50 | 14/25 | 12/25 |
| @100 | 15/25 | 14/25 |
| @250 | 20/25 | 16/25 |
| @500 | **23/25** | 16/25 |

- Independent easy **5/5**, hard **5/5**.
- Determinism: two runs, identical per-case hashes.
- D02 5/6: C6 `EmployeeDetail.cs` missing.
- D12 1/2: S1 `sp_UpdateOrgTotalEmployee.sql` missing.
- D09: off rank 41 → on rank 364; 323 closure items; @100/@250 lost,
  recovered only @500.
- D07: @100/@250 regressions, recovered @500.

Resource:

| Metric | Value |
|---|---:|
| Declaration build | 2,628 ms |
| Declaration PSS | 53,247 KiB |
| Anchor parse mean / p95 | 285 ms / 354 ms |
| Total wall | 6.46 s |
| Per-case on p50 / p95 | 15.7 ms / 24.7 ms |
| Peak RSS | **74,028 KiB (~72.3 MiB)** |
| PSS end | **69,387 KiB (~67.8 MiB)** |
| Fixed gate | **FAIL 64 MiB** |

- Improvement from E1: 393,368 → 74,028 KiB peak RSS.
- Pre-profile found the full path duplicated `DbSetFact` per file: P3/P2/P1
  had 38,610/99,684/53,118 entries vs **234 unique**.
- Remaining cost: declaration index/parser retention + anchor full facts.
- I/O: declaration phase rchar 982,030 B; initial 5,400,637 B source load
  happened before the counter, so total I/O is **not reconciled**.
- Allocation count not instrumented.
- Gate result: coverage/independent/determinism/provenance PASS; **PSS/RSS and
  I/O FAIL**.

## 3. What changed in the current commit

`416a312`:
- `ProjectDeclarations` and `build_declaration_index` in `csharp_facts.rs`:
  types, fields, unique DbSets, parsed one file at a time.
- `parse_anchor_facts`: full methods/assignments/invocations/saves only for
  anchor files; declaration scope passed separately.
- `expand_with_declarations` in `candidate.rs`: closure reads compact
  declarations; no full `ProjectFacts` merge or per-case declaration clones.
- Existing full path and historical receipts remain available.
- No new parser/index/LSP; typed closure/provenance semantics retained.

Commits:
- `d95cd40` — resource refactor pre-registration
- `416a312` — compact declaration index + receipts
- `e7eac77` — E1 forensics
- `f62b830` — H7 E1
- `6be6420` — H7 E1 pre-registration
- `88b91d7` — H6 label correction

## 4. Exact next state

Do not continue H7 E2. Do not tune closure caps against gold. E1R remains
NO-GO until a resource change is separately authorized and measured against the
same fixed gate.

If continuing Discovery, owner must authorize a **new bounded resource
experiment**; the only evidence-backed direction is to reduce the remaining
declaration/anchor retained memory, not to change coverage gates. Do not use
the previous compact result as a GO.

Independent R1 work can continue now:
- CLI/MCP/service integration
- cache/version invalidation
- p95/PSS/concurrency load
- source admission for generated/results paths
- PII/secret classification, auth/audit
- independent validation and release/rollback

## 5. Evidence index

- `rust/docs/rag-v1-backlog.md`
- `rust/docs/rag-discovery-h7-e1-results.md`
- `rust/docs/rag-discovery-h7-e1-forensics.md`
- `rust/docs/rag-discovery-h7-resource-results.md`
- `benchmarks/rag-impact/results/discovery-h7-e1.json` (historical, immutable)
- `benchmarks/rag-impact/results/discovery-h7-e1-forensics.json`
- `benchmarks/rag-impact/results/h7-e1r-compact.json`
- `benchmarks/rag-impact/results/h7-resource-preprofile.json`
- `benchmarks/rag-impact/results/h7-anchors-dev-independent*.json`

## 6. Build/containment recipe used

External `CARGO_TARGET_DIR` under `~/.cache/zvec-grep/`; bwrap with read-only
host, writable external target and private llama runtime cache; offline Cargo;
`LD_LIBRARY_PATH`/`ZVEC_LIB_DIR` pointing at the rollback native library.
Every build in this session used external target; protected `rust/target` was
not used.

## 7. Historical/open caveats

- Protected `rust/target` incident evidence remains at
  `~/.local/share/zvec-grep/r4-gap-prototype/parent-containment-review/`.
  No restoration is claimed; prior bytes were not captured.
- H6 is correctly described as frequency-ranked observed-name search in the
  backlog/results note; its historical receipt was not rewritten.
- E1/E1R abbreviations are internal; do not treat either as RAG v1.0 GO.
