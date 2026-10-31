# Development tracks

Status index for active and planned work. This is intentionally under
`rust/docs/` so it does not duplicate the upstream product docs under `docs/`.

Current handoff: [`handoff-20261031.md`](handoff-20261031.md).

Updated: 2026-10-31. Frozen RC: `integration/upstream-rust` at
`c2347d5`, tag `rust-v0.3.0-rc.2` (code baseline `289674b`).

## Active / next

| Track | Status | Scope | Next action | Evidence / plan |
|---|---|---|---|---|
| **Daemon/CLI UX V2** | **UX0 implemented / UX1–UX4 proposed** | Read-only job visibility, manual index priority, explicit routing, cancellation, watcher scope | UX0: `--server jobs`, queued/running index display, protocol 16 JobView. Next: UX1 explicit routing/`--no-wait` | [`daemon-cli-ux-v2.md`](daemon-cli-ux-v2.md) |
| **Harness V2** | **P1 contract layer implemented / P0 and P2–P6 open** | Policy v1 + receipt/protocol readers, golden fixtures, M4 refactor, upstream retrieval as canonical | P1: `benchmarks/harness-v2/contracts/` and golden tests. Next: P0 provenance freeze or P2 containment primitives | [`harness-v2-refactor-plan.md`](harness-v2-refactor-plan.md), [`../../benchmarks/harness-v2/README.md`](../../benchmarks/harness-v2/README.md) |
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
