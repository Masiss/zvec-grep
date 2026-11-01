# Native Checksum Writer Hardening — W2b

Status: **PASS for the checksum-writer acceptance, with a named generation
lifecycle blocker for W3.** No installed library, Rust binary, scheduler, UX V2,
clean-scan V6 or searchable format was changed.

Baseline: `9b31c79` (W2), native source `8f6d600…`. Date: 2026-11-01.

## 1. What changed

Patch: `benchmarks/harness-v2/operations/deep-storage-integrity/w2b/patches/w2b-checksums.patch`

- `IndexMapping::refresh` now computes CRC32c for **every segment** and a
  `content_crc` over the full content region of the active header.
- The active header is marked with
  `MetaHeader.reserved3_[0] = 0x5A564543484B5331` (`CHECKSUM_CAPABILITY_V1`).
- Checksums are recomputed on every refresh/seal after the last writes, before
  the footer/header are flushed.
- `MMapFileStorage::flush()` and `close()` return
  `IndexError_InvalidChecksum` when seal checksum computation failed. The error
  propagates to `ChunkBroker::flush`; a failed index does not publish.
- On failure, `content_crc` and the capability marker are cleared, so the
  artifact is **INCOMPLETE**, not a false PASS.
- `IndexUnpacker::validate_checksum` now starts at `MetaHeader.content_offset`
  instead of `sizeof(header)`. This is required for the mmap layout (content
  begins after the segment table); for the packer layout the values are equal.

The custom native `.so` was built in the local build tree, copied to scratch,
then the source files and local build `.so` were restored. The Rust binary and
the installed/prebuilt `libzvec_c_api.so` were not replaced.

## 2. New index coverage

New HNSW index built with the custom native library:

| Item | Old W2 index | W2b index |
|---|---:|---:|
| `content_crc` | 0 | nonzero (`0x6e9a9d5f` in the first build) |
| Segment CRCs | 2/7 | **7/7** |
| HNSW bytes without CRC | 3,154,012 | **0** |
| Capability marker | 0 | `CHECKSUM_CAPABILITY_V1` |

The verifier reports `pass_complete`, exit `0`, `coverage_complete=true`.

## 3. Fault matrix

Rerun of the W2 matrix on a W2b index:

| Case | Exit | Result |
|---|---:|---|
| clean | 0 | `pass_complete` |
| HNSW data bit flip | **1** | **detected** (content CRC) |
| HNSW tail truncate 4 KiB | **1** | **detected** (content read/CRC) |
| `IndexMeta` bit flip | 1 | detected |
| header/footer flip | 1 | detected |
| Proxima truncate below footer | 1 | detected |
| Proxima random replacement | 1 | detected |
| Proxima valid replacement (old index) | 2 | `pass_incomplete`, not PASS |
| FTS/idmap SST flip | 1 | detected by RocksDB |
| FTS SST truncate | 1 | detected by RocksDB |
| `CURRENT` corruption | 1 | detected by RocksDB |
| `MANIFEST` bit flip | 1 | detected by RocksDB |
| scalar `.ipc` flip | 0 | still outside verifier coverage |
| reconciliation `.bin` flip | 0 | still outside verifier coverage |

The HNSW bit flip and tail truncate are the acceptance cases; both are now
detected. Scalar `.ipc`, reconciliation `.bin`, WAL and semantic consistency
remain W3 scope.

## 4. Legacy compatibility

Old W2 index (`content_crc=0`, capability 0):

- Verifier: `pass=true`, `coverage_complete=false`, `legacy=true`, exit **2**
  (`pass_incomplete`). It is not reported as PASS.
- Read path: the patched custom `zg` still answered an FTS query on the old
  index (`rc=0`). Old indexes remain readable.

## 5. Seal failure and lifecycle

### Forced seal failure

A test-only native build with `ZVEC_W2B_FORCE_SEAL_FAILURE=1` causes checksum
computation to fail. Result:

- `zg --index` exit **1**;
- no workspace manifest was published;
- errors included `Failed to compute index checksums …`,
  `Failed to refresh storage for Invalid checksum`, and
  `failed to finalize index storage`.

This verifies that a seal failure is not accepted as a clean build.

### Crash during incremental update

Killing `zg` with `SIGKILL` during a 1000-file incremental update on a scratch
workspace left the **already-active** generation mutated. The W2b verifier
reported `status=corrupt`, exit `1`; the subsequent query failed with `rc=101`.
This is not a false PASS: checksums correctly expose the partial write. But W2b
does not make in-place incremental publication crash-atomic. That is a
generation-lifecycle/recovery problem, not a CRC-algorithm problem, and must be
addressed before production verification claims.

## 6. Cost

Fixture: 2,000 files × 8 KiB, isolated arms, warm page cache. Single-host, small
number of runs; values are descriptive, not a benchmark release gate.

| Operation | Original native | W2b native |
|---|---:|---:|
| Initial index | 6.79 s | 7.09 s (+4.4%) |
| Incremental one file | 2.11 s | 1.71 s (one run; not a reliable speedup claim) |
| Rebuild median (3 runs) | 6.70 s | 7.73 s (~15%) |
| Rebuild range | 6.66–7.29 s | 6.78–8.26 s |
| Post-rebuild no-op | 0.57 s | 0.60 s |

Initial max RSS: 406,852 KiB → 423,332 KiB (+4%). Rebuild variance is high; the
15% median is not a precise cost claim.

Verifier on a larger fixture (4,000 files, 32 MB source, 7.4 MB Proxima):

| Metric | Value |
|---|---:|
| Wall p50 | 45.64 ms |
| Max PSS | 15,370 KiB |
| `/usr/bin/time` user/system | 0.02 / 0.03 s |
| Max RSS | 18,816 KiB |
| FS input/output | 0 / 8 (warm cache) |

Small W2 fixture remained ~34 ms / 8.9 MiB PSS. Verification is cheap relative
to index construction, but this is warm-cache and not cold-disk.

## 7. W3 gate

The checksum writer is ready to be used as a native capability. W3 may expose
the verifier API and add `.ipc`/`.bin` coverage, but it must include:

1. generation identity and a verifier result that distinguishes
   `PASS_COMPLETE`, `INCOMPLETE_LEGACY`, and `CORRUPT`;
2. atomic publication/repair for incremental updates, because a crash can leave
   the active generation partially written (W2b detects it but cannot repair
   it);
3. read-only verification on a stable generation with no writer;
4. explicit `.ipc`, reconciliation `.bin`, WAL and semantic consistency scope.

Minimal C/Rust API remains the one proposed in W2, with `REQUIRE_COMPLETE`
returning `INCOMPLETE` for legacy zero-CRC indexes.

Evidence: `benchmarks/harness-v2/operations/deep-storage-integrity/w2b/`.
