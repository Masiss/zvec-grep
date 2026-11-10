# Foundation investigation — no-op cost audit and E3 real-model pre-spike

Status: **investigation only**. No product behavior, schema, policy or benchmark
threshold changed. Main stays `9444eed`; foundation stays `d64e54e`; the E1
experiment branch (`6012ef4`) is referenced, not integrated.

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/investigation-20261110` |
| Branch | `work/foundation-investigation-20261110` |
| Base / HEAD | `d64e54e` + this change (harness `index_bench` profile phase, `e3_spike` example, this document) |
| Tooling | external `CARGO_TARGET_DIR`, temp/receipts/model cache under `~/.cache/zvec-grep/out-inv` |

Harness-only additions:

- `index_bench --phase profile`: in one process measures `info()` (manifest
  only), `info(include_status)` and three no-op `index()` calls, plus engine
  construction, close and mock-server spawn.
- `examples/e3_spike.rs`: index a real corpus with a real local model and dump
  ranked hits per route for offline scoring.
- `index_bench` now reports the mock server spawn duration.

---

## A. No-op indexing cost

### A.1 What a no-op pass actually costs

Fresh process, existing index, no changed file (`--change none`), five
repetitions per corpus, medians:

| Corpus | files | index | process wall | user CPU | RSS | process FS output |
|---|---:|---:|---:|---:|---:|---:|
| dense | 300 | 54.2 MB | **0.48 s** (0.47–0.55) | 0.28 s | 280 MiB | 6.3 k blocks ≈ 3.2 MB |
| mixed | 525 | 28.5 MB | **0.48 s** (0.47–0.48) | 0.28 s | 279 MiB | 6.2 k blocks ≈ 3.2 MB |

The E1 result reported 0.79 s for the same no-op. That number is reproducible
only when the pass follows a chained-edit sequence with cold caches; the
steady-state cold-process value measured here is **0.48 s**, and the spread
explains the earlier figure (host noise and cache state, not a different code
path).

### A.2 Fixed, per-file or per-byte?

Scaling the same no-op over six workspaces:

| Corpus | files | index size | no-op wall |
|---|---:|---:|---:|
| dense | 25 | 6.6 MB | 0.48 s |
| dense | 100 | 19.8 MB | 0.48 s |
| dense | 300 | 54.2 MB | 0.51 s |
| mixed | 100 | 7.3 MB | 0.49 s |
| mixed | 300 | 17.4 MB | 0.51 s |
| mixed | 525 | 28.5 MB | 0.50 s |

Flat from 25 to 525 files and from 6.6 MB to 54.2 MB: the cost is **fixed per
process/collections**, not per file and not per byte.

### A.3 Stage attribution (dense; same for mixed)

| Component | Measured | Evidence |
|---|---:|---|
| process exec + tokio runtime + teardown | ≈40 ms | process wall 480 ms − index call 440 ms |
| mock embedding server spawn (harness only) | 10 ms | harness `server_spawn_us` |
| `ZvecGrep::new()` | 0.1 ms | profile span |
| workspace manifest read (`info` without status) | 0.1–1 ms | profile span |
| **`index()` call** | **≈440 ms** | profile span |
| — index pipeline (scan 3.2, diff 0.1, everything else 0) | **3.2 ms** | `result.timings` |
| — storage open, file-table read, publish, close | **≈437 ms** | residual |
| close + flush + manifest publish (warm estimate) | ≈55 ms | warm index 180 ms − warm open 122 ms − pipeline 3 ms |
| **native collection open (raw zvec probe, cold)** | **356 ms** | `directories` 9.1, `entities` 7.6, `files` 10.7, **`fragments` 329.2** |
| native open (warm, same process) | 122 ms (fragments 96–104) | probe rounds 1–3 |

The raw probe opens only the index directories read-only and calls `stats()`,
so it isolates the native open cost from engine logic. The `fragments`
collection (FTS + HNSW + scalar) alone accounts for **~75 % of the whole no-op
pass on a cold process**, and the cost is the same for a 54 MB and a 17 MB
index — it is fixed index initialization, not data volume.

### A.4 A no-op pass still writes

Writing a fresh-process no-op pass and listing the touched files:

| Path | Size | Note |
|---|---:|---|
| `fragments_*/0/embedding.index.5.proxima` | **34.1 MB** | vector index file, mtime rewritten |
| `fragments_*/1/scalar.index.1.rocksdb/OPTIONS-*` | 64 KB | new options file per pass |
| `files/1/scalar.index.1.rocksdb/OPTIONS-*` | 55 KB | new options file per pass |
| `entities`, `directories`, `idmap` OPTIONS/MANIFEST | 7–29 KB each | rotated per pass |
| `.zvec-grep/manifest.json` | 1.3 KB | rewritten every successful pass |

Process FS output is ~3.2 MB, so the 34 MB file is not fully rewritten (the
kernel counts dirty-page writeback, not file size), but the pass does touch the
whole vector index mapping and rotates RocksDB metadata. **A read-only query
pass touches zero files**, which shows the churn comes from opening storage
read-write and flushing on close, not from reading.

### A.5 Source-confirmed mechanism

| Cost | Source |
|---|---|
| Every pass opens all four collections read-write | `storage/store/mod.rs:227` (`IndexStore::open`), `:283-289` |
| `info(include_status)` also opens storage (read-only) and lists files | `pipelines/indexing/pipeline.rs:238`, `:434` |
| The file table is read in full before the pipeline timings start, so its cost lands in the residual | `pipelines/indexing/pipeline.rs:434`, timings begin after |
| Close flushes **every** collection unconditionally | `storage/store/mod.rs:955-960`, `:701-760` |
| Fragment flush iterates all model collections | `storage/fragments/mod.rs:209-214` |
| Finalize calls native `stats()` on the fragments collection every pass | `storage/fragments/mod.rs:216-224` |
| The workspace manifest is rewritten (and `updatedTime` bumped) on every successful pass, even with no changes | `pipelines/indexing/service.rs:448-461` |

### A.6 What remains unexplained

- The split between native open work inside the 329 ms `fragments` open (HNSW
  graph load vs FTS reader vs RocksDB) is not exposed by zvec; **UNKNOWN**.
- The exact bytes actually written to the `proxima` file are not separable from
  process FS-block accounting; physical writes are **UNKNOWN**.
- Whether `updatedTime` bumping on a no-op is load-bearing for freshness
  opinions is not documented; it does invalidate symbol cursors (cursors bind
  generation + `updatedTime`).

### A.7 Top three opportunities

| # | Change | Expected benefit | Risk |
|---|---|---|---|
| 1 | Open only the collections a pass needs, and skip write-storage entirely when the diff has no candidates (read `files`/`directories` only) | no-op 0.48 s → plausibly **0.10–0.15 s** (−70 %); removes the 34 MB vector-index touch and ~3 MB of writes; also lowers every incremental edit by ~0.3 s | medium: must keep scan/freshness, coverage reporting and publication semantics; needs a read-only "plan" path and tests |
| 2 | Flush only collections that changed (track per-collection dirtiness; close currently flushes all four) | removes RocksDB OPTIONS/MANIFEST rotation and the 34 MB proxima touch per no-op; ≈3 MB fewer process writes per pass | medium-low: durability unchanged if dirty collections still flush; needs crash-barrier review |
| 3 | Do not rewrite the workspace manifest when nothing changed | ~1.3 KB + fsyncs per no-op, and stops invalidating symbol cursors on no-ops | low, but `updatedTime` semantics need an explicit decision |

**Verdict A: worth opening a bounded optimization experiment** (opportunity 1,
with 2 as a follow-up). The evidence is measured, size-independent and does not
require touching retrieval, freshness or coverage contracts.

---

## B. E3 real-model pre-spike

### B.1 Real model (verified, offline)

| Property | Value |
|---|---|
| Reference | `local/embeddinggemma-300m` |
| File | `hf_ggml-org_embeddinggemma-300M-Q8_0.gguf` (llama.cpp, Q8\_0) |
| Size / SHA-256 | 333 590 944 B / `b5ce9d77…90d63` — **matches** `EMBEDDINGGEMMA_ARTIFACTS` (`models/catalog/entries.rs:11-15`) |
| Dimension / metric | 768, cosine; FP16 storage; context 2048; max batch 16 |
| Use | private cache via hardlink + `ZVEC_GREP_HOME`, `allow_remote: false` — no download, no shared cache change |

### B.2 Corpus

Real, non-synthetic C# checked into this repository as vendored third-party
sources (not RAG fixtures, not held-out/gold): Apache Arrow C# and ANTLR4
Runtime C#. 60 files selected for doc-comment density (888 KB), copied outside
the repository; source tree untouched.

Measured index (real model, 768-d):

| Symbol type | entities | fragments (= vectors) |
|---|---:|---:|
| function | 970 | 981 |
| value | 533 | 533 |
| class | 91 | 289 |
| module | 58 | 58 |
| interface | 6 | 16 |
| none (gap/text) | 132 | 135 |
| **total** | **1 790** | **2 012** |

Measured composition: fragments collection 11.41 MB, entity table 3.01 MB, files
0.39 MB, reconciliation 0.35 MB; logical FP16 vectors 3.09 MB (27 % of the
fragments collection), FTS text 1.79 MB, remainder ids/scalars/RocksDB/HNSW
(HNSW share **UNKNOWN**).

Measured indexing cost: 797 s wall, 3 120 s user CPU, 2.3 GiB RSS, 75 k FS
blocks for 2 012 vectors ⇒ **≈0.40 s wall and 1.55 s CPU per vector** on this
CPU-only run (batch 16). This is the real cost that any vector-count policy
would reduce.

### B.3 Why the quality pre-spike is BLOCKED

No independent labeled retrieval corpus is available locally:

- SWE-QA-bench ships only task descriptors; its `references.json` is an isolated
  judge set and its repositories are absent — using it would touch held-out
  material.
- BrowseComp-Plus needs a 100 k-document download and is not code.
- Ground truth derived from the indexed source is not usable: the documentation
  text is part of **both** representations, by source —
  `lexical_text` appends `code.documentation` (`pipelines/indexing/pipeline.rs:1464-1489`)
  and `vector_metadata_text` prepends `doc: …` (`extraction/service.rs:210-225`).
  A query copied from a doc comment is therefore a literal substring of the
  FTS text and of the embedding input, for the same entity. Any score computed
  from such labels is self-fulfilling and cannot compare FTS against vector.

Consequently P0–P3 **quality** (R@10/50, MRR, nDCG, unique vector evidence,
distinct relevant sources) is **UNKNOWN** for every policy. No counterfactual
number is reported here, because a counterfactual ranking over a leaked label
set would still be meaningless.

### B.4 What is measured: cost per policy (real corpus, measured cardinality)

| Policy | vectors | vs P0 | logical vector bytes | embedding wall (measured rate) | embedding CPU | measured disk effect |
|---|---:|---:|---:|---:|---:|---|
| P0 all | 2 012 | — | 3.09 MB | 797 s | 3 120 s | baseline |
| P1 no containers (class+interface+module) | 1 649 | −18.0 % | 2.53 MB | −144 s | −565 s | ≈−0.6 MB vectors + proportional HNSW |
| P2 no `value` | 1 479 | −26.5 % | 2.27 MB | −211 s | −825 s | ≈−0.8 MB vectors + proportional HNSW |
| P3 containers only | 363 | −82.0 % | 0.56 MB | −653 s | −2 559 s | ≈−2.5 MB vectors + HNSW |

Disk is estimated from **logical** vector bytes; the HNSW graph share is
unattributed, so the whole-index disk reduction is a band: on this real corpus
P2 removes ≈5–8 % of the index, P3 ≈15–20 %. That is far below the −33…−48 %
projected from the synthetic property-dense corpus, because real C# is
function-dominated: `value` is only 26 % of vectors here versus 71 % there.

Policy risk (hypothesis, not measured): P2 removes semantic retrieval for
properties/fields (they remain exact-FTS searchable); P3 removes it for all
members, leaving only FTS for methods and properties.

### B.5 Verdict

- Real-model availability and offline operation: **GO** (verified).
- E3 **mechanism** experiment (split collections, policy = P0): **NO-GO for now
  / INCONCLUSIVE**. The mechanism is implementable (the earlier review rejected
  the nullable-vector shortcut) but yields ~0 bytes and ~0 time by itself; its
  only purpose is to enable P1–P3, whose acceptance depends on retrieval
  quality, which cannot be measured with the corpora available today.
- P1/P2/P3 policy: **INCONCLUSIVE** (no independent labeled corpus).

The binding blocker is the evaluation corpus, not the storage design.

---

## C. Evidence classes

| Class | Items |
|---|---|
| **Measured** | no-op wall/CPU/RSS/FS blocks at six corpus sizes; per-collection native open (cold/warm) with a raw zvec probe; touched-file inventory of a no-op pass; read-only pass touching zero files; real-model index time/RSS; real-corpus cardinality by symbol type; real index composition |
| **Source-confirmed** | unconditional collection open/flush, `stats()` per finalize, manifest rewrite per pass, documentation in both FTS and embedding text |
| **Hypothesis / UNKNOWN** | split of the 329 ms fragments open; physical bytes behind the 34 MB touch; HNSW share of disk; effect of `updatedTime` bumping on callers; P1–P3 quality |

## D. Next steps (ranked)

1. **Bounded no-op fast path** (open needed collections only; skip flush and
   manifest rewrite when nothing changed). Expected −0.3 s per pass and −3 MB
   writes, measurable with the harness added here; risk bounded by existing
   storage tests plus new read-only-plan tests.
2. **Decide on an evaluation corpus before any E3 work.** Without an external
   labeled set (or an owner-approved protocol) P1–P3 stay INCONCLUSIVE; the
   split refactor should not start first.
3. **Re-scope E1's expected benefit** using this audit: ~0.48 s of the ~0.85 s
   per-edit floor is no-op overhead, so maintenance deferral's wall headroom is
   smaller than reported; its write-amplification result stands.
