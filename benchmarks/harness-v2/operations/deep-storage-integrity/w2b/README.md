# W2b native checksum writer

Status: checksum-writer acceptance **PASS**. Generation crash atomicity is
resolved by W2c (`d366692`). See `rust/docs/deep-storage-integrity-w2b.md` and
`rust/docs/deep-storage-integrity-w2c.md`.

## Patch/build

The native patch is `patches/w2b-checksums.patch`. It applies to the local
`zvec-native-profile` checkout at `8f6d600…`:

```sh
git -C .local/native/zvec-native-profile apply \
  benchmarks/harness-v2/operations/deep-storage-integrity/w2b/patches/w2b-checksums.patch
cmake --build .local/native/zvec-native-build --target zvec_c_api -j 8
cp .local/native/zvec-native-build/lib/libzvec_c_api.so <scratch>/libzvec_c_api.so
```

Do not overwrite the installed `rust/target/release/libzvec_c_api.so`. Use a
scratch copy of `zg` with the custom `.so` in `$ORIGIN`. Restore the source and
the local build `.so` afterward.

The verifier source is `w2_verify.cc.in`. It must be compiled against the patched
`IndexUnpacker` header (content offset change). The test-only seal-failure hook
is not included in the patch.

## Results

- `results/new-clean.json` — new index `pass_complete`.
- `results/legacy-incomplete.json` — old index `pass_incomplete`, exit 2.
- `results/cases.json` — W2 matrix on a W2b index.
- `results/case-summary.json` — compact fault-detection matrix.
- `results/index-cost.json`, `cost-summary.json`, `rebuild-repeats.json` —
  checksum creation cost.
- `results/large-clean.json`, `large-resource.json` — larger verify cost.
- `results/seal-failure.*` — forced seal failure logs.
- `results/crash-active.json`, `crash-query.txt` — incremental crash evidence.
- `results/provenance.json` — exact source/patch/binary hashes.
