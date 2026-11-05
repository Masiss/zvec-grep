# Fork recovery — active checkpoint

## Owner scope and protected state

Owner (2026-11-04, continued 2026-11-05): consolidate V1 newer work onto the
released V2 fork, use Luna writers plus parent review, commit/push one maintained
branch, then retire old personal sources. **Not production rollout.**

- Canonical: `work/zg-recovered`, currently in `zvec-grep-recovery`.
- Baseline: release code `289674b`, packaged V2 tip `c2347d5`.
- Preserve upstream refs, local comparison `main`, upstream-baseline worktree and
  pinned benchmark sources. Do not fetch/push upstream or force-push owner origin.
- Installed binaries/daemon and live indexes remain untouched. PID191539 still
  maps a deleted V1 target executable; **do not delete original `rust/target`**.
  Installed PID802372 is also untouched. No new large index run is needed.

## Preservation already complete

- V1 source snapshot `13a3555`; source/private preservation under
  `~/.local/share/zvec-grep/source-recovery/20261104/`, `COMPLETE` verified with
  11,832 source/backup-hashed entries, including native/submodule patches.
- Owner-origin archive tags pushed and verified:
  `archive/zg-v1-before-recovery-20261104`, `archive/zg-v2-release-20261104`.
- Source/research archives integrated, not applied wholesale. Bulk generated
  data, copied diagnostic source/indexes and credentials are not staged.

## Port/review map

| Seam | Canonical implementation/evidence | Status |
|---|---|---|
| V2 native/storage | zvec .8, FP16, format2, safe scalar finalization lock | preserved; no ABI/format migration |
| CLI/daemon UX | `3786d16`, `3d2bd96`; protocol2, auto/direct/server routing, jobs/cancel/detach/wait, bounded lanes/config, results exclusion, watcher consent hygiene | reviewed/integrated |
| Scheduler/stream correctness | exact submitted job ID, queued Watch->Manual lane wake, queue admission before superseding watcher | regression-tested/integrated |
| Optional M2 compact hints | `4ec2b73`, `f022b87`, `f24e33e`; binary ZGM2 plus legacy JSON reads, FP16 identity, malformed/nonregular/oversize misses | reviewed/integrated |
| Conservative clean scan | same engine commits; content+metadata seals, Unix only, 20k entries/16MiB file/64MiB seal caps, cancellation and safe opens; uncertainty uses normal indexing | reviewed/integrated; no performance qualification |
| Test portability | `5cc9cb9` keeps moved-root coverage on unsupported cache platforms | integrated; cross-target build unverified |
| Protocol integration tests | `c274311`, `6cec6d6`: fail-closed old/missing/newer versions, value0, stale records, explicit authenticated restart, positive MCP/full-toolset, PID/auth/port safety | focused10 PASS; integrated |
| RAG evidence API | `a8048a0`, `cb848dc`, 10 focused tests | source API only; R0/R1 quality work incomplete |
| Harness/M4/native legacy | tracked harness/source/evidence, `research/legacy-rust-v1`, `research/legacy-native-0.7.2`, V1 docs archive | historical/research only, NOT current runtime qualification |
| RAM diagnosis | `index-memory-investigation-20261104.md`, `index-memory-diagnostic-result-20261104.md` | bounded diagnostic complete; no global RAM fix/leak claim |

Never re-enable .8 outside-lock scalar optimize. Deferred maintenance #10 is not
implemented. M4 remains unqualified/NO-GO production. Archive receipts are not
permission to launch new campaigns or import old native behavior.

## Lane state

- UX `recovery/ux`: clean at `ca2fafb`, integrated as `dc88237`. Final daemon
  lint cleanup reviewed; borrowed semaphore permits remain held through execution/
  completion; lane notification and atomic claim ordering unchanged. Scheduler41
  and runtime/lifecycle60 PASS, daemon-only clippy PASS (`--no-deps`; parent has
  already fixed the dependency config casts). All child writers are complete.
- Engine `recovery/engine`: clean at `30033c5`; all changes integrated. Final child
  `da770216-b6eb-4226-a719-dbe94d48b78f` confirmed pending-journal bypass with a
  failing regression, then fixed it: canonical `f3b95f0` + `4408bee`. Cache hit
  requires build record `Ok(false)`; uncertainty follows existing recovery.
  Focused regression1 passed; baseline recovery ordering is unchanged. Corrective
  engine focused59/fmt passed earlier; full clippy still pending.
- Parent owns canonical integration, final verification, push and consolidation.
  Workspace clippy on `1a6fe37` found evidence-test unwraps, checked-conversion,
  oversized-function/signature and optional-load lint defects. Parent fixed only
  `api/evidence.rs` + `config.rs` in `cb469b4` (AST-scoped test expects, equivalent
  let-else, checked usize conversions). LSP scope is outside this session cwd;
  compiler verification is required, no clean-LSP claim.
  Child `76fe6c00-5f1c-4b0d-818e-5c9d3b3ff5c6` completed `29cd144`, reviewed/
  integrated as `c6754b3`: small helper extractions, private optional load returns
  Option, shared-storage error facade preserved, all assertions retained.
  Engine clippy/all-targets and focused59 PASS. No blanket lint suppression or
  runtime semantic changes. Parent `a1be39b` then fixed two unwrap calls hidden
  inside assert macro tokens and documented public job-output errors.
  Full clippy advanced to daemon-only findings: four long orchestration functions
  and two test MutexGuards across await. Last actual log:
  `clippy-20261105T081451895262444.log`. Child
  `4ef1970b-faa3-4233-8bf4-6ed7ab0a853a` stopped at usage limit; owner explicitly
  continued it as `bf1d4725-e26f-4b1e-9274-be6df92d362e`, now completed above.
  A disk-floor refusal launched no build. Parent restored headroom under the
  exclusive build lock (prune receipt `cache-prune-20261105T115941175747Z.json`)
  before releasing focused commands. Five orphan daemons from the earlier private
  protocol tests were identified by exact executable inode, PID/start identity and
  private temporary home, then terminated via pidfd/SIGTERM; owner PID191539 and
  PID802372 untouched. Receipt `private-test-orphan-cleanup-20261105.json`.

## Verification state — not final PASS

- Offline harness43 and RAG10 focused tests passed earlier in recovery.
- UX focused CLI180/daemon132/protocol7/scan1/binary6 passed; review fixes then
  scheduler41/runtime60 and in-worktree `rg_files` fixture1 passed.
- Engine corrective focused59 passed. Optional cache budgets/open behavior were
  reviewed; no performance, cross-target or unconstrained-memory claim.
- First integrated fmt PASS; full offline/locked workspace tests on `f24e33e`
  stopped at `server_lifecycle` with27 PASS/9 FAIL. Eight failures were stale
  literal-version1 assertions; one treated current version2 as unsupported.
  Old automatic-replacement expectations contradicted chosen fail-closed policy.
  Raw failed log remains `tests-20261105T063741108932289.log` in private evidence.
- Repaired protocol fixtures passed10 focused tests, including migration safety,
  stale-record handling and authenticated positive MCP/full six-toolset checks.
  No tests ignored/deleted to obtain PASS; production replacement remains refused.
- **Full integrated suite, workspace clippy and release executable build still
  pending. No release/production readiness claim.**

## Disk/build controls

Disk initially had only233MB free. Removed only verified-unused generated lane
caches/shared incremental cache, then obsolete shared-target app/test artifacts
under exclusive `debug/.cargo-lock` and process path+device/inode reference
checks. Sources, backups, indexes, external dependency libraries/shared objects,
original V1 target and installed processes are preserved. Btrfs reclaim is delayed.

- Shared target `/home/masis/src/zvec-grep-integration/rust/target`.
- Explicit `CARGO_TARGET_DIR`, `CARGO_BUILD_JOBS=2`, `CARGO_INCREMENTAL=0`;
  absolute canonical-worktree `TMPDIR`; runtime .8 library from
  `~/.local/share/zvec-grep/rollback-20261101T130300Z` (SHA5093c7d7…).
- Host-only root/rust Cargo configs are excluded. Do not recreate lane targets.
- Before **every new build**, exact free bytes must be >=4,294,967,296. A failed
  preflight starts no build; restore space before another attempt. No cap lowering.
- No parallel lane builds. Bound long commands; no downloads/live operations.
- Private driver/helpers: `~/.pi/agent/reports/run_zg_recovery_verification_20261105.sh`
  and `prune_zg_build_cache_20261105.py`. Private logs/start/result/prune receipts:
  `~/.local/share/zvec-grep/recovery-verification-20261105/`.
- Before canonical validation, remove unused test/debug executables (with the
  helper's lock/ref checks), including shared top-level debug CLI, so another
  worktree's binary cannot satisfy a stale top-level path. Keep library caches.

## Exact next authorized actions / remaining acceptance

1. Rerun integrated fmt and workspace clippy (metadata-only avoids linking the
   whole test set before lint corrections). Current FULL clippy is NOT PASS;
   engine-only clippy passed. The original failed raw log
   `clippy-20261105T074440083531025.log` is preserved. Correct in-scope defects,
   no broad suppression/weakened tests. Parent evidence/config fixes are committed
   but await that compiler/test verification.
2. Prune only unused test/debug outputs; verify headroom. Run offline/locked
   workspace tests after all fixes. Then reclaim unused test executables as
   needed and release **`cargo build -p zg --release`** (not library-only zg-cli),
   plus isolated version/help/native-pair smoke checks. Every build keeps4GiB floor.
3. Record actual results/ignored tests and runtime-vs-archive feature limitations.
   Push canonical to owner origin and verify exact published commit; clarify owner
   default branch if permitted, no force push/upstream mutation.
4. Only after verified preservation+publication: converge original worktree to
   canonical and retire personal/lane worktrees/branches. Preserve installed
   process references, original target and live index paths; do not blanket clean.

The Laya diagnostic is finished: source-only2,714 files/64MiB, zero failed files,
exact `zg` peak1,310,944KiB (~1.25GiB), mostly anonymous heap. It did not reproduce
>3GB, prove a leak or implement a full-index memory cap. Do not rerun it.
