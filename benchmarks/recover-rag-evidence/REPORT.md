# Recover Downstream RAG Evidence — final report

Status: `DONE / research-only`. No production change, no binary promotion, no
model/graph/query/chunking change, no held-out opened. Branch:
`work/recover-rag-evidence-20261126`, based on `b05960d`. Artifacts:
`/home/masis/.cache/zvec-grep/recover-rag-evidence-20261126`.

## 1. Implementation

Commits:

- `f228831` — preregistration.
- `eae1115` — implementation and harness.
- `798df98` — A6 amendment (A1 + R1 pack).
- final commit — report and fixtures.

Changes are all in the isolated branch:

1. **Fragment identity / lineage**: `ContextItem` now carries `fragment_id` and
   `file_sha256`; stage traces include both. Source ranges and
   `content_range` byte offsets are preserved.
2. **A1**: `ZVEC_RAG_NO_DIVERSITY=1` sets effective `FILE_DECAY=0` in ranking.
   Normal search is unchanged when the env var is absent.
3. **A2**: `ZVEC_RAG_RANK_LIMIT=200` delays the rank cutoff; discovery recall,
   FTS/vector routes and RRF fusion stay unchanged.
4. **A3**: `ZVEC_RAG_BUDGET_SELECT=1` replaces hard top-K with greedy
   budget-aware selection: score order, 10 items / 24 KiB default, redundancy
   controlled by source-range overlap (`>0.5`) instead of file identity.
5. **A4/A6**: `ZVEC_RAG_EVIDENCE_PACK_FILE=<path>` builds an R1 Verified
   Evidence Pack from selected `content_range` byte offsets + discovery-time
   whole-file hashes, using `api::evidence::assemble`.
6. **A5**: combination A1+A2+A3+R1 pack.

Baseline binary SHA-256 `5a4c8201…`; candidate binary SHA-256 `eda2619b…`.

## 2. Results at fixed 24 KiB / 10-item budget

Macro over 12 Rust and 12 C# development cases. Precision = delivered bytes
intersecting gold / delivered bytes. Oracle uses the same pool and budget.

| Arm | Rust exact | Rust precision | Rust tokens | C# exact | C# precision | C# tokens |
|---|---:|---:|---:|---:|---:|---:|
| A0 baseline | 0.250 | 0.049 | 4,213 | 0.083 | 0.007 | 4,624 |
| A1 no diversity | **0.333** | **0.065** | 4,768 | 0.083 | 0.006 | 4,672 |
| A2 delayed cutoff | 0.250 | 0.049 | 4,213 | 0.083 | 0.007 | 4,624 |
| A3 budget/range selection | 0.222 | 0.047 | 3,767 | 0.083 | 0.007 | 4,368 |
| A4 R1 pack only | 0.125 | 0.013 | 2,266 | 0.083 | 0.010 | 2,911 |
| A5 full combination | 0.153 | 0.038 | 1,312 | **0.167** | 0.021 | 1,387 |
| A6 A1 + R1 pack | 0.181 | 0.033 | 2,166 | 0.083 | 0.009 | 2,874 |

Oracle ceiling is unchanged by all runtime changes: Rust `0.7639`, C# `0.7223`.
No arm comes close to the oracle under the same budget.

Resource:

| Arm | Rust p95 / CPU / RSS | C# p95 / CPU / RSS |
|---|---|---|
| A0 | 1.43 s / 17.37 s / 415,716 KiB | 1.89 s / 21.83 s / 449,940 KiB |
| A1 | 1.60 s / 17.97 s / 417,016 KiB | 2.12 s / 23.59 s / 450,912 KiB |
| A5 | 1.77 s / 21.52 s / 417,128 KiB | 2.46 s / 27.57 s / 451,404 KiB |

### Preregistered gate result

An isolated change needed `>= +0.05` exact coverage with `<=25%` token growth:

- A1 passes on Rust (`+0.083`, tokens `+13.2%`) but is neutral on C#.
- A2 is neutral on both.
- A3 regresses Rust.
- A4 regresses Rust.
- A5 helps C# (`+0.083`) but regresses Rust (`−0.097`).
- A6 regresses Rust and is neutral on C#.

**No configuration passes both languages.** There is no defensible universal
promotion candidate from these dev results. This is `PARTIAL`, not a GO.

## 3. Task correctness

Tool-free Pi `opencode-go/hy4-flash-free`, no repository tools, question + pack
only. Grader is deterministic keyword/abstention based.

| Split | A0 | A1 | A5 |
|---|---:|---:|---:|
| Rust correct /12 | 4 | **5** | 2 |
| C# correct /12 | 0 | 0 | 0 |
| Rust total tokens | 51,647 | 57,452 | 21,462 |
| C# total tokens | 67,118 | 67,763 | 24,470 |

Rust A1 converts the evidence improvement into +1 correct answer. A5's token
saving comes with worse answering. C# keyword grading was unreliable/too strict
for these cases and most agents abstained; it cannot be used as a decisive
quality signal. No human grading was performed.

## 4. Regression

- Normal-search parity: 6/6 sampled Rust/C# cases produced byte-identical
  stdout between baseline `5a4c8201…` and candidate `eda2619b…` with all RAG
  env switches unset.
- Focused engine tests: `cargo test -p zg-engine --lib -- pipelines::indexed_search`
  → **49 passed, 0 failed, 1 ignored**.
- Fragment/file-hash fields are `Option` with serde defaults; old ContextItem
  consumers are unaffected.
- No production index, binary, config, embedding or held-out file changed.

## 5. What worked and what did not

Worked:

- Removing file diversity improves Rust exact coverage and precision within
  budget and modest latency/token increase; task correctness also improved by
  one case.
- R1 Verified Evidence Pack produces hash-validated items and explicit gaps;
  it sharply reduces tokens but loses evidence under the current 24 KiB pack
  budget because selected entity ranges are still coarse.
- Range-overlap-aware selection reduces tokens but loses recall: it is pruning
  too aggressively before a better sub-unit representation exists.

Did not work:

- Delaying cutoff alone: the top ranks do not change.
- Budget-aware range selection alone: too lossy.
- Full combination: language-dependent outcome, not promotable.

## 6. Limits

- One run per arm; 12 development cases per language; no held-out.
- Deterministic span overlap is not answer correctness; C# keyword grader is
  weak.
- RAG switches are env-gated in an experimental binary, not a production API.
- No representation/index-unit change was allowed, so coarse entity ranges
  remain the hard ceiling for context precision.
- No graph/query/model work was attempted by design.

## 7. Owner decision required

- If the target workload is Rust-like and 13% more tokens / 12% p95 latency is
  acceptable, A1 is the only isolated improvement with positive evidence and
  task-correctness signal.
- If the target workload is C#-like, A5 improves exact coverage but regresses
  Rust; do not promote globally without a larger paired corpus.
- R1 pack delivery is architecturally ready but needs smaller evidence units
  before it can preserve coverage at 24 KiB.

No promotion was performed. All artifacts and receipts are reproducible from
the commands in `tools/`.
