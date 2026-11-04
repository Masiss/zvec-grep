# Handoff — 2026-11-01 — Deep Integrity W3

## State

- W3 status: **PASS for the native verifier surface and coverage**.
- Evidence/design: [`deep-storage-integrity-w3.md`](deep-storage-integrity-w3.md).
- Plan: [`deep-storage-integrity-w3-w4-plan.md`](deep-storage-integrity-w3-w4-plan.md).
- W4: **draft, not started**; the four owner decisions are still open.
- Branch/worktree: `/home/masis/src/zvec-grep`, `work/zg-optimization`.

## What changed

- Native C API `zvec_collection_verify_integrity` plus report accessors,
  flags, header declarations and export-list wiring.
- Read-only verifier source `src/binding/c/integrity_verify.cc`:
  Proxima structure/content/per-segment CRC, RocksDB read-only
  `paranoid_checks`/`VerifyChecksum`, Arrow IPC structure, ZGM2 sidecar
  structure, missing-collection detection, generation identity, `flock`-based
  active-writer refusal. No writes.
- Writer fix in `IndexMapping::refresh`: recompute checksums per header with the
  header's own content base; revisit sealed headers only when a capability or
  segment CRC is missing. Closes the W2c late-segment zero-CRC residual.
- New opt-in Rust crate `zg-integrity-native` with dynamic symbol resolution;
  not wired into `zg-engine` yet.

## Evidence

- `benchmarks/harness-v2/operations/deep-storage-integrity/w3/`
  - `patches/w3-integrity-verifier.patch`
  - `run_cases.py`, `verify_cli.py`
  - `results/cases.json` — 23/23 passed
  - `results/clean.json`, `large-clean.json`, `legacy-zero-crc.json`
  - `results/provenance.json`
- Clean fixture: 201 files / 7 segments / 0 zero-CRC.
- Large fixture: 2,001 files / 80,040 entities / 48 segments / 119.8 MB
  Proxima / 0 zero-CRC.
- Legacy fixture: `INCOMPLETE_LEGACY`, rc 2.
- Installed `rust/target/release/libzvec_c_api.so` SHA-256 unchanged
  (`58381ac7…`); production `zg` not replaced.

## Verification

- Native target `zvec_c_api`: built from the local profile at `8f6d600` plus the
  W3 patch.
- `cargo test -p zg-integrity-native` — 4 passed.
- `cargo clippy -p zg-integrity-native --all-targets -- -D warnings` — clean.
- `cargo fmt -p zg-integrity-native` — clean.
- Fault matrix — 23/23.
- Patch reverse-check against the applied profile — pass.
- Read-only tree-hash proof — clean and legacy unchanged.

## W3 limits

- `SIGKILL` consistency only; no power-loss durability claim.
- `.ipc` and `.bin` are structural (no content checksum); reported as
  `limitations`, not silently content-verified.
- Non-empty WAL is unsupported and forces `INCOMPLETE`; sealed generations leave
  zero-length logs.
- No repair/rebuild/background scrub/scheduler change.

## Next — W4 owner decisions

1. Manual-only verification, or also scheduled/background?
2. On `CORRUPT`, is a user-initiated rebuild the only allowed action?
3. Direct-only `--verify` first, or also `--server`?
4. Is production rollout part of W4 or explicitly deferred?

Do not overwrite the installed native library or production binary. Rebuild the
scratch candidate from the W3 patch when resuming. Do not reopen Deferred
Maintenance #10 or native concurrent optimize under this handoff.
