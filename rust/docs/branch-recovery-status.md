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

- UX `recovery/ux`: clean, latest `5726203` after `37b3698`/`c127000`/`bfeb5eb`.
  Last child `b5fdf2fc-e9aa-445c-bc0a-12afc754082f` completed. Parent reviewed all
  corrective commits and integrated them. No further child work is outstanding.
- Engine `recovery/engine`: clean, latest `f3b1a24` after `79137ec`/`11ce93b`/
  `9f16951`. Last child `4dc6ae58-0434-439e-90d2-932d29f6a487` completed.
  Parent integrated all commits. Corrective engine focused59 tests/fmt passed;
  engine clippy was not rerun because the disk floor was reached.
- Parent owns canonical integration, final verification, push and consolidation.

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

1. Prune only unused test/debug outputs; verify headroom. Run integrated fmt and
   offline/locked workspace tests after all fixture fixes, preserving earlier
   failure. Repair in-scope defects and recheck; do not weaken contracts/tests.
2. Once tests complete, reclaim unused test executables as needed, then workspace
   clippy `--all-targets -- -D warnings`, release **`cargo build -p zg --release`**
   (not library-only zg-cli), and isolated version/help/native-pair smoke checks.
3. Record actual results/ignored tests and runtime-vs-archive feature limitations.
   Push canonical to owner origin and verify exact published commit; clarify owner
   default branch if permitted, no force push/upstream mutation.
4. Only after verified preservation+publication: converge original worktree to
   canonical and retire personal/lane worktrees/branches. Preserve installed
   process references, original target and live index paths; do not blanket clean.

The Laya diagnostic is finished: source-only2,714 files/64MiB, zero failed files,
exact `zg` peak1,310,944KiB (~1.25GiB), mostly anonymous heap. It did not reproduce
>3GB, prove a leak or implement a full-index memory cap. Do not rerun it.
