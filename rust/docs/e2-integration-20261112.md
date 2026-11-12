# E2 final hardening and isolated integration

Date: 2026-11-12  
Integration branch: `work/e2-integration-20261112`  
Integration HEAD: `5744704` (report commit follows)  
Base: `work/noop-fastpath-integration-20261111` @ `24af3e6`  
E2 source reused: `work/e2-content-addressed-20261111` @ `8ee9790`  
Main untouched: `work/zg-recovered` @ `9444eed`

## 1. Integration composition

`8ee9790` already has `24af3e6` as its merge-base (`git rev-list --left-right --count
24af3e6...8ee9790` = `0 4`). The integration branch therefore starts at `8ee9790` and
contains the complete E2 chain, with no cherry-pick and no duplicate adaptation:

- `0b1c667` E2-A/B implementation
- `e93b5db` sidecar bound to stored vectors
- `d1e98b7` ownership + readback fail-closed
- `8ee9790` prior qualification report
- `5744704` this hardening

E2-C, E1, G1–G4, E3 and experiment harnesses are absent. No schema, index version,
canonical format, migration behavior or vector policy changed.

## 2. Backend identity hardening

`crates/zg-engine/build.rs` now hashes:

- workspace `Cargo.lock`;
- `crates/zg-engine/src/models` (all backend/runtime sources);
- `crates/zg-llama-native`;
- `build.rs` itself.

The hash is exported as `ZG_EMBEDDING_BACKEND_BUILD` and is part of the persisted
embedding-input fingerprint:

- `llama-cpp-embedding-v3|backend_build=<hash>|format|context|uri|artifacts`
- `model2vec-embedding-v2|backend_build=<hash>|repo@revision|files|normalize|prefixes|artifacts`

The previous manually maintained llama revision constant is gone. This closes the
failure mode where a dependency or wrapper change kept the old cache identity. `build.rs`
also emits `rerun-if-changed` for every hashed file, not only the top-level directory;
an early version missed nested test/source edits, which was found and fixed.

Mechanism check (external): changing the llama backend source changed the generated
identity from `7e700b…` to `ab1252…`; restoring the file restored `7e700b…`. Final
release and debug builds agree on `9b190d9b…f79804`.

Policy by backend:

- `llama.cpp`, `model2vec`: stable cross-pass identity + persisted reuse.
- `qwen`, `transformers`: no declared stable identity -> cross-pass reuse (E2-B) is
  disabled and no persisted fingerprint is written. Indexing still works.
- Same-pass dedup (E2-A) is now a separate `SamePass(model, digest(effective input))`
  key. A backend without a cross-run identity can still deduplicate identical inputs
  within one pass, but that key is never persisted and never authorizes E2-B.

Artifact/template/config changes are covered by model metadata and the opaque backend
identity. Legacy v1 sidecars cannot match the v2 fingerprint. Backend-upgrade, config,
unknown-identity and legacy-fingerprint tests were added.

## 3. Crash, publication and sidecar recovery

Added a fault-injected boundary test:
`stale_sidecar_after_failed_publication_cannot_reuse_replaced_vector`.

It commits new native rows with a changed vector, fails manifest publication, then
verifies that the stale sidecar cannot authorize the replaced vector (vector-fingerprint
mismatch -> re-embed). Readback failure is non-fatal to the committed file and publishes
an empty sidecar, so indexing succeeds and the next pass re-embeds.

Existing coverage still passes: torn/missing/corrupt sidecar, rename/replacement,
storage crash fixtures, reopen, concurrent readers/writers, cursor invalidation, No-op
Fast Path and Index v3. The exact SIGKILL instant between native commit and sidecar
rename is not fault-injected; it remains a cache-miss window, not a reuse window.

## 4. Independent integration qualification

Corpus: 14 real C# entity files, 217 entities / 229 fragments; local
`embeddinggemma-300M-Q8_0` artifact verified offline. Control is the baseline binary
from `24af3e6`; integration is `5744704`. Two paired repetitions per workload on the
same host. Medians:

| workload | control | integration | E2 hits |
|---|---:|---:|---:|
| line shift | 26.07 s / 93.15 s CPU | **9.75 s / 29.82 s CPU** | 199/229 reused; 30 requests |
| local edit | 2.97 s / 5.91 s CPU | **2.71 s / 2.86 s CPU** | 13 reused |
| doc edit | 3.50 s / 5.47 s CPU | **3.03 s / 2.91 s CPU** | 12 reused |
| duplicate | 4.33 s | 4.24 s | 0 (two files split across batches) |
| high change | 13.15 s | 12.85 s | 1 reused (low-reuse control) |
| rename | 2.73 s | 2.73 s | 0 (delete + add) |
| no-op | 29.4 ms | 30.7 ms | none |

Line shift: **2.67x wall, 3.12x CPU**, RSS unchanged (~1610 MB); no storage growth.
Local/doc edits gain CPU but wall is dominated by model startup. Duplicate gains nothing
when copies exceed the model batch and are split; this is a known E2-A batching
limitation, not a regression. Rename, no-op and high-change are neutral.

A 64-file generated-heavy stress corpus showed only 730/1161 reused and ~9% wall
improvement: prepending a line changes large top-level generated entities. E2-B benefit
is content/structure dependent; it is not a universal speedup claim.

Vector equivalence on 229 common fragments:

- cosine mean 1.0, min 0.99999976;
- max absolute component error 0.0625 (FP16 step);
- max norm drift 0.0204;
- 53/229 exact FP16 fingerprints;
- top-10 vector query paths, order and scores identical for 4 queries
  (`CamCamera`, `public int`, `ApplicationUser`, `CreatedAt`).

Full suite: **757 passed, 0 failed, 25 ignored**, all integration targets pass;
`cargo fmt --check` passes. Clippy: 32 warnings, no build-script warning; remaining
warnings are existing pedantic categories. Native byte I/O counters read 0 in the
sandbox, so byte-level I/O is UNKNOWN; block footprint was unchanged.

## 5. Gates and verdict

| gate | result |
|---|---|
| no false reuse | PASS |
| fail closed on unknown identity / stale storage | PASS |
| crash/rollback/reopen safe | PASS (cache-miss window only) |
| FTS/vector/hybrid/exact/source/freshness parity | PASS |
| Index v3 + No-op Fast Path | PASS |
| E2-A/B benefit above noise on eligible workloads | PASS on line/local/doc; none expected on rename/no-op/high-change |
| no systematic low-reuse regression | PASS (within variance) |
| bounded resources | PASS |

**Verdict: READY_FOR_PROMOTION_REVIEW.**

Remaining conditions for promotion:

1. Keep the build-derived backend identity; any backend/native source change now
   invalidates persisted reuse automatically. Do not reintroduce a manual revision.
2. Accept that E2-A only deduplicates inputs that land in the same batch; cross-batch
   coalescing remains out of scope (E2-C NO-GO).
3. E2-B gains depend on content structure; generated top-level entities and renames
   will re-embed. Do not claim a universal speedup.
4. SIGKILL at the exact sidecar-publication instant is not injected; recovery is safe by
   construction (missing/torn/stale sidecar is a miss), but not measured end-to-end.
