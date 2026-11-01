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

## Next authorized work (W3 only, after owner approval)

1. Expose the native verifier through C/Rust FFI with
   `PASS_COMPLETE` / `INCOMPLETE_LEGACY` / `CORRUPT`.
2. Add `.ipc`, reconciliation `.bin`, WAL and semantic consistency coverage.
3. Add generation identity to verifier output and a read-only verification
   operation on a stable generation.
4. Recompute per-segment CRCs in a final native flush if per-segment
   localization is required; content CRC already covers the full content region.
5. Do not claim power-loss durability; only SIGKILL consistency was tested.
6. Do not reopen Deferred Maintenance #10 or native concurrent optimize under
   this handoff.
