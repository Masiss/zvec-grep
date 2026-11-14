# E8 — Embedding compute & backend acceleration

**Status:** DONE — Vulkan candidate QUALIFIED_GO (explicit opt-in; CPU query for pure-vector parity); H2 affinity NO_GO.
**Branch:** `work/e8-embedding-acceleration-20261114` (base E7 `0e21001`)
**Main:** `work/zg-recovered` @ `6d5b523` (untouched)
**Receipts:** `/home/masis/.cache/zvec-grep/e8-20261114/receipts/`

## Hardware / backend inventory

- CPU: Intel Core i7-12700H, 6 P-cores / 8 E-cores, 20 logical CPUs, AVX2 + AVX-VNNI (no AVX-512).
- GPU: Intel Iris Xe Graphics (ADL GT2), Vulkan 1.4, Mesa 26.2.6, unified memory; no discrete GPU.
- llama.cpp CPU build: `GGML_NATIVE=OFF`, generic x86-64 flags, OpenMP off, Vulkan off.
- Vulkan build: `GGML_VULKAN=ON`, same model/weights; build used local SPIRV-Headers package and system Vulkan loader/glslc. No system package installation.
- Model: EmbeddingGemma 300M Q8 GGUF, 333,590,944 bytes, SHA-256 `b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63`.
- Index: v3, FP16 vector + HNSW; same schema/layout in both arms.

## Phase A — cost anatomy (MEASURED unless marked)

- E7 stage attribution: Gemma embedding 96–99% of log index wall; native commit/optimize small.
- Token probe (11 representative inputs: C# files/slices, README, 2 log chunks): 70, 224, 397, 401, 744, 1,257, 1,429, 1,856, 1,978, 2,030, 2,054 tokens. The 2,030/1,978/2,054 cases are above the model `max_input_tokens`/context and were truncated by the engine; the log chunks are not truncated.
- E4 C# workload token receipt (MEASURED, C# document fragments): p50 190, p95 246, max 419, total 47,315.
- Log 2,000-char chunks tokenize to ~1,257–1,429 tokens; C# fragments are ~5–10x shorter. This tracks the E7 per-fragment cost difference.
- Preprocessing/tokenization is small relative to decode; no separate repeated tokenization stage was found in the index path.
- Batch/ubatch: source inspection shows batch capacity = next power of two of max input (min 32, max context), and `gemma-embedding` uses the full ubatch. Log 1k is ~1,024 ubatch; normal C# ~256. No H2 re-test was performed.
- Cold load vs warm: Vulkan first-ever one-file run 138.3 s; subsequent warm one-file run 4.12 s. A later run with a fresh `MESA_SHADER_CACHE_DIR` took 8.13 s, so the 138 s was a first-ever host/driver/shader setup event, not a stable per-cache penalty. The driver shader cache is host-specific and not shipped by zg.
- Peak RSS: CPU Gemma ~2.06–2.23 GiB; Vulkan ~0.69–0.70 GiB.
- CPU utilization: CPU ~373–387% (4 threads); Vulkan ~30% host CPU, Iris Xe active.

## Phase B — hypotheses

### H1: Vulkan backend / Intel Iris Xe (same GGUF, same input format, same index layout)

Mechanism: move GGML matrix kernels from 4 CPU threads to the iGPU's Xe EUs; expected large wall and CPU/RSS reduction. Hardware supports Vulkan 1.4 and FP16; build feature exists in `zg-engine` (`vulkan`). Device is explicit (`--device vulkan` / `ZVEC_GREP_DEVICE=vulkan`), so no default change is needed.

**Verdict: QUALIFIED_GO** for explicit Vulkan indexing, with the query-device qualification below; hybrid quality is exactly matched on the full E6 gold.

| workload | CPU | Vulkan | speedup |
|---|---:|---:|---:|
| 40 C# cold index | 317.4 s, 2.06 GiB, 373% CPU | 102.6 s, 698 MB, 30% CPU | 3.09x |
| log 1k, current load | 170.0 s | 36.3 / 43.8 s repeat | 3.9–4.7x |
| 11-input vector probe | 44.37 s | 11.08 s | 4.00x |
| one C# file, warm | 6.60 s | 4.12 s | 1.6x |
| one C# file, first-ever Vulkan run | — | 138.3 s | cold shader penalty |

Vector parity (MEASURED): 11 inputs, dim 768, finite; cosine min 0.999404, mean ~0.99952; max abs 1.4589; norm drift max 1.09% (vectors are not unit-normalized). Full E6 matched-layout quality is reported below.

### H2: CPU affinity / P-core placement

Mechanism: keep the 4 llama threads on P-cores, avoiding E-core/HT placement. Under current load (~8.5–12), unpinned log 1k = 170.0 s; `0-11` = 179.7 s; `0,2,4,6` = 201.5 s; `0-7` = 210.9 s; E-cores `12-19` = 306.4 s. No tested placement improved; **NO_GO** (load-limited, but direction is clear).

## Correctness / identity

- Existing E2 embedding identity did not distinguish CPU from explicit Vulkan. CPU and Vulkan vectors are not bitwise equal (cosine 0.9994, norm drift), so a CPU sidecar could be mixed into a GPU index on incremental indexing. E8 adds an explicit device suffix to **explicit** accelerator identities; default/CPU identity is unchanged. Unit test `embedding_identity_binds_explicit_accelerator_device` PASS.
- E8 also adds a fail-closed index-device guard: changing `--device` against an existing index without `--rebuild` now errors (`existing index was built with a different embedding device`) instead of relabeling or mixing vectors. The guard covers a no-op pass too; unit test `device_change_requires_rebuild_guard` PASS. Cross-device probe with a changed file is rejected; same-device CPU and Vulkan increments reuse as expected. Auto/fallback remains a documented residual risk because the guard uses the requested/config device, not the actual fallback backend.
- `ZG_EMBEDDING_BACKEND_BUILD` still hashes backend sources, including test-only files; E8 did not loosen it. A future refinement can exclude test-only modules, but that is a separate identity proposal.
- Full E6 matched-layout quality: PASS for hybrid (all overall metrics exactly equal); vector with CPU query is non-degraded at K=50 and improves K=10, with one extra hard-negative hit in an answerable query (no new no-answer false positive). Native Vulkan query is exact for hybrid but loses one vector-only K=50 anchor (dapper-07).
- Independent verifier: `benchmarks/retrieval-evidence/tools/verify_e8.py`; vector PASS, NaN negative rejected. Retrieval mode ran on full reports: recommended vector has only improvements; hybrid has zero metric differences.
- No-op fast path (full 392-file indexes): CPU 0.07 s / 64 MB RSS, Vulkan 0.08 s / 72 MB RSS; both reported 392 files, 0 modified, 0 entities. No model call.
- Tests: `cargo test -p zg-engine --release --lib` **763 passed, 0 failed, 28 ignored** (default, serial); with `--features vulkan` **763 passed, 0 failed, 28 ignored**; harness contract 4 passed; `cargo fmt --check` PASS. E8 vector probe is `#[ignore]` and was run explicitly for the vector receipts.


## Full matched-layout E6 quality

The full 392-file C# corpus was indexed twice with the same model, source, schema and
globs. The two full indexes were built concurrently on the same host; their wall
times are **not** a controlled speed A/B (the CPU index was slowed by the GPU job and
by host load). File/layout cardinality is identical: 392 files, 564 `.zvec-grep` files,
60 MB each. Stage timings:

| index | wall | user CPU | peak RSS | embedding stage |
|---|---:|---:|---:|---:|
| CPU full | 89:32 (concurrent) | 20,908 s | 2.71 GiB | 7,289 s recorded |
| Vulkan FP16 full | 54:43 (concurrent) | 752 s | 698 MB | 4,295 s recorded |

The clean end-to-end A/B remains the 40-file and log measurements above; the full run
is for quality and scale/resource evidence. Even while sharing the host, Vulkan used
**27.8x less CPU** and **3.9x less RSS** for the full corpus.

Index receipts:
- `index-full-cpu-sha256.txt` (564 files, 63,246,967 B) SHA-256 `933cd5e7…5947`
- `index-full-vk-sha256.txt` (564 files, 63,246,969 B) SHA-256 `d1f1f23e…b723`
- `index-full-vk-fp32-sha256.txt` (564 files) SHA-256 `fb198f0d…d55f`; FP32 full
  25:18 standalone, 377 s user CPU, 880 MB RSS (not the selected default).

### Recommended device split (measured)

- **Indexing:** Vulkan (the expensive 9,223-fragment work).
- **Query embedding for pure-vector retrieval:** CPU (`--device cpu` on the query;
  32 query vectors, ~8 s, negligible). This preserves the K=50 result set.
- **Hybrid retrieval:** CPU or Vulkan query gives exactly the same E6 metrics.

| full E6 route / query device | R@10 | R@50 | solved@10 | solved@50 | nDCG@10 | no-answer FP@10 |
|---|---:|---:|---:|---:|---:|---:|
| CPU index + CPU query (vector) | 0.5679 | 0.8673 | 0.3704 | 0.7407 | 0.5244 | 0.2000 |
| Vulkan index + CPU query (vector) | **0.6049** | **0.8673** | **0.4074** | **0.7407** | **0.5481** | 0.2000 |
| Vulkan index + Vulkan query (vector) | **0.6049** | 0.8302* | **0.4074** | 0.7037* | **0.5481** | 0.2000 |
| CPU index + CPU query (hybrid) | 0.6142 | 0.8025 | 0.4444 | 0.6667 | 0.5387 | 0.0000 |
| Vulkan index + CPU query (hybrid) | 0.6142 | 0.8025 | 0.4444 | 0.6667 | 0.5387 | 0.0000 |
| Vulkan index + Vulkan query (hybrid) | 0.6142 | 0.8025 | 0.4444 | 0.6667 | 0.5387 | 0.0000 |

`*` GPU **query** embedding causes one vector-only K=50 mismatch (dapper-07);
using a CPU query over the same Vulkan index restores R@50/solved@50 to the CPU
baseline. The GPU **document** vectors still add one hard-negative hit in an
otherwise-improved answerable query (`antlr-03`), while no-answer false positives are
unchanged. Hybrid E6 metrics are **exactly identical** for every query.

Independent verification (`tools/verify_e8.py`) over full reports: vector recommended
configuration has metric differences only in two answerable queries (one large
improvement, one small nDCG improvement); hybrid has zero metric differences. The
verifier also rejects a deliberately NaN-corrupted vector receipt.

### Vulkan precision variant (FP32)

`GGML_VK_DISABLE_F16=1` was tested as a precision variant (same Q8 weights). The
11-input probe took 11.19 s vs 11.08 s FP16 (no meaningful speed change) and had
similar cosine/geometry. A full FP32 GPU index completed in 25:18 standalone
(377 s user CPU, 880 MB RSS), but query quality did not remove the vector-only
hard-negative difference; hybrid MRR/nDCG were slightly higher, while recall/solved
were unchanged. FP32 is **not selected**: it does not establish a strict parity
advantage and adds another runtime precision dimension to identity/reuse. FP16 is
the measured default for this candidate.

### Cold shader / device fallback

- First-ever Vulkan one-file run: 138.3 s; warm one-file: 4.12 s; a later run with
  an empty Mesa shader-cache dir: 8.13 s. The 138 s was a first-ever host/driver
  setup event, not a stable per-cache penalty. The cache is host-specific and is
  not reproduced by just shipping the GGUF.
- Explicit Vulkan on a CPU-only build falls back to CPU with a warning and succeeds;
  this is safe for availability, but it means an explicit GPU identity can label
  CPU-fallback vectors. This remains a residual risk for persisted reuse; do not
  treat an explicit `--device vulkan` as proof that every vector was GPU-produced.
- E2 cross-device probe (pre-guard identity test): CPU sidecar -> Vulkan incremental
  re-embedded 20/20 fragments (no reuse); Vulkan -> Vulkan incremental reused.
  With the final guard, a changed file on a CPU index with `--device vulkan` is
  rejected before any write; same-device increments remain valid.

## Verdict and promotion checklist

**H1 Vulkan: QUALIFIED_GO (explicit opt-in).**

- Build: `cargo build --release --features vulkan` (system Vulkan loader/headers and
  SPIRV-Headers/glslc at build time; local SPIRV extraction used for E8).
- Runtime: `--device vulkan` or `ZVEC_GREP_DEVICE=vulkan`; no default change.
- Use Vulkan for indexing; for pure-vector retrieval use a CPU query (`--device cpu`)
  until a future GPU-query parity fix; hybrid is exact with either query device.
- Rollback: build without the feature / use `--device cpu`; no schema or data migration.
- Do not merge/promote from E8; this is a promotion review package.

**H2 CPU affinity: NO_GO.** Pinning P-cores (0-11 or 0,2,4,6) did not beat unpinned
under the measured load; E-cores were clearly slower. No affinity change should ship.

**Residual uncertainties / next minimal work**

1. Cold Vulkan shader compilation needs a prewarm strategy or documented first-run
   budget; the cache is Mesa/driver-specific.
2. GPU-query vector K=50 tail (dapper-07) is a one-query difference; CPU query is the
   current mitigation. A full GPU-query vector parity study is required before using
   GPU query embeddings for vector-only production.
3. Explicit-GPU fallback to CPU can produce a GPU-labelled CPU vector; E2 does not
   observe the actual backend at identity time. A future change should bind identity
   to the actual backend or disable persisted reuse after fallback.
4. `ZG_EMBEDDING_BACKEND_BUILD` still hashes test-only source files; this causes
   unnecessary cache invalidation. Excluding provably test-only modules is a separate
   negative-tested identity refinement, not done here.
5. Full-index wall comparison was concurrent and is not the speed claim; the clean
   A/B is 40 C# / log.

## Artifacts and cleanup

- Branch: `work/e8-embedding-acceleration-20261114`
- Receipts: `/home/masis/.cache/zvec-grep/e8-20261114/receipts/` (raw logs, stage JSON,
  full E6 reports, vector probe, verifier, negative test, tests).
- External build deps: `$E8/deps` (SPIRV-Headers only), targets, full indexes and
  workloads are removed after receipts/hashes; shared qmd model cache is unchanged.
- Main worktree remains clean; E8 worktree removed after push.
- No production index, daemon, model default, ranking or schema change.
