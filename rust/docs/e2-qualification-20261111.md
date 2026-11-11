# E2 — Final correctness and performance qualification

Date: 2026-11-11/12  
Branch: `work/e2-content-addressed-20261111`  
Baseline: `work/noop-fastpath-integration-20261111` @ `24af3e6`  
Commits under test: `0b1c667`, `e93b5db`, plus qualification fixes `d1e98b7`.

## 1. Verdict

- **E2-A (batch-local embedding-input dedup): GO.**
- **E2-B (content-addressed persisted vector reuse): GO.**
- **E2-C (pass-level cross-file cache): NO-GO; not implemented.**
- Overall: **READY_FOR_INTEGRATION_REVIEW**, not merged and not released.

No false reuse was found. No FTS/vector/exact-symbol/source/freshness regression was found.
The performance gains are large on identity/range churn and absent on no-op/low-reuse work.

## 2. Source and identity audit

Reuse eligibility and vector integrity are independent checks:

1. `embedding_input_fingerprint` = SHA-256 of
   `(EMBEDDING_FINGERPRINT_VERSION, EmbeddingModelInfo, backend identity, effective Content[])`.
   Version is now `2`; v1 sidecars cannot match.
2. The sidecar entry stores `(fragment_id, model, embedding_fingerprint, vector_fingerprint)`.
3. `vector_fingerprint` is SHA-256 of the FP16 bits decoded **from native storage**.
   `e93b5db` binds it after a storage round trip instead of hashing the encoder input.
4. Reuse requires an exact `(model, embedding_fingerprint)` match, then an exact
   `vector_fingerprint` match for the fetched vector. Any miss re-embeds.
5. `d1e98b7` scopes fetched rows to the sidecar's owning `file_id`; a sidecar cannot
   read another file's vector.
6. Backends without a stable identity (`qwen`) produce an empty fingerprint and are
   never deduplicated or reused. `model2vec` and `llama.cpp` provide explicit identities.
   `llama.cpp` identity now includes the pinned `llama-cpp-2` git revision.

Manifest sidecars remain format version 2 and are advisory. A missing, corrupt, torn,
foreign, or unverifiable sidecar is a cache miss. `d1e98b7` also makes a readback failure
non-fatal to an already-committed file: it publishes an empty sidecar and re-embeds next
time rather than failing indexing.

## 3. Adversarial and recovery evidence

Added/verified tests:

- content-addressed reuse across changed EntityId/FragmentId/range;
- rejection on changed input, model, vector fingerprint, or unstable identity;
- sidecar bound to stored vector, v1→v2 fingerprint incompatibility;
- per-file vector ownership;
- readback failure commits rows but publishes no reuse evidence;
- duplicate inputs share one vector without merging fragment identity or FTS;
- torn/missing sidecar, rename/replacement, partial sidecar decode, reopen;
- existing storage crash, concurrent reader/writer, cursor and retrieval-parity suites.

Full suite (serial): **755 passed, 0 failed, 25 ignored**, plus all integration targets
(8/1/4/17/8/1/2/11/2/3/2) passing. `cargo fmt --check` passes. No test was weakened.

Cross-binary test with the baseline binary (`24af3e6`):

- baseline reads a v2 sidecar: manifest hit `0`, re-embeds; no reuse.
- new binary reads a v1 sidecar: `content_addressed_reused=0`, re-embeds 9/9.

Crash boundary: sidecar publication uses atomic write; existing crash/torn-sidecar tests
cover missing or partial files. The exact SIGKILL window between native commit and sidecar
publication was not fault-injected; it remains a cache-miss window by design.

## 4. Vector equivalence (real `local/embeddinggemma-300m`)

Reused index vs fresh rebuild, 341 common fragments, and fresh-vs-fresh baseline:

| metric | reused vs fresh | fresh vs fresh |
|---|---:|---:|
| common fragments | 341 | 341 |
| cosine mean | 1.0 | 1.0 |
| cosine min | 0.99999958 | 0.99999988 |
| max absolute component error | 0.0625 | — |
| max norm drift | 0.0402 | — |
| exact FP16 fingerprints | 64/341 | — |

The residual difference is FP16/batch-dependent inference, not reuse: the same magnitude
appears between two independent fresh builds. Top-10 vector query paths, order, and scores
are identical for 8 queries (`camera`, `ApplicationUser`, `public int`, `CreatedAt`,
`Guid Id`, `namespace`, `get; set;`, `DbSet`). No observable retrieval-quality change.

## 5. Paired performance qualification

Host: same machine, local llama.cpp embeddinggemma-300m, 421-file source subset
(24 files / ~267 entities / 341 fragments), fresh index copy per trial. Three repeats for
line-shift/duplicate/no-op, two for the rest, alternating arm order. Medians shown;
ranges in parentheses.

| workload | P0 | E2-A | E2-B / A+B | actual inputs |
|---|---:|---:|---:|---:|
| line shift | 135.95 s (135.24–137.97) | — | **3.07 s (3.07–4.21)** | 341 → 24 |
| line shift + A | — | — | **3.02 s (2.88–4.00)** | 341 → 24 requested / 13 unique |
| duplicate | 3.26 s (2.98–4.65) | **2.44 s (2.39–2.54)** | n/a | 12 → 6 |
| local edit | 2.72 s (2.60–2.84) | — | **2.17 s (2.10–2.25)** | 7 → 2 |
| doc edit | 2.70 s (2.55–2.85) | — | **2.30 s (2.22–2.38)** | 6 → 2 |
| rename/move | 2.74 s (2.66–2.82) | — | 2.81 s (2.81–2.81) | 6 → 6 (no reuse) |
| high change | 19.8–26.6 s | — | **7.37–7.99 s** | 53 → 25 |
| no-op | 34.9 ms (28.7–50.7) | — | 36.9 ms (31.3–49.8) | none |

CPU and memory on line shift: **520.97 s → 6.23 s CPU**, **2141.6 MB → 838.0 MB RSS HWM**.
Duplicate: **7.10 s → 4.07 s CPU**. Rename/no-op are within run variance (no systematic
regression). Storage blocks are identical across arms; sidecar size is 38,235 bytes for the
341-fragment index. Native read/write counters were unavailable (`/proc/self/io` read 0
inside the sandbox), so byte-level I/O is UNKNOWN; block counts are reported instead.

## 6. Gate table

| gate | result | evidence |
|---|---|---|
| no false reuse | PASS | fingerprint + ownership checks; negative tests |
| correct stored vector/model/input | PASS | round-trip sidecar binding; cosine/query parity |
| recovery/rollback fail-closed | PASS | torn/foreign/readback-failure tests; v1/v2 cross-binary |
| FTS/vector/exact/source/freshness parity | PASS | full suite + 8-query parity |
| no unbounded growth/leak | PASS (bounded) | pass cache removed; sidecar bounded 4 MiB/file; fixed storage blocks |
| savings above noise | PASS | line shift 44× wall, 84× CPU; duplicate 25% wall / 43% CPU |
| low-reuse no systematic regression | PASS (within variance) | rename +2.6%, no-op +2 ms |
| repeated runs support claim | PASS | 2–3 repeats, alternating order |

## 7. Risks and integration plan

- E2-B benefit is workload-dependent: strong for line/range/identity churn, neutral for
  local edits already covered by same-id reuse, none for rename/move (delete+add).
- `llama-cpp-2` revision is embedded as an explicit constant; it must be updated with the
  workspace dependency. `model2vec` identity does not include a backend binary revision.
- FP16 delivery is not bitwise reproducible across batch composition; equality claims must
  use retrieval/cosine tolerances, not byte equality.
- Readback adds one native fetch per modified file. Measured readback time is 24 µs for a
  24-file line-shift pass on this corpus; scaling was not tested on very large files.
- E2-C remains NO-GO: a bounded completed-vector pass cache produced 0 hits across
  concurrent batches; implementing in-flight coalescing is out of scope.
- Migration: none. Sidecars are versioned and advisory. Rolling back to the baseline binary
  ignores v2 sidecars and re-embeds; no index schema change.

Integration review should focus on the sidecar ownership/readback code, the backend
identity constant maintenance, and the decision to keep E2-C out.
