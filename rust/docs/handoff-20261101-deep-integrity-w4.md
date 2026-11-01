# Handoff — Deep Storage Integrity W4 (2026-11-01)

## State

**W4 PASS; local rollout installed; storage integrity track closed.**
Baseline was W3 `5b218ae`. W4 adds the operational `zg --verify` surface, the
light seal check and a qualified native ABI, and installs the new `zg` +
`libzvec_c_api.so` pair on this host.

## Implementation

- `zg --verify [root] [--json] [--require-complete]`, direct-only; exit `0`
  PASS_COMPLETE, `1` CORRUPT/identity mismatch, `2` INCOMPLETE/legacy/
  uninitialized/unavailable.
- Engine: `VerifyOptions`/`VerifyResult`, `ZvecGrep::verify`; manifest generation
  identity is enforced.
- Seal: light native check after stage close, before manifest publication.
  Failure aborts publication; the active generation is untouched.
- Native ABI `1`: `zvec_integrity_abi_version`, `SKIP_STABILITY`, `LIGHT`.
- No scheduler, daemon protocol, repair, delete or rebuild on `CORRUPT`.

## Evidence

Directory: `benchmarks/harness-v2/operations/deep-storage-integrity/w4/`.

- `results/cli-cases.json` — 25/25.
- `results/crash.json` — 5/5 SIGKILL cases.
- `results/concurrency.json` — parallel verify/search and verify-during-index.
- `results/restart.json`, `regression.json`, `native-abi.json`.
- `results/disk-full.json` — RLIMIT_FSIZE proxy; true ENOSPC blocked.
- `results/measurements.json` — verify/light/incremental/no-op CPU, PSS, I/O.
- `results/rollout.json`, `results/provenance.json`.
- Design: `docs/deep-storage-integrity-w4.md`.

Rust: engine 563 pass / 0 fail / 39 ignored; CLI 95 pass; wrapper 4 pass.
Format and new-code clippy are clean; workspace-wide `-D warnings` still trips
pre-existing unrelated warnings.

## Installed pair

- `/home/masis/.local/bin/zg` — `6db0f67e…` (0.0.1)
- `/home/masis/.local/lib/libzvec_c_api.so` — `532f16dd…`
- Daemon on `127.0.0.1:7999` restarted and ready; verify/smoke passed.

Rollback: `/home/masis/.local/share/zvec-grep/rollback-20261101T130300Z/`
(`zg` `ad1d9c2c…`, library `5093c7d7…`). Copy both back and restart the daemon.
Rollback was smoke-tested with the old workspace.

## Residual risks

- Pre-W4 rc.2 workspaces use a different manifest schema; the new binary reports
  rebuild-required and never rewrites/deletes them. No registered live workspace
  exists outside scratch fixtures.
- Mixed binary/library pairs are unsupported; install together.
- `.ipc`/`.bin` are structural checks only; content checksums are declared
  limitations.
- True ENOSPC and power-loss durability are not claimed; only the bounded
  write-failure proxy and SIGKILL evidence exist.

## Next

Storage track is closed. Proceed to **RAG R0/R1** under `docs/rag/`:
read `README.md` and `implementation-plan.md`, run R0/R1, record evidence.
Do not open a new optimization track.
