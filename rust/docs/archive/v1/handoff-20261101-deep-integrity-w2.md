# Handoff — 2026-11-01 — Native Deep Integrity W2

## Checkpoint

- Worktree: `/home/masis/src/zvec-grep`, branch `work/zg-optimization`.
- Baseline: `0a0e958`; audit `eedd27c`.
- Verdict: **PARTIAL**.
- No installed native library, live index, engine, scheduler, UX V2, clean-scan
  V6 or searchable format changed.

## Result

- RocksDB 8.1.1 `VerifyChecksum` detects SST bit flips, SST truncation,
  `CURRENT` replacement and `MANIFEST` bit flips across all column families.
- Proxima `IndexUnpacker` structural validation detects header/footer/table
  damage and `IndexVersion` corruption.
- Current mmap-written HNSW indexes have `content_crc=0` and zero `data_crc` on
  five HNSW segments (3,154,012 bytes). HNSW bit flips and tail truncation pass.
- A valid replacement `.proxima` also passes; semantic consistency is not
  covered.
- Scalar `.ipc`, reconciliation `.bin` and WAL are outside the verifier.

## Evidence

- `rust/docs/deep-storage-integrity-w2.md`
- `benchmarks/harness-v2/operations/deep-storage-integrity/w2/`
- Probe `.so` SHA-256: `de699925…`; installed prebuilt unchanged at
  `58381ac7…`.

## Resource

Clean sealed copy: 34.22 ms wall p50, max PSS 8,905 KiB, warm page cache.
13 RocksDB dirs + one 4.4 MB Proxima file. No active writer or concurrent
optimize tested.

## Next authorized action

Do not start W3 API integration yet. First decide whether to patch the native
writer to compute per-segment and full-content CRC at seal, with a capability
marker so old zero-CRC indexes report `INCOMPLETE`. Rerun the W2 matrix after
that patch; HNSW bit flip and tail truncation must be detected while the clean
control still passes.

If the writer/format commitment is not approved, keep the result PARTIAL and do
not integrate a verifier that can return PASS on zero-CRC HNSW data.
