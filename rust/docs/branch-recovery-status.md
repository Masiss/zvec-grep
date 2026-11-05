# Fork recovery — canonical source checkpoint

## Scope and protected state

Owner authorized recovery of newer V1 work onto released V2, Luna implementation
plus parent review, one maintained published branch and retirement of old personal
sources. **Not production rollout.**

- Canonical branch: `work/zg-recovered`; source currently in `zvec-grep-recovery`.
- Baseline: packaged V2 `c2347d5` / release code `289674b`.
- Preserve upstream refs, local comparison `main`, upstream-baseline worktree and
  pinned benchmark source `/home/masis/bench/fork-src`.
- Do not install/restart/replace the user's binary/daemon or modify live indexes.
  Owner PID191539 still maps a deleted V1 executable: preserve original `rust/target`.
  Owner PID802372 is also untouched. No new large index run is required.

## Verified source — DONE

All Luna lanes completed and were reviewed/integrated. Every lane-exclusive patch
is covered by canonical (`git cherry` all minus); no outstanding writer remains.

| Seam | Current runtime / evidence |
|---|---|
| Native/storage | zvec 0.8, FP16, format2, strict incompatible-format refusal; scalar optimize under state lock |
| CLI/daemon UX | protocol2, auto/direct/server routes, jobs/cancel/detach/wait, bounded lanes/config, watcher consent hygiene |
| Scheduler correctness | exact submitted job ID, queued Watch→Manual wake, admission before watcher superseding, permits through completion |
| Optional M2 hints | compact ZGM2 + legacy JSON reads; identity checked; malformed/nonregular/oversize misses |
| Conservative clean scan | content+metadata seals; Unix only; entry/file/byte caps; cancellation and safe opens; uncertainty uses normal index/recovery |
| Recovery correctness | cache hit requires no pending build record; failure/uncertainty retains baseline recovery ordering |
| RAG | bounded source-evidence API + citation revalidation; not complete R0/R1 quality delivery |
| Archive | V1/native0.7/M4/harness source and evidence preserved, not imported wholesale as runtime |

Verified code: `4807eaaa44e2dbcfaf6acfd9e58cb41a05a7dd45`.

- Full fmt and workspace/all-targets clippy `-D warnings`: PASS.
- Full offline/locked workspace suite: **1,437 passed / 0 failed / 16 ignored**.
- Release **executable** `cargo build -p zg --release`: PASS.
- Isolated version/help/index-help/server-help and native-loader pair smoke: PASS.
- Full identities, commands and limitations: [verification](recovery-verification-20261105.md).
- No blanket lint suppression, deleted assertions or new ignored tests for PASS.
- No GPU/cross-target/performance/production-readiness claim. Outside-lock scalar
  maintenance remains blocked; deferred maintenance #10 and production M4 remain
  unimplemented/unqualified.

The separate Laya measurement is finished: 2,714 files, zero failures, about1.25GiB
peak `zg` RSS, mostly anonymous memory. No >3GiB reproduction, leak proof or global
RAM cap fix. Do not rerun it as part of source consolidation.

## Preservation — DONE; retirement not yet applied

- Original V1 snapshot `13a3555`; private preservation
  `~/.local/share/zvec-grep/source-recovery/20261104/COMPLETE`, including native /
  submodule patches and sensitive/generated leftovers that must not be published.
- Owner-origin archive tags already pushed/verified:
  `archive/zg-v1-before-recovery-20261104`, `archive/zg-v2-release-20261104`.
- Original untracked audit: 10,125 paths (10,124 regular + one known symlink),
  1,788,531,855 regular bytes; both source and backup SHA-256 match the manifest.
  Audit was read-only. Original source remains unchanged until publication verifies.
- Private helper `quarantine_zg_legacy_untracked_20261105.py` requires exact published
  canonical HEAD before atomic private retirement; it checks original HEAD/clean
  tracked state, manifests, hashes, filesystem and symlink metadata. Never bulk-add
  private files or use blanket clean/reset.
- Five leftover daemons from earlier private protocol tests were fingerprint-verified
  and terminated via pidfd/SIGTERM; owner processes untouched. Cleanup receipt is
  private. No active lane/integration executable or mapped-file references found
  in the last pre-retirement check; recheck before actual cache/worktree changes.

## Remaining authorized actions

1. Commit documentation-only verification record, push canonical to owner origin
   and verify exact remote commit. Clarify canonical default branch where permitted;
   do not force-push or mutate upstream.
2. Only after that verification, revalidate and quarantine original untracked legacy
   files privately; converge original worktree to canonical without overwriting
   ignored data, original target or live indexes.
3. Retire inactive personal/lane sources and branches after patch/preservation checks.
   Keep upstream/comparison/pinned sources. Shared build cache currently lives inside
   old integration; preserve or safely relocate it before removing that worktree.
4. Record final source path/ref and actual retirement outcome. Publication and
   consolidation are still pending at this checkpoint, not DONE by a test PASS.

## Operational controls

Private build/smoke logs and candidate artifact:
`~/.local/share/zvec-grep/recovery-verification-20261105/`.
The candidate is not installed. Native .8 pair is the verified rollback library.

Build cache: `/home/masis/src/zvec-grep-integration/rust/target`; two jobs,
incremental off, absolute temporary path, offline/locked dependencies. Every explicit
new validation build requires >=4,294,967,296 free bytes. Preserve gate failures;
restore space under exclusive Cargo lock + process path/inode reference checks
before a new admission. No concurrent lane builds or automatic cap increases.
Source/backup/index/model files and original V1 target are not cache-pruning targets.
