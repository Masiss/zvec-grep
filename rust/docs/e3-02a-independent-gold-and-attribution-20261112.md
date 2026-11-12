# E3-02A — independent gold review and retrieval attribution

**Status:** DONE
**Branch:** `work/e3-02a-attribution-20261112`
**Base:** `work/e3-01-retrieval-corpus-20261112` @ `ae3aab2`
**Runtime reference:** `work/e2-integration-20261112` @ `96d543c`
**Main:** `work/zg-recovered` @ `9444eed` untouched.

No production schema, index version, vector policy, ranking, installed binary,
daemon, production index, held-out data, or main branch was changed. E3-02B was
not opened.

## 1. Verdicts

| Area | Verdict |
|---|---|
| Gold dataset | **QUALIFIED** — v2; all 32 queries and 54 anchors reviewed; no DISPUTED query |
| Evaluation harness | **PASS** — schema, hashes, ranges, groups, absence checks, boundary/duplicate tests, deterministic repeats |
| Retrieval attribution | **SUFFICIENT** for a P0 decision; not a production generalization |
| E3-02B | **NO-GO** for a broad split-collection / vector-policy experiment |

The NO-GO is about the proposed **split-collection complexity**, not about
whether hybrid retrieval is useful. Hybrid is the strongest route at top-10.
The observed complement is real but small, container candidates dominate the
relevance, and P0 cannot turn post-hoc filtering into an ANN-equivalent estimate
of removing a collection.

## 2. Model and index identity

- Model: `local/embeddinggemma-300m`
- Artifact: `hf_ggml-org_embeddinggemma-300M-Q8_0.gguf`
- Artifact bytes: `333,590,944`; SHA-256 `b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63`
- Artifact revision: `0f741b5a6585bd53aeb15cd1372c56f2a0f65e12`
- Cache completion fingerprint: `efec4039b54e689ff153b650`
- Dimension/metric: **768 / cosine**; max input **2048** tokens; max batch **16**
- Format: `embeddinggemma`; backend: `llama-cpp-embedding-v3`; build identity:
  `9b190d9b1f18649320c98119878215ecd91584dab62785478f923f2ab5f79804`
- Device: CPU (no GPU plugin used)
- Preprocessing: query `task: search result | query: <text>`; document
  `title: none | text: <text>`
- Index: version 3, generation `697c38ac-f941-4266-8303-ed3dffbba4b0`,
  392 C# files, **8,182 indexed entities**, scan glob `*.cs`, no automatic
  updates during measurement.
- Index disk: **60 MB** total; `embedding.index.5.proxima` is 21.5 MB.
- Model cache: 319 MB.
- Exact vector-row cardinality is not exposed by the public API; 8,182 is the
  entity count and the vector file size is reported separately.

## 3. Gold review

The complete per-query decision table is `benchmarks/retrieval-evidence/GOLD-AUDIT.md`.
The v1 dataset is preserved at `dataset/archive/v1/`; v2 is the current freeze.

Summary:

- 32 queries: 27 answerable, 5 no-answer.
- 54 evidence anchors, 20 hard negatives.
- 8 queries contain an identifier; 24 do not.
- Every query has a PASS decision with a source rationale; no query is marked
  DISPUTED or UNKNOWN.
- Three v1 evidence gaps were corrected in v2, with no query text or retrieval
  result used to choose the correction:
  1. `dapper-05` added `SqlMapper.TypeMapProvider`;
  2. `newtonsoft-11` added the actual `JsonProperty.PropertyName` assignment;
  3. `antlr-04` added the `Parser.Match` delegation call site.
- No-answer absence was checked both by the executable regex and by reading the
  scoped behavior; `any_result` is not treated as no-answer correctness.
- Lexical overlap between query text and indexed source was low in the spot
  review (no query copied from a doc comment); identifier leakage is explicit.

Freeze hashes:

- v2 `queries_sha256`: `a2d2210e759f948ddd8dfe3387069fb219f84a307546cb0f905502439e229650`
- v2 `corpus_sha256`: `ac8c5d80b8dcc6f8ecc34cc235b71a30fe02773834efdb6334802ff54c07224f`
- v1 archive: `queries_sha256` `5e35c3d6...`, `corpus_sha256` `6f877914...`

## 4. Harness audit

Independent checks performed:

- `tools/validate.py`: schema, per-file hashes, anchor hashes/ranges, required
  anchor IDs/groups, hard-negative overlap, corpus scope, absence regexes — **OK**.
- `tools/selftest.py`: perfect, trap, partial-chain, boundary, wrong-path, and
  duplicate-fragment fixtures — **OK**.
- Candidate ranks are sorted before scoring; duplicate fragments do not count as
  additional unique evidence.
- `required_anchor_coverage` and `solved` require every required anchor ID, not
  just one anchor in a semantic group.
- `any_result` is reported separately from no-answer trap-FP.
- Per-query corpus filters were checked on every measured run: **0 cross-corpus
  candidates**.
- Three repeats per route: **27/27 answerable queries had identical top-10
  hit sets**; mean Jaccard **1.0**.
- One duplicate candidate key was observed per route
  (`SqlMapper.GridReader.Async.cs:125-215` at two ranks). It is an engine
  artifact, not counted as extra evidence; if it lands in a top-K slot it can
  waste a slot and is recorded as a residual harness/engine observation.

The mapping remains line-overlap with the annotated range. That is intentionally
strict and reproducible; it is not a human judgment of “the agent could read
this adjacent code”.

## 5. Real-model baseline

Same frozen v2 queries and corpus; `limit=50`; three runs per route; model CPU;
no tuning of weights, ANN, ranking or indexing between routes.

| Route | R@5 | R@10 | R@50 | PR@10 | Anchor@10 | Solved@5 | Solved@10 | Solved@50 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FTS | 0.4599 | 0.5494 | 0.7469 | 0.5926 | 0.5494 | 0.2593 | 0.4074 | 0.5926 | 0.5660 | 0.4782 |
| Vector | 0.5370 | 0.5741 | **0.8179** | 0.6667 | 0.5741 | 0.3333 | 0.3704 | **0.7037** | 0.5733 | 0.5117 |
| Hybrid | **0.5463** | **0.6142** | 0.8025 | **0.7037** | **0.6142** | **0.3704** | **0.4444** | 0.6667 | **0.5963** | **0.5387** |

Repeated-run variance for these macro metrics was **0.0**; the engine/ANN path was
deterministic on this corpus for the observed top-10 sets. This is not proof of
ANN determinism under different concurrency or index layouts.

No-answer: trap-FP@10 **0.0** for all routes; any-result@10 **1.0**; declared
no-answer traps were not returned. Answerable hard-negative rate@10: FTS **0.111**,
vector **0.148**, hybrid **0.148**. The main distractor hits were the Dapper
`DefaultTypeMap`/`DbString`/`CommandFlags` negatives and the Newtonsoft
`SnakeCaseNamingStrategy`; they are real plausible-but-wrong retrievals.

## 6. Unique contribution

At K=10, counting unique evidence anchors (not duplicate fragments):

| Route | Unique anchors |
|---|---:|
| FTS | 30 |
| Vector | 28 |
| Hybrid | 31 |

Type-only counts are in §7. Across FTS vs vector: 5 anchors are FTS-only, 3 are vector-only, 25 are common.
At K=5 the split is 2 / 4; at K=50 it is 2 / 5. Query-level examples:

- **Vector-only:** `newtonsoft-07-linq-property-case` (JObject comparison
  overload), `newtonsoft-08-iso-date-format` (ISO converter default format).
- **FTS-only:** `newtonsoft-04-settings-to-contract` (settings/apply flow),
  `antlr-07-multihop-error` (one recovery piece).
- **Neither route reached any gold anchor:** `newtonsoft-05-enum-as-string`,
  `antlr-01-adaptive-predict`, `antlr-03-invalid-state-number`,
  `antlr-06-interval-complement`.
- Hybrid at K=50 did not contain every vector K=50 hit; fusion can trade recall
  for top-rank precision.

These are **observed P0 candidates**, not an ablation of a vector collection.

## 7. Declaration-type attribution

Using the candidate entity type returned in the result list (a container/class
entity may contain several method anchors):

| Route | container | method | value | type-only container | type-only method | type-only value |
|---|---:|---:|---:|---:|---:|---:|
| FTS | 22 | 4 | 4 | 10 | 2 | 1 |
| Vector | 19 | 6 | 3 | 1 | 4 | 1 |
| Hybrid | 22 | 6 | 3 | 10 | 4 | 1 |

The extractor exposes `class/interface/enum/module`, `function`, and `value`;
properties and fields are both `value`, so a separate property count is not
available from the public metadata. “Container” dominance is partly because
class entities contain method bodies and can overlap several anchors.

Observed type-only examples:

- vector-only container: `newtonsoft-11` attribute anchor;
- vector-only method: `dapper-01`, `dapper-06`, `dapper-09`, `newtonsoft-07`;
- value-only: `antlr-04` default strategy field (all routes);
- FTS/hybrid container-only: many configuration/default anchors.

This does **not** establish what happens if container or value vectors are
removed: a method entity may still be returned in the refilled candidate list.
No ANN-equivalent P1/P2/P3 experiment was run.

## 8. Resource and cost baseline

| Item | Observed |
|---|---|
| Full index wall | **3,881.222 s (64m41.2s)** from manifest created/updated timestamps; job wall 64m41s |
| Index CPU | sampled ~390% (about 4 cores); exact CPU seconds were not captured after process exit |
| Index peak RSS | ~2.0 GiB observed during embedding; later ~0.58 GiB |
| Index disk | 60 MB (vector index 21.5 MB) |
| Model disk | 319 MB |
| Corpus disk | 4.6 MB |
| Query process peak RSS | 758–782 MB |
| Query wall, 3 runs | FTS 11.8–13.8s; vector 14.4–21.3s; hybrid 13.9–16.2s |
| Per-query latency | FTS p50 377ms/p95 496ms; vector 499/805ms; hybrid 431/573ms |
| No-op index | 0.07s, 63.6 MB peak RSS, 0 changed files |

Latency is single-host CPU and includes process/model startup per route process;
it is a baseline, not a production SLA. The vector p95 is the noisiest value.

Potion control (separate E3-01 index, model2vec 16M):
FTS R@10 0.466 / solved@10 0.222; vector 0.488 / 0.296; hybrid 0.488 / 0.296.
This is **not** a clean model-only comparison: the index collection names differ
(`fragments_bf8b` vs `fragments_c213`) and fragment/context layout is tied to the
embedding model config. FTS is therefore not model-independent across differently
built indexes.

## 9. E3 feasibility answers

1. **Where do FTS and vector complement?** FTS adds configuration-flow and
   multi-hop recovery anchors; vector adds LINQ/comparison and hard-coded
   date-format behavior. Hybrid is best at top-10 and solved@10; vector is best
   at K=50. The complement is real but small (8 unique anchors across routes at
   K=10, out of 33 anchors reached).
2. **Which declaration types provide unique relevant evidence?** Container and
   method candidates dominate; observed unique type-only anchors are mostly
   container/method, with one value-only anchor in the ANTLR default field and
   vector-only container/method examples. The extractor collapses property and
   field into `value`, limiting finer attribution.
3. **Risk of removing container/value vectors?** Container candidates are the
   only observed source for 10 FTS/hybrid anchors and 1 vector anchor; `value`
   is the only observed source for the ANTLR default field. Removing either
   without refill would lose these; with ANN refill the loss is unknown. The
   risk is highest for configuration/default and property/value queries.
4. **Are CPU/disk/query-cost savings enough to justify split collections?**
   No. The total index is 60 MB, the vector index 21.5 MB, full build is 65 min
   on CPU, and query p50 is ~0.43s. The observed unique benefit is too small and
   too confounded by container overlap to justify additional collection routing,
   migration, or ANN complexity.
5. **What can be concluded?** Hybrid is a better top-10 default for this dataset
   and model than either single route, but this is one C# corpus, one 300M model,
   line-overlap labels and 32 queries. It cannot establish a general vector
   policy, production latency, or safe removal of a declaration class.


## Appendix A — per-query run-1 evidence

Values are `recall@10 / solved@10`; run-1 only (three-run hit sets were identical).

| Query | Cat | FTS | Vector | Hybrid |
|---|---|---:|---:|---:|
| `dapper-01-query-buffered` | exact_symbol | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `dapper-02-column-name-map` | nl_implementation | 1.00 / 1 | 0.50 / 0 | 1.00 / 1 |
| `dapper-03-string-param-size` | hardcoded_value | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `dapper-04-bypass-plan-cache` | configuration | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `dapper-05-type-map-selection` | cross_file | 1.00 / 1 | 0.67 / 0 | 0.67 / 0 |
| `dapper-06-literal-substitution` | sql_database | 0.33 / 0 | 0.67 / 0 | 0.67 / 0 |
| `dapper-07-multimap-split-default` | hardcoded_value | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `dapper-08-async-cancellation` | data_flow | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `dapper-09-buffered-false` | docs_vs_impl | 0.50 / 0 | 0.50 / 0 | 0.50 / 0 |
| `newtonsoft-01-camel-case` | exact_symbol | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `newtonsoft-02-ignore-defaults` | nl_implementation | 0.50 / 0 | 0.50 / 0 | 0.50 / 0 |
| `newtonsoft-03-max-depth` | hardcoded_value | 0.50 / 0 | 0.50 / 0 | 0.50 / 0 |
| `newtonsoft-04-settings-to-contract` | configuration_flow | 0.25 / 0 | 0.00 / 0 | 0.00 / 0 |
| `newtonsoft-05-enum-as-string` | serialization | 0.00 / 0 | 0.00 / 0 | 0.00 / 0 |
| `newtonsoft-06-nan-infinity` | serialization | 0.50 / 0 | 0.50 / 0 | 0.50 / 0 |
| `newtonsoft-07-linq-property-case` | linq | 0.00 / 0 | 1.00 / 1 | 1.00 / 1 |
| `newtonsoft-08-iso-date-format` | hardcoded_value | 0.00 / 0 | 1.00 / 1 | 1.00 / 1 |
| `newtonsoft-09-preserve-references` | serialization | 0.33 / 0 | 0.33 / 0 | 0.33 / 0 |
| `antlr-01-adaptive-predict` | exact_symbol | 0.00 / 0 | 0.00 / 0 | 0.00 / 0 |
| `antlr-02-bail-on-error` | nl_implementation | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `antlr-03-invalid-state-number` | hardcoded_value | 0.00 / 0 | 0.00 / 0 | 0.00 / 0 |
| `antlr-04-parser-recovery` | cross_file | 0.25 / 0 | 0.25 / 0 | 0.25 / 0 |
| `antlr-05-atn-deserialize` | serialization | 1.00 / 1 | 1.00 / 1 | 1.00 / 1 |
| `antlr-06-interval-complement` | data_structure | 0.00 / 0 | 0.00 / 0 | 0.00 / 0 |
| `antlr-07-multihop-error` | multi_hop | 0.33 / 0 | 0.00 / 0 | 0.33 / 0 |
| `dapper-11-parameter-flow` | multi_hop | 0.33 / 0 | 0.33 / 0 | 0.33 / 0 |
| `newtonsoft-11-property-name-flow` | multi_hop | 1.00 / 1 | 0.75 / 0 | 1.00 / 1 |

## 10. Threats to validity

- Small, C#-only corpus; 27 answerable queries; many categories have n=1–5.
- Line-overlap labels are strict; adjacent correct code can be scored as a miss.
- Container entities can overlap multiple anchors; type counts are candidate
  types, not independent vector rows.
- One real model; potion control has a different index/fragment layout.
- CPU-only, single host, one process per route; no GPU/ANN concurrency study.
- No-answer trap-FP is zero, but `any_result` is one: the engine still returns
  code for absence questions. That is not correctness.
- The second independent annotation pass was not completed; the source-first
  review and v2 anchor corrections are recorded, but residual annotation
  judgment remains.
- Exact vector cardinality and per-type vector counts are not exposed; cost
  estimates are upper-bound/observational, not a split-collection simulation.

## 11. E3-02B recommendation

**NO-GO for a broad E3-02B split-collection experiment.** The evidence supports
hybrid as the P0 default but not a claim that removing container/value vectors
saves enough to justify P1–P3 complexity. If the owner wants to revisit, the
next task should be bounded instrumentation, not a policy experiment:

1. expose vector cardinality and per-declaration-type vector counts;
2. run same-index post-hoc candidate filtering only as a diagnostic, explicitly
   not ANN-equivalent;
3. add held-out queries for configuration/default/multi-hop cases;
4. only then preregister a bounded split test with a concrete CPU/disk/query
   saving hypothesis and a no-regression gate.

No P1–P3 collection split, ranking change, schema change or release was made.
