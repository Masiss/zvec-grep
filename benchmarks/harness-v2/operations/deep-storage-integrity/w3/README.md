# W3 — Native Integrity Verifier Surface and Coverage

Status: **PASS for W3 acceptance.** W4 remains a draft scope pending owner
decisions. Design and results:
[`rust/docs/deep-storage-integrity-w3.md`](../../../../../rust/docs/deep-storage-integrity-w3.md).

## Contents

- `patches/w3-integrity-verifier.patch` — native candidate patch. Contains the
  W2b checksum writer, the W3 verifier source/header/export wiring and the
  late-segment CRC fix. Applies to the local native profile at `8f6d600`.
- `verify_cli.py` — ctypes entry point for the new C API.
- `run_cases.py` — fault matrix: clean, Proxima, RocksDB, IPC, sidecar,
  missing-collection, active-writer, WAL, subset flags, legacy and invalid
  arguments.
- `results/cases.json` — 23/23 passing fault matrix.
- `results/clean.json`, `results/large-clean.json`,
  `results/legacy-zero-crc.json` — representative reports.
- `results/provenance.json` — source/patch/source-file/library hashes, fixture
  identities and the unchanged installed library hash.

## Run

```sh
cd /home/masis/src/zvec-grep

# Native candidate (never installs or replaces the prebuilt library)
cmake --build .local/native/zvec-native-build --target zvec_c_api -j 8

# Representative single verification
python3 benchmarks/harness-v2/operations/deep-storage-integrity/w3/verify_cli.py \
  .local/native/zvec-native-build/lib/libzvec_c_api.so \
  <generation>/storage

# Full fault matrix
python3 benchmarks/harness-v2/operations/deep-storage-integrity/w3/run_cases.py \
  --library .local/native/zvec-native-build/lib/libzvec_c_api.so \
  --clean <clean-generation> \
  --legacy <legacy-generation> \
  --out benchmarks/harness-v2/operations/deep-storage-integrity/w3/results/cases.json
```

## Coverage limits

`.ipc` and reconciliation `.bin` files are verified for structure only: both
formats have no content checksum. Payload bit flips inside a valid-looking
record are reported through `limitations` (`ipc.content_checksum`,
`sidecars.content_checksum`), not silently claimed. Arrow footer/schema/batch
metadata corruption and sidecar magic/count/length/trailing-byte corruption are
detected. Non-empty RocksDB WAL files are explicitly unsupported and force
`INCOMPLETE`.

No installed native library, production binary, scheduler, UX V2, index format,
policy or threshold was changed.
