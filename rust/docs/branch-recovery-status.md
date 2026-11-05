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
| UX | `zvec-grep-recovery-ux` / `recovery/ux` | CLI/daemon/protocol controls, routing, queue/timeouts, scan/consent regressions | runtime reviewed/integrated as `3786d16` + `3d2bd96`; stale protocol integration fixtures being corrected | latest Luna `690d7923-836f-47b5-966a-2fd6876e6441` |
| Engine | `zvec-grep-recovery-engine` / `recovery/engine` | optional compact sidecar + safe clean-scan on V2 FP16/layout | DONE reviewed/integrated `4ec2b73` + `f022b87` + `f24e33e`; portable test fix `5cc9cb9` | final child `4dc6ae58-0434-439e-90d2-932d29f6a487` completed |
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

## Active continuation — 2026-11-05

- Both previous Luna runs stopped on provider usage limits, not completed work.
  Owner explicitly authorized continuation after usage returned. Same retained
  sessions resumed once; latest IDs are in the lane board. Engine dirty
  `manifest.rs` robustness patch and UX ~1,473-line dirty port/new files remain
  intact; do not restart either port from scratch.
- Owner also flagged critically full disk (~233 MB free). Verified no user
  executable, cwd, mmap or FD referenced the two lane `rust/target` directories
  or shared `target/debug/incremental`; no compiler was active. Removed only
  those rebuildable caches: **8,899,952,640 bytes recovered**, ~8.6 GiB free.
  V1 target, both running `zg` processes, source, backups and indexes untouched.
- Host-only excluded Cargo config now exists at both lane roots and their
  `rust/.cargo/` directories: shared integration target, jobs=2, incremental=false.
  Explicit CARGO_TARGET_DIR/CARGO_BUILD_JOBS=2/CARGO_INCREMENTAL=0 remains mandatory
  for validation. Do not recreate lane targets or `cargo clean`; check disk
  before new builds and do not start them below 4 GiB free.
- Separately owner-authorized bounded Laya RAM diagnostic completed before the
  interruption: pilot64 and source-only2,714 files both passed, no retries/cap
  hits/file failures. Exact `zg` peak RSS: 485,056 / 1,310,944 KiB; larger run
  mostly anonymous heap, not merely file mmap. No >3 GB reproduction or leak
  attribution claimed. See `index-memory-diagnostic-result-20261104.md` and
  preregistration. Raw evidence remains private; do not rerun the index.
- Temporary Cargo measurement hold is RELEASED. Shared-cache reuse is working
  (no private lane targets recreated), but new validation artifacts reduced free
  disk to ~4.7 GiB; check the 4 GiB floor before NEW builds.
- UX initial handoff reports focused tests passed (CLI180, daemon132, protocol7,
  scan1, binary6), not release/clippy qualification. Parent identified three
  concrete correctness issues: root-inferred streamed job ID can refer to the
  wrong same-root job; Watch->Manual queued promotion can retain the old lane
  wait; queue-full manual submission can cancel accepted watcher work before
  admission. Latest UX run must reproduce/fix these with regressions and repair
  `rg_files` fixture isolation under in-worktree TMPDIR (a /tmp-only rerun is
  insufficient). Changes are still uncommitted/unaccepted pending this pass.
- Engine first handoff committed compact hints and conservative all-tree seals
  (`9f16951`, `11ce93b`), with focused tests/fmt/engine clippy passed. Parent has
  NOT accepted unbounded all-source hashing: it includes ignored weights/.git/
  targets, lacks cancellation/budgets, and can regress Laya's ~99 GB root.
  Latest engine run must bound bytes/entries/open behavior with cache-miss
  fallback, fix non-Unix temporary borrows, and bound compact encoding before
  allocation. No benchmark or weakened content-identity check is authorized.
- Build artifacts reduced disk to ~4.1 GiB. After lanes held Cargo, parent took
  exclusive shared `debug/.cargo-lock`, verified no compiler and 140 user-process
  references, then removed only two obsolete unused test executables (old Nov4
  `zg_engine-ec1f03fc72ac6be4`, oldest `zg_daemon-ea869dbeaf13741d`). Btrfs reclaimed
  ~626 MiB after a delay; free space ~4.7 GiB. Current variants/dependencies kept.
  UX completed `c127000`; focused scheduler41/runtime60 and exact in-worktree
  rg_files fixture1 passed. Parent reread the corrected queue-admission/lane and
  exact-ID/cancellation paths. UX tree is clean, no active Cargo remains.
  Before engine validation, exact free4283207680 bytes was BELOW4GiB: no build
  started. Under the idle build lock parent pruned only unused Nov4 app .rlib/
  .rmeta pairs (libzg_engine363ce, libzg_cli3b34). After Btrfs delayed reclaim,
  bounded readiness receipt recorded4469587968 bytes free, above4294967296 floor.
  Engine hold is explicitly RELEASED in latest run for focused checks+commit;
  parent does not compile concurrently. Five engine dirty files are source-ready
  and reviewed, but remain unverified until that run completes. New-build floor
  remains4GiB; check bytes, not rounded df output.
- UX hung a newly written regression by trying to cancel a shared Watch via its
  original client signal. Parent authorized stopping ONLY that worker-owned test
  PID, preserving output, correcting cleanup with explicit scheduler cancel and
  bounded waits/command timeout. No installed daemon process is involved.
- Reviewed UX wire+implementation integrated on canonical as `3786d16` +
  `3d2bd96`. Native pin remains .8; no storage-format change. `rust/AGENTS.md`
  build example corrected: executable package is `zg`, not library `zg-cli`.
  Final checks will include the real binary and workspace clippy; host-local
  canonical Cargo configs route shared target/jobs2/incremental=false (excluded).
## Current integrated verification — 2026-11-05 14:xx

- Engine `79137ec` passed 59 focused tests and fmt; clippy blocked by disk floor.
  Parent reviewed/integrated engine as `4ec2b73`, `f022b87`, `f24e33e`. Cache is
  optional/bounded: 20k entries, 16 MiB/file, 64 MiB/seal; cancellation and safe
  regular-file opens, no weaker metadata-only trust. Non-Unix cache is disabled.
- Test-only portability correction `f3b1a24` integrated as `5cc9cb9` after the
  first full run stopped; supported Unix retains strict no-op failure injection,
  unsupported targets exercise ordinary indexing. Cross-target build not claimed.
- Shared debug app/test caches were pruned under exclusive build lock and full
  process path+device/inode reference checks: 97 generated outputs, ~5.06 GiB
  logical. Vendor dependency libraries/shared objects, original V1 target, installs,
  sources/backups/indexes preserved. Receipts/logs live privately under
  `~/.local/share/zvec-grep/recovery-verification-20261105/`.
- First integrated fmt PASS, then offline/locked workspace tests on `f24e33e`
  FAILED in `zg --test server_lifecycle`: 27 passed /9 failed. Eight failures
  assert fresh daemon version2 equals stale literal1; the ninth incorrectly treats
  current version2 as unsupported. Old fixtures also expect automatic legacy
  replacement, contradicting explicitly selected fail-closed UX. Preserve raw
  `tests-20261105T063741108932289.log`; do not restore automatic replacement.
- Latest UX run fixes only protocol/migration-safety integration fixtures,
  preserving PID/token/auth/port/IPv4+IPv6/no-fallback coverage. Production version
  constant is PRIVATE; use captured fresh instance-record version in fixtures,
  no new public API. Then focused `protocol_upgrade` test filter+commit.
- After that failure free space ~2 GiB: no new build admitted. Pruned only39
  inactive test/debug ELFs, library caches kept. Receipt
  `cache-prune-20261105T064822809746Z.json`; bounded reclaim job43 observed
  6413881344 bytes free. UX focused Cargo hold RELEASED, check exact4GiB floor
  before each new build. Parent does not compile concurrently with this lane.
- Parent next: review/cherry-pick corrected protocol fixtures; remove stale test
  executables/top debug CLI before canonical revalidation so another lane cannot
  supply the shared top-level CLI binary. Run integrated fmt/tests, then (after
  cache-only headroom recovery as needed) workspace clippy and release `-p zg`.
  Verification driver: `/home/masis/.pi/agent/reports/run_zg_recovery_verification_20261105.sh`.
  No PASS claim for full suite/clippy/release yet; canonical push, original-source
  convergence and personal-worktree retirement remain pending. No live operations.

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
