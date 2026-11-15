# E3-02B-R0 — bounded vector-policy opportunity

**Status:** DONE — all three screened policies **NO_GO**; no `SCREENING_GO`
**Branch:** `work/e3-02b-r0-vector-policy-20261114`
**Base:** E6 `work/e6-benchmark-integrity-20261114` @ `9c1306d`
**Main:** `work/zg-recovered` @ `6d5b523` (untouched)
**Receipts:** `/home/masis/.cache/zvec-grep/r0-20261114/receipts/`
**Explicitly not authorized/not done:** production vector policy, schema,
storage, ranking or model-default change; rebuilt-ANN experiment; E3-02B broad
split-collection reopening.

## 1. Preflight and integrity

| Item | State |
|---|---|
| Git base | E6 `9c1306d`; experiment worktree `/home/masis/src/zvec-grep-e6-r0` |
| Main | `work/zg-recovered` @ `6d5b523`, clean |
| Dataset | frozen `e3-01-v2`, SHA-256 `a2d2210e759f948ddd8dfe3387069fb219f84a307546cb0f905502439e229650` |
| Gold | `validate.py` over the frozen corpus: **OK** — checksums, anchors, groups, absence checks, manifest |
| Corrected retrieval | E6 Gemma arm only; every raw run has `measurement_contract.status = PASS`, matching dataset/model identity, 32/32 results, 50 items/query |
| Structural scan | separate Model2Vec Potion index of the same 392-file corpus: 10,905 fragments / 8,182 entities, 1,024-token layout |
| Model/index identity | structural scan manifest `local/potion-code-16m-v2`, dimension 256, maxInputTokens 1,024; query evidence uses E6 `local/embeddinggemma-300m`, dimension 768, 9,223 fragments |
| Disk | 56 GB free at start; external target, index, models and venv under `/home/masis/.cache/zvec-grep/r0-20261114` |

The Potion index is a **structural scan**, not a quality arm. Its fragment
boundaries differ from Gemma's 2,048-token layout; entity/type metadata is
extractor-derived and independent of the embedding model. All policy
percentages below use the Potion structural denominators; Gemma-layout
estimates are marked **DERIVED**.

## 2. Corrected evidence attribution

E6 corrected Gemma at K=10: **7 FTS-only / 12 vector-only / 16 both / 19
neither** anchors. All 54 anchors are required by `solved_when`; 27 are
primary and 27 supporting. Hybrid misses at least one anchor in 15/27 answerable
queries.

| Anchor route set | Function | Value | Class | Enum | Primary | Supporting |
|---|---:|---:|---:|---:|---:|---:|
| FTS-only (7) | 5 | 2 | 0 | 0 | 3 | 4 |
| Vector-only (12) | 8 | 4 | 0 | 0 | 10 | 2 |
| Both (16) | 7 | 5 | 2 | 2 | 9 | 7 |
| Neither (19) | 16 | 3 | 0 | 0 | 5 | 14 |

Key attribution observations:

- **Function** fragments carry 36/54 anchors; **value** fragments 14/54;
  class/enum only 4/54. The retrieval unit for a class-level question is often
  a method or field fragment inside the class, not the container chunk.
- Vector-only anchors are disproportionately primary (10/12) and include four
  multi-hop anchors; FTS-only anchors are mostly supporting (4/7).
- Hybrid does not recover all anchors: 15 queries miss at least one, including
  `antlr-04-parser-recovery` (3), `antlr-07-multihop-error` (2) and
  `dapper-11-parameter-flow` (2).
- The full per-anchor matrix is
  `receipts/attribution-anchors.csv` and the JSON in
  `receipts/r0-analysis.json` (`attribution.per_query`); it records query,
  corpus, category, group, role, required flag, structural type/scope, source
  range, route rank/score and top-10 membership.

## 3. Structural cardinality

Potion structural scan of the frozen corpus (1,024-token layout):

| Type | Entities | Fragments | Fragment chars | Share of chars |
|---|---:|---:|---:|---:|
| class | 474 | 2,652 | 5,219,589 | 54.3% |
| function | 4,093 | 4,584 | 3,384,909 | 35.2% |
| value | 2,388 | 2,392 | 454,882 | 4.7% |
| unknown | 740 | 750 | 373,964 | 3.9% |
| interface | 42 | 80 | 111,781 | 1.2% |
| enum | 51 | 53 | 35,546 | 0.4% |
| module | 394 | 394 | 27,382 | 0.3% |
| **total** | **8,182** | **10,905** | **9,608,053** | **100%** |

Additional facts:

- **515 entities** have more than one fragment; class containers average 5.6
  fragments/entity. Class is 5.8% of entities but 24.3% of fragments and 54.3%
  of embedding characters.
- Normalized-text duplicates: **797 duplicate occurrences** (10,108 unique
  texts; largest duplicate group 71). Duplicates are mostly module/unknown/
  value fragments, not class containers.
- Gemma's 2,048-token layout has **9,223 fragments**, mean 942.95 chars
  (E6/E4), i.e. about 8.70M fragment chars and **27.0 MiB of FP32 vectors**
  (9,223 × 768 × 4 bytes, DERIVED; the index is FP16 per storage code).
- The official index is FP16 (`embedding` field `VectorFp16`), so the on-disk
  vector payload is roughly half the FP32 figure; the 63.2 MB E4 index disk
  includes FTS, scalar, HNSW and metadata. Do not use 27 MiB as disk usage.

## 4. Cost attribution

Observed, serial E4 Gemma measurement (the E6 builds ran three-way concurrent
and are not resource-comparable):

| Component | Observation | Interpretation |
|---|---|---|
| Full index | 1:10:36 wall, 16,619 s user CPU, 2.99 GiB peak RSS, 63.2 MB disk, 9,223 fragments | total index, not phase-isolated |
| Embedding inference | 484 doc tok/s batch 1; 524 doc tok/s batch 16; query 498 tok/s | E4 fixed workload |
| Fragment input | 8.70M chars / 942.95 mean; ~2.01M Gemma tokens at 4.33 chars/token | **DERIVED** from E4 token workload |
| Vector storage | 9,223 × 768 FP16 ≈ 13.5 MiB (FP32 27.0 MiB) | **DERIVED**; actual disk includes HNSW/FTS/scalar |
| Query ANN | 488 ms p50 vector route (E4) | wall latency, not a linear function of vector count |
| Native commit/optimize | not isolated | E6 parallel builds are unusable for phase attribution |

Policy savings at the structural-scan level:

| Policy | Fragments excluded | % fragments | Chars excluded | % chars | Vector-count reduction | Inference estimate |
|---|---:|---:|---:|---:|---:|---:|
| P1 keep containers only | 7,370 | 67.6% | 3.87M | 40.3% | 67.6% | ~40% fewer tokens |
| P2 skip value+module | 2,786 | 25.5% | 0.48M | 5.0% | 25.5% | ~5% fewer tokens |
| P3 keep first container fragment | 2,218 | 20.3% | 4.64M | 48.2% | 20.3% | ~48% fewer tokens |

These are **not wall-time savings**. P3 removes long overlapping class chunks;
P2 removes many short value/module fragments; P1 removes all function/value
work. Native commit/optimize and ANN latency were not measured under these
policies.

## 5. Counterfactual screening

The three hypotheses were chosen because the attribution shows that (a) all
evidence is function/value/class, and (b) class containers are long and heavily
overlapped while function/value fragments are numerous.

| Policy | Inclusion rule | Excluded entities/fragments | Top-10 items removed | Known anchor loss (vector / hybrid) |
|---|---|---:|---:|---|
| **P1** `keep_container_types` | embed class/interface/enum/unknown; skip function/value/module | 6,875 / 7,370 | 197 / 161 | **8 / 5** |
| **P2** `skip_value_module` | embed class/interface/enum/function/unknown; skip value/module | 2,782 / 2,786 | 83 / 62 | **3 / 1** |
| **P3** `keep_first_container_fragment` | keep first class/interface/enum fragment; skip later chunks | 263 affected entities / 2,218 | 49 / 71 | **3 / 7** |

All lost anchors are **required** by `solved_when`, not optional supporting
context. Representative losses:

- **P1**: `dapper-01-query-buffered-e1` (function, primary),
  `dapper-02-column-name-map-e1` (function, primary),
  `antlr-04-parser-recovery-e1` (value, primary), plus
  `newtonsoft-07-linq-property-case-e1` (function, primary) in hybrid.
- **P2**: `antlr-04-parser-recovery-e1` (value, primary) and both
  `newtonsoft-03-max-depth` value anchors; this is a direct loss of
  hardcoded-value evidence.
- **P3**: `newtonsoft-11-property-name-flow` value + two function anchors and
  `antlr-07-multihop-error-e1` (function, primary) in hybrid. Later container
  chunks are not redundant with the first chunk.

Per-query removed-item counts, before/after known anchor counts and lost
anchor IDs are recorded under
`r0-analysis.json → screening.<policy>.routes.<route>.per_query`.

**Offline limitation:** the raw runs are top-50. If a removed item was in
top-10, the replacement after filtering is unknown; the screening reports
**known top-10 loss**, not a rebuilt-ANN recall. Every raw run does have the
required 50-item depth, so the known loss is not a truncation artifact. The
losses above are sufficient to reject the policies; no filtered top-10 result
is treated as a production-quality estimate.

## 6. Decision gate

No policy satisfies `SCREENING_GO`.

| Policy | Verdict | Reason |
|---|---|---|
| P1 keep containers only | **NO_GO** | 67.6% fewer vectors and 40.3% fewer input chars, but 8 vector and 5 hybrid required anchors lost, including primary function/value evidence |
| P2 skip value+module | **NO_GO** | 25.5% fewer vectors but only 5.0% fewer chars; loses 3 vector and 1 hybrid required value anchors |
| P3 keep first container fragment | **NO_GO** | 20.3% fewer vectors and 48.2% fewer chars, but 3 vector and 7 hybrid required anchors lost, including multi-hop and property-flow evidence |

No percentage threshold was invented after seeing results. The rejection is
based on known critical evidence loss, or negligible resource opportunity, not
on a post-hoc metric gate. There is **no `SCREENING_GO` and no rebuilt-ANN
experiment** in R0.

The bounded vector-policy direction is closed at this evidence level. The next
useful investigation is **indexing scalability** (chunk overlap, segment/
compaction cost, ANN maintenance), not another selective-vector policy derived
from this gold set.

## 7. Architectural implications

The current layout is coupled:

```
FTS(text) + scalar metadata + non-nullable vector("embedding") + HNSW
```

Source: `fragments_schema` creates `FieldSchema::new("embedding",
DataType::VectorFp16, false, dimension)` and attaches HNSW. `false` means the
vector is **not nullable**. Every fragment document therefore needs a vector
and is included in the vector index.

Consequences:

1. **Inference-only reduction is possible without schema change** if the
   pipeline can skip model calls and write a placeholder/zero vector. That
   saves encoder work but still stores and HNSW-indexes a vector; it does
   **not** reduce vector cardinality, disk or ANN graph. A zero vector can also
   pollute cosine ranking.
2. **Vector-cardinality/storage reduction requires an architectural change**:
   nullable vector plus query-side filtering, or a separate vector collection
   keyed by a policy, plus index-version/migration/rollback design. That is
   outside R0 and outside the current production authorization.
3. **FTS and exact-symbol retrieval are independent** and can remain intact;
   structural documents must not be deleted to save vectors.
4. **Chunk overlap is the dominant cost/evidence tension**, not merely
   container role: class chunks overlap heavily (first/last windows share
   members), and later chunks carry required evidence. Reducing overlap or
   making chunking content-addressed is an indexing-scalability question.
5. **Generalist-first:** this is 27 C# queries from three libraries. Rust,
   SQL, documentation and log workloads may have different evidence units;
   do not promote a C#-specific structural split to a general policy.

## 8. Independent verification

- `tools/r0_verify.py` re-reads the scalar index, dataset, gold hashes and E6
  raw results without importing `r0_analyze.py`. It verifies:
  fragment/entity/type/char counts, all 54 anchor identities and source
  hashes, per-route ranks and 7/12/16 contribution, policy exclusion counts and
  fragment IDs, excluded chars, and independently recomputed policy loss sets.
  Result: **PASS** (`receipts/verify-r0.json`).
- `tools/r0_negative.py`: **7/7 rejected** — missing anchor, wrong fragment
  count, wrong dataset hash, wrong contribution, top-50 depth loss, wrong model
  identity, fabricated screening loss.
- `tools/validate.py` over the frozen corpus/dataset: **OK**.
- Raw E6 files retain contract PASS, matching dataset hash, model identity and
  50 items/query; no uncontracted receipt was used for a decision.

## 9. Limitations

- C# only; 27 answerable queries / 54 anchors; not a production quality
  estimate.
- Structural counts use the 1,024-token Potion scan; Gemma 2,048 layout counts
  are derived, not a second full Gemma index.
- Offline filtering of top-50 cannot reveal replacements or rebuilt-ANN
  recall; reported losses are known top-10 losses. This is why no policy is
  called production-qualified.
- Public metadata distinguishes module/class/function/value/interface/enum,
  not property vs field or all container/member roles; `unknown` remains
  `UNKNOWN`.
- Cost reduction is derived from chars/tokens and E4 throughput, not a
  policy-built index; native commit/optimize and query ANN were not separately
  measured under a selective policy.
- Single host, CPU, one model for quality (Gemma) and one structural scan
  model (Potion).

## 10. Git, receipts, cleanup

- Experiment branch: `work/e3-02b-r0-vector-policy-20261114`, based on E6
  `9c1306d`.
- Receipts: `r0-analysis.json`, `r0-analysis.stdout`,
  `attribution-anchors.csv`, `verify-r0.json`, `negative-r0.json`,
  `gold-validation.txt`, `index-potion.log`, per-policy manifests
  `P1_*.json`/`P2_*.json`/`P3_*.json`, `SHA256SUMS` and `manifest.json`.
- No production code, schema, ranking, model default, frozen gold or historical
  receipt was changed.
- External target/work/models/venv are removed after hashing; the R0 worktree
  is removed after commit/push; main remains clean.
