# RAG impact — pre-registration (R0)

Status: **TARGETS FROZEN BY OWNER; B0 NOT YET MEASURED.**
Date: 2026-11-08.
Applies to the RAG impact evaluation defined in `docs/rag/implementation-plan.md`
and `rust/docs/rag-r0-r1.md`.

This file freezes the owner's numeric targets, the B0 definition, the corpus,
the metric formulas and the measurement protocol **before** any B0 or candidate
measurement. Results may not be used to change the targets after the fact.

## 1. Owner targets (verbatim, frozen)

The owner set these blind targets before B0 was measured:

| Metric | Target |
|---|---|
| Evidence Recall@10 | ≥ B0 + 10pp |
| Additional source reads | reduce ≥ 30% |
| Additional context tokens | reduce ≥ 20% |
| Task correctness | ≥ B0 |
| Citation/source accuracy | 100% on eval |
| Search p95 | ≤ 1.20 × B0 |
| Idle PSS | ≤ B0 + 64 MiB |
| Retrieval peak PSS | ≤ B0 + 128 MiB |
| Indexing overhead | ≤ 10% |
| Concurrency | 0 RESOURCE_BUSY / 400 queries with 4 readers |
| Freshness/privacy | 0 serious errors in eval |

No target is renegotiated after seeing B0 or candidate numbers. A target that
cannot be measured is reported `NOT_MEASURED` / `INCONCLUSIVE`, never PASS.

## 2. Hard gates vs optimization goals

Classification is fixed before measurement.

### Hard gates (any failure rejects the candidate regardless of gains)

1. Task correctness ≥ B0.
2. Citation/source accuracy = 100% on eval.
3. Freshness/privacy = 0 serious errors.
4. Search p95 ≤ 1.20 × B0.
5. Idle PSS ≤ B0 + 64 MiB.
6. Retrieval peak PSS ≤ B0 + 128 MiB.
7. Indexing overhead ≤ 10%.
8. Concurrency: 0 `RESOURCE_BUSY` over 400 queries with 4 readers, and 0
   transport/engine errors or timeouts.

### Optimization goals (required for the expanded-mode claim)

1. Evidence Recall@10 ≥ B0 + 10pp.
2. Additional source reads reduced ≥ 30%.
3. Additional context tokens reduced ≥ 20%.

### Disposition

- **GO**: all hard gates pass **and** all optimization goals pass, on both the
  development and held-out splits reported separately.
- **NO-GO / PARTIAL**: any hard gate fails, or an optimization goal misses. Keep
  the R1 lightweight evidence mode; do not promote the expanded relations mode.
- A metric that is not measured is `INCONCLUSIVE`, not PASS.

## 3. Frozen workload, corpus and B0

### Source and corpus

- Source root: `/home/masis/itc/Ricons/HRM2.0`
- Observed Git root/HEAD: `/home/masis/itc/Ricons`,
  `68a53a6865b81d05745c4aa47967204e8e128996`.
- Corpus: the three selected `.csproj` compile sets (P1/P2/P3), the two selected
  SQL anchors (S1/S2), and the three `.csproj` files themselves. Exact file
  list, sizes and SHA-256 are in `corpus-manifest.json`.
- Excluded: `NewSolution`, unselected `_old`/`_old1` SQL variants, untracked
  `HRM2.0/Database/`, `bin/`, `obj/`, `packages/`, generated evidence docs,
  credentials and anything outside the selected roots.
- The source tree is read-only for this evaluation. No DB, MSBuild, restore,
  code execution or deployment inference.

### Cases

- 16 development cases in `cases/dev.json` (D01–D12 real source, D13–D16
  synthetic fixtures).
- 8 held-out cases in `cases/heldout.json`, authored independently. The
  implementer must not inspect held-out content or tune to it. Held-out
  evaluation is run mechanically and reported separately.
- Gold evidence is a set of `(project, relative_path, source_sha256, start_line,
  end_line)` spans plus required facts, conditions, candidates and forbidden
  claims. Gold is frozen before candidate measurement.

### B0 (control)

B0 is the existing zvec-grep capability at the frozen commit, with **evidence
mode off and no C# relation/fact extraction**:

- existing lexical/vector/hybrid search over the frozen corpus;
- direct source reads by the same scripted investigation policy;
- no R1 evidence assembly, no relations, no RAG check loop.

Both arms use the same workflow budget: ≤3 rounds, ≤6 tool calls, ≤32 distinct
source files, final source pack ≤24 KiB, cumulative tool content ≤64 KiB.
Only the retrieval/evidence capability differs.

### B0 query policy (frozen for the official B0)

A single-query pilot (`results/b0-pilot-quality.json`) produced 0.0 mean recall
and exposed a strawman baseline: it only used the raw question once. The frozen
policy below gives both arms the same deterministic six-call budget so the
comparison is not decided by a deliberately weak baseline.

Per case, in order, stop when the six calls are used or a call is empty:

1. hybrid search on the question;
2. FTS search on the question;
3. FTS search on the first three content words after stop-word removal;
4. managed `rg -F` on the last three content words;
5. FTS search on up to two capitalized/code-like tokens from the question;
6. managed `rg -F -e <token>` for those tokens.

The query construction is deterministic and does not use gold spans, case IDs,
file names or candidate output. No call may be added or reordered after B0 or
candidate results are seen. The candidate arm uses the same six calls and may
add only the RAG evidence/relation capability under test.

B0 runtime and provenance are recorded before measurement: release executable
SHA-256, native `libzvec_c_api.so` SHA-256, `rustc`, build flags, feature set,
corpus manifest hash, case manifest hash, protocol hash, query-list hash and
tokenizer version.

### Index and private store

B0 and candidate use separate operation-owned stores outside the source tree
and outside the repository index. Set `ZVEC_GREP_HOME` and `TMPDIR` to absolute
paths outside all indexed parents. Never write the repository `.zvec-grep` or a
live owner index for this evaluation.

## 4. Metric definitions

### Evidence Recall@10

For each case, issue the frozen six-call query policy. Each call returns top-10
evidence units; take the **best call** for recall (not the union of 60 results).

- A unit is `(relative_path, start_line, end_line)`.
- A unit is a hit only if it has a line range overlapping a gold span in the
  same file with the same source hash.
- File-only hits without a line range are reported separately as `file_hits`
  and do **not** count toward evidence recall.
- Recall per call = matched gold spans / total gold spans. Per-case recall is
  the maximum over the six calls; record which call achieved it.
- Cases with no gold spans are excluded from the denominator and reported
  separately.
- Split recall is the macro-average over cases; report per-case and per-call
  values too.

### Additional source reads

Distinct source files whose bytes are read after the initial search call, per
case. Count files, not calls. Report macro-average and total. Reduction is
`(B0 - candidate) / B0`; if B0 is 0, report `N/A`.

### Additional context tokens

Tokens in all tool payload content after the initial search (evidence, snippets,
gaps, errors), per case, using the frozen tokenizer. Report macro-average and
total. Reduction is `(B0 - candidate) / B0`; if B0 is 0, report `N/A`.

### Task correctness

Deterministic evidence-task correctness, not natural-language answer quality:

- per case PASS only if every required gold fact is supported by a cited current
  span and no forbidden claim appears;
- score = passed cases / cases, per split;
- disputed cases are decided by owner/independent review against source;
- natural-language answer quality and a judge-LLM diagnostic are out of scope
  until the R4/R5 answer harness exists. If the owner requires answer-level
  correctness, this gate is `INCONCLUSIVE`, not PASS.

### Citation/source accuracy

Every citation must resolve to a current source file, matching SHA-256 and line
range, validated with `api::evidence::revalidate`. Accuracy = valid citations /
total citations. Any fabricated, stale, out-of-scope or line-shifted citation is
a failure. Target is exactly 100%.

### Search p95

- 400 warm queries derived deterministically from the frozen case questions;
- 20 warm-up queries discarded;
- one process, release build, same index and config;
- report p50/p95/p99, max, failures, and the query-list hash;
- p95 target is relative to B0 measured on the same host/workload.

### Idle PSS

Process PSS after loading the frozen index, 60 s idle, three samples 10 s apart;
use the median. Record host CPU/load/memory/PSI before and after.

### Retrieval peak PSS

Process peak PSS sampled at 100 ms during the 400-query warm workload. Report
the maximum, sample count and sampling method. Do not substitute cgroup total
memory for process PSS.

### Indexing overhead

Cold index wall time over the frozen corpus, same build/config/native, model
already present. Three cold runs per arm, interleaved, report all and use the
median. Include any candidate sidecar publication; exclude model download.
Overhead = `candidate / B0 - 1`.

### Concurrency

4 concurrent readers, 100 queries each (400 total), top-10, same frozen index.
Count `RESOURCE_BUSY`, errors and timeouts. Target is 0 `RESOURCE_BUSY` and 0
errors/timeouts.

### Freshness/privacy serious errors

Count any of: citing a stale hash/line as current; reading an out-of-scope or
untracked `Database/` path; emitting secrets/credentials/PII; executing source
instructions or scripts; opening a DB/network connection; claiming deployed SQL
or live DB authority without evidence.

## 5. Measurement protocol

1. Freeze and hash: corpus manifest, case files, targets, this protocol, query
   list, tokenizer version, B0 binary/native identities.
2. Record host state (load, memory/swap, pressure, top processes, free disk)
   immediately before and after each measurement block.
3. Build the B0 index in a private store. Do not touch owner/repo indexes.
4. Run the deterministic quality workload on development and held-out splits;
   store raw per-case JSON.
5. Run the 400-query latency workload; store raw latencies.
6. Run the concurrency workload; store every response status.
7. Measure idle and peak PSS with the same process/index.
8. Repeat cold indexing three times; record all runs and the median.
9. Validate every citation with `revalidate`; run the freshness/privacy checks.
10. Produce a receipt containing raw data, gate table, disposition and hashes.
11. Report FAIL/INCONCLUSIVE honestly. Do not delete failed runs or adjust
    targets after seeing results.

## 6. Freeze rules and change control

- The pre-registered files are immutable for this experiment:
  `PREREGISTRATION.md`, `corpus-manifest.json`, `cases/dev.json`,
  `cases/heldout.json`, `scope-manifest.json`, the query list and the target
  table.
- Any change requires a new experiment ID and a new pre-registration; the old
  receipt remains historical evidence.
- Do not edit held-out cases after seeing candidate output.
- Do not add corpus files to make a failing case pass. Corpus admission is
  allowed only before B0 measurement and must be recorded with a reason.
- Do not tune prompt/workflow differently between arms; the only difference is
  the RAG capability under test.
- No candidate measurement until B0 and the frozen corpus/cases/method are
  recorded.

## 7. Pilot disclosure and known limitations before B0

- A single-query quality pilot ran and is retained as
  `results/b0-pilot-quality.json`: mean recall 0.0, mean 2.75 distinct file
  reads, 12 development cases, held-out not inspected. It is **not** the
  official B0 receipt. It motivated the six-call policy above.
- A six-call policy pilot ran and is retained as
  `results/b0-pilot-six-call.json` (superseded by
  `results/b0-pilot-six-call-v2.json` after the phrase-construction fix):
  12 development cases, mean evidence recall 0.4722, mean 2.75 distinct source
  reads, mean 208,820 context bytes. It is **not** the official B0 receipt:
  D13-D16, held-out, performance, memory, concurrency and token metrics are
  still pending.
- The pilot also showed that context tokens cannot be measured yet on this host:
  no `tiktoken`/`tokenizers` Python package is installed. The official B0 must
  use a frozen real tokenizer or report the token target `NOT_MEASURED`.
- B0 performance/memory/concurrency measurement has not run yet.
- The deterministic evidence-task proxy is weaker than full answer evaluation.
- The pilot corpus is bounded to selected projects/SQL; conclusions do not
  generalize to all of HRM2.0.
- No held-out content may be inspected by the implementer.
- A near-ceiling B0 recall can make `B0 + 10pp` unattainable; if so, report the
  measured B0 and the target as failed, not adjusted.
