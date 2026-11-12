# E3-01 — independent retrieval evidence benchmark

**Status:** DONE — corpus, annotation, validation, and a three-route smoke run are
complete. E3-02 is the consumer; this report does **not** declare a retrieval-
quality winner.

**Update:** E3-02A created `e3-01-v2` after source review; see
`benchmarks/retrieval-evidence/GOLD-AUDIT.md` and
`rust/docs/e3-02a-independent-gold-and-attribution-20261112.md`. This document
remains the E3-01 v1 record.

**Branch:** `work/e3-01-retrieval-corpus-20261112`

**Base:** `work/e2-integration-20261112` @ `96d543c`

**Main:** `work/zg-recovered` @ `9444eed` untouched.

No production schema, index version, vector policy, installed binary, daemon,
held-out/gold set, RAG/Selection/Expansion data, or main branch was changed.

## 1. What was built

```
benchmarks/retrieval-evidence/
  README.md
  dataset/queries.json       32 frozen queries, 51 evidence anchors, 20 hard negatives
  dataset/corpus.json        392 C# files, per-file SHA-256/bytes/lines
  dataset/manifest.json      SHA-256 bindings and counts
  schema/query.schema.json
  schema/corpus.schema.json
  schema/result.schema.json
  tools/build_dataset.py     source-only builder; never calls zg
  tools/validate.py          schema + hashes + ranges + groups + absence checks
  tools/evaluate.py          Recall/MRR/nDCG/group/anchor/no-answer scoring
  tools/selftest.py          synthetic perfect/trap/partial-chain assertions
  tools/run_harness.sh       native-library wrapper
  tools/requirements.txt     jsonschema
  harness/                   minimal public-API Rust runner
```

Dataset manifest:

```json
{
  "queries_sha256": "5e35c3d6b3d1868864538320a0788bd3d2b76cc859cd9a8f2fe45e9089cb5662",
  "corpus_sha256": "6f87791413b383f65ef8fccc473d9f6af558a3566629280c5f2e57ad0b20940f",
  "query_count": 32,
  "corpus_files": 392,
  "corpus_lines": 98666
}
```

## 2. Corpus selection and provenance

| Repo | Revision | Tag | License | Files |
|---|---|---|---|---:|
| Dapper | `eb47546a408bbf6c178bda4f04dc205bc51ffbfe` | 2.1.89 | Apache-2.0 | 51 |
| Newtonsoft.Json | `0a2e291c0d9c0c7675d445703e51750363a549ef` | 13.0.3 | MIT | 209 |
| ANTLR4 C# runtime | `2ac185af855ff39b4b8c41854b756767099c356f` | 4.13.2 | BSD-3-Clause | 132 |

Why these projects:

- Dapper is small and reflection-heavy; good for exact APIs, SQL/parameter flow,
  and default type-map resolution.
- Newtonsoft.Json has large files and many near-duplicate converters/strategies;
  good for lexical traps and documentation-versus-implementation.
- ANTLR runtime has deep ATN/parser structures; good for multi-locus retrieval
  and error-recovery distinctions.

Excluded: tests, benchmarks, documentation trees, provider packages, other
language runtimes, generated grammars, and build tooling. The frozen corpus is
**C# only**. `corpus.json` pins source URL, revision, tag, license path, tarball
SHA-256, and every included file's hash. Source tarballs are not vendored.

## 3. Query design

**32 queries: 27 answerable, 5 no-answer.**

| Category | Count |
|---|---:|
| exact_symbol | 3 |
| nl_implementation | 3 |
| hardcoded_value | 5 |
| configuration | 1 |
| configuration_flow | 1 |
| cross_file | 2 |
| data_flow | 1 |
| sql_database | 1 |
| serialization | 4 |
| linq | 1 |
| data_structure | 1 |
| docs_vs_impl | 1 |
| multi_hop | 3 |
| no_answer | 5 |

Coverage includes FTS-favorable identifiers, semantic-favorable behavioral
paraphrases, hybrid-favorable cross-file flow, hard-coded values, SQL/database
access, serialization behavior, and absence questions. Leakage is labeled:
9 queries contain a code-shaped identifier, 23 do not.

Each answerable query has 1–4 **evidence anchors** with exact path, 1-based line
range, symbol, role, rationale, confidence, and SHA-256 of the slice. Multi-hop
queries require all semantic groups **and** all required anchor IDs:

- `antlr-07`: `inline_recovery`, `rule_sync`, `diagnostics` (three separate
  recovery pieces, not one causal chain);
- `dapper-11`: `parameter_store`, `parameter_metadata`, `command_projection`;
- `newtonsoft-11`: `attribute`, `contract_property`, `json_emission`.

Hard negatives are real ranges that look plausible but are wrong: another
overload, another naming strategy/converter, a reader-side default instead of a
serializer default, or a helper that does not define the behavior. Twenty
queries have at least one; exact-symbol, hard-coded-value, and no-answer
categories are required by the validator to have one.

No-answer queries are scoped, evidence-free, and backed by executable absence
regexes: PostgreSQL binary COPY, MessagePack, left-recursion elimination in the
runtime, connection pooling in Dapper's core, and YAML in the Newtonsoft subset.

## 4. Validation evidence

- `tools/validate.py` applies the JSON Schemas, recomputes every file and anchor
  hash, re-walks the source tree, checks required anchor IDs/groups, rejects
  hard-negative/evidence overlap, checks anchors stay inside the query's corpus,
  and executes every absence regex. Result: **OK**.
- Building twice produced byte-identical `queries.json`, `corpus.json`, and
  `manifest.json`; `tools/selftest.py` asserts perfect, trap, and partial-chain
  metric behavior.
- The harness was built against the public `zg-engine` API and indexed an
  external copy of the frozen corpus with a C# glob: **392 files, 0 failed,
  8,182 entities**. A per-query corpus filter (`dapper/**`, `newtonsoft/**`,
  `antlr/**`) prevents cross-corpus candidates.
- Three smoke routes over all 32 queries at `limit=50`, model
  `local/potion-code-16m-v2`, one run each:

| Route | Recall@10 | Primary recall@10 | Group@10 | Anchor@10 | Solved@10 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| FTS | 0.4630 | 0.4815 | 0.4630 | 0.4630 | 0.2222 | 0.5566 | 0.4164 |
| Vector | 0.4815 | 0.5185 | 0.4877 | 0.4815 | 0.2963 | 0.5449 | 0.4179 |
| Hybrid | 0.4815 | 0.5185 | 0.4877 | 0.4815 | 0.2963 | 0.5407 | 0.4173 |

Smoke only: one model, one index, one run per route, no warmup/repeat control.
Latency p50/p95 also exists but is not comparable here. These numbers validate
that the dataset and evaluator execute; they are **not** an E3-02 verdict.

No-answer behavior on the smoke run: trap-FP@10 **0.0**, any-result@10 **1.0**,
hard-negative hits **0**. The engine returns adjacent code for absence questions
but does not return the declared traps. Answerable hard-negative rate@10 was
0.03 (FTS), 0.06 (hybrid), 0.09 (vector), showing the distractors can be
retrieved and are not inert.

## 5. Independent review

Annotation was written source-first; no zg result was consulted. An independent
evidence audit and a design review were run against the frozen corpus and the
tools. Their blocking findings were repaired before this report:

- removed a false `Parser.Recover` premise; the query now asks how generated
  parser code delegates to the installed default strategy;
- removed the incorrect Sync→Notify causal chain from the multi-hop query;
- replaced the self-contradictory `QueryImpl` hard negative;
- moved the cancellation evidence from an `ExecuteAsync` declaration that does
  not touch the token to the query path and reader helper that do;
- linked default-value omission to `ShouldWriteProperty`, not dynamic-only code;
- narrowed the settings-flow anchors from a whole 455-line class to the actual
  setting/apply/resolver/writer lines;
- made multi-hop groups semantic and added required anchor IDs;
- replaced the constant “any result” no-answer metric with trap FP, plus a
  separate any-result rate;
- added two scoped no-answer questions and exact/hard-coded hard negatives.

The repaired dataset passes the automated validator, schema checks, absence
checks, deterministic rebuild, and metric selftest. A second full independent
pass was started but did not complete within the review budget; no claim of a
second independent PASS is made. Residual risk is therefore annotation judgment,
not unchecked hashes or unexecuted negative checks. E3-02 should inspect any
surprising query before attributing a score to the retriever.

## 6. Limitations

- Small diagnostic corpus (392 files, 98.7k lines); not a production estimate.
- C# only; no edit planning, generation, or repository-scale exploration.
- Line-overlap relevance is strict and reproducible; adjacent correct evidence
  outside an anchor is scored as a miss and should be inspected.
- Five no-answer questions detect declared traps, not a calibrated FP rate.
- One local model in the smoke run; E3-02 must state its model/panel and cannot
  generalize beyond it.
- Smoke results have no repeat/warmup control and must not be used for ranking.

## 7. E3-02 handoff

Recommended protocol:

1. Freeze this dataset hash; do not tune query text or anchors after seeing
   results. Any change creates `e3-01-v2` with a new manifest.
2. Build one index per model, then run FTS, vector, hybrid, and hybrid-symbol
   against the same index; `limit=50`, per-query corpus filter, no auto-update.
3. Warm up the model, then run repeated measurements with fixed order or
   interleaving; report p50/p95 and paired query-level deltas, not just means.
4. Report overall, by corpus, by category, and by leakage. Use
   `required_anchor_coverage`/`solved` for multi-hop, `recall` for breadth.
5. Treat no-answer `trap_fp` as primary; report `any_result` separately and never
   convert “returned something” into a correctness claim.
6. Inspect answerable hard-negative hits and zero-recall queries before
   attributing a score to the retriever; distinguish annotation/ranges from
   engine behavior.
7. Only then issue KEEP/REJECT/INCONCLUSIVE for a retrieval policy. This dataset
   deliberately does not encode that decision.
