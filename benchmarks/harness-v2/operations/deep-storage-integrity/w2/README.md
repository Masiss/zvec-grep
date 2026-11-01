# W2 native integrity PoC

Verdict: **PARTIAL**. RocksDB `VerifyChecksum` detects SST/metadata corruption.
The current Proxima mmap writer leaves HNSW data segments with zero CRC and
`content_crc=0`, so HNSW bit flips and tail truncation are not detected.

See `rust/docs/deep-storage-integrity-w2.md` for the full result and W3 gate.

## Build

Requires the local native checkout and build tree:

```sh
./build.sh
```

Outputs `build/libzvec_w2_verify.so` and `build/w2_verify`. The installed
`libzvec_c_api.so` is not touched. `w2_verify.cc.in` is renamed from `.cc` so
generic C++ language servers do not try to compile it without the native
include paths; compile it with `-x c++`.

## Probe

```sh
python3 run_cases.py \
  --base <sealed-generation>/storage \
  --verifier build/w2_verify \
  --second-proxima <other-valid-index>.proxima \
  --out results/cases.json
```

`results/cases.json` contains the raw reports; `results/case-summary.json` is the
compact matrix. `results/provenance.json` records source/archive/compiler
hashes. `results/resources.json` records clean-run PSS/wall samples.

## Interpretation

`pass=true` means no checksum or structural failure was found. Read
`coverage_complete` and `unprotected_bytes`: for the current HNSW fixture the
verifier passes but explicitly reports incomplete coverage. Do not treat that
as proof of data integrity.
