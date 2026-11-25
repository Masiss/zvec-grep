# zvec-grep 0.3.0-rc.3 — release notes

## Qualification scope

This candidate qualifies the **Linux x64 GNU CPU** npm artifact. macOS, Windows,
ARM64 and CUDA package policies are unchanged but are not qualified by this
candidate's evidence. The Linux x64 CPU artifact requires glibc 2.39 or
newer (Ubuntu 24.04 baseline); older hosts are rejected before installation.

## Changes

- Clears `cargo clippy --workspace --all-targets -- -D warnings` without
  blanket lint allowances or disabled tests.
- Uses the pinned zvec 0.8.0 native runtime and its embedded Jieba dictionary.
  External dictionary files are now an optional compatibility override for
  indexes created by older native libraries.
- Adds `zg --build-info`, a JSON provenance receipt containing version, Git
  commit/dirty state, lockfile hash, target/profile/features, Rust toolchain,
  zvec version, index format and native-library hash.
- Adds a self-healing npm launcher. npm 11.19 blocks dependency lifecycle
  scripts by default; the first `zg` invocation materializes and verifies the
  native payload when postinstall was skipped.
- Qualifies default CPU installation through npm's real optional-dependency
  selection against a loopback registry, rather than passing platform tarballs
  directly.
- Corrects current guidance: workspace index format is **3**; the optional
  embedding-reuse sidecar remains independently version 2.

## Upgrade and rollback

- Version 2 indexes are refused without mutation. Rebuild with
  `zg --index --rebuild`; there is no in-place v2→v3 migration or v3→v2 downgrade.
- npm installs are atomic and retain the previous native payload until the new
  payload verifies. Same-prefix rc.2→rc.3 upgrade and rc.3→rc.2 rollback are
  qualification gates.
- Stop a running daemon before replacing native libraries. The launcher reports
  a locked-file failure rather than silently mixing runtimes.

## Verification

See `rust/docs/rc3-qualification-20261125.md` and the frozen receipts under
`/home/masis/.cache/zvec-grep/rc3-qualification-20261125/` for exact commands,
hashes and the independent reviewer verdict.
