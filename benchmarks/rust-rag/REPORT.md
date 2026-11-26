# Comprehensive Rust RAG investigation — report

Status: `DONE / research-only`. No production change, no binary promotion, no
model/ranking/RAG change, no old C# held-out opened, no gold used by a runtime
policy. All artifacts are under
`/home/masis/.cache/zvec-grep/rust-rag-20261126`; all reproducible scripts and
this report are committed on `work/rust-rag-investigation-20261126`.

## 1. Scope and reproducibility

- Base: `main` @ `51c9ebacf6d25c060de03ecf8af07633a55cd383`.
- Experiment branch/worktree: `work/rust-rag-investigation-20261126` at
  `/home/masis/src/zvec-grep-rust-rag-20261126`, created from that commit.
- Independent Rust corpus: `clap-rs/clap` @
  `4201754a400f676c9744da66383c89eb9f41af88`, 645 tracked files, 3,655,578
  bytes total; 335 Rust files indexed, 4,810 entities, index version 3,
  storage generation `09f464df-a502-4360-bfcf-62a48773a04a`.
- Frozen gold: 12 cases R01–R12, `cases.json` SHA-256
  `c6e1a7e7441bb6ad235271bddfb5ed7ac6c6729d61c31e361d7dfd4f984885a1`.
- Corpus manifest SHA-256
  `d48597b3fd659d7d9a2cfc45c0ec8f0f2c415a907038e71165c7b46a56084113`.
- Binary: isolated debug build of the unmodified `zg` source, SHA-256 recorded
  in `baseline-binary.sha256` under the artifact directory.
- Model: Potion `minishlab--potion-code-16M-v2`, local CPU, copied to the
  task cache; no embedding/ranking change.
- Every gold span was verified from the pinned file hash and boundary lines
  *before* indexing or search: `tools/freeze_cases.py`.
- Host state: before run root 39 GiB free, memory available ~7.6 GiB; after run
  root 34 GiB free, memory available ~7.5 GiB. Build target is outside both
  repositories. Single-run measurements are flagged as single-run evidence.

## 2. Pipeline audit

| Layer | Evidence | Finding |
|---|---|---|
| Extraction | `extraction/code/adapter.rs:134-150`, `extraction/code.rs:137-145` | Rust uses tree-sitter Rust; entities include `function_item`, `function_signature_item`, `impl_item`, `mod_item`, `struct_item`, `enum_item`, `trait_item`, `const_item`, `static_item`, `type_item`, `union_item`; scopes are `impl`, `mod`, `trait`. |
| Representation | `extraction/service.rs:170-218`, `pipelines/indexing/pipeline.rs:1590-1623` | Embedding input is `symbol/scope/signature/doc` prefix + chunk content, metadata budget capped at 1/4 of chunk chars; lexical FTS input adds symbol/scope/signature/doc with C# scope omitted but **Rust scope retained**. |
| Chunking | `extraction/chunking.rs:9-55` | UTF-16 char window with overlap; evidence unit remains the declaration or generated fragment, not a method-level semantic unit. |
| Query understanding | `pipelines/indexed_search/context.rs:81-95`, `storage/fragments/mod.rs:584-598` | Every primary query becomes the same raw FTS + raw vector pair; FTS is OR of tokens, max 64 terms. No engine-local reformulation, no corpus-grounded expansion. |
| Retrieval/ranking | `pipelines/indexed_search/ranking/mod.rs:16-33,65-208`, `ranking/README.md` | RRF + bounded code rules + greedy per-file diversity; no graph edge, call intent or method-body semantics. |
| Graph | `domain/graph.rs:12-31`, `storage/store/graph_tests.rs`, `extraction/code.rs:51` | Graph schema exists (`Contains/Calls/Imports/Extends/Implements`, pending refs), but code extraction writes `graph: None`; no Rust graph edge reaches retrieval. |
| Evidence selection | `api/evidence.rs`, `pipelines/indexed_search/context.rs:480-535` | Bounded, revalidated evidence-pack API exists but is library-only; normal CLI text path selects fragments/full declarations and renders text. |
| Observability | `zg/src/main.rs`, `pi-zvec-grep/src/core/queries.ts` | CLI `--trace` emits per-hit recall/fusion/ranking JSON; the Pi adapter does not pass `--trace`, so daily Pi dogfood still has `lineage: null`. This experiment used CLI trace directly, so no implementation change was needed. |

## 3. Frozen questions and gold

Questions cover: typed value conversion (R01), derive defaults (R02), help error
(R03), subcommand dispatch (R04), typo suggestions (R05), environment fallback
(R06), short-flag clusters (R07), shell completion (R08), man-page rendering
(R09), typed `ArgMatches` retrieval (R10), conflict/required validation (R11),
and `#[command(flatten)]` derive expansion (R12). Questions are natural-language
and deliberately avoid exact internal function names where possible. Each case
stores path hashes, line spans, facts, grader keywords and explicit gaps.

## 4. Baseline and experiment receipts

One isolated binary, one index, one frozen case file. Each arm is one search
call per case except derived arms H3/H4/H5, which reuse the baseline call and do
offline candidate transformation. Contract: <=6 calls, <=3 rounds, 10 items,
24 KiB pack, 64 KiB delivered.

| Arm | Change | Pool recall | Delivered recall | Potion tokens mean | p95 latency | Peak RSS |
|---|---|---:|---:|---:|---:|---:|
| baseline | raw question, fused FTS+vector, limit 50 | **0.7292** | **0.2500** | 4,578.7 | 1.46 s | 412,872 KiB |
| H2 | corpus-derived identifier expansion | 0.5625 | 0.1528 | 3,758.8 | 1.30 s | 412,832 KiB |
| H3 | method-unit narrowing, original rank | 0.7292 | 0.2222 | 4,133.4 | 1.25 s | 413,344 KiB |
| H4 | one-hop local call graph expansion | 0.7292 | 0.1528 | 2,215.1 | 1.28 s | 413,156 KiB |
| H5 | H3 + lexical question/source rerank | 0.7292 | 0.2500 | 4,749.5 | 1.35 s | 413,212 KiB |

Raw receipts: `results/arm-receipt.json`, `results/analysis.json`,
`results/oracle-diagnostics.json`, per-call stdout/stderr/time under
`results/raw/`, per-arm packs under `results/packs/`, logs under `logs/`.

Token counts use a non-truncating Potion tokenizer build
(`count_tokens_full.rs`); the stock example silently truncates at 512 tokens
and was not used for reported figures.

### Oracle diagnostic (evaluation-only)

Over the 50 baseline candidates, an oracle choosing at most 10 ranges under the
same 24 KiB pack reaches **0.7292 delivered macro recall**, exactly the pool
ceiling. Rank-top-10 before byte packing is 0.3611; actual delivered pack is
0.2500, so byte packing also drops some rank-top-10 candidates. No gold term
was used by any runtime policy; this is a post-hoc ceiling calculation.

## 5. Failure taxonomy

| Failure | Cases | Evidence |
|---|---|---|
| Acquisition miss | R01, R02, R03, R05, R06, R07 | Gold span appears in <100% of the 50-candidate pool. |
| Pool-to-pack zero | R01–R07 | Gold is pooled but no delivered range overlaps it. |
| Selection / ordering loss | R04, R08, R10, R11, R12 | Pool recall 1.0 but delivered recall <1.0. |
| Coarse-unit noise | 9 of 12 cases | Selected item includes ranges >200 lines; R01's parent span `parser.rs:46-1547` covers gold but delivers ~1,500 lines. |
| Query-formulation loss | R01/R02/R05/R06/R07 | Natural-language terms retrieve neighbors, not the declarations that answer the question; exact source probes verify the gold exists. |
| Graph gap | R01/R03/R04/R06/R11 | Local call relationships exist in source but have no persisted edge in the engine and the prototype expansion added noise. |
| Task-correctness gap | 8/12 baseline cases | Deterministic keyword grader failed or agent abstained; see below. |

## 6. Task correctness and inference cost

Tool-free Pi agent, `opencode-go/hy4-flash-free`, thinking off, no repository
tools and no context files. The agent saw only question + selected pack. Saved
answers and per-session usage are in `results/agent/` and `agent-sessions/`.
Grading is deterministic: required keywords plus explicit `INSUFFICIENT`
conclusion; normalization strips `_`/`-` (no gold used in the prompt).

| Arm | Task correct | Valid citations | Insufficient | Total tokens (12 cases) | Mean tokens/case | Mean wall |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 4/12 | 7/12 | 3/12 | 55,301 | 4,608 | 3.58 s |
| H3 | 2/12 | 4/12 | 8/12 | 49,544 | 4,129 | 3.81 s |
| H4 | 3/12 | 1/12 | 8/12 | 30,449 | 2,537 | 2.77 s |

Provider-reported cost was 0 for this free model; no dollar estimate is made.
Citation validity is strict exact `[path:start-end]` matching against the pack,
so approximate-but-real citations are counted invalid. This is a weak
task-correctness proxy, not human acceptance.

## 7. Hypotheses tested

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Index coverage causes Rust RAG misses | **Refuted** | 335/335 Rust files indexed, 4,810 entities; gold exists and is searchable by exact probes. |
| Raw natural-language query is a major acquisition loss | **Supported** | 6/12 cases have incomplete pools; corpus-derived exact probes recover them; C# had the same 9/25 query-formulation loss. |
| Corpus-grounded identifier expansion fixes acquisition | **Refuted** | H2 pool 0.5625 < baseline 0.7292, delivered 0.1528 < 0.2500. |
| Method-level narrowing alone fixes selection | **Refuted as implemented** | H3 delivered 0.2222; narrowing without a sub-unit signal loses gold ordering. |
| Bounded call-graph expansion fixes multi-hop cases | **Refuted** | H4 delivered 0.1528; compact tokens fell but correctness did not improve. |
| Lexical question/source rerank fixes selection | **Refuted** | H5 delivered 0.2500, threshold 0.3500 not met. |
| 24 KiB / 10-item budget is the binding constraint | **Refuted** | Oracle over the same pool reaches 0.7292 within the budget. |
| C# scope omission alone explains the quality gap | **Refuted** | Rust keeps scope in lexical and embedding metadata yet shows the same acquisition/selection pattern. |
| Changing Potion/model is required before representation work | **Not tested / unsupported** | Potion kept as baseline by contract; all failures occurred before model capacity was binding. |

## 8. Cross-language comparison with C#

| Result | C# HRM (frozen receipts) | Rust clap (this mission) | Interpretation |
|---|---|---|---|
| Index coverage | 822 files, 0 C# coverage failures (`r4-index-truth-audit-20261118.md`) | 335/335 Rust files, 0 coverage failure | Coverage is not the root cause in either language. |
| Raw-question retrieval | B0 dev deployable 0.3750, oracle 0.4722 (`retrieval-selection-ceiling.json`) | raw fused baseline 0.2500 delivered | Both show large gap between reachable and delivered evidence. |
| Query formulation | 9/25 gold missed by question phrasing (`rag-discovery-research.md`) | 6/12 cases with acquisition miss; exact probes recover | Same language-to-source gap. |
| Selection ceiling | Pool 19/25, oracle 0.8472, best policy 0.5278 (`r4-deep-optimization-20261118.md`) | Pool 0.7292, oracle 0.7292, policy 0.2500 | Selection signal, not budget, is dominant in both. |
| Representation unit | 700-line class vs 20-line method; representation gap proven | 1,500-line impl parent covers gold but floods pack | Same coarse-unit defect reproduced in Rust. |
| Typed/graph traversal | H7 closure 23/25 @500 but 392,500 KiB RSS NO-GO (`discovery-h7-e1.json`) | one-hop prototype added noise, no recall gain | Graph facts are promising but need a resource-bounded, resolver-grade design. |
| Scope metadata | C# scope omitted from FTS/vector from `894b71e` | Rust scope retained | Scope omission is not sufficient explanation. |

## 9. Limits

- One run per arm; no variance estimate.
- 12 development cases; not a held-out generalization claim. Old held-out was not opened.
- Deterministic keyword grader and one free model; no human answer grading.
- H3/H4/H5 are offline prototypes, not production index/graph changes.
- Query expansion used a regex symbol inventory, not the engine's tree-sitter facts; a stronger parser might behave differently, but no evidence here supports it.
- Rust graph prototype used regex call names, not resolved type-aware edges; it cannot refute a resource-aware typed resolver, only the cheap variant.
- `--trace` was passed by the harness; the Pi adapter still does not pass it, so daily Research mode still lacks per-hit lineage.

## 10. Owner-ready design consequences

1. Change the evidence unit before tuning selection: emit method/function-level
   ranges with declaration headers and stable identity for Rust and C#; keep
   declaration/range identity separate from source-span identity.
2. Add an engine-local query-understanding layer grounded in extracted symbols,
   scopes and docs. Do not use an external blind planner; C# Gate B and Rust H2
   both refuted that path.
3. Rerank sub-units with source-visible semantic features (signature, doc,
   caller/callee, type/role), not generic lexical overlap; H5 refuted the cheap
   version.
4. Make the bounded evidence-pack API the delivery surface for CLI/MCP/Pi,
   with byte-aware allocation across subjects. The oracle shows the budget is
   not the problem; the pack must carry the right units.
5. Persist and consume graph edges with a strict resource gate. Schema exists,
   but extraction emits `graph: None`; C# H7 shows unbounded facts blow the
   resource budget.
6. Pass `--trace` from Pi/Codex in Research mode to capture daily candidate
   lineage, while keeping metadata-only privacy controls.

## 11. Reproduce

```bash
W=/home/masis/src/zvec-grep-rust-rag-20261126
A=/home/masis/.cache/zvec-grep/rust-rag-20261126
python3 "$W/benchmarks/rust-rag/tools/freeze_cases.py" \
  --corpus "$A/corpus/clap" --out "$W/benchmarks/rust-rag"
python3 "$W/benchmarks/rust-rag/tools/run_arms.py" \
  --worktree "$W" --corpus "$A/corpus/clap" --artifacts "$A" --arms baseline,h2,h3,h4,h5
python3 "$W/benchmarks/rust-rag/tools/analyze_receipt.py" \
  --artifacts "$A" --tokenizer "$TOKENIZER" --counter "$A/build/target/debug/examples/count_tokens_full"
python3 "$W/benchmarks/rust-rag/tools/run_agent.py" \
  --worktree "$W" --artifacts "$A" --arms baseline,h3,h4
python3 "$W/benchmarks/rust-rag/tools/grade_answers.py" \
  --worktree "$W" --artifacts "$A"
```
