# H7 E1 — fact-layer typed reference closure

Status: **coverage/independent PASS; resource FAIL => E1 NO-GO. E2 not run.**
Date: 2026-11-08. Base `88b91d7`, branch `work/h7-e1`.
Pre-registration: `benchmarks/rag-impact/DISCOVERY-H7-PREREGISTRATION.md`.
Receipt: `benchmarks/rag-impact/results/discovery-h7-e1.json`.

## Implementation

- Removed the product `CoreDBContext` special cases from `candidate.rs` and
  `csharp_facts.rs`.
- Added `path`, `line_start`, `line_end` to `DbSetFact`.
- Generic closure from seed-method `AssignmentFact` / `InvocationFact` /
  `SaveFact` receiver types + observed `DbSetFact` members + selected
  `SqlBinding`.
- Closure is bounded by the existing `budget` and emits provenance in
  `reason`: `resolved|anchor=...|expr=...|declaration=...|target=...|rule=...`.
- `expand_with_closure(..., false)` is the refs-off ablation.
- No new Python parser; no new index/model/LSP.

## Coverage

| Budget | E1 closure | refs-off |
|---|---:|---:|
| @10 | 7/25 | 7/25 |
| @50 | 13/25 | 12/25 |
| @100 | 15/25 | 14/25 |
| @250 | 20/25 | 20/25 |
| @500 | **23/25** | 20/25 |

- +3 spans in D02; D12 S1 still missing; one D02 span still missing.
- D09 gets no gold at any budget; D07 loses @100/@250 vs refs-off but recovers
  @500.
- Independent: easy **5/5**, hard **5/5** using H1 anchors.
- Determinism: two fresh runs produced identical per-case closure hashes and
  coverage.

## Resource

- Peak RSS **392,500 KiB (~383 MiB)** — fails 64 MiB gate.
- CPU user 11.76 s, sys 0.15 s; wall 11.97 s for 12 cases (~1 s/case).
- Search calls: 6/case (B0/H1 anchors); closure uses project facts/catalog, no
  broad additional search.
- Per-case p95, PSS sampling and I/O reconciliation were not instrumented in
  this harness; cold/warm parse not separated.
- Candidate precision is low: 500 ranges/case, hundreds of receiver-type
  closure items; range precision 23/(12*500) ≈ 0.38% at @500.

## Verdict

E1 meets the pre-registered coverage and hard-independent targets, but fails
the resource gate (RSS >64 MiB) and does not provide p95/I/O evidence. Therefore
**E1 FAIL / NO-GO**; do not run E2. Selection/Expansion remain frozen. No
RAG v1.0 GO.

## Limits

- Dev anchors are B0/H1 calls, not the H5 selected-unit file.
- Independent anchors are H1 pool units generated before measurement.
- Merged P1/P2/P3 facts are used for cross-project resolution; production call
  sites may expand per project.
- Unresolved histogram is derived from missing spans, not emitted per reason by
  the harness.
