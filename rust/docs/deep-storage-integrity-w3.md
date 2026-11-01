# Deep Storage Integrity — W3 Native Verifier

Status: **PASS for W3 acceptance.** W4 remains a draft scope pending the owner
decisions listed in
[`deep-storage-integrity-w3-w4-plan.md`](deep-storage-integrity-w3-w4-plan.md).

Baseline: `52394f3` (W3/W4 plan). Date: 2026-11-01.
Native candidate source: local `zvec-native-profile` at `8f6d600` plus
`benchmarks/harness-v2/operations/deep-storage-integrity/w3/patches/w3-integrity-verifier.patch`.

Scope: read-only native verification surface, coverage, generation identity and
read-only stability. No installed native library, production binary, scheduler,
UX V2, index format, ZGM2, policy or threshold was changed.

## 1. What changed

### 1.1 Native C API

`src/binding/c/integrity_verify.cc` adds a read-only verifier exported from
`libzvec_c_api.so`:

```c
int zvec_collection_verify_integrity(const char *collection_path,
                                     unsigned int flags,
                                     zvec_integrity_report **out);
int zvec_integrity_report_ok(const zvec_integrity_report *report);
int zvec_integrity_report_complete(const zvec_integrity_report *report);
const char *zvec_integrity_report_json(const zvec_integrity_report *report);
void zvec_integrity_report_free(zvec_integrity_report *report);
```

Flags: `ZVEC_INTEGRITY_ROCKSDB=1`, `ZVEC_INTEGRITY_PROXIMA_STRUCTURE=2`,
`ZVEC_INTEGRITY_PROXIMA_CONTENT=4`, `ZVEC_INTEGRITY_REQUIRE_COMPLETE=8`.
Flags `0` select RocksDB plus Proxima structure and content.

Return codes: `0=PASS`, `1=CORRUPT`, `2=INCOMPLETE` (including
`INCOMPLETE_LEGACY`), `3=INVALID_ARG`.

The report JSON carries `status`, `pass`, `complete`, `legacy`, `stable`,
`stability`, `stability_detail`, `generation`, per-category `coverage`,
`missing_coverage`, `limitations`, `errors` and per-component details.

The symbols are added to `src/include/zvec/c_api.h` and both export lists
(`exported_symbols.lds`, `exported_symbols.txt`).

### 1.2 Rust FFI wrapper

New opt-in crate `zg-integrity-native`:

- resolves the verifier symbols from the already-loaded native library with
  `libloading`; it does not link the W3 candidate at build time;
- returns `IntegrityError::Unavailable` when the installed library predates the
  verifier, so no normal open/search/index path changes;
- exposes typed `IntegrityReport`, `IntegrityStatus`, per-category coverage and
  component details;
- owns the native report with a drop guard;
- is **not** wired into `zg-engine`; W4 decides exposure.

### 1.3 Late-segment CRC gap

The W2b writer only recomputed checksums for the active header. A sealed header
whose last segment still had `data_crc=0` (observed as a 204-byte late
`HnswT4S0` in W2c) kept that gap.

`IndexMapping::refresh` now recomputes checksums per header using that header's
own `content_offset` and start offset:

- the active header is recomputed on every refresh;
- a sealed header is revisited only when its capability marker is absent, its
  `content_crc` is zero, or any non-empty segment still has `data_crc=0`;
- a failed recompute clears the capability marker and `content_crc` instead of
  publishing a partial seal.

Legacy zero-CRC files remain `INCOMPLETE_LEGACY`; they are never promoted to
`PASS`.

## 2. Coverage semantics

| Category | Check | Status |
|---|---|---|
| `proxima` | header/footer/segment-table CRCs, bounds, content CRC, per-segment CRC | `covered`; `partial` for legacy/zero-CRC |
| `rocksdb` | read-only open, `paranoid_checks`, `VerifyChecksum` over SST/MANIFEST/CF metadata | `covered` |
| `wal` | zero-length RocksDB logs in a sealed generation | `covered`/`not_present`; non-empty logs are `unsupported` → incomplete |
| `ipc` | Arrow footer, schema and every record batch | `partial` (structure), limitation `ipc.content_checksum` |
| `sidecars` | ZGM2 magic/version/identity/counts/lengths/trailing bytes | `partial` (structure), limitation `sidecars.content_checksum` |

`partial` means the format has no content checksum. It is listed in
`limitations`, not silently claimed as content-verified. Structural corruption
(magic, footer, counts, lengths, truncation, trailing bytes, missing collection)
is `CORRUPT`.

Generation identity is derived from the closest UUID ancestor of the storage
path. W4 compares it with the manifest identity.

Stability requires a stable generation: when `storage.lock` exists, the verifier
takes a non-blocking shared `flock`; an active writer makes the result
`INCOMPLETE` with `stable=false`. A sibling `build.json` also marks the
generation unstable. The verifier never creates, repairs or rewrites storage.

## 3. Fault matrix

Raw receipt:
`benchmarks/harness-v2/operations/deep-storage-integrity/w3/results/cases.json`
— **23/23 passed**.

| Case | Expected | Observed |
|---|---|---|
| clean control | `PASS_COMPLETE`, rc 0, read-only proof, identity, coverage names | pass |
| Proxima header flip | `CORRUPT`, rc 1 | pass |
| Proxima segment content flip | `CORRUPT`, rc 1 | pass |
| Proxima segment-table flip | `CORRUPT`, rc 1 | pass |
| Proxima tail truncate | `CORRUPT`, rc 1 | pass |
| RocksDB SST flip | `CORRUPT`, rc 1 | pass |
| RocksDB MANIFEST flip | `CORRUPT`, rc 1 | pass |
| RocksDB CURRENT corruption | `CORRUPT`, rc 1 | pass |
| IPC footer flip | `CORRUPT`, rc 1 | pass |
| IPC truncate | `CORRUPT`, rc 1 | pass |
| Sidecar magic flip | `CORRUPT`, rc 1 | pass |
| Sidecar truncate | `CORRUPT`, rc 1 | pass |
| Sidecar count overflow | `CORRUPT`, rc 1 | pass |
| Sidecar trailing bytes | `CORRUPT`, rc 1 | pass |
| Missing collection directory | `CORRUPT`, rc 1 | pass |
| Active writer held (`flock`) | `INCOMPLETE`, rc 2, `stable=false` | pass |
| Non-empty WAL | `INCOMPLETE`, rc 2 | pass |
| RocksDB-only subset flags | `INCOMPLETE`, rc 2 | pass |
| `REQUIRE_COMPLETE` on clean | `PASS_COMPLETE`, rc 0 | pass |
| Legacy zero-CRC | `INCOMPLETE_LEGACY`, rc 2, read-only proof | pass |
| Null path | `INVALID_ARG`, rc 3 | pass |
| Empty path | `INVALID_ARG`, rc 3 | pass |
| Missing path | `INVALID_ARG`, rc 3 | pass |

Clean and legacy read-only proofs compare a SHA-256 tree snapshot before and
after verification; no file changed.

## 4. Clean and large fixtures

- Clean: 201 files, 201 entities, 7 Proxima segments, 0 segments without CRC,
  capability marker present. `results/clean.json` → `PASS_COMPLETE`.
- Large: 2,001 files, 80,040 entities, 48 Proxima segments, 119,791,616 Proxima
  bytes, 0 segments without CRC, capability present.
  `results/large-clean.json` → `PASS_COMPLETE`.
- Legacy: W2c fixture with zero `content_crc`; `results/legacy-zero-crc.json` →
  `INCOMPLETE_LEGACY`, rc 2.

The large fixture exercised 48 segments but one meta header. The writer fix is
structural: sealed headers are revisited whenever a zero segment CRC or missing
capability is present, so the W2c zero-CRC residual cannot persist through a
seal regardless of header count.

## 5. Verification

```sh
cargo test -p zg-integrity-native
cargo clippy -p zg-integrity-native --all-targets -- -D warnings
cargo fmt -p zg-integrity-native

python3 benchmarks/harness-v2/operations/deep-storage-integrity/w3/run_cases.py \
  --library .local/native/zvec-native-build/lib/libzvec_c_api.so \
  --clean <clean-generation> --legacy <legacy-generation> \
  --out benchmarks/harness-v2/operations/deep-storage-integrity/w3/results/cases.json
```

- Native target: `cmake --build .local/native/zvec-native-build --target
  zvec_c_api -j 8` — success.
- Rust wrapper: 4 passed; `clippy -D warnings` clean; `rustfmt` clean.
- Fault matrix: 23/23.
- Installed `rust/target/release/libzvec_c_api.so` SHA-256 unchanged
  (`58381ac7…`); production `zg` not replaced.
- Patch reverse-check against the applied profile: pass.

## 6. Limits / non-goals

- No power-loss durability claim; W2c remains `SIGKILL` evidence only.
- `.ipc` and `.bin` payload bit flips inside structurally valid records are not
  detectable without a format checksum. They are reported as limitations.
- Non-empty WAL bytes are not verified. A sealed generation normally leaves only
  zero-length logs; any non-empty log makes the result `INCOMPLETE`.
- No automatic repair, rebuild or generation switch.
- No background scrub and no verification on every open.
- No scheduler, UX V2, ZGM2, index format, policy or threshold change.
- Deferred Maintenance #10 and native concurrent optimize remain closed.

## 7. Next — W4

W4 still needs the owner decisions from the plan:

1. manual-only verification or also scheduled/background;
2. on `CORRUPT`, whether only a user-initiated rebuild is allowed;
3. direct-only `--verify` first, or expose through `--server`;
4. whether production rollout is part of W4 or explicitly deferred.
