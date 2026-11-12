# E3-01 — independent retrieval evidence benchmark

This benchmark measures whether `zvec-grep` returns **the source evidence a
coding agent needs**, not merely text that is semantically similar. It is built
from third-party C# source with frozen revisions, per-file hashes, and source-
first annotations. It is not derived from the repository's own docs, tests, or
retrieval outputs.

## 1. What is frozen

The current dataset is **v2**. See `GOLD-AUDIT.md` for the v1→v2 errata;
v1 remains under `dataset/archive/v1/`.

| Repository | Revision | Tag | License | Included subset | Files |
|---|---|---|---|---|---|
| Dapper | `eb47546a408bbf6c178bda4f04dc205bc51ffbfe` | 2.1.89 | Apache-2.0 | `Dapper/` core | 51 |
| Newtonsoft.Json | `0a2e291c0d9c0c7675d445703e51750363a549ef` | 13.0.3 | MIT | `Newtonsoft.Json/{Serialization,Linq,Utilities,Converters,*.cs}` | 209 |
| ANTLR4 C# runtime | `2ac185af855ff39b4b8c41854b756767099c356f` | 4.13.2 | BSD-3-Clause | `runtime/CSharp/src/{Atn,Misc,*.cs}` | 132 |

Total: **392 files, 98,666 lines**. `dataset/corpus.json` contains the URL,
revision, tag, license path, tarball SHA-256, and every included file's SHA-256,
byte size, and line count. The source tarballs are not vendored; `corpus.json`
is the reproducible manifest. `dataset/manifest.json` binds the query dataset
and corpus manifest by SHA-256.

The source checkout used to build this dataset is external and is not part of
the repository. Rebuilding requires that checkout:

```bash
python3 tools/build_dataset.py --source /path/to/frozen/corpus --output dataset
python3 tools/validate.py --source /path/to/frozen/corpus --dataset dataset
```

`validate.py` applies the JSON Schemas and then checks every anchor hash, range,
required-anchor set, hard-negative overlap, corpus scope, and absence regex.

The builder and validator never call `zg`; annotations were written from source
reading before any retrieval run.

## 2. Corpus selection rationale

The three projects exercise different retrieval failure modes:

- **Dapper** is compact, reflection-heavy, and full of exact API names. It tests
  SQL/parameter/config flow and cross-file type-map resolution.
- **Newtonsoft.Json** has large files, many similarly named converters and
  strategies, and subtle defaults. It tests lexical traps, serialization flow,
  and documentation-versus-implementation questions.
- **ANTLR4 runtime** has deep ATN/parser data structures and cross-file error
  recovery. It tests multi-hop evidence and distinguishing runtime prediction
  from grammar generation.

Excluded: tests, benchmarks, documentation trees, provider-specific packages,
other language runtimes, generated grammars, and build tooling. Those would add
noise and, for no-answer questions, would make absence claims ambiguous.

## 3. Query set

**32 queries / 27 answerable / 5 no-answer (v2), 54 evidence anchors.** Categories:

| Category | Count | What it asks for |
|---|---:|---|
| exact_symbol | 3 | A named API/method |
| nl_implementation | 3 | Behavior described without the implementation name |
| hardcoded_value | 5 | A constant, default, or literal |
| configuration | 1 | Public flag/setting |
| configuration_flow | 1 | Settings through serializer/contract |
| cross_file | 2 | Resolve then use another implementation |
| data_flow | 1 | Cancellation through async calls |
| sql_database | 1 | SQL literal substitution |
| serialization | 4 | Enum/date/float/reference encoding |
| linq | 1 | `JObject` lookup semantics |
| data_structure | 1 | Interval-set complement |
| docs_vs_impl | 1 | Deferred versus buffered behavior |
| multi_hop | 3 | 2–3 required evidence groups |
| no_answer | 5 | Absent capability in frozen scope |

Coverage includes FTS-favorable exact identifiers, semantic-favorable
behavioral paraphrases, and hybrid-favorable cross-file/call-flow questions.
No-answer scopes are explicit (`dapper/`, `newtonsoft/`, `antlr/`) and backed by
regex absence checks over the frozen files.

Leakage is annotated from the question text and gold symbols: 8 queries intentionally contain an identifier; 24 do not. Use this field to report leakage-stratified results; do not infer it from retrieval scores.
E3-02 should report both overall and leakage-stratified metrics.

### Evidence and hard negatives

Each answerable query has 1–4 **evidence anchors**: corpus-relative path, exact
1-based line range, symbol, role (`primary`/`supporting`), rationale, confidence,
and SHA-256 of the exact slice. Multi-hop queries require every group in
`solved_when.required_groups` and every ID in `solved_when.required_anchor_ids`.

Hard negatives are real source ranges that look plausible but are wrong: another
overload, another naming strategy, another converter, or a nearby parser path.
They are never counted as relevant. Every no-answer query must have at least one
hard negative and one absence check.

## 4. Metrics

`tools/evaluate.py` defines candidate relevance as **same path + line-range
overlap** with an evidence anchor. A candidate with no line range cannot match.

- **Recall@K** — unique evidence anchors hit / all evidence anchors.
- **Primary Recall@K** — same, restricted to `role=primary`.
- **Group coverage@K** — semantic evidence groups hit / required groups.
- **Required-anchor coverage@K** — required evidence anchor IDs hit / all
  required IDs. This is stricter than group coverage and catches a partial
  multi-anchor chain.
- **Solved@K** — every required anchor ID hit and at least
  `min_primary_evidence` primary anchors hit.
- **MRR@10** — reciprocal rank of the first relevant anchor.
- **nDCG@10** — graded gain: primary 2, supporting 1.
- **No-answer trap FP@K** — whether a declared hard-negative trap is returned
  in the top K. This is the primary no-answer metric.
- **No-answer any-result@K** — whether any candidate is returned. It is reported
  separately because a useful engine may legitimately return adjacent code; it
  is not treated as a gold-evidence false positive.
- **Answerable hard-negative rate@10** — share of answerable queries where a
  declared distractor is returned, revealing plausible-but-wrong retrievals.
- **Latency** — per-query wall time; report p50/p95, not a throughput claim.

Confidence intervals use a deterministic query-level bootstrap
(2,000 resamples, seed `20261112`) and are reported for macro averages.
`tools/selftest.py` synthesizes perfect, trap, and partial-chain results to test
these definitions without an engine.

## 5. Running

Install the validator dependency (`python3 -m pip install -r tools/requirements.txt`).
Build the harness against the public `zg-engine` API, then index an external copy
of the frozen corpus (never index the dataset/source checkout in place). The
harness indexes only `*.cs` and applies a per-query `corpus/**` filter; the
expected C# count is 392, matching `corpus.json`.

```bash
cd harness
CARGO_TARGET_DIR=/external/target cargo build --release

cd ..
tools/run_harness.sh index --root=/external/work/corpus \
  --cache=/external/model-cache --rebuild=true \
  --model=local/embeddinggemma-300m

tools/run_harness.sh query --root=/external/work/corpus \
  --cache=/external/model-cache --dataset=dataset/queries.json \
  --route=hybrid --limit=50 --output=/external/receipts/hybrid.json

python3 tools/evaluate.py --dataset=dataset/queries.json \
  --results=/external/receipts/hybrid.json \
  --output=/external/receipts/hybrid-report.json \
  --markdown=/external/receipts/hybrid-report.md

# Optional fixed-order repeated baseline (3 runs per route):
tools/run_matrix.sh /external/work/corpus /external/model-cache \
  /external/receipts/matrix local/embeddinggemma-300m 3 dataset/queries.json
python3 tools/aggregate_matrix.py --dataset=dataset/queries.json \
  --matrix-dir=/external/receipts/matrix \
  --output=/external/receipts/matrix-summary.json
```

Routes are explicit (`fts`, `vector`, `hybrid`, `hybrid-symbol`). The harness
does not alter the index between routes and returns only public context items.

## 6. Scope and limitations

- This is a **C# evidence-retrieval** benchmark. It does not cover other
  languages, repository-scale exploration, edit planning, or generation.
- No-answer evidence is negative by construction; with five scoped questions it
  is a trap detector, not a calibrated false-positive probability.
- 392 files / ~99k lines is deliberately small enough for exhaustive manual
  annotation; it is a diagnostic set, not a production quality estimate.
- One model (or a small model panel) is enough to compare retrieval behavior,
  but results do not generalize to every embedding backend.
- Line-overlap relevance is reproducible and strict; it can miss a correct
  answer that lands in an adjacent, non-annotated range. Those misses should be
  inspected, not assumed to be model failures.
- No-answer FP@K is intentionally strict; a useful engine may still return
  nearby code. Use the hard-negative column to interpret it.
- This benchmark does not authorize changes to production schemas, vector
  policies, or the main branch.

## 7. Files

```
THIRD_PARTY.md            source attribution and license references
dataset/queries.json       frozen v2 queries + evidence + hard negatives
GOLD-AUDIT.md              v1→v2 errata and per-query source audit
dataset/archive/v1/        preserved v1 manifest and files
dataset/corpus.json        source provenance and per-file hashes
dataset/manifest.json      SHA-256 bindings
schema/*.json              JSON Schema for query, corpus, and run results
tools/build_dataset.py     source-only builder (no retrieval calls)
tools/validate.py          checksum/range/group/absence validator
tools/evaluate.py          deterministic scoring and bootstrap CIs
tools/selftest.py          synthetic metric assertions
tools/attribute.py         FTS/vector overlap and declaration-type attribution
tools/aggregate_matrix.py  repeated-run variance and route aggregation
tools/run_matrix.sh        fixed-order repeated route runner
tools/requirements.txt     validator dependency
tools/run_harness.sh       native-library wrapper
harness/                   minimal public-API Rust runner
```
