# Fork recovery — active checkpoint

Owner scope (2026-11-04): consolidate the user's V1 `work/zg-optimization`
and V2 `integration/upstream-rust` into one maintained fork branch, preserving
new work, commit/push the recovered source, then retire confusing old sources.
Original upstream refs/worktree, installed binaries/daemon and live indexes
must remain untouched. No benchmark campaign is authorized by this recovery.

## Baselines and preservation

- Target product baseline: V2 `c2347d5`, tag `rust-v0.3.0-rc.2`.
- Source of newer work: V1 `234749d` plus dirty/untracked changes.
- Main post-RC continuation window: 43 first-parent commits beginning `4f26942`.
- Recovery branch: `work/zg-recovered`.
- Private preservation: `/home/masis/.local/share/zvec-grep/source-recovery/20261104/`.
  Require its verified `COMPLETE` marker before any legacy source cleanup.
- Upstream (`upstream/*`, local `main` and `zvec-grep-upstream-baseline`) is
  comparison material and is not a merge target or cleanup target.

## Writer lane board

| Lane | Worktree / branch | Owned seam | Next gate | Handoff |
|---|---|---|---|---|
| Parent/integration | `zvec-grep-recovery` / `work/zg-recovered` | preservation, selected harness/archive docs, integration/review, final push and cleanup | preservation + source/test review | this file |
| UX | `zvec-grep-recovery-ux` / `recovery/ux` | CLI/daemon/protocol controls, routing, queue/timeouts, scan/consent regressions | focused CLI/daemon/protocol tests | child report + commit |
| Engine | `zvec-grep-recovery-engine` / `recovery/engine` | optional compact sidecar + safe clean-scan optimization on V2 FP16/layout; later stages only after handoff | engine focused failure/parity tests | child report + commit |
| RAG | `zvec-grep-recovery-rag` / `recovery/rag-harness` | source-only evidence API, R0/R1 artifacts and tests | source span/hash/gap tests | child report + commit |

Each lane owns a distinct worktree. No child pushes or changes any other
worktree. Parent is the only integrator. Native pin stays 0.8.0, canonical
index version/layout and production defaults stay unchanged unless a separately
reviewed recovery stage explicitly requires and documents an owner decision.
Never re-enable 0.8.0 outside-lock scalar optimize or deferred maintenance.
V1 research/native receipts are historical evidence, not V2 qualification.

## Pending stages

1. Preserve dirty source, native source and evidence; archive history safely.
2. Recover UX, optional engine optimizations and source-only RAG in isolation.
3. Preserve remaining native/crash-atomic/M4 work as clearly non-production
   source/patches; assess separate V2-compatible runtime recovery.
4. Integrate, run proportional verification, review every port against V2.
5. Push only to the owner's `origin`, verify published refs.
6. Retire old personal branches/worktrees only after preservation and push;
   leave comparison/upstream and running installations alone.

This checkpoint is not a completion or production-readiness claim.
