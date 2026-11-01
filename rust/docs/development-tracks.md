# Development tracks

Status index for active and planned work. This is intentionally under
`rust/docs/` so it does not duplicate the upstream product docs under `docs/`.

Current handoff: [`handoff-20261101-deep-integrity-w3.md`](handoff-20261101-deep-integrity-w3.md).

Updated: 2026-11-01. Frozen RC: `integration/upstream-rust` at
`c2347d5`, tag `rust-v0.3.0-rc.2` (code baseline `289674b`).

## Active / next

| Track | Status | Scope | Next action | Evidence / plan |
|---|---|---|---|---|
| **Daemon/CLI UX V2** | **Feature-complete; closed** | Read-only job visibility, auto routing, manual/watch lanes, queue position/cancel, bounded waits, watcher scope | UX0–UX4 implemented and baseline recorded. No UX-layer optimization justified. See closure limitations and performance baseline | [`ux-v2-closure.md`](ux-v2-closure.md), [`ux-v2-performance-baseline.md`](ux-v2-performance-baseline.md) |
| **Index/Finalize Qualification** | **INCONCLUSIVE** | Decide whether M3 dirty finalize is good enough or #10 is warranted | Preflight passed with gaps; frozen fixture was too expensive to run full 45-cell matrix. Need new preregistration for snapshot reuse or smaller budget | [`index-finalize-qualification-v1-result.md`](index-finalize-qualification-v1-result.md), [`index-finalize-preregistration.md`](index-finalize-preregistration.md) |
| **Direct-path Startup Optimization** | **Implemented; keep** | Reduce warm direct no-op startup latency/memory without changing correctness or searchable state | Clean-scan sidecar fast path is in place; measure cold page-cache and multi-workspace next if authorized | [`direct-path-startup-optimization.md`](direct-path-startup-optimization.md) |
| **Storage Footprint Optimization** | **Implemented; keep** | Reduce on-disk index size without changing correctness, quality, stability, or searchable index format | Compact reconciliation sidecars are in place; remaining 88% is zvec vector/entity storage. Further work needs a new authorization/format decision | [`storage-footprint-optimization.md`](storage-footprint-optimization.md) |
| **Harness V2** | **P0–P6 implemented** | Policy v1 + receipt/protocol readers, golden fixtures, host/measurement primitives, complete Rust lifecycle split, operation registry/adapters, upstream retrieval as canonical | Next: none; Harness V2 closure complete | [`harness-v2-refactor-plan.md`](harness-v2-refactor-plan.md), [`../../benchmarks/harness-v2/README.md`](../../benchmarks/harness-v2/README.md) |
| **Native deep integrity** | **W4 PASS; closed; installed locally** | Native checksum writer, crash-atomic incremental generation, verifier surface, operational verification | Complete. `zg --verify` + light seal check installed with the W4 native library. See rollout/rollback and residual risks | [`deep-storage-integrity-w4.md`](deep-storage-integrity-w4.md), [`handoff-20261101-deep-integrity-w4.md`](handoff-20261101-deep-integrity-w4.md) |
| **RAG retrieval quality** | **R0/R1 starting** | Local retrieval benchmark, HRM2-style task pilot, failure slices, retrieval-only work | Read `docs/rag/README.md` and `implementation-plan.md`; run R0/R1 without opening a new optimization track | [`rag/README.md`](rag/README.md), [`rag/implementation-plan.md`](rag/implementation-plan.md) |
| **Production qualification** | **Not ready** | 421-file/production benchmark, release gates beyond RC | Owner authorization + benchmark policy | `/home/masis/src/zvec-grep-integration/docs/release/RELEASE-READINESS.md` |
| **M3-B scalar outside-lock** | **Blocked** | zvec 0.8.0 rejects readers/writers during optimize | Reopen only with a native version/API that passes the gate | `/home/masis/src/zvec-grep-integration/docs/release/M3B-HANDLE-MAINTENANCE-FEASIBILITY.md` |
| **#10 deferred maintenance** | **Not implemented** | Deferred optimize policy, storage growth, recall/latency | Owner policy + benchmark; visibility evidence exists | `/home/masis/src/zvec-grep-integration/docs/release/RELEASE-READINESS.md` §8 |

## Frozen / historical

- RC `0.3.0-rc.2`: do not change for the tracks above; open a new branch or
  worktree for implementation.
- M4 P3: partial/closed; P3 query-lock runners are research-only and should be
  archived under Harness V2, not extended.
- Fork retrieval copy: stale versus upstream `benchmarks/zg-retrieval`; use
  upstream as canonical.

## Conventions

- Put implementation/refactor plans in `rust/docs/`; keep M4 policy/evidence in
  `research/m4-native/`; keep release evidence in the RC worktree
  `/home/masis/src/zvec-grep-integration/docs/release/`.
- Do not put new plans at repository root or in `docs/` unless they are upstream
  product documentation.
- Every track needs: status, scope, non-goals, acceptance criteria, evidence
  path, and a frozen-baseline note.
