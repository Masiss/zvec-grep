# Deep Storage Integrity Audit — zvec / zvec-grep

Status: **PARTIAL FEASIBLE**. No verifier was implemented. No engine,
scheduler, index format, model, policy, or threshold was changed.

Date: 2026-11-01. Baseline worktree: `8313b95`.

## 1. Versions and provenance

| Item | Evidence |
|---|---|
| Rust binding | `zvec-rust = "=0.7.2"` (`rust/Cargo.toml`) |
| FFI binding | `zvec-rust-sys 0.7.2`, checksum `c5a02fde...` |
| Build helper | `zvec-rust-build 0.7.2` |
| Native library | bundled prebuilt `libzvec_c_api.so`, 38,067,280 bytes, SHA-256 `58381ac7...` |
| Native version API | `zvec_get_version()` returns **`v0.0.0`**; major/minor/patch all 0 |
| Native source checkout | `.local/native/zvec-native-profile` at `8f6d600db769dcabf92f7af11a8242453b7ae5bf` (2026-10-13), local worktree dirty |
| zvec sparse/detail | native source reports git sha `8f6d600...`, but the prebuilt artifact timestamp predates that checkout and returns `v0.0.0` |

**Provenance finding:** the Rust pin is exact, but the native binary's semantic
version is not reliable. `v0.0.0` is the CMake fallback when tags are absent;
the prebuilt artifact cannot be mapped to an exact native tag from the binary
alone. Future integrity claims must be tied to the native `.so` SHA-256 and, if
built locally, an explicit `OVERRIDE_GIT_DESCRIBE`.

## 2. Integrity APIs actually available

### Rust API

`zvec-rust 0.7.2` exposes:

- `Collection::open`
- `Collection::flush`
- `Collection::iter` / `iter_with_options`
- `Collection::optimize`
- `Collection::stats`
- schema validation

There is **no** `verify`, `checksum`, `integrity`, `scrub`, `repair`, or
`recover` method on `Collection`. `zvec_rust::sys` likewise has no such C call.

### C API

`src/include/zvec/c_api.h` exposes schema validation only:
`zvec_field_schema_validate` and `zvec_collection_schema_validate`. It does not
expose a collection integrity/checksum entry point.

`zvec_collection_open`, `zvec_collection_flush`, and
`zvec_collection_optimize` are lifecycle operations, not verification:

- `open`: recovery/metadata/segment open;
- `flush`: write durability/flush;
- `optimize`: compaction/rewrite;
- `iter`: iterate records;
- `stats`: counts/completeness metrics.

None of these means “all data pages and indexes are semantically correct”.

### Native Proxima index

The native source does have a checksum path:

- `IndexUnpacker::unpack(..., checksum)` validates a CRC32c of the content
  when the footer has a nonzero `content_crc`.
- `MmapFileReadStorage` has
  `proxima.mmap_file.container.checksum_validation`.
- `FileReadStorage`, `MemoryReadStorage`, and `BufferReadStorage` have similar
  parameters.

But the defaults are **false**:

- `MmapFileReadStorage::checksum_validation_{false}`
- `IndexSegmentStorage::open` calls `unpack(..., false)`
- no exposed C/Rust API sets the checksum-validation parameter
- no zvec-grep call site sets it

Therefore an `open` of a vector/HNSW index does **not** verify the full index
content under the current integration.

### RocksDB-backed stores

`idmap`, delete state, scalar inverted state, and FTS use RocksDB. The native
wrapper does not set `paranoid_checks` or `verify_checksums`; it also sets
`disableWAL = true` for writes. This gives block checksums on the read path when
RocksDB reads a block, but not a full database verification pass and no WAL
replay to audit.

### Scalar / metadata

Scalar `.ipc` files and reconciliation `file-*.bin` sidecars are structurally
validated when their readers decode them. That is not a full content checksum of
all records. Reconciliation sidecars are not queried by search and do not prove
the searchable index is intact.

## 3. Lifecycle and consistency

Generation layout is:

```text
.zvec-grep/generations/<generation>/storage/
  fragments_<...>/0/embedding.index.*.proxima
  fragments_<...>/0/scalar*.ipc
  fragments_<...>/0/fts*.rocksdb/
  entities/0/scalar*.ipc
  entities/idmap.0/
  files/
  reconciliation/file-*.bin
```

The generation selected by the workspace manifest is read-only from the
reader's perspective. The writing segment is mutable until finalize/close.
Checksums are consistent only after the generation is sealed and no writer is
changing it. A read-only open is supported (`CollectionOptions::set_read_only`)
and a read-only direct query succeeded while a daemon held the workspace in the
probe. Full checksum verification against a live writing generation is not
consistent; it needs a quiesced generation or a copied snapshot.

## 4. Controlled fault probe

Fixture: 64 × 8 KiB files, isolated copy per case. Original index was not
modified. Flipping one byte at the middle of a file tests silent mutation;
truncating 4 KiB tests structural failure.

| Corrupted target | FTS | Vector | Interpretation |
|---|---|---|---|
| Vector `.proxima` bit flip | success | success | not detected on this read path |
| Vector `.proxima` truncate | fail/open | fail/open | structural detection |
| FTS RocksDB SST bit flip | success | success | not detected on this read path |
| FTS RocksDB SST truncate | fail/open | fail/open | structural detection |
| idmap RocksDB SST bit flip | success | success | not detected on this read path |
| idmap RocksDB SST truncate | fail/open | fail/open | structural detection |
| reconciliation `.bin` flip | success | success | not consulted by query |
| reconciliation `.bin` truncate | success | success | not consulted by query |

A bit flip that is not on a read path is not proof that no checksum exists;
source inspection shows the native full-content checksum is disabled by
default. The probe demonstrates that successful `open`/query is not a full
integrity guarantee.

## 5. Options

### A. Native verifier

**Not available as an API.** It would require native changes and a new C/Rust
entry point. The underlying CRC32c path exists but is default-off and only
covers index content, not every scalar/RocksDB/metadata file. Estimated cost:
full read of index content, page-cache pressure, and a native API/version
commitment. Concurrency: needs an immutable generation or read-only snapshot.

### B. External checksum verifier

Feasible without engine changes if run on a **sealed generation**:

- walk generation files;
- hash file bytes + relative path + size (SHA-256);
- store a manifest/seal after successful finalize;
- verify against a read-only copy or quiesced generation.

Coverage: truncation, replacement, bit rot, missing files, whole-file
corruption. Blind spots: HNSW/scalar semantic consistency, deleted/tombstone
semantics, checksums embedded inside RocksDB/Proxima, and anything that changes
while hashing. Cost: full generation read; the 2,000-file fixture is ~900 MB.
Concurrency: do not hash the active writing segment; use read-only generation
or snapshot. Recovery: a manifest is only valid for the exact generation ID.

### C. Hybrid

Recommended target:

1. external generation digest for immutable files;
2. native `open` for structural recovery;
3. enable native Proxima CRC in a future native build and expose it;
4. optional targeted RocksDB verification for idmap/FTS/scalar;
5. never call this a full semantic verifier.

This gives cheap structural coverage now and deep content coverage only in an
explicit verification mode.

## 6. Verdict

**PARTIAL.**

- External whole-generation checksum verification is **FEASIBLE**.
- Native verifier reuse is **BLOCKED** without new native API/version work.
- Current `open`/`iter`/`flush`/`optimize` are not verification.
- A successful open is not evidence of data-page integrity.
- Exact native version provenance is weak: the pinned Rust binding is exact, but
  the prebuilt native library reports `v0.0.0`; use its SHA-256 as the binding
  identity.

No verifier was implemented and no production index was touched.

Evidence: `benchmarks/harness-v2/operations/deep-storage-integrity/results/probe-results.json`.
