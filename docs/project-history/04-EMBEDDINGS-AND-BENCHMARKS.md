# 04 — Embeddings, Acceleration and Benchmarks

## 1. Model identity rules

Never collapse these identities:

- **catalog default** (configuration fallback);
- **workspace-selected model** (persisted in manifest);
- **actually loaded runtime**;
- **index schema/model identity**;
- **embedding artifact** (file/hash/revision);
- **device/backend** (CPU vs Vulkan/CUDA/Metal);
- **evaluation model**.

Current verified snapshot: installed index is **Potion Code 16M v2**, not
Gemma, even though a Gemma GGUF is cached. The catalog default is also
`local/potion-code-16m-v2` in the current source
(`rust/crates/zg-engine/src/models/catalog/resolution.rs:80-95`).

## 2. Model candidates observed in E4

| Model | Dim / context | Artifact | Role | Result |
|---|---:|---|---|---|
| `local/potion-code-16m-v2` | 256 / 1024 | ~32.5 MB, model2vec | default / active local | very fast; retrieval modest |
| EmbeddingGemma 300M Q8 | 768 / 2048 | 333,590,944 B, `b5ce9d77…` | reference embedding | compatible; quality reference |
| EmbeddingGemma QAT Q4_0 / Q8 | 768 / 2048 | 229–329 MB | quantization experiment | **BLOCKED** — projection head omitted |
| Jina v2 Base Code Q5_K_M | 768 / 8192 (2048 controlled) | 121,395,616 B | experimental code model | compatible community GGUF |
| CodeRankEmbed Q8 | 768 / 2048 | 146,029,792 B | experimental code model | compatible community GGUF |
| BGE Small EN v1.5 | 384 / 512 | ~61 MB ONNX q4 | small baseline | compatible; weaker retrieval |
| Qwen remote | provider-defined | remote | remote option | permission-gated; not used as active local |

The E4 inference/throughput/RAM table is valid; the E4 retrieval table is not
pure-route evidence after E5/E6.

## 3. E4 inference and resource observations

From `rust/docs/e4-embedding-selection-20261113.md:127-155`:

| Model | Batch | Doc tok/s | Doc vec/s | Query tok/s | Doc p50 | Loaded PSS | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|
| EmbeddingGemma Q8 | 1 | 484 | 2.62 | 498 | 369 ms | 402 MB | 880 MB |
| EmbeddingGemma Q8 | 16 | 524 | 2.84 | 571 | 5,531 ms/16 | 401 MB | 873 MB |
| Jina Q5_K_M | 1 | 353 | 2.11 | 407 | 458 ms | 171 MB | 278 MB |
| Jina Q5_K_M | 8 | 368 | 2.20 | 644 | 3,594 ms/8 | 172 MB | 274 MB |
| CodeRank Q8 | 1 | 552 | 2.77 | 463 | 355 ms | 181 MB | 267 MB |
| BGE Small | 1 | 3,617 | 18.13 | 2,989 | 47 ms | 85 MB | 160 MB |
| Potion | 1 | 808,017 | 4,491 | 445,861 | 0.22 ms | 116 MB | 131 MB |

This table measures model runtime, not a production SLA. Host and single-run
uncertainty apply.

## 4. E5 correction — the big measurement error

### Root cause

The benchmark harness passed both:

```rust
ContextOptions { query: Some(text), routes, ... }
```

`query` is a **primary** request that normalizes to **FTS + Vector**; `routes`
are supplemental. Every “FTS” or “Vector” request was therefore:

```
primary FTS + primary Vector + supplemental requested route
```

### Consequences

- E4 “FTS depends on embedding model” claim: **RETRACTED**.
- Native pure FTS is deterministic/model-independent when the fragment table is
  identical.
- E4 retrieval table route numbers are invalid as pure FTS/vector numbers.
- E3-01 smoke route table invalid as a route measurement.
- E3-02A baseline/attribution invalid; the **v2 gold review, hashes, index cost
  and resource measurements remain valid**.
- E3-02B’s `NO-GO` rationale has an invalid attribution dependency and requires
  owner review; do not automatically reopen it.
- Corrected Gemma in E3-02A erratum: FTS R@10 0.4444, vector 0.5679, hybrid
  0.6142; unique-anchor attribution 7/12/16, not 5/3/25.

### What is valid now

- Pure FTS on matched fragment layouts: 32/32 identical, top-10 320/320.
- E4 inference/throughput/RAM: valid.
- E4 QAT artifact blocker: valid.
- Hybrid numbers can be numerically reproduced but the old contract was
  contract-invalid; use corrected route contract.

## 5. E6 integrity contract

E6 added a fail-closed route contract and validity matrix:

- explicit route mode must match actual route modes;
- no duplicate primary/supplemental routes;
- no hidden `matched_by=vector` in a pure-FTS result;
- missing/invalid contract exits nonzero;
- E3/E4 dependency invalidation is recorded rather than silently repaired.

Use E6-compatible receipts only. Old E4 retrieval numbers must not be quoted as
current pure-route scores.

## 5b. No-op Fast Path — qualified and merged internally

The no-op path is a **correctness-sensitive fast path**, not a microbenchmark
trick. It lazy-opens native collections, performs a shared read-only preflight,
and skips flush/optimize/manifest rewrite only when the complete state proves a
true no-op. `published_state_current` prevents the dangerous false no-op where
native rows committed but the process died before manifest publication.

Historical qualification measurements (`HISTORICAL_USER_REPORTED`, 2026-11-11):

| Workload / metric | Baseline | Fast path | Reduction |
|---|---:|---:|---:|
| Dense no-op wall | 0.591 s | 0.047 s | ~92% |
| Mixed no-op wall | 0.575 s | 0.062 s | ~89% |
| Peak RSS | ~281 MiB | ~62 MiB | ~78% |
| No-op index writes | present | 0 | eliminated |

The later ChatGPT context reports a slightly different run (0.53 s → 0.06 s).
That is the same capability across runs, not a second implementation; do not
average or promote the best number. Changed-file indexing showed no systematic
regression. The path was integration-qualified, then merged through E8-P2.

Status: `MERGED_INTERNAL`; not the same as installed-runtime proof. The current
installed binary/index predates E8-P2.

## 5c. Foundation G1–G4 — microbench wins, no end-to-end win

The Foundation optimization track tried:

- **G1** fewer text clones/copies;
- **G2** typed scalar projection instead of transient JSON;
- **G3** smaller native fetch projections;
- **G4** indexed reconciliation instead of O(F²);
- **G5** streaming hash (tried, slower, rolled back).

Microbenchmarks improved, but end-to-end indexing did not move materially:
native storage, embedding inference and fragment cardinality dominate. The
lesson is architectural: optimize the measured end-to-end bottleneck, not a
plausible local allocation.

Synthetic dense 300-file C# corpus showed the cardinality effect: v2 ≈300
fragments → v3 ≈12,600 structural fragments; index ~8.56 MB → ~54.15 MB. Real
source composition differs, so this synthetic ratio must **not** be used as a
production vector-policy forecast.

Do not reopen G5/streaming-hash or speculative SIMD/mmap/allocator work without
new hotspot evidence.

## 6. E1 — bounded deferred maintenance

Status: **experimental branch only; not merged**.

- One-file edit cost dominated by native `optimize` (~1.19 s), not embedding or
  commit.
- Deferred optimization can cut wall time, but no-op/scan/open floor remains
  ~0.79 s; ceiling ≈ −42…−50%, not −75%.
- Bounded K=8 showed 0.81 s/edit vs 1.61 s control; disk growth ~1.13× over 8
  edits; catch-up costs remain.
- Historical arm table (`HISTORICAL_USER_REPORTED`):

| Arm | Wall/edit | FS blocks/edit | Verdict |
|---|---:|---:|---|
| Control | 1.61 s | 208,355 | baseline |
| A4 scalar | 1.66 s | 171,653 | FAIL |
| B2 | 1.31 s | 122,270 | FAIL |
| B4 | 0.97 s | 78,248 | FAIL wall gate |
| B8 | 0.81–0.91 s | 56,989 | INCONCLUSIVE (p95/disk growth) |
| B16 | 0.88 s | 32,792 | FAIL latency/growth |

B4 cut FS output ~62% but wall only ~38%; B8 cut writes further but did not
prove stable p95 and approached the disk-growth bound. E1 is therefore a
write-amplification trade, not a free win. Needs a maintenance/recovery policy;
not a production feature.

Do not claim E1 is in installed or maintained source.

## 7. E2 — content-addressed reuse

Status: **READY_FOR_PROMOTION_REVIEW** and included in E8-P2.

- E2-A same-pass dedup; E2-B cross-pass reuse bound to stored FP16 vector.
- Identity includes model/backend build/config/format/context and artifact.
- Reuse is fail-closed on unknown/stale identity; sidecars are optional cache
  metadata.
- Integration measurements: line shift 26.07 s → 9.75 s, local edit 2.97 →
  2.71 s, doc edit 3.50 → 3.03 s; high-change/duplicate controls show low reuse.
- No schema/index-version change.

### E2-C — in-flight coalescing

Status: **NO-GO for now**. Cross-file/cross-batch coalescing needs coordination,
singleflight, failure propagation and cancellation semantics. Natural duplicates
exist, but concurrent batches make a pass-level cache miss. Do not reopen merely
because duplicates exist.

### Measurement variants

The integration line reports 26.07 s → 9.75 s; the qualification line reports
~135.95 s → 3.07 s (~44×) on a line-shift workload; the ChatGPT context reports
142.7 s → 4.11 s and 56 vs 8 persisted-span rows. These are **different workload
sizes/runs**, not interchangeable speedups. The invariant is workload-dependent
reuse, with high vector parity; there is no universal E2 multiplier.

## 8. E7 — indexing scalability

Status: **NO_GO**; no candidate.

- Baseline 1k log: 142.3 s wall, 139.7 s embedding, 547.9 s user CPU, 2.21 GiB
  peak.
- Multi-sequence batch 2: 157.9 s, 612.3 s user, 2.21 GiB — worse.
- Multi-context: no effective gain.
- Bottleneck is model/inference and scan/open overhead, not a simple scheduler
  knob.
- Historical scaling slices reported ~142 s/1k, 285 s/2k, 445 s/3k; 4k/8k
  attempts were incomplete/out of scope. The near-linear growth is consistent
  with per-fragment inference + storage cost, not proof of a specific model
  asymptote.

## 9. E8 — embedding acceleration

Status: **DONE, explicit opt-in accelerator; CPU default**.

- Vulkan/CUDA/Metal are explicit device choices; no silent CPU fallback.
- Vulkan/CUDA/Metal indexes are **rebuild-only for writes** in P2; incremental
  accelerator write fails closed.
- Vulkan query-only access remains supported.
- 40-file cold: CPU 282.76 s vs Vulkan 77.41 s (3.65×); CPU user 1,087.70 s vs
  Vulkan 20.62 s; peak RSS 2.07 GB vs 0.70 GB.
- Hybrid R@10/R@50/nDCG/solved identical CPU/Vulkan: 0.6142 / 0.8025 / 0.5387 /
  0.4444.
- Pure vector Vulkan K=50 loses one anchor; this is why Vulkan is not default.

## 10. E8-P1 / E8-P2 hardening

E8-P1:

- explicit device identity/fail-closed;
- 1k-log peak RSS 2,229,472 KB CPU vs 696,340 KB Vulkan (3.20× less);
- CPU/Vulkan matrix; hybrid exact parity;
- CPU writes incremental; accelerator writes rebuild-only.
- The supplementary measured 40-file run reports CPU 279.31 s vs Vulkan 69.44 s
  (~4.03×), while E8-P2 integration reports 282.76 s vs 77.41 s (~3.65×).
  Different runs/builds; keep both as `HISTORICAL_USER_REPORTED` variants and
  use the E8-P2 receipt for the merge claim.
- Output determinism was checked by separate CPU/Vulkan expected digests; the
  accelerator is a performance option, not a quality change.

E8-P2:

- merges Index v3, no-op fast path, E2 reuse and E8/P1 into maintained branch;
- CPU default; Vulkan opt-in;
- version-3 index; version-2 is rejected with rebuild guidance, no auto-migrate;
- legacy `auto` accelerator index is query-only;
- no installed binary/daemon/index change by the merge.

## 11. Benchmark quality boundaries

- E3/E4/E6 corpora are small and synthetic/C#-only in places.
- E3-01 uses 392 real C# files from Dapper/Newtonsoft/ANTLR, 32 queries, 54
  anchors, 20 hard negatives; it is a benchmark, not full HRM2.0.
- E4 model quality after E5/E6 is **provisional**, not promotion-ready.
- R@10 fractions are case-level; do not compare tables with different
  denominators, budgets or contamination status.
- No human-independent quality review; no p95/concurrency SLA except isolated
  smoke measurements.

## 12. Distillation — design discussion only

No distillation implementation, student artifact, training receipt or versioned
design document exists in the repository/evidence scope. The historical ChatGPT
context contains a **design discussion**, so the state is
`HISTORICAL_USER_REPORTED / DESIGN_ONLY`, not an implemented capability:

- motivation: replace a slow 300M teacher with a small code embedding student;
- possible teacher roles: frontier LLM for query generation, stronger embedding
  teacher for labels; no teacher was benchmarked for this purpose;
- possible approaches: synthetic query→passage pairs, hard negatives,
  distillation/contrastive training, ONNX/int8 export;
- open risks: teacher-family bias, query leakage/contamination, private-source
  egress, regression on hard negatives, model/index identity and rollback;
- owner criterion reported in conversation: do **not** train a student from only
  ~32 queries; require a substantially larger, versioned source-grounded set
  and independent evaluation.

Do not treat this as a roadmap commitment or infer a trained student. A future
distillation project would need its own preregistration, data/licensing review,
teacher identity, training receipts and fresh held-out evaluation.

## 13. Evidence anchors

- `rust/docs/e4-embedding-selection-20261113.md:54-75,127-155,161-235`.
- `rust/docs/e5-fts-reproducibility-20261114.md:1-80`.
- `rust/docs/e6-benchmark-integrity-20261114.md:1-100`.
- `rust/docs/e3-01-retrieval-evidence-benchmark-20261112.md:1-100`.
- `rust/docs/e3-02a-independent-gold-and-attribution-20261112.md:1-70`.
- `rust/docs/e7-indexing-scalability-20261114.md:1-180`.
- `rust/docs/e8-embedding-acceleration-20261114.md` and
  `rust/docs/e8p1-vulkan-hardening-20261114.md`.
- `rust/docs/e8p2-integration-20261115.md:13-40,65-150`.
- `rust/crates/zg-engine/src/models/catalog/resolution.rs:80-95`.
- Historical supplement `history/MASTER-HANDOFF-20261113.md` at
  `/home/masis/downloads/zvec-grep-project-context-20261115/` (user-reported; not
  repo-fetched); see `SOURCE-REGISTER.md`.
