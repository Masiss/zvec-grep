# W3/W4 Plan — Native Integrity Verification and Operation

Status: **W3 ready to start; W4 is a draft scope pending owner confirmation.**

Baseline: `6025b96` (W2c docs/handoff). Date: 2026-11-01.

## 1. Current state

- W2 proves RocksDB checksum verification and Proxima structural checks.
- W2b adds a custom native checksum writer: per-segment CRC32c plus
  `content_crc`, capability marker, seal-failure rejection.
- W2c makes ordinary incremental writes clone/publish a staged generation. A
  `SIGKILL` before publication leaves the old generation readable.
- The installed native library and production `zg` binary remain unchanged. No
  verifier is exposed through the C API or Rust FFI yet.
- W2c scratch binaries/fixtures were removed after the run. Retained evidence is
  under `benchmarks/harness-v2/operations/deep-storage-integrity/w2c/`.
- The local native checkout at `.local/native/zvec-native-profile` is `8f6d600`
  with **pre-existing local modifications** (16 files). The W2b checksum patch
  currently applies cleanly; verify with `git apply --check` before applying.

## 2. W3 — native verifier surface and coverage

### Objective

Expose a read-only integrity verifier that distinguishes complete, legacy
incomplete, and corrupt generations, with explicit coverage and generation
identity.

### Work items

1. **Rehydrate the native candidate**
   - Apply `w2b/patches/w2b-checksums.patch` to the local profile.
   - Build a scratch `libzvec_c_api.so`; record source commit, patch hash, build
     flags, and library SHA-256.
   - Never overwrite the installed/prebuilt library or the production `zg`.

2. **Close the late-segment CRC gap**
   - W2c observed a 204-byte late `HnswT4S0` segment with `segment_crc=0` while
     `content_crc` was valid.
   - Recompute per-segment CRCs in the final flush/seal after all segment writes,
     or explicitly document that content CRC is the completeness authority.
   - Rebuild repeatedly and check all segments across large fixtures.
   - Keep legacy zero-CRC behavior: `INCOMPLETE`, never `PASS`.

3. **Implement the C API**
   Use the minimal W2 surface:

   ```c
   int zvec_collection_verify_integrity(const char *collection_path,
                                        unsigned int flags,
                                        zvec_integrity_report **out);
   int zvec_integrity_report_ok(const zvec_integrity_report *report);
   int zvec_integrity_report_complete(const zvec_integrity_report *report);
   const char *zvec_integrity_report_json(const zvec_integrity_report *report);
   void zvec_integrity_report_free(zvec_integrity_report *report);
   ```

   - Flags: `ROCKSDB=1`, `PROXIMA_STRUCTURE=2`, `PROXIMA_CONTENT=4`,
     `REQUIRE_COMPLETE=8`.
   - Return codes: `0=PASS`, `1=CORRUPT`, `2=INCOMPLETE/UNVERIFIED`,
     `3=INVALID_ARG`.
   - Report must include status, coverage, missing coverage, generation identity
     when supplied, and component-level errors.
   - Read-only: never create/repair/rewrite storage.

4. **Rust FFI wrapper**
   - Add a narrow wrapper with owned report lifetime and no behavior change to
     normal open/search/index paths.
   - Keep it opt-in until W4 integration is approved.

5. **Coverage scope**
   - Proxima content/structure.
   - RocksDB FTS, idmap and scalar indexes (SST + metadata).
   - `.ipc` files and reconciliation `.bin` sidecars.
   - WAL and semantic delete/tombstone consistency: classify explicitly as
     covered, partial, or unsupported. Do not silently promote them to PASS.

6. **Generation identity and read-only safety**
   - Verify only a stable generation with no active writer.
   - Compare the supplied generation UUID/manifest identity with the storage
     being verified.
   - Refuse or return `INCOMPLETE` for an ambiguous/unstable target.
   - Do not verify an actively written active generation.

7. **Fault matrix**
   - Clean control.
   - HNSW bit flip and tail truncate.
   - RocksDB SST/metadata corruption.
   - `.ipc`/`.bin` corruption once covered.
   - Legacy zero-CRC index.
   - Truncated/replaced generation.
   - No false positives on clean controls.
   - Old indexes remain readable even when verification returns `INCOMPLETE`.

### W3 acceptance

- A clean W2b/W3 candidate returns `PASS_COMPLETE`.
- HNSW bit flip and tail truncate are detected (`CORRUPT`).
- Legacy zero-CRC returns `INCOMPLETE_LEGACY`, not `PASS`.
- Report includes generation identity and explicit coverage.
- Verifier is read-only and refuses an active-writer generation.
- Tests cover clean, legacy, corrupt and invalid-argument paths.
- Installed native/binary unchanged unless separately authorized.

### W3 non-goals

- Automatic repair or rebuild.
- Background scrub.
- Production migration or replacing the installed native library.
- Deferred Maintenance #10 or native concurrent optimize.

## 3. W4 — operational verification (draft)

**W4 scope is a proposal; confirm or replace it before implementation.**

### Candidate objective

Make W3 verification usable without turning it into an automatic repair system.

### Candidate scope

- `zg --verify [--json] [--require-complete]`.
- Direct mode verifies the active stable generation read-only.
- Server/daemon routing may expose the same verifier; do not add a scheduler lane
  or change scheduler capacity.
- Exit codes: `0` complete/PASS, `1` corrupt, `2` incomplete/unverified.
- JSON receipt includes generation UUID, coverage, status and errors.
- Clear operator guidance for `CORRUPT`/`INCOMPLETE`; never silently rebuild,
  delete, or switch generations.
- No automatic verification on every open and no background scrub unless the
  owner explicitly authorizes it.
- Measure cold/warm verification cost and document the scope; do not claim
  production readiness from a single fixture.

### W4 acceptance (draft)

- Clean index: command succeeds with complete coverage.
- Legacy index: command returns incomplete, not success.
- Injected corruption: non-zero exit and component-level error.
- Active writer/concurrent update: refuses or reports unsupported safely.
- Read-only proof: no storage/manifest changes after verification.
- Daemon path does not alter scheduler behavior.

### Owner decisions for W4

1. Is verification manual-only, or also scheduled/background?
2. On `CORRUPT`, is the only allowed action a user-initiated rebuild?
3. Should `--verify` be exposed through `--server` in W4, or direct-only first?
4. Is production rollout in W4, or explicitly deferred?

## 4. Resume checklist

```sh
cd /home/masis/src/zvec-grep
git status --short
git log --oneline -5

# Native patch applicability (do not apply blindly; profile is dirty)
git -C .local/native/zvec-native-profile apply --check \
  benchmarks/harness-v2/operations/deep-storage-integrity/w2b/patches/w2b-checksums.patch

# Build scratch native candidate; keep installed .so untouched
# See w2b/README.md and w2/README.md for the exact build path.
```

Read in this order:

1. `rust/docs/deep-storage-integrity-audit.md`
2. `rust/docs/deep-storage-integrity-w2.md`
3. `rust/docs/deep-storage-integrity-w2b.md`
4. `rust/docs/deep-storage-integrity-w2c.md`
5. `rust/docs/handoff-20261101-deep-integrity-w2c.md`
6. `benchmarks/harness-v2/operations/deep-storage-integrity/w2b/results/provenance.json`
7. `benchmarks/harness-v2/operations/deep-storage-integrity/w2c/results/`

## 5. Known limitations and risks

- W2c tested `SIGKILL` consistency only; no power-loss durability claim.
- Clean-scan V6 is metadata freshness, not deep integrity verification.
- The native profile is dirty; preserve unrelated local changes and verify patch
  context before building.
- W2c content CRC covers the full content region; per-segment CRC is for
  localization and is not yet guaranteed on every late segment.
- Three `zg-engine` authorization-fixture tests fail pre-existing; they are
  unrelated to storage integrity.
- W3/W4 must not change scheduler, UX V2, ZGM2, index format, policy or
  thresholds without explicit authorization.
