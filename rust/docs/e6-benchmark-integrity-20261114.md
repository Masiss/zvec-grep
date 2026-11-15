# E6 — benchmark integrity and requalification

**Status:** DONE
**Branch:** `work/e6-benchmark-integrity-20261114`
**Base:** `work/e5-fts-reproducibility-20261114` @ `7013a47`
**Main:** `work/zg-recovered` @ `6d5b523` (untouched)
**Receipts:** `/home/masis/.cache/zvec-grep/e6-20261114/receipts/`

## 1. Why this audit exists

E5 found that the E4 "FTS" result was actually FTS+Vector+FTS because the
harness set both `ContextOptions.query` and an explicit route. E6 audits every
consumer that inherited that harness, proves which metrics are invalid, adds a
fail-closed measurement contract, and requalifies the decision-critical routes.

## 2. Contamination audit

All three retrieval reports inherited the same harness request:

```rust
ContextOptions {
    query: Some(text),   // primary group: FTS + Vector
    routes,              // supplemental group: declared route
    ..
}
```

Source proof: `benchmarks/retrieval-evidence/harness/src/main.rs` at E3-01,
E3-02A, and E4 all set `query: Some(text)` before `engine.context(...)`.
`pipelines/indexed_search/context.rs` expands each primary query into
`[Fts, Vector]`. A route-only run therefore actually contains 3 routes:

```
legacy FTS request: FTS(primary) + Vector(primary) + FTS(supplemental)
actual diagnostics: ["fts", "fts", "vector"], primary_groups=1, duplicate=1
```

This was reproduced directly with the hardened harness on the preserved
E3-01 Potion index (`--legacy-primary=true`):

```
contract status: FAIL
actual.route_modes: ["fts", "fts", "vector"]
actual.primary_groups: 1
actual.duplicate_routes: 1
```

### Validity matrix

| Artifact / claim | Declared | Effective | Validity | Dependency |
|---|---|---|---|---|
| E3-01 §4 smoke FTS/Vector/Hybrid table | FTS / Vector / Hybrid | primary FTS+Vector + supplemental route | **INVALID** | none retained |
| E3-01 dataset, hashes, evaluator, selftest | n/a | n/a | **VALID** | E3-02A gold |
| E3-02A v2 gold review | n/a | n/a | **VALID** | E6 requalification |
| E3-02A baseline table | FTS / Vector / Hybrid | primary FTS+Vector + supplemental route | **FTS/vector INVALID; hybrid contract-invalid but numerically reproduced** | E3-02B rationale |
| E3-02A unique-anchor attribution (5/3/25) | FTS/Vector | contaminated | **INVALID** | E3-02B rationale |
| E3-02A index wall/RSS/fragments | n/a | n/a | **VALID** | resource claims |
| E4 retrieval table (all models/routes) | FTS / Vector / Hybrid | primary FTS+Vector + supplemental route | **FTS/vector INVALID; hybrid contract-invalid, numerically reproduced** | E4 model verdicts |
| E4 `FTS is not model-independent` | FTS | contaminated | **RETRACTED** (E5) | none |
| E4 inference/throughput/RAM table | n/a | n/a | **VALID** | model resource verdicts |
| E4 QAT Q4_0 `BLOCKED` | artifact inspection | n/a | **VALID** | none |
| E5 pure-FTS result on matched layout | explicit FTS | FTS | **VALID** | E6 cross-model check |
| E3-02B `NO-GO` rationale | derived from E3-02A attribution | invalid dependency | **UNKNOWN — owner review** | do not reopen automatically |

### Dependency map

```
E3-02B NO-GO rationale
  <- E3-02A unique-anchor attribution (5/3/25)   INVALID
  <- E3-02A baseline route table                 INVALID
  <- E3-02A v2 gold review                       VALID

E4 model verdicts
  <- E4 retrieval table (FTS/vector/hybrid)      INVALID
  <- E4 inference/throughput/RAM table           VALID
  <- E4 QAT artifact blocker                     VALID

E3-01 smoke table                                INVALID
  <- E3-01 dataset/evaluator/validator           VALID

E5 pure-FTS finding                              VALID
```

E3-02A reported FTS R@10 0.5494 and E4's Gemma arm reproduced the same
0.5494 — not because two independent measurements agreed, but because both
used the same contaminated request shape. The identical number is evidence of
the shared defect, not of validity.

## 3. Measurement contract

The harness now emits `run.measurement_contract` and refuses to write a result
unless all checks pass:

- declared route = normalized route = observed execution;
- expected/actual route modes and group count;
- zero implicit primary groups for route-only requests;
- zero duplicate `(mode, query)` contributions;
- route queries and filters match the request;
- `items <= limit`; every item stays inside the per-query corpus glob;
- pure FTS has no vector-matched item, pure vector has no FTS-matched item;
- dataset SHA-256 matches the frozen manifest;
- index manifest model equals the requested model;
- every query produced a result.

`evaluate.py`, `aggregate_matrix.py`, `attribute.py` and `verify_e6.py` reject a
run without `status: "PASS"`. `--allow-uncontracted` is audit-only.

Negative tests:

- legacy primary request: `["fts","fts","vector"]` → harness contract FAIL;
  evaluator exit 2 with no report; verifier exit 2;
- missing contract → evaluator/verifier exit 2;
- PASS contract plus a hidden `matched_by=vector` item → verifier exit 2;
- duplicate routes, primary group, multiple groups, filter escape, limit
  overflow → Rust contract unit tests fail closed.

## 4. Corrected E3-01 Potion control

The preserved E3-01 v1 index was requalified with the corrected harness and the
v1 frozen dataset, so the only change is route execution.

| Route | Reported R@10 | Corrected R@10 | Reported Solved@10 | Corrected Solved@10 | Reported MRR | Corrected MRR |
|---|---:|---:|---:|---:|---:|---:|
| FTS | 0.4630 | **0.4136** | 0.2222 | **0.1852** | 0.5566 | **0.4889** |
| Vector | 0.4815 | **0.4136** | 0.2963 | **0.2593** | 0.5449 | **0.4544** |
| Hybrid | 0.4815 | 0.4815 | 0.2963 | 0.2963 | 0.5407 | 0.5407 |

Three corrected repeats on v2 (`potion3`) were bitwise stable (32/32 top-10
query sets) with contract PASS. Hybrid happened to be closest to intended;
FTS and vector were materially inflated by the implicit vector/FTS routes.

## 5. Corrected E3-02A / E4 matrix

All arms below used the same frozen `e3-01-v2` corpus copy, `*.cs` scan rule,
`limit=50`, `auto_update=false`, CPU, three repeats, route-only requests
(`query=None` unless declared), and a PASS measurement contract. Top-10 hit
sets were identical in 32/32 queries for every route and arm.

| Arm | Route | R@5 | R@10 | R@50 | Solved@10 | Solved@50 | MRR@10 | nDCG@10 | Trap-FP@10 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemma Q8 | FTS | 0.3179 | 0.4444 | 0.6049 | 0.2963 | 0.4444 | 0.4312 | 0.3552 | 0.00 |
| Gemma Q8 | Vector | 0.5123 | 0.5679 | 0.8673 | 0.3704 | 0.7407 | 0.5932 | 0.5244 | 0.20 |
| Gemma Q8 | Hybrid | **0.5463** | **0.6142** | 0.8025 | **0.4444** | 0.6667 | **0.5963** | **0.5387** | 0.00 |
| CodeRank Q8 | FTS | 0.3179 | 0.4444 | 0.6049 | 0.2963 | 0.4444 | 0.4312 | 0.3552 | 0.00 |
| CodeRank Q8 | Vector | 0.4691 | 0.5432 | 0.7685 | 0.3704 | 0.5926 | 0.5707 | 0.4737 | 0.00 |
| CodeRank Q8 | Hybrid | 0.4167 | 0.5463 | 0.7407 | 0.3333 | 0.4815 | 0.5372 | 0.4581 | 0.00 |
| Jina Q5_K_M @2048 | FTS | 0.3179 | 0.4444 | 0.6049 | 0.2963 | 0.4444 | 0.4312 | 0.3552 | 0.00 |
| Jina Q5_K_M @2048 | Vector | 0.4259 | 0.4722 | 0.7932 | 0.2963 | 0.5926 | 0.4735 | 0.3912 | 0.20 |
| Jina Q5_K_M @2048 | Hybrid | 0.4259 | 0.5864 | 0.7685 | 0.3704 | 0.5185 | 0.5437 | 0.4747 | 0.00 |
| BGE Small | FTS | 0.2932 | 0.3796 | 0.5185 | 0.1852 | 0.2963 | 0.4384 | 0.3373 | 0.00 |
| BGE Small | Vector | 0.2623 | 0.3333 | 0.6389 | 0.1481 | 0.4444 | 0.3386 | 0.2669 | 0.20 |
| BGE Small | Hybrid | 0.2685 | 0.4043 | 0.5185 | 0.2222 | 0.2963 | 0.3710 | 0.3187 | 0.20 |
| Potion 16M | FTS | 0.3395 | 0.4167 | 0.5463 | 0.1852 | 0.3333 | 0.4889 | 0.3775 | 0.00 |
| Potion 16M | Vector | 0.3210 | 0.4074 | 0.6327 | 0.2593 | 0.4444 | 0.4544 | 0.3547 | 0.00 |
| Potion 16M | Hybrid | 0.3519 | 0.4877 | 0.6358 | 0.2963 | 0.4815 | 0.5407 | 0.4184 | 0.00 |

### Legacy versus corrected

| Arm | Route | Legacy R@10 | Corrected R@10 | Legacy Solved@10 | Corrected Solved@10 |
|---|---|---|---:|---:|---:|---:|
| Gemma Q8 | FTS | 0.5494 | **0.4444** | 0.4074 | **0.2963** |
| Gemma Q8 | Vector | 0.5741 | **0.5679** | 0.3704 | 0.3704 |
| Gemma Q8 | Hybrid | 0.6142 | 0.6142 | 0.4444 | 0.4444 |
| CodeRank Q8 | FTS | 0.5062 | **0.4444** | 0.2963 | 0.2963 |
| CodeRank Q8 | Vector | 0.4969 | **0.5432** | 0.2963 | **0.3704** |
| CodeRank Q8 | Hybrid | 0.5463 | 0.5463 | 0.3333 | 0.3333 |
| Jina @2048 | FTS | 0.4969 | **0.4444** | 0.2963 | 0.2963 |
| Jina @2048 | Vector | 0.5957 | **0.4722** | 0.3704 | **0.2963** |
| Jina @2048 | Hybrid | 0.5864 | 0.5864 | 0.3704 | 0.3704 |
| BGE Small | FTS | 0.3735 | 0.3796 | 0.1852 | 0.1852 |
| BGE Small | Vector | 0.3889 | **0.3333** | 0.1852 | **0.1481** |
| BGE Small | Hybrid | 0.4043 | 0.4043 | 0.2222 | 0.2222 |
| Potion 16M | FTS | 0.4660 | **0.4167** | 0.2222 | **0.1852** |
| Potion 16M | Vector | 0.4877 | **0.4074** | 0.2963 | **0.2593** |
| Potion 16M | Hybrid | 0.4877 | 0.4877 | 0.2963 | 0.2963 |

The **hybrid rows reproduced the legacy numbers exactly** for all five arms.
That is an observation, not a validity claim: the legacy request still declared
one route and executed duplicate hybrid groups. The corrected rows are the
authoritative measurements. The **FTS and vector rows changed materially**, in
different directions: CodeRank vector improved, Jina vector collapsed, and
every 2048-layout FTS row converged to the same value.

### Pure FTS is model-independent at matched layout

Gemma, CodeRank and Jina @2048 each built exactly **9,223 fragments** with
identical fragment text (SHA-256 prefix `449cb6308908`). Their corrected pure
FTS hit sets are byte-identical across all 32 queries (canonical hash
`d77e3a009364…`), and all three report R@10 0.4444, solved@10 0.2963,
MRR@10 0.4312, nDCG@10 0.3552. BGE/Potion differ because their context sizes
produce different fragment layouts. This is the opposite of the E4
"FTS is not model-independent" claim, which E5 retracted.

### E3-02A corrected baseline and attribution

E3-02A used the Gemma arm, so the corrected baseline is the Gemma row above:

| Route | Legacy R@10 | Corrected R@10 | Legacy Solved@10 | Corrected Solved@10 | Legacy MRR | Corrected MRR |
|---|---:|---:|---:|---:|---:|---:|
| FTS | 0.5494 | **0.4444** | 0.4074 | **0.2963** | 0.5660 | **0.4312** |
| Vector | 0.5741 | **0.5679** | 0.3704 | 0.3704 | 0.5733 | **0.5932** |
| Hybrid | 0.6142 | 0.6142 | 0.4444 | 0.4444 | 0.5963 | 0.5963 |

Unique-anchor attribution changes from **5 FTS / 3 vector / 25 both** to
**7 FTS / 12 vector / 16 both**. At query level it changes from
2 FTS-only / 2 vector-only / 19 both / 4 neither to
4 FTS-only / 7 vector-only / 14 both / 2 neither. The old claim that the
complement was only 8 anchors is not supported; the corrected complement is
19 anchors. Hybrid remains the strongest route.

## 6. Independent verification

- `verify_e6.py` re-derives dataset, contract, provenance, per-query metrics,
  aggregate metrics and top-K stability without importing `evaluate.py`. On the
  Potion corrected FTS run it matched the evaluator with zero metric delta.
- Cross-model FTS verification groups only by index layout and compares
  canonical hit sets. Gemma/CodeRank/Jina @2048: **PASS**, three identical
  hashes (`receipts/verify-cross-fts-2048.json`).
- The verifier rejects a clean result if it carries a hidden
  `matched_by=vector` item, a failing/missing contract, or wrong dataset/model
  provenance (`tools/test_e6_negative.py`).
- Harness contract unit tests cover duplicate routes, hidden primary group,
  multiple groups, filter escape, hidden route evidence and limit overflow:
  **4 passed**.
- Three repeats per route produced 32/32 identical top-10 sets for every arm.
  Per-query and paired comparison artifacts are under `receipts/requal/*/`.
- The corrected index manifests bind each collection to the requested model;
  all result files carry the matching dataset hash and contract PASS.

## 7. Impact and verdicts

### Contract and audit

- **Measurement contract: PASS.** Route-only requests are now fail-closed;
  legacy contaminated receipts cannot be scored, aggregated or attributed
  without the explicit audit flag.
- **E3-01:** smoke route table **INVALID**; dataset/evaluator/validator remain
  **VALID**. Corrected Potion control: FTS 0.4136, vector 0.4136, hybrid
  0.4815 R@10.
- **E3-02A:** baseline FTS/vector rows and the 5/3/25 attribution **INVALID**;
  v2 gold review, hashes, index/resource measurements remain **VALID**.
  Corrected hybrid baseline is unchanged at 0.6142/solved 0.4444; corrected
  attribution is 7/12/16.
- **E3-02B:** **UNKNOWN — owner review required.** Its NO-GO rationale was
  explicitly derived from the now-invalid 5/3/25 complement. The corrected
  complement is larger (19 anchors, 7 vector-only queries), so the old
  rationale cannot be reused unchanged. This audit does **not** reopen E3-02B
  or authorize a split-collection experiment.
- **E4:** FTS/vector rows **INVALID**; hybrid rows reproduced the legacy
  numbers but remain contract-invalid; inference/resource table and QAT
  conversion blocker **VALID**.

### E4 model verdicts after requalification

| Candidate | Verdict | Corrected evidence |
|---|---|---|
| EmbeddingGemma 300M Q8 | **Provisional reference** | Best corrected hybrid: R@10 0.6142, solved@10 0.4444, nDCG 0.5387 |
| CodeRankEmbed Q8 | **NO_GO** | Hybrid R@10 −6.8 pp, solved@10 −11.1 pp, nDCG −8.1 pp vs Gemma; vector R@10 −2.5 pp (legacy −7.7 pp was wrong) |
| Jina v2 Code Q5_K_M @2048 | **NO_GO** | Vector R@10 −9.6 pp, solved@10 −7.4 pp, MRR −12.0 pp, nDCG −13.3 pp, trap-FP 0.2; hybrid solved@10 −7.4 pp |
| BGE Small EN v1.5 | **NO_GO** | Hybrid R@10 −21.0 pp, solved@10 −22.2 pp; vector R@10 −23.5 pp |
| Potion Code 16M v2 | **NO_GO on quality** | Hybrid R@10 −12.7 pp, solved@10 −14.8 pp; vector R@10 −16.1 pp. Resource champion: 227× faster index, 116 MB PSS |
| EmbeddingGemma QAT Q4_0 | **BLOCKED** | Independent artifact defect: published QAT GGUFs omit `dense_2`/`dense_3`; vocab stays Q8, only 16.7% smaller |

No candidate is promoted. EmbeddingGemma remains the provisional default
reference only; this is not production qualification.

### What the corrected matrix changes

- The E4 *hybrid* decision inputs are numerically stable for this corpus, so
  the candidate NO_GO outcomes survive — but their provenance must cite the
  corrected runs, not the contaminated receipts.
- The E4 *vector* narrative changes materially: Jina's matched-layout R@10
  advantage becomes a 9.6 pp loss, while CodeRank's vector gap shrinks to
  2.5 pp. Any future vector-only decision must use the corrected table.
- The E4 *FTS* claim is retracted; pure FTS is identical across matched
  layouts, so it cannot rank embedding models.
- E3-02A's hybrid baseline is intact; its FTS/vector baseline and complement
  attribution are not.

## 8. Unresolved uncertainty

- **C# corpus only.** The corrected matrix covers 27 answerable C# queries and
  5 no-answer traps; it does not establish non-C# or production generalization.
- **Chunking confound for Potion/BGE.** Their context sizes produce 10,905 and
  15,531 fragments, so their FTS/vector gaps mix model quality with chunking.
  Only Gemma/CodeRank/Jina @2048 share the 9,223-fragment layout.
- **Community GGUF fidelity.** CodeRank and Jina use community conversions;
  the corrected comparison is valid for those artifacts, not for upstream
  model parity.
- **Jina native 8192 not requalified.** E6 uses the matched-layout `@2048`
  alias; the native 8192 arm remains an E4 result with a chunking confound.
- **E3-02B owner decision.** The corrected complement is larger than the
  rationale assumed; reopening or revising E3-02B is an owner-level choice.
- **E3-02A annotation depth.** v2 gold received source-first review and two
  structure passes, but not a second fully independent adjudication.
- **Contract scope.** The contract guards this harness and its consumers; the
  engine API still permits `query` + `routes` for backward compatibility, so
  other callers can construct the same ambiguity unless they adopt the
  diagnostics check.
- **Index wall/RSS in E6 are not comparable.** The three 2048 builds ran
  concurrently under memory pressure/swap; use the E4 serial index table for
  resource claims. E6 query latency was also measured during indexing.
- **Single-host CPU.** No GPU, ANN index variant or distributed execution was
  evaluated.

## 9. Git, receipts, cleanup

- Branch: `work/e6-benchmark-integrity-20261114`, based on E5 `7013a47`.
- Main worktree `work/zg-recovered` remains untouched.
- Receipts: `/home/masis/.cache/zvec-grep/e6-20261114/receipts/` with
  per-arm `requal/`, contract tests, cross-model verification, attribution,
  compare/bootstrap output and `SHA256SUMS` + `manifest.json`.
- Models/indexes/target/venv are external task artifacts under the same cache
  directory and are removed after the receipt hashes are recorded; historical
  E3/E4/E5 receipts are not modified.
