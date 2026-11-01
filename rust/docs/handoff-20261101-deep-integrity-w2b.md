# Handoff — 2026-11-01 — Native Checksum Writer W2b

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`, branch `work/zg-optimization`.
- Baseline: `9b31c79` (W2), native source `8f6d600…`.
- Verdict: checksum-writer acceptance **PASS**; generation crash atomicity is a
  W3 blocker.
- Installed/prebuilt native `.so` unchanged:
  `58381ac7…`; local build `.so` restored to `cbb596ca…`.

## Result

- All 7 Proxima segments now carry CRC32c; `content_crc` nonzero; capability
  marker `0x5A564543484B5331`.
- HNSW bit flip and 4 KiB tail truncate are detected.
- Legacy indexes return `pass_incomplete` / exit 2, not PASS.
- Old index remains readable by the patched custom binary.
- Forced seal failure returns exit 1 and publishes no manifest.
- `SIGKILL` during in-place incremental update can corrupt the already-active
  generation. The verifier detects it (`corrupt`, exit 1) but cannot repair it;
  this must be handled in W3 generation/publication work.

## Evidence

- `rust/docs/deep-storage-integrity-w2b.md`
- `benchmarks/harness-v2/operations/deep-storage-integrity/w2b/`
- Custom `.so`: `b8f35074…`; seal-failure hook `.so`: `ce2be9b9…`.

## Next authorized action

W3 may start on generation identity, C/Rust FFI and `.ipc`/`.bin` coverage, but
must not promise recovery/atomicity until the active-generation crash case is
fixed. Do not integrate the verifier as a production gate yet.
