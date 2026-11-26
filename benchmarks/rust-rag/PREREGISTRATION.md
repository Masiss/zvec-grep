# Rust RAG investigation — preregistration

Frozen before any index build or query execution on this corpus.

## Corpus and gold

- Independent repository: `clap-rs/clap`, commit `4201754a400f676c9744da66383c89eb9f41af88`.
- Corpus manifest: `benchmarks/rust-rag/corpus-manifest.json`, SHA-256
  `d48597b3fd659d7d9a2cfc45c0ec8f0f2c415a907038e71165c7b46a56084113`.
- Gold cases: `benchmarks/rust-rag/cases.json`, SHA-256
  `c6e1a7e7441bb6ad235271bddfb5ed7ac6c6729d61c31e361d7dfd4f984885a1`.
- 12 cases R01–R12. Every `source_refs` span was verified against the pinned
  source file hash and span boundary line before baseline. The case file is
  evaluation-only; runtime policies see `question` text only.
- Old C# held-out (`cases/heldout.json`, D13–D16 fixtures) is not opened or used.

## Engine baseline

- Source branch: `work/rust-rag-investigation-20261126` at base `51c9eba`.
- Binary: isolated debug build with `CARGO_TARGET_DIR` under
  `~/.cache/zvec-grep/rust-rag-20261126/build/target`; SHA-256 recorded in
  `baseline-binary.sha256` in the artifact directory.
- Potion `minishlab--potion-code-16M-v2` and current RC3 extraction/ranking are
  the baseline. No model, embedding, ranking or production file is changed.

## Contract

Per case, at most 6 tool/search calls, 3 rounds, 10 selected items, 24 KiB
selected pack and 64 KiB delivered tool content. These bounds follow the R4
contract so Rust results remain comparable with C# receipts. Any prototype that
exceeds a bound is reported as NO-GO, not silently retuned.

## Metrics

- `pool_recall`: fraction of gold spans appearing anywhere among candidate results.
- `selected_recall`: fraction of gold spans covered by the final <=10 item pack.
- `macro_recall`: per-case recall averaged over the 12 cases.
- `citation_precision`: fraction of delivered spans whose file hash/line range
  revalidates against the pinned corpus.
- `context_tokens`: Potion tokenizer count of the delivered pack.
- `p50/p95 latency`, peak RSS and PSS per run, captured externally.
- `task_correctness`: deterministic grader over required facts plus an external
  agent answer where run; no claim of human acceptance.
- `inference_cost`: input/output tokens of external agent runs from session
  receipts. No dollar estimate without provider pricing.

## Preregistered hypotheses and stop rules

| ID | Hypothesis | Isolated change | Success threshold | Stop rule |
|---|---|---|---|---|
| H1 | Raw natural-language questions are the dominant acquisition loss | Baseline only, no expansion | None; establishes reference | One baseline run |
| H2 | Corpus-grounded identifier expansion improves pool recall | Add symbol/fact terms derived from the corpus, no gold | Pool recall >= baseline + 0.10 without extra calls | One run; reject if selection also drops |
| H3 | Method-level evidence units improve selected recall | Narrow retrieved declarations to method ranges with declaration headers, same candidates | Selected recall >= baseline + 0.10 at <=24 KiB | One run; reject if oracle-over-pool drops |
| H4 | Bounded one-hop typed traversal improves multi-hop cases | Resolve locally declared calls from seed spans, <=1 hop, bounded items | Selected recall >= baseline + 0.05 on R01/R06/R11-class cases | One run; reject if RSS > 256 MiB or calls > 6 |

Every experiment records command, binary hash, corpus hash, case hash, raw
candidates, selected pack, timing/resource counters and a verdict. No gold term
is inserted into a runtime query. No threshold is changed after measurement.

## Amendment A — H5 (added after baseline, before running H5)

Baseline diagnosis showed pool recall `0.7292` but delivered recall `0.2500`,
and an evaluation-only oracle over the same pool reaches `0.7292` under the same
10-item/24 KiB pack. That motivated one isolated selection experiment:

- **H5**: reuse baseline candidates, narrow large declarations to method units
  as in revised H3, then rerank by lexical overlap between the *question text*
  (available to any runtime policy) and each candidate's symbol/signature/source
  window. No gold fact, gold span or gold identifier is used by the scorer.
- Success threshold: delivered macro recall `>=0.35` (baseline `0.25 + 0.10`)
  with no new search call and pack `<=24 KiB`.
- If H5 fails, selection is recorded as unresolved and no further blind rerank
  variant is run in this mission.
