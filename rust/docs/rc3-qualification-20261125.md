# zvec-grep 0.3.0-rc.3 qualification plan

Date: 2026-11-25. Budget: 4 h maximum. Worktree:
`/home/masis/src/zvec-grep-core-20261124`, branch
`work/core-acceptance-20261124`, base maintained `work/zg-recovered` @
`d9a9b1a`, with the uncommitted core-acceptance fixes from 2026-11-24.

## Objective

Produce a distributable RC3 candidate, not merely a version bump. Qualify the
actual packaged artifact through clean install, upgrade, uninstall and rollback
in isolated HOME/workspace/prefix directories. Do not touch the real binary,
daemon, protected indexes, maintained branch, tags, remotes or registry.

## Constraints

- No feature, retrieval, embedding-model or RAG work. Keep Potion Code 16M v2.
- No blanket lint allowances, lowered gates or disabled tests.
- No automatic user-index deletion/rebuild. v2→v3 remains explicit.
- Agent benefit is a separate product track and not an RC3 gate.
- External build/cache/artifacts live under
  `/home/masis/.cache/zvec-grep/rc3-qualification-20261125/`.
- Current disk: 27 GiB free; abort a new large build below 8 GiB free.

## Checkpoints

1. **C0 — review and snapshot (20 min).** Sol reviews this plan before code
   changes. Record source/base/dirty state, toolchain, native library and model
   identities. Commit the accepted core fixes as a local checkpoint.
2. **C1 — source quality (45 min).** Reproduce and fix all 37 Clippy errors
   under `cargo clippy --workspace --all-targets -- -D warnings`; keep behavior.
   Update stale index-version documentation (v3, not v2). Re-run fmt and the
   affected tests.
3. **C2 — build/provenance/payload (45 min).** Add embedded build provenance
   (commit, dirty state, zvec version, index format, native runtime identity)
   and a public diagnostic command. Remove the obsolete forced native-library
   pin from development configuration; make the supported build use explicit,
   portable environment inputs. Vendor/package `jieba_dict` with both npm
   platform and local packages. Validate required native symbols and colocated
   dependency discovery. Include provenance/checksums in release artifacts.
4. **C3 — lifecycle implementation/tests (45 min).** Exercise and harden the
   existing npm release/install paths: fresh install, upgrade from rc.2,
   daemon-running upgrade, v2 index refusal, missing dictionary/library,
   corruption, uninstall and rollback. Use isolated HOME/workspace/prefix.
   Never rebuild/delete a user index automatically.
5. **C4 — artifact qualification (60 min).** From a clean external target,
   build the Linux x64 CPU artifact, pack platform + meta tarballs, verify
   SHA-256/ABI/symbols, install from a local artifact registry, and run CLI,
   MCP, daemon, lexical/FTS/vector/hybrid, fresh/incremental indexing, restart,
   upgrade/uninstall/rollback. Record exact receipts and platform coverage.
6. **C5 — version/docs/final review (25 min).** Only after C1–C4 pass, set
   `0.3.0-rc.3`, rebuild/repack the exact artifact, write release notes and
   upgrade instructions, freeze hashes, and obtain Sol's independent
   `RC_QUALIFIED` / `RC_NOT_QUALIFIED` verdict.

Stop at any failed checkpoint; do not label an incomplete candidate qualified.

## Sol plan-review amendments (mandatory)

- The current Linux x64 CPU build requires **glibc 2.39 or newer** (Ubuntu
  24.04 baseline). The installer rejects older glibc before payload activation,
  and the ABI audit enforces the same ceiling across every shipped ELF. The
  earlier Ubuntu 20.04/glibc 2.31 claim is not supported by this toolchain.
- Scope the candidate to **Linux x64 GNU CPU** using a separate qualification
  manifest; do not weaken the default multi-platform/CUDA release policy.
- Qualify installation by asking npm for only the exact-version meta package
  from a loopback registry that also serves the candidate platform package;
  do not pass both tarballs directly and bypass optional-dependency selection.
- Bind embedded provenance and receipts to commit, dirty state, lockfile,
  target/toolchain/features, zvec/native hashes and index format. zvec 0.8.0
  embeds the default Jieba dictionary/HMM model (verified by absence of the
  removed legacy dictionary-configuration symbols); no external dictionary
  payload is shipped. Audit ELF architecture, RUNPATH, GLIBC/GLIBCXX/CXXABI and
  all dependencies; then exercise FTS and FP16 through the installed artifact.
- Clear inherited `ZVEC_JIEBA_DICT_DIR` in installed-artifact tests so a user
  environment cannot mask missing payload.
- Freeze rc.2 and rc.3 tarballs. Test same-prefix upgrade/rollback, a running
  baseline daemon, interruption/missing/corrupt payload, reinstall/uninstall,
  and v2-index refusal without mutation. Never imply in-place index downgrade.
- npm 11.19 blocks dependency lifecycle scripts by default. The meta package
  therefore ships a self-healing launcher outside the native payload directory;
  qualification must install with scripts blocked and materialize on first run.
- Reserve contingency; cut optional UI/cross-platform/GPU work first.

## Required Sol review questions

1. Is the artifact/lifecycle strategy sufficient, or must a standalone archive
   be added alongside npm?
2. Are provenance fields and ABI/symbol checks adequate without changing the
   native pin or retrieval behavior?
3. What exact upgrade/rollback receipts are required, especially with a daemon
   running and a v2 index present?
4. Which failures must block RC qualification, and which platform limitations
   can be disclosed rather than fixed in this Linux-only environment?
5. Is the time box realistic; what should be cut first if it is not?
