# E7 — indexing scalability and work elimination

**Status:** DONE — bottleneck attributed; both bounded optimizations **NO_GO**
**Branch:** `work/e7-indexing-scalability-20261114`
**Base:** R0 `work/e3-02b-r0-vector-policy-20261114` @ `e909ff5`
**Main:** `work/zg-recovered` @ `6d5b523` (untouched)
**Receipts:** `/home/masis/.cache/zvec-grep/e7-20261114/receipts/`
**No merge/promotion/release; no production index, schema, ranking or model-default change.**

## 1. Baseline and ancestry

| Item | Baseline |
|---|---|
| Engine branch | R0 `e909ff5`, which contains E2 integration (`96d543c`), E5 (`7013a47`), E6 (`9c1306d`) and Index v3/no-op fast path ancestry |
| Main | `work/zg-recovered` @ `6d5b523`, clean; E7 worktree isolated |
| Model | `local/embeddinggemma-300m` Q8 GGUF, 333,590,944 bytes, catalog SHA `b5ce9d77…d63` |
| Index | index version 3, schema `fragments` with FTS + FP16 vector + HNSW; model identity recorded |
| Frozen C# | `e3-01-v2`, 392 files / 98,666 lines; known 9,223 fragments, mean 942.95 chars |
| Log workload | `/var/log/pacman.log` slices: 1k = 72 KB / 43 chunks; 2k = 144 KB / 86; 3k = 228 KB / 129 (2,000-char token-dense chunks) |
| Baseline binary | `/home/masis/.cache/zvec-grep/e7-20261114/baseline-harness`, built before E7 env experiments |
| Target/artifacts | external `target`, models, indexes and venv under `/home/masis/.cache/zvec-grep/e7-20261114` |

The E4 full-C# serial index (1:10:36, 16,619 s user CPU, 2.99 GiB peak RSS, 63.2 MB
disk, 9,223 fragments) is the historical baseline for the full corpus. E7 did not
repeat that 70-minute run; E7 used a 40-file C# subset and log workloads for the
A/B measurements. This is stated wherever a full-corpus number is used.

## 2. End-to-end stage attribution

### Full C# structural control (Potion, 392 files)

| Stage | Wall / recorded | Share |
|---|---:|---:|
| total | 15.43 s wall | 100% |
| scan | 11.9 ms | 0.1% |
| prepare (read/classify/extract/chunk) | 0.968 s | 7% |
| embedding | 5.06 s | 36% |
| commit | 6.79 s | 49% |
| optimize | 5.29 s | overlapping; not additive |

Potion is a structural control only (model2vec), not a quality arm. It proves that
on the same 392-file corpus the non-embedding pipeline is small compared with
Gemma embedding.

### Gemma log workloads

| Workload | Fragments | Total wall | Prepare | Embedding | Commit | Optimize | User CPU | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| log 1k (72 KB) | 43 | **142.3 s** | 0.0009 s | **139.7 s** | 0.139 s | 0.827 s | 547.9 s | 2.21 GiB |
| log 2k (144 KB) | 86 | **284.6 s** | 0.003 s | **282.1 s** | 0.124 s | 0.619 s | 1,101 s | 2.20 GiB |
| log 3k (228 KB) | 129 | **444.6 s** | 0.003 s | **427.5 s** | 13.62 s | 2.19 s | 1,683 s | 2.24 GiB |

- Embedding is **96–99%** of wall; prepare is below 0.1%; optimize is below 1%.
- CPU is consistently **~3.8 cores** (547–1,683 user seconds over 142–445 s wall).
- Scaling is **linear in fragments**: 43 → 86 → 129 fragments gives 142 → 285 →
  445 s, i.e. **3.25–3.31 s per fragment**. The E4 statement that the log was
  “superlinear” is not supported by these stage measurements; 3,000 lines exceeds
  a 300 s budget because the linear cost is already large, not because of an
  observed O(n²) term.
- The log fragments are 2,000-char token-dense chunks (the detector chooses
  100 chars/100 tokens), so each input is roughly twice the character budget of
  ordinary C# text and costs proportionally more decode time.
- Potion on the same 1k log records **0.96 s** of pipeline time (1.94 s process
  wall; embedding 0.053 s, commit 0.062 s, optimize 0.641 s), confirming that native commit/optimize and
  extraction are not the large-log bottleneck.

### Full C# Gemma historical baseline

E4 measured 9,223 fragments in 1:10:36 wall / 16,619 s user CPU, 2.99 GiB peak,
63.2 MB disk. That is ~**0.46 s/fragment**, versus ~3.3 s for 2,000-char token-dense
log fragments. The difference is input length/token density, not a hidden
duplicate-work stage. E2-A/B already removed the known duplicate-input work
(same-pass and persisted reuse); R0's 797 normalized-text duplicates are mostly
short module/unknown fragments, not a cold-log workload.

## 3. Work elimination review

- **E2 reuse/dedup is already active** before E7. The index path plans same-pass
  dedup and persisted E2-A/B reuse; no additional cold-log duplicate inputs were
  found in the 43/86/129-fragment slices.
- **Text overlap** is 15% by design (`CHUNK_OVERLAP_PERCENT`); it creates repeated
  characters but is required for boundary evidence. Reducing it or changing chunk
  boundaries would change fragment identity, vectors and retrieval; no evidence
  parity was established, so it was not treated as free work.
- **Token-dense chunking** is conservative and intentional; the detector halves
  the character budget for non-letter-dense logs. A tokenizer-aware size change
  would be an extraction/chunking change, not an execution-only optimization, and
  is not justified by the current retrieval evidence.
- **Native commit/optimize** is not dominant for logs (≤1%), and Potion's structural
  control shows it is not the primary full-corpus cost. No E1 batching/deferred
  maintenance retry was attempted.
- **No-op** is already fast: rerunning the indexed 1k log took **0.08 s wall,
  0.02 s user, 62 MB RSS**, with no embedding work. The no-op fast path remains
  protected.

## 4. Bounded optimization hypotheses

### H1 — single-context llama.cpp thread count

`threads_per_context` normally returns 0 for `execution_concurrency <= 1`, which
delegates to llama.cpp's conservative default (4 threads on this host). E7 added an
experiment-only `ZVEC_GREP_E7_THREADS` override and measured 2/4(default)/6/8 threads
on the same 1k log, same model, same layout:

| Threads | Wall | Embedding | User CPU | Peak RSS |
|---:|---:|---:|---:|---:|
| 2 | 237.1 s | 233.4 s | 459.8 s | 2.23 GiB |
| **4 (default)** | **142.3 s** | **139.7 s** | 547.9 s | 2.21 GiB |
| 6 | 186.2 s | 182.0 s | 1,070 s | 2.23 GiB |
| 8 | 171.1 s | 167.8 s | 1,315 s | 2.23 GiB |

More threads increase CPU work and reduce throughput; 4 is the best tested point.
This matches llama.cpp's default and leaves no safe thread-tuning opportunity.
**Verdict: NO_GO.**

### H2 — parallel/batched inference scheduling

Two variants were tested:

1. **Multi-sequence decode**: group independent inputs into one llama.cpp batch
   when total tokens fit the context (`ZVEC_GREP_E7_BATCH`).
2. **Multiple llama contexts**: raise scheduler context count
   (`ZVEC_GREP_E7_CONTEXTS`) so independent files can run in separate contexts.

Log 1k:

| Variant | Wall | Embedding | User CPU | Peak RSS |
|---|---:|---:|---:|---:|
| baseline | 142.3 s | 139.7 s | 547.9 s | 2.21 GiB |
| batch 2 | 157.9 s | 156.1 s | 612.3 s | 2.21 GiB |
| batch 4 / 8 | 139.8 / 138.6 s | 137.5 / 136.3 s | 540 / 534 s | 2.23 GiB |

The log's ~1,000-token inputs only allow two sequences in the 2,048-token context,
so “4/8” is effectively two and is within noise. C# 40-file subset:

| Variant | Wall | Peak RSS | Result |
|---|---:|---:|---|
| baseline | 305.5 s | 2.06 GiB | — |
| multi-sequence batch 8 | 364.9 s | 2.77 GiB | slower, higher RSS |
| 2 contexts × 4 threads | 306.4 s | 2.05 GiB | no improvement |
| 2 contexts × 2 threads | 384.9 s | 1.93 GiB | slower |
| 4 contexts × 2 threads | 357.3 s | 2.10 GiB | slower |

The scheduler/context experiments did not increase effective CPU use or throughput;
multi-sequence batching increased RSS and did not reduce wall. Sequence batching
also changes the execution shape and would need exact vector/identity parity before
any promotion; it demonstrated no benefit on either workload.
**Verdict: NO_GO.**

## 5. Correctness and regression gates

- **Default path parity:** candidate binary with all E7 env switches unset vs the
  pre-E7 baseline binary, same 40-file C# subset and same fixed Gemma query set:
  the 10 antlr queries that have corpus hits returned **identical top-10 paths,
  line ranges, order and scores** (the other subset queries correctly returned
  zero because those source files were not included).
- **Engine suite:** `cargo test -p zg-engine --release --lib -- --test-threads=1`:
  **761 passed, 0 failed, 27 ignored**, 90.8 s. The parallel default run hit the
  known pre-existing config-permission test interference; the test passes alone and
  the serial suite is clean.
- **Harness contract tests:** 4 passed (`e6_contract_tests`).
- **Formatting:** `cargo fmt --check` passes after formatting.
- **No schema/index-version/ranking/model-default change.** The E7 thread/batch code
  is environment-gated and defaults to the pre-E7 behavior. It is an experiment
  artifact, not a production candidate.
- **No-op:** 0.08 s / 62 MB RSS on the indexed 1k log; no model call.
- **E2:** existing qualified line-shift/local-edit/no-op and crash/sidecar evidence
  remains the reference for incremental/recovery; E7 did not reopen E2-C. An E7 log
  append probe was not used as reuse evidence because the E7 backend source edit
  changes `ZG_EMBEDDING_BACKEND_BUILD` (by design), invalidating the old sidecar for
  that probe.

## 6. Verdicts and next step

| Hypothesis | Verdict | Evidence |
|---|---|---|
| H1 single-context thread tuning | **NO_GO** | default 4 threads best; 2/6/8 all slower end-to-end |
| H2 parallel/batched inference | **NO_GO** | no log gain; C# batching slower and +RSS; multi-context no effective gain |

E7 closes with **no `QUALIFIED_GO`**, no integration candidate and no production
change.

The measured bottleneck is **llama.cpp CPU embedding compute per input**, with
4-thread utilization and linear scaling in fragment/token count. The remaining
levers are outside an execution-only patch:

- model/backend acceleration (GPU, a faster quantized runtime, or a different
  embedding model) — changes model identity/vectors and requires a new quality
  evaluation;
- tokenizer-aware chunk sizing or overlap reduction — changes extraction,
  fragment identity and vectors, and requires evidence/retrieval parity, not a
  microbenchmark;
- larger architectural changes to batch/parallelize llama contexts — the tested
  variants did not improve and increased memory.

**Recommendation:** do not pursue H1/H2 further. If indexing scalability remains a
priority, open a separate bounded **token-aware chunking / model-backend** study with
the E6 measurement contract and frozen-gold parity gates. This is a proposal, not
authorized implementation.

## 7. Git, receipts and cleanup

- Branch: `work/e7-indexing-scalability-20261114`, based on R0 `e909ff5`.
- Receipts: stage-timing JSON/logs, thread/batch/context matrices, C# subset A/B,
  full-C# Potion control, query parity, no-op/incremental probes, test logs,
  `summary.json`, `SHA256SUMS` and `manifest.json` (including Gemma artifact hash).
- External target, model cache, workloads and venv removed after receipts and hashes;
  E7 worktree removed after commit/push; main remains clean.
- No production code path or default is changed by the experiment; rollback is
  simply not merging the branch / unsetting the E7 environment variables.
