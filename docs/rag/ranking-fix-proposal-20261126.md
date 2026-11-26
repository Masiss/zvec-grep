# Ranking fix proposal — multi-route entity slate with budget-aware scheduling

## Recommendation

Replace the current single fragment-local score with an **entity-level,
multi-route ranker** and a **reserved entity slate**, then keep the proven soft
entity-byte saturation from hybrid-ranking-v1.

Do **not** return to entity-centric-only ranking, do **not** sum every fragment
in an entity, and do **not** reindex yet. The first fix should use only data
already loaded by the current pipeline: fragment scores, entity content,
`CodeMetadata`, source ranges and the existing arm A resolver.

## Why the current ranker and hybrid v1 are insufficient

Current G1K4 ranking is:

\[
normalized\_RRF + 0.02 \times term\_coverage
\]

It ranks fragments independently and ignores entity-level evidence density,
stored symbol/signature metadata and budget occupancy. Hybrid v1 added metadata
and soft saturation, but only rescued D04; Rust added no all-gold case and R11
remained unresolved.

A read-only feature study on the same 24 frozen cases measured gold-bearing
entity recall at different top-k cutoffs. This study used gold only for scoring,
never as a ranking feature.

| Route | Rust recall@8 | Rust recall@16 | C# recall@8 | C# recall@16 |
|---|---:|---:|---:|---:|
| Base fragment anchor | 0.273 | 0.424 | 0.261 | 0.391 |
| Hybrid-v1 static score | 0.333 | 0.515 | 0.348 | 0.435 |
| Metadata/declaration match | 0.182 | 0.242 | 0.130 | 0.130 |
| Entity-content TF-IDF | 0.394 | 0.515 | 0.565 | 0.565 |
| Three-route RRF | 0.364 | 0.455 | 0.391 | 0.478 |
| Reserved multi-route slate | **0.424** | **0.606** | **0.522** | **0.652** |

The reserved slate also increased cases with all gold entities in the top eight:
Rust `0.167 -> 0.333`, C# `0.167 -> 0.417`.

No single route dominates:

- Base is strongest for R10/R11.
- Content TF-IDF is strongest for D03/D04/D11 and R07/R12.
- Metadata is strong for explicit identifiers such as `EmployeeUpdateInfo` and
  `UpdateEmployeeAttr`, but noisy for generic lowercase words such as
  `required`.
- R03 and R08 require route diversity rather than a higher weight on one score.

## Proposed ranking architecture

### 1. Aggregate by entity, but keep fragment routing

Group recalled fragments by `entity_id`. Compute entity-level route scores, but
inside each entity preserve the original fragment rank. The resolver still works
from the selected fragment and source range.

This avoids the failed v1 behavior where a broad entity was boosted by summing
all of its fragments.

### 2. Route A — base fragment relevance

For each entity:

\[
anchor(e)=\max_{f\in e} current\_fragment\_score(f)
\]

Also retain second/third scores only as small support, as in hybrid v1:

\[
support(e)=0.15\,second(e)+0.05\,third(e)
\]

This route protects strong FTS/vector consensus and cases such as R10/R11.

### 3. Route B — entity-content TF-IDF

Use the entity content already loaded in `SearchHit.entity.content()`; no new
source read is required.

Tokenizer:

- identifiers and camelCase/snakeCase parts;
- lowercase;
- length >=3;
- remove a fixed stopword list;
- cap at 24 query terms.

For the candidate entities in one question:

\[
idf(t)=\ln\frac{N+1}{df(t)+1}+1
\]

\[
content\_score(e)=
\frac{\sum_{t\in query\cap entity(e)} idf(t)}
{\sum_{t\in query} idf(t)}
\]

This route is cheap, deterministic and particularly strong when the answer is
inside a method body but its name/signature does not mention the query term.
Examples: D03 `FullName`/`Keyword`, D04 `EmployeeUpdateInfo`, R07 short-flag
parsing and R12 flattened-item augmentation.

### 4. Route C — declaration metadata match

Use stored `CodeMetadata.symbol_name`, `signature` and `scope`.

\[
metadata\_score(e)=
3\,explicit\_exact(e)+name\_coverage(e)+signature\_coverage(e)
\]

Important correction from hybrid v1: an exact match receives the large bonus
only for an **explicit identifier** — CamelCase, snake_case, backticked or
otherwise name-like. A generic lowercase word such as `required` must not get
the same exact-symbol weight as `UpdateEmployeeAttr`.

This route is responsible for pulling low-base-rank entities such as D04's
rank-119 `EmployeeUpdateInfo` into consideration.

### 5. Route D — query-facet coverage

Treat weighted query terms as provisional facets. Greedily select entities that
cover the most previously uncovered IDF weight, with base relevance as tie-break.

This route is not used alone. It is a diversity reserve for multi-hop questions
where different entities cover different clauses, such as R03, R07, R08 and R11.

### 6. Optional structural propagation

If a child declaration strongly matches the query, propagate a bounded bonus to
its containing parent entity using stored `scope` and source-range containment.
Keep the child and parent in the same scheduling neighborhood.

This targets R11: `validate_required`/`validate_exclusive` should reinforce the
`Validator` entity instead of allowing unrelated entities to consume the budget
before rank 66. It also helps R10 when typed getter methods distinguish otherwise
similarly named `ArgMatches` impl entities.

For the first arm, use only containment relationships already present in the
loaded candidate pool. Fetching additional index entities is a later variant.

## Reserved entity slate

Do not collapse the routes into one fragile weighted sum. Build an ordered,
deduplicated slate with reserved positions:

1. Top **3** entities from base route.
2. Top **4** entities from content TF-IDF.
3. Top **2** entities from metadata route.
4. Top **2** entities from facet coverage.
5. Fill remaining positions by hybrid-v1 static priority, then three-route RRF.

Maximum reserved entities before normal fill: **11**. Duplicates retain their
earliest route position. This is a research quota, not a tuned production
constant; it must be preregistered before the next replay.

The slate fixes the main weakness of additive reranking: a strong but irrelevant
route cannot completely drain the pack before a low-base-rank but exact/content
entity is considered.

## Budget-aware scheduling

Keep hybrid-v1's soft saturation:

- accepted entity bytes <4,096: factor `1.0`;
- 4,096–8,191: factor `0.5`;
- >=8,192: factor `0.25`;
- no hard entity cap.

At each step choose the highest-priority remaining slate entity, then its best
remaining fragment by original rank. This preserves R11-style same-entity
coherence while preventing R10/D11-style starvation.

Arm A resolver, clipping, dedupe, atomic fallback, 24,576-byte / 64-item pack cap
and resolver I/O quotas remain unchanged.

## Why this should address the measured cases

| Case | Expected mechanism |
|---|---|
| D04 | Content and metadata routes both surface `EmployeeUpdateInfo`; saturation prevents broad `CoreService` entities from filling the pack first. |
| D11 | Explicit `UpdateEmployeeAttr` metadata route plus base rank 1 keeps the target first. |
| D03 | Content TF-IDF ranks `EmployeeEditThongTinCaNhan` and its containing service highly even when metadata is weak. |
| R07/R12 | Content TF-IDF covers all gold entities within top 4/8 in the feature study. |
| R10/R11 | Base route protects the relevant `ArgMatches`/`Validator` entities; structural propagation and soft saturation preserve enough budget. |
| R03/R08 | Slate diversity retains base, content and metadata candidates instead of allowing many `help`/`Arg` duplicates to dominate. |

These are hypotheses supported by entity-recall analysis, not yet pack-level
results.

## Preregistered next experiment

Run exactly one offline arm with the architecture above:

- same frozen G1K4 pool, model, index, tokenizer and arm A resolver;
- no gold, case ID or oracle ordering in the policy;
- routes and slate quotas fixed before scoring;
- no semantic model calls, source re-parse or index changes in this first arm;
- score C0 vs candidate with union exact, legacy single-item exact, macro
  precision, full tokens, bytes/items and per-case all-gold transitions;
- require 24/24 integrity receipts, 0 gaps, R10 3/3, no C0 all-gold loss,
  at least one newly all-gold case, precision >= C0 and mean tokens <=1.25× C0;
- diagnose R03/R07/R08/R10/R11/R12/D03/D04/D11 target reach and byte occupancy.

Stop after that one configuration. Do not tune route weights after seeing the
score.

## Later options, only if the first arm fails

1. **Semantic metadata route:** embed candidate `symbol_name + signature` with
   the existing model and fuse its rank. This may help `Bash` vs `shell` and
   cross-language naming, but adds embedding latency and needs a p95 gate.
2. **Query decomposition:** generate 2–4 facet subqueries and reserve one entity
   per subquery. Useful for multi-hop questions, but requires a separate
   decomposition policy and cost evaluation.
3. **Representation change:** method/block or multi-scale index units for the
   residual 6 Rust + 11 C# spans without a containing <=4 KiB declaration and
   3 Rust + 9 C# spans without a pool entity. Reindex requires separate owner
   approval.

## Reproduction of the feature study

```bash
BASE=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126/ranking-fix-research-20261126
OLD=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
ROOT=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final
PYTHONPATH="$OLD/tools:$ROOT/tools:$ROOT/ranking-decision/union-replay/tools" \
  python3 "$BASE/ranking_feature_study.py"
```

Artifacts: `feature-study.json`, script and resource/log receipts under `BASE/`.
The study measures entity recall only; it does not claim pack coverage or
production readiness.
