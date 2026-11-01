# Native Deep Integrity PoC — W2

Status: **PARTIAL**. Native RocksDB checksum verification works. Proxima/HNSW
content is not protected in indexes produced by the current mmap writer. No
native library, live index, engine, scheduler, UX V2, clean-scan V6 or
searchable format was changed.

Baseline: `0a0e958`, audit `eedd27c`. Date: 2026-11-01.

## 1. Provenance and isolated build

| Item | Value |
|---|---|
| Native source | `.local/native/zvec-native-profile` at `8f6d600db769dcabf92f7af11a8242453b7ae5bf` |
| Local build tree | `.local/native/zvec-native-build`, CMake source = same checkout |
| Local version string | `v0.0.0-g8f6d600` |
| Installed prebuilt `.so` SHA-256 | `58381ac7…` (unchanged) |
| RocksDB | 8.1.1 |
| Ailego archive SHA-256 | `409d52d5…` |
| RocksDB archive SHA-256 | `a30d2a22…` |
| PoC verifier `.so` SHA-256 | `de699925…` |
| Compiler | `g++ (GCC) 16.2.1 20260810` |

The PoC does **not** rebuild or replace `libzvec_c_api.so`. It builds a separate
`libzvec_w2_verify.so` from isolated source `w2_verify.cc.in`, linking the pinned
Ailego and RocksDB archives. No native core patch was required to run the probes.
The checksum-path source files inspected were clean at the commit; the local
worktree had unrelated modifications elsewhere. Full hashes are in
`w2/results/provenance.json`. A clean `build.sh` rebuild differs byte-for-byte
from the probe `.so` only because the ELF embeds build-path metadata; the source
and pinned archives are identical.

The PoC exports a minimal C ABI:

```c
const char *zvec_w2_verify_generation(const char *root);
void zvec_w2_free_string(const char *value);
```

It returns a JSON report. The Python `ctypes` call was verified; the CLI wrapper
is `w2_verify`.

## 2. What Proxima actually protects

Source evidence:

- `IndexUnpacker::unpack(..., true)` always checks header, footer and
  segment-table CRC; it checks `content_crc` only when it is nonzero.
- It validates the `IndexVersion` segment CRC, but not other segment CRCs.
- `IndexPacker::finish` would write a full `content_crc`, but the mmap writer
  used by the observed HNSW index does not use that path.
- `IndexMapping` writes the footer with `SetupMetaFooter` (zeroed) and updates
  only segment-table size/total size. It never sets `content_crc`.
- `MMapFileStorage` sets `data_crc` only for `IndexVersion`; HNSW segments are
  appended without `update_data_crc`.

Observed HNSW fixture (`embedding.index.5.proxima`, 4,456,448 bytes):

| Segment | Bytes | `data_crc` |
|---|---:|---|
| `IndexVersion` | 346 | nonzero |
| `HnswT1S0` | 4,192 | **zero** |
| `HnswT2S0` | 128 | **zero** |
| `HnswT3S0` | 2,880,000 | **zero** |
| `HnswT4S0` | 7,548 | **zero** |
| `HnswT5S0` | 262,144 | **zero** |
| `IndexMeta` | 4,684 | nonzero |

`footer.content_crc = 0` and `content_size = 3,403,776`. Five HNSW segments
cover **3,154,012 bytes with no checksum**. The PoC report therefore sets
`coverage_complete=false` even when it returns `pass=true`.

This is not merely legacy compatibility: the current mmap writer still produces
the same zero-CRC HNSW layout. New indexes are not automatically protected.

## 3. RocksDB

The PoC opens every RocksDB directory read-only with all column families,
sets `paranoid_checks=true`, `ReadOptions.verify_checksums=true`, and calls
`DB::VerifyChecksum()`.

Observed: 13 RocksDB directories in the fixture, including FTS, scalar, id map,
entity and directory stores.

Detected:

- SST data-block bit flip (`block checksum mismatch` with stored/computed CRC).
- SST truncation.
- `CURRENT` replacement.
- `MANIFEST-*` bit flip at open/verification.

Blind spots: WAL replay is disabled by the native writer (`disableWAL=true`);
`VerifyChecksum` is not a semantic record/graph audit; scalar `.ipc` and
reconciliation `.bin` are outside RocksDB and outside this verifier.

## 4. Controlled fault matrix

Clean control passed repeatedly. Detection means the verifier reported
corruption; non-detection means it returned `pass=true` with no errors.

| Case | Detected | Notes |
|---|---|---|
| clean | no | control; `coverage_complete=false` |
| HNSW data bit flip | **no** | zero segment CRC, zero content CRC |
| `IndexMeta` bit flip | yes | PoC checks all nonzero segment CRCs; native unpack alone missed it |
| Proxima header flip | yes | header CRC |
| Proxima footer flip | yes | footer CRC / structure |
| Proxima tail truncate (4 KiB) | **no** | only unprotected HNSW tail removed |
| Proxima truncate below footer | yes | structural |
| Proxima random replacement | yes | parse failure |
| Proxima valid replacement | **no** | checksums are self-contained; semantic consistency not checked |
| FTS SST bit flip | yes | RocksDB block checksum |
| idmap SST bit flip | yes | RocksDB block checksum |
| FTS SST truncate | yes | RocksDB |
| `CURRENT` corruption | yes | RocksDB open |
| `MANIFEST` bit flip | yes | RocksDB open/verify |
| scalar `.ipc` bit flip | **no** | not covered by verifier |
| reconciliation `.bin` bit flip | **no** | not covered by verifier |

Important: for the `IndexMeta` case, `unpack(..., true)` itself returned success
(`checksum_ok=true`); only the PoC's extra per-segment CRC loop found the
mismatch. For HNSW and tail truncation, the verifier passes because it can see
that coverage is incomplete, not because the data is proven intact.

Raw reports: `benchmarks/harness-v2/operations/deep-storage-integrity/w2/results/`.

## 5. Resource measurement

Clean fixture, warm page cache, sealed generation copy:

| Metric | Value |
|---|---:|
| Wall p50 | 34.22 ms |
| Max PSS | 8,905 KiB |
| `/usr/bin/time` wall | 0.02 s |
| User / system | 0.01 s / 0.03 s |
| Max RSS | 12,508 KiB |
| File-system inputs/outputs | 0 / 0 (page cache warm) |

This is a PoC cost for one 4.4 MB Proxima file and 13 RocksDB directories. It is
not a whole-repository or cold-cache cost. The verifier was run on a copied,
sealed generation; no active writer or concurrent optimize was tested.

## 6. Verdict and W3 gate

**PARTIAL.**

- RocksDB checksum verification is usable and detects real SST/metadata
  corruption.
- Proxima structural checks detect header/footer/segment-table damage.
- Proxima HNSW data has no checksum coverage in the current writer, so silent
  graph/data corruption and tail truncation pass.
- `open`/`iter`/`flush`/`optimize` remain non-verification.
- A valid replacement by another valid index passes; semantic consistency is
  not covered.

W3 should not start with a verifier API alone. It needs a native writer change
first:

1. At seal/refresh, compute CRC32c for every segment and a full `content_crc`
   in `IndexMapping`/`MMapFileStorage` (or route HNSW writes through the packer).
2. Add a format/capability marker so old zero-CRC indexes report `INCOMPLETE`,
   not `PASS`.
3. Add read-only `VerifyIntegrity` with flags for RocksDB, Proxima structure and
   Proxima content; default must not claim full coverage.
4. Rebuild the fixture and rerun this matrix. W3 is only justified if the HNSW
   bit flip and tail truncation become detected while the clean control still
   passes.

Minimal proposed C API for W3 (not integrated):

```c
typedef struct zvec_integrity_report zvec_integrity_report;

int zvec_collection_verify_integrity(const char *collection_path,
                                     unsigned int flags,
                                     zvec_integrity_report **out);
int zvec_integrity_report_ok(const zvec_integrity_report *report);
int zvec_integrity_report_complete(const zvec_integrity_report *report);
const char *zvec_integrity_report_json(const zvec_integrity_report *report);
void zvec_integrity_report_free(zvec_integrity_report *report);
```

Flags: `ROCKSDB=1`, `PROXIMA_STRUCTURE=2`, `PROXIMA_CONTENT=4`,
`REQUIRE_COMPLETE=8`. Return `0=PASS`, `1=CORRUPT`, `2=INCOMPLETE/UNVERIFIED`,
`3=INVALID_ARG`. With `REQUIRE_COMPLETE`, a zero-CRC old index must return
`INCOMPLETE`, never `PASS`.

Minimal Rust FFI shape:

```rust
pub struct IntegrityReport(*mut std::ffi::c_void);
pub fn verify_collection(path: &Path, flags: u32) -> Result<IntegrityReport, Error>;
```

Do not integrate until the writer coverage gate above is met. Scalar `.ipc`,
reconciliation sidecars, WAL and semantic delete/tombstone consistency remain
separate decisions.
