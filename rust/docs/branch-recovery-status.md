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
  `COMPLETE` verified: 11,832 preserved entries with source/backup hashes;
  nested native submodule refs and dirty patches are included. The initial two
  preservation failures are retained, not erased. Generated data/private files
  are not staged for publication.
- Upstream (`upstream/*`, local `main` and `zvec-grep-upstream-baseline`) is
  comparison material and is not a merge target or cleanup target.

## Writer lane board

| Lane | Worktree / branch | Owned seam | Next gate | Handoff |
|---|---|---|---|---|
| Parent/integration | `zvec-grep-recovery` / `work/zg-recovered` | preservation, selected harness/archive docs, integration/review, final push and cleanup | preservation + source/test review | this file |
| UX | `zvec-grep-recovery-ux` / `recovery/ux` | CLI/daemon/protocol controls, routing, queue/timeouts, scan/consent regressions | implementing full seam; protocol-only `bfeb5eb` NOT accepted alone | Luna resumed `4ec9a783-3fe4-442c-bacd-8d2061a2470a` |
| Engine | `zvec-grep-recovery-engine` / `recovery/engine` | optional compact sidecar + safe clean-scan on V2 FP16/layout | compact `9f16951` committed, malformed-sidecar and no-op review pending | Luna resumed `2c76562d-37f9-4802-9c19-0ca70ce6a879` |
| RAG | temporary worktree retired / `recovery/rag-harness` | source-only evidence API, R0/R1 artifacts and tests | DONE reviewed port + coordinate fix, 10 focused tests PASS | integrated `a8048a0`, `cb848dc` |

Each lane owns a distinct worktree. No child pushes or changes any other
worktree. Parent is the only integrator. Native pin stays 0.8.0, canonical
index version/layout and production defaults stay unchanged unless a separately
reviewed recovery stage explicitly requires and documents an owner decision.
Never re-enable 0.8.0 outside-lock scalar optimize or deferred maintenance.
V1 research/native receipts are historical evidence, not V2 qualification.

## Current implementation and evidence

- Legacy snapshot committed `13a3555`; explicitly staged 94 source/doc/test
  files, no bulk untracked fixtures or local secret files. Native and nested
  third-party modifications are preserved as non-applied patches.
- Archive tags pushed and verified on **origin only**:
  `archive/zg-v1-before-recovery-20261104` and
  `archive/zg-v2-release-20261104`. Upstream/main refs unchanged.
- Recovered tracked Harness V2, standalone M4 source/evidence and archived V1
  plans in `0129cbc`. Historical raw receipts and patches keep their bytes;
  `.gitattributes` protects them from normalization. No benchmark launched.
- Offline harness checks PASS: contracts 23, host 10, measurement 5,
  lifecycle split/refusal 5. Initial missing-guidance errors were corrected
  by restoring repository instructions; raw evidence was not rewritten.
- RAG source API + current V1 artifacts integrated with a reviewer fix for
  forged line/column coordinates and blank projects. Focused evidence tests:
  10 passed, 0 failed. Existing V1 pilot receipts remain historical; R0 is
  incomplete and candidate quality/performance targets are not yet accepted.
- `research/legacy-rust-v1/` retains all old post-RC Rust/runtime work plus the
  exact pre-recovery dirty patch (`a89226f`). It is NOT applied wholesale.
  Native patches remain `research/legacy-native-0.7.2/`, not current .8 runtime.
- UX recovery deliberately bumps daemon wire version 1 -> 2 for the new job
  contract, failing closed with actionable help. Do not auto-restart a legacy
  daemon or silently fall back to a second direct writer. Index version stays 2.
- Use shared Cargo cache while recovering. Host-local Cargo configuration is
  excluded via `.git/info/exclude`; it must never be published. Disk was under
  pressure due duplicated language-server build caches. The finished RAG lane
  worktree/cache was removed after reviewed commits were integrated. Do NOT
  remove V1's build cache: a running `zg` still references deleted mappings
  there; the installation/process remains untouched.

## Remaining acceptance criteria / exact next work

1. Finish UX and engine writers; inspect actual source/commits, fix accepted
   findings, integrate into `work/zg-recovered` as parent-only writer.
2. Run proportional final engine/daemon/CLI/protocol/MCP/host/FP16 tests, fmt,
   clippy/build as appropriate against the correct .8 library, not installed W4.
3. Reconcile feature matrix: no old-layout/native/M4 production import; retain
   those sources explicitly as unqualified archives. Never claim old PASS as
   qualification of the recovered runtime.
4. Push canonical branch to owner origin and verify exact published commit;
   set a clear single maintained branch without modifying comparison/upstream.
5. Only then move preserved residual V1 untracked material out of active source,
   switch the main worktree to canonical recovery, retire extra personal/lane
   worktrees and branches, and update source-location guidance. No blanket clean,
   force push, live-index rebuild, installed binary or daemon operation.

This checkpoint is not completion, source-port parity, or production readiness.
