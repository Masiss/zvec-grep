# RAG R4 — bounded evidence expansion pilot

Status: **R4 QUALITY PILOT FAIL against the frozen optimization goals; no
candidate GO. Performance/PSS/concurrency and held-out were not measured.**
Date: 2026-11-08. Follows `docs/rag/implementation-plan.md` §6,
`rust/docs/rag-r3-sql-facts.md` and `benchmarks/rag-impact/PREREGISTRATION.md`.

## What was implemented

`crates/zg-engine/src/extraction/candidate.rs` is a bounded evidence expander:

- consumes existing search seeds (`path`, line range);
- adds containing methods, query-matching method/assignment/invocation evidence,
  resolved callees/callers, receiver-context type definitions and selected SQL
  definitions;
- deduplicates by `(path, start_line, end_line)`, sorts by a deterministic score
  and applies a hard item budget;
- keeps dynamic/reflection/dynamic-SQL targets as candidates;
- no index/search default change, no storage change, no new model.

The pilot harness reuses the frozen B0 search seeds (the six-call policy) and
compares the 10-item candidate pack against the frozen B0 metrics. It writes
`benchmarks/rag-impact/results/candidate-pilot.json`.

## Result (development split, 12 real cases)

| Metric | B0 | Candidate | Target | Result |
|---|---:|---:|---:|---|
| Evidence Recall@10 | 0.4722 | **0.2917** | B0 + 10pp | **FAIL (-18.06pp)** |
| Additional source reads | 2.75 | **5.75** | ↓ ≥30% | **FAIL (+109.1%)** |
| Additional context tokens | 3,716 | **2,433** | ↓ ≥20% | PASS (-34.5%) |
| Task-correctness proxy | 0.3333 | **0.2500** | ≥ B0 | **FAIL** |
| Citation/source accuracy | not measured for candidate | — | 100% | NOT MEASURED |
| Search p95 / PSS / indexing / concurrency | B0 only | not measured | relative gates | NOT MEASURED |

The candidate reduced tokens but lost evidence recall, increased distinct files
and failed the task proxy. It is not a promote-able candidate.

## Why it failed

- Ranking seeds by path/method term overlap displaced B0 evidence that did not
  match literal query terms.
- The expander added many query-matching methods from seed files, consuming the
  ten-item budget with lower-value spans.
- File diversity increased instead of being bounded; reads rose.
- The pilot is a deterministic expander, not the full DEFINE/DISCOVER/EXPAND/
  CHECK/FINAL agent loop, so it cannot recover from a bad seed set.

## Gate status

| R4 gate item | Status |
|---|---|
| Bounded item budget / dedup / cycle-safe expansion | PASS (unit tests) |
| Source instruction is not executed | PASS at fact level (R2/D16) |
| Scripted-agent end-to-end loop | NOT IMPLEMENTED |
| Missing mapping / wrong branch / stale evidence / budget exhausted | PARTIAL (R1 evidence tests; no full loop) |
| Candidate quality/benefit targets | **FAIL** |
| Candidate performance/PSS/concurrency | NOT MEASURED |
| Held-out candidate evaluation | NOT RUN (implementer must not inspect) |

## Disposition

**NO-GO for expanded relation/evidence mode.** Keep the R1 lightweight evidence
contract as the safe mode. Do not adjust the frozen targets or corpus after
seeing this result. A future attempt needs a new experiment ID and a new
pre-registration, and should focus on:

1. preserving B0 evidence before adding expansion items;
2. a file/byte budget rather than only an item budget;
3. a real scripted agent loop with CHECK/corrective round;
4. held-out and performance measurement with independent review.

## Evidence

- `benchmarks/rag-impact/results/candidate-pilot.json` — per-case candidate/B0
  metrics and limitations.
- `benchmarks/rag-impact/results/b0-official.md` — frozen baseline.
- `rust/docs/rag-r2-csharp-facts.md`, `rust/docs/rag-r3-sql-facts.md`.
