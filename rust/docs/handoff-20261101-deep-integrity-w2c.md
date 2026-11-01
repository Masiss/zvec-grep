# Handoff — 2026-11-01 — Crash-Atomic Incremental Generation W2c

## State

- Implementation and evidence: `d366692` — `feat(storage): make incremental
  generations crash-atomic`.
- Parent: `50d53ea` (W2b checksum writer).
- W2c status: **PASS for staged incremental crash consistency**.
- W3 status: **gated** on the native verifier surface and coverage.

## What is done

- Ordinary incremental/rename/settings-refresh paths clone the active generation
  into a new stage, open/close/seal it, then publish via the existing manifest
  path.
- Reflink (`FICLONE`) with byte-copy fallback; no hardlinks of mutable files.
- Failure before publish keeps the old manifest and generation readable.
- SIGKILL matrix: 7/7 cases preserved or correctly switched the active manifest;
  all active generations verified complete; FTS and vector queries worked;
  recovery cleared abandoned stages.
- Cost/amplification receipts: 858 MiB generation, stage exclusive increment
  **1.56 MiB** at crash; recovery and clone timings recorded.

## Evidence

- `rust/docs/deep-storage-integrity-w2c.md`
- `benchmarks/harness-v2/operations/deep-storage-integrity/w2c/`
- `results/crash-results.json`, `results/cost.json`,
  `results/disk-amplification.json`
- `results/vector-old-after-crash.txt`,
  `results/fts-old-after-crash.txt`

## Verification

- Build tests: 18 passed.
- Indexing service tests: 34 passed.
- Full `zg-engine` lib: 560 passed, 3 pre-existing authorization-fixture
  failures, 39 ignored.
- `rustfmt --check` on changed Rust files passed.
- No installed native library or production binary replaced.
- No scheduler, UX V2, ZGM2, index format, policy or threshold change.

## Next session — W3 and W4

W3/W4 are queued for the next session. Full scope, acceptance and resume steps:
[`deep-storage-integrity-w3-w4-plan.md`](deep-storage-integrity-w3-w4-plan.md).

- **W3** is ready: expose the native verifier (C/Rust FFI), add explicit
  `PASS_COMPLETE` / `INCOMPLETE_LEGACY` / `CORRUPT`, generation identity,
  read-only stable-generation checks, and `.ipc`/`.bin`/WAL coverage.
- **W4** is a draft: manual/daemon verification command and reporting. Its scope
  and the owner decisions listed in the plan must be confirmed before
  implementation.
- Rebuild the scratch native candidate from
  `benchmarks/harness-v2/operations/deep-storage-integrity/w2b/patches/`.
  W2c scratch binaries/fixtures were cleaned up; committed receipts remain.
- The local native profile is dirty at `8f6d600`; the W2b patch currently applies
  cleanly. Do not overwrite the installed library or production binary.
- Do not claim power-loss durability; W2c tested `SIGKILL` only.
- Do not reopen Deferred Maintenance #10 or native concurrent optimize under
  this handoff.
