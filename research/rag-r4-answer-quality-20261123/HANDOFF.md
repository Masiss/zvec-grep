# R4 evidence projection investigation — handoff

**Decision:** `HOLD_WITH_ROOT_CAUSE`
**Scope:** R4 Indexed Semantic Retrieval → RAG evidence pack, source-only development trials.
**No production promotion:** no merge, push, release, index rebuild, public API change, or default change was made.

## Outcome

The main failure is confirmed after entity retrieval: R4 can recall the right entity and several sibling fragments, then deliver only the first route-ordered sibling. A second confirmed loss occurs when one entity needs multiple disjoint ranges: context construction emits one item per entity and deduplicates by entity ID. These mechanisms explain concrete `BufferedTokenStream` and `val-03/GridReader` failures.

There are two separate `BufferedTokenStream` cases in the artifacts. The Indexed validation reproduction concerns `SetTokenSource` at source lines 367–375: the selected sibling missed that range while another recalled sibling covered it. RAG dev task `a02` instead asks about `Reset()` at lines 146–149; candidate A chose the nearby `SetTokenSource` documentation range 339–387 and still missed the answer-bearing implementation. Do not merge these into one task or claim that solving one fixed the other.

No raw run, task record, or structured manifest maps the historical label `C-NEW-03` to a specific task; only prose summaries mention it. The closest preserved Candidate C reproduction is Indexed `val-03`: baseline delivered `SqlMapper.GridReader.cs` lines 70–100; neighbor expansion widened that to lines 44–130; the adjudicated answer requires nonlocal lines 208–236 and 438–458. Neither expansion pack covered those ranges. This is evidence against local adjacent-window expansion as a solution for multi-branch questions, but it is not enough to claim a one-to-one identity with the historical label `C-NEW-03`.

## B2 failures: candidate presence versus selector choice

`projection-prototype-v1/b2-candidate-signal-audit-v1.json` joins the frozen B2 output to the full pre-ranking loaded-recall trace, B2 route evidence/attempts, A's saved fragment relevance trace, and required ranges. Gold was read only after candidate outputs were fixed; it did not affect B2 selection.

| Failed task | What was present | What B2 did | Diagnosis |
|---|---|---|---|
| `a02` | `BufferedTokenStream` is selected at rank 4. Same-group route evidence has sibling ordinal 2 (lines 99–161, vector rank 67) and ordinal 3 (lines 146–199, vector rank 17); both cover the required `Reset()` range 146–154 and pass a posthoc check of B2's one-slot constraints. | B2 spends its exchange first on rank 1 `TokenStreamRewriter` and returns; neither target sibling is attempted. | Selector order/budget loss, not candidate absence. |
| `a03` | `BufferedTokenStream` is selected at rank 3. Sibling ordinal 5 (lines 228–272) fully contains required lines 232–253; ordinal 4 (189–237) only intersects part of that range. Both are same-group route evidence and pass the posthoc one-slot constraints. | B2 picks ordinal 0 (lines 34–72) as the first eligible sibling and stops. | Selector order/budget loss, not candidate absence. |
| `a04` | Required `UnbufferedTokenStream` behavior is in the full pre-ranking route pool (target entity fusion rank 56; vector rank 188), but that entity is outside the baseline top 10. | B2 can exchange only siblings of already-selected baseline entities. | Entity cutoff; changing only the sibling selector cannot recover it. |
| `n11` | Both required `JsonTextReader` ranges occur in the full route pool, on entities with fusion ranks 74 and 125; their vector route ranks are 44, 118, and 90. Neither target entity is in the baseline top 10. | B2 cannot admit either entity. | Entity cutoff; changing only the sibling selector cannot recover them. |
| `n12` | The selected rank 1 `JsonWriter` entity inventory has siblings covering the required range (ordinals 11 and 12), but the target range has no route-evidence fragment in the loaded pool. | B2 only considers route-evidence siblings, so those inventory-only fragments are outside its candidate set. | Fragment-level route acquisition / B2 input-scope gap. |

Thus, among the five incomplete tasks, **two have a posthoc-feasible required sibling in B2's scope, two are blocked by entity cutoff, and one lacks the required fragment in route evidence**. The two selector misses are genuinely fixable by choosing differently within B2's current allowed pool; that does not prove a reliable choice signal exists.

### Existing signals measured

- **Query-symbol alignment:** The two `a02` target siblings contain exact identifier `Reset`, but so does the nearby false-positive documentation fragment selected by A. A's trace gives the false positive 5/9 informative terms versus 3/9 and 4/9 for the target siblings. For `a03`, the target sibling matches 3/10 query terms while B2's ordinal 0 matches 4/10. Plain identifier/token overlap therefore does not distinguish required behavior reliably in these examples.
- **Fragment route provenance:** It is logged in the experimental trace but B2 orders siblings by deterministic fragment identity and baseline order; it does not rank by provenance. In `a03`, the target ordinal 5 has vector rank 11 versus ordinal 0's vector rank 39 within the same vector route, a plausible signal on that case. In `a02`, the unrelated rank 1 entity's chosen sibling has vector rank 11, ahead of the target entity's ordinal 3 at vector rank 17. Route order is not sufficient, and FTS/vector scores are not compared across modes.
- **Method structure:** Fragment records contain IDs and byte ranges; route evidence adds route mode/id/rank/score, but no method/symbol range. Entity outlines list member names such as `Reset()` and `Fetch(int n)` without mapping those members to sibling ranges. Structure exists at entity level, not as a measured per-fragment selector input.
- **Range complementarity:** A's saved token-feature trace shows no marginal query tokens or exact identifiers for the `a02/a03` target siblings relative to the other baseline items, even though posthoc required-range coverage shows they add the missing behavior. B2 checks non-overlap and group coverage, not semantic complementarity.

These measurements do not establish that the signals are sufficient. The route ranks suggest a possible discriminator for one `a03` sibling; the `a02` false positive and zero marginal token gain show why it must be tested rather than assumed. The reproducible signal audit is `projection-prototype-v1/b2-candidate-signal-audit-v1.json`.

Two distinct mechanisms were evaluated against the frozen ten-task RAG development set. B was first tested append-only (B1), then revised once within the same mechanism to exercise one-slot allocation (B2):

| Candidate | Mechanism | Result |
|---|---|---|
| B1 | Append non-overlapping sibling fragments after the baseline pack | No task gains. The baseline already filled all 10 item slots, so no siblings could be appended under the unchanged cap. This did not test slot reallocation. |
| B2 | Exchange the lowest-ranked baseline item for one disjoint sibling from an already-selected entity; max two fragments per entity | **5/10** complete, up from 4/10; required-step coverage rises from **8/16 to 9/16**. It preserves all four baseline-complete tasks and passes all 10 pack invariants, but misses the 6/10 gate. `d08` is the only task newly complete. |
| A | Replace one selected fragment per selected entity using query-aware lexical relevance | The corrected replay changes 12 snippets. After the overlap guard, all pack invariants pass, but completion remains **4/10**, equal to baseline. The four baseline-complete tasks remain complete. The 6/10 offline gate is not met. |

Prototype A is **rejected as an improvement hypothesis on this development set**. B2 shows a limited development evidence gain but **fails the predeclared offline gate**; stop this B allocation variant as Sol directed. These results do not reject every possible fragment-packing design. No answer model, grader, or validation call was run because no candidate reached 6/10. No gold span was used by any selector.

## Exact source and frozen inputs

- Current root checkout at investigation start: commit `663566546f03768b9b021c65eb3accc4aa0720a1` (schema v2). It is **not** the source used for the v3 trial.
- Matched R4 v3 source: isolated, clean worktree `/home/masis/src/zvec-grep-r4-evidence-20261119`, commit `e7d0a89c27e199489ea4ad786c619211bad5ac2d`.
- Prototype source snapshot: `/tmp/r4-evidence-projection-v3-20261124/rust`; patch is `projection-prototype-v1/prototype-r4-v3.patch` and passes `git apply --check` against the matched worktree.
- Read-only corpus/index: `/tmp/zvec-public-source-20261123`; generation `ed3d5c63-abb4-4c9c-8b4d-5b12daf5fee7`; index schema v3; 1,279/1,279 source files.
- Index manifest SHA-256: `bc9fef25daefee90b682fe6eba1e9bbbd6ccb568385103b0a275d5f7161ba1b2`. Rechecked after the replay; unchanged.
- Source-lock SHA-256: `214298e7f84f610a2a47acee48aa1f102f472bee6c9e8a76cdd95a6a9df7ebf2`.
- Frozen task input: `projection-prototype-v1/tasks.jsonl`, SHA-256 `469a5c0a951c15dbdbf107032a64285c60b630d761edc1483b1eb86ddde75ad6`; the input contains queries and task IDs, not gold ranges.
- Request contract: hybrid search, limit 10, trace enabled in the probe, `RefreshPolicy::Off`, `auto_update=false`, local CPU embedding.

## Verified data path

```text
FTS / vector route hits
  → candidates keyed by EntityId; each InternalEvidence retains fragment + route mode/id/rank
  → RRF fuses and ranks entities; deterministic code ranker reorders entities
  → load evidence; sort siblings by route-local rank / route order / route ID / document ID
  → SearchEvidence retains the fragment but drops its route provenance
  → one ContextItem per SearchHit; context_item_target uses hit.evidence.first()
  → context items deduplicate by entity ID; query-group coverage then global-rank fill
  → R4 adapter emits source coordinates / hashes; context limits apply
```

Source map in the matched v3 worktree:

| Boundary | Source and behavior | Evidence risk |
|---|---|---|
| Entity construction | `domain/entity.rs::Entity` is a logical search unit with file ID, entity source range, content, metadata, and fragments. `EntityId::new` hashes the content fingerprint plus serialized source range with the file ID. | Entity ID is the grouping key later used by fusion and context dedupe; it is not a method-level answer identifier. |
| Fragment construction | `pipelines/indexing/pipeline.rs::bind_entities` converts extracted ranges to `EntityFragment`s; `FragmentId` is entity ID plus stable ordinal. Entity validation rejects empty/out-of-bounds ranges. Text/code fragments use entity-relative byte ranges; images use full range. | Fragments are byte windows, not guaranteed method boundaries; context later expands byte windows to whole lines. |
| Route recall | `StorageSearchHit` carries document/fragment ID, entity ID, file ID, route path, and score. `pipeline.rs::add_recall_hits` derives route-local rank from result position and attaches evidence to candidates keyed by `EntityId`. | Route candidate absence cannot be repaired later. Route ranks are local to each route. |
| Fusion and rank | `pipeline.rs::fuse_candidates` computes entity RRF from recall ranks; the code ranker then scores entities using query-derived symbol, definition, path and support signals. | This ranks entities, not answer-bearing fragments. Raw FTS and vector scores are not calibrated against each other. |
| Evidence loading | `pipeline.rs::candidate_to_hit` sorts by route-local rank and deterministic tie breakers, then constructs `SearchEvidence { fragment }`. | Per-fragment route identity/rank/score are discarded at this boundary; list position is the only remaining order signal. |
| Context projection | `context.rs::search_plan_to_context_items` emits one item per hit; `context_item_target` reads only `hit.evidence.first()`. Byte windows are expanded to whole source lines and mapped back to source coordinates. | A relevant sibling can be present but omitted; whole-line expansion can include adjacent unrelated code while preserving coordinate correctness. |
| Dedupe and selection | `context.rs::select_and_rank_context_items` uses `entity:<id>` as the dedupe key, merges query-group matches, prioritizes group coverage, then global rank. | Disjoint sibling ranges from one entity cannot be separate items on this path. |
| Packing and citation | Frozen RAG gate is 10 items / 24,576 UTF-8 source bytes. The R4 adapter emits inclusive line ranges plus authoritative half-open decoded UTF-8 byte offsets, encoding-aware raw offsets, and content SHA-256. | In these runs the item cap binds before the byte cap; appended siblings get no slots. Keep range/hash integrity as invariants. |

The code locations above refer to the matched v3 source snapshot, not root HEAD v2. The current source audit is `r4-pipeline-audit-20261124.md`.

## Failure classification

| Layer | Finding | Classification and limits |
|---|---|---|
| Fragment projection | `BufferedTokenStream`: the selected entity had a recalled sibling that covered the needed `Reset` evidence, while the projected sibling did not. `val-03`: two recalled `GridReader` siblings covered separated required ranges, but the emitted item covered neither. | Confirmed downstream selection/projection loss. It does not prove all incomplete packs have this cause. |
| Entity cutoff | In the RAG dev trace, complete required spans exist in the loaded route-recall pool for 10/10 tasks, but only 8/10 survive the top-10 entity pool; `a04` and `n11` target entities sit below the cutoff. | Fragment-only reranking cannot fix these two tasks. This is a ten-task diagnostic result, not a corpus-wide rate. |
| Route acquisition | `val-08` has no target-file hit in the saved direct FTS/vector top-50 pools; R4 returned helper evidence. | Acquisition is unresolved: direct top-50 pools are proxies for the adaptive hybrid map, and gold scope is uncertain. Do not label this a proven vector failure. |
| Multi-branch evidence | `val-04` and `val-10` require multiple branches; some non-final source-range joins are incomplete. Candidate C `val-03` expands the GridReader region from lines 70–100 to 44–130, while required ranges are 208–236 and 438–458. | The `val-03` expansion is a direct counterexample to local-neighbor expansion for nonlocal branches. No raw task/trace maps the prose label `C-NEW-03`, so its exact mapping remains unknown. Do not attribute every multi-branch miss to one selector. |
| Answer use | Sufficient-pack answers still fail dimensions on `a04`, `d06`, and `n11`. Earlier prompt revisions did not exceed the sufficient-pack baseline. | Answer reasoning/citation use is a separate layer. No answer call was run for this candidate. |
| Observability | Normal traces do not consistently join every non-final candidate to source ranges; direct route top-50 snapshots are not the adaptive hybrid candidate pool. | Limits acquisition-vs-ranking attribution and rank-efficiency claims. |

## B2 failures: candidate presence versus selector choice

`projection-prototype-v1/b2-candidate-signal-audit-v1.json` is a posthoc join of frozen B2 outputs, the full pre-ranking loaded-recall trace, route evidence, the B2 attempt trace, A's saved query-feature trace, and development required ranges. Gold is used only to classify already-frozen outputs; it did not affect candidate selection.

| Failed task(s) | Required source versus B2's candidate scope | Classification |
|---|---|---|
| `a02` | `BufferedTokenStream` is already selected at rank 4. Same-group route evidence contains sibling ordinals 2 and 3; ordinal 3 spans lines 146–199 and ordinal 2 spans 99–161, both covering required `Reset()` lines 146–154. Both pass a posthoc simulation of B2's one-slot exchange constraints, but B2 already exchanged rank 10 for a sibling from rank 1 `TokenStreamRewriter` and stopped. | Candidate exists; loss is B2's first-eligible global exchange order. |
| `a03` | `BufferedTokenStream` is selected at rank 3. Sibling ordinal 5, lines 228–272, fully contains required lines 232–253; ordinal 4, lines 189–237, intersects only part of that range. Both are route evidence in Q1 and pass the posthoc exchange checks. B2 instead selects ordinal 0, lines 34–72, then stops. | Candidate exists; loss is sibling ordering and the one-exchange budget. |
| `a04` | Required `UnbufferedTokenStream` range appears in the full pre-ranking route pool (entity fusion rank 56, vector rank 188), but that entity is not in the B2 top-10 baseline. | Entity cutoff; a sibling selector over retained entities cannot recover it. |
| `n11` | Both required `JsonTextReader` ranges appear in the full pool (target entities have fusion ranks 74 and 125; vector route ranks 44, 118, and 90), but neither entity is in the B2 baseline. | Entity cutoff; a sibling selector over retained entities cannot recover them. |
| `n12` | The selected `JsonWriter` entity inventory contains siblings spanning required lines 490–506, but no loaded route-evidence fragment for that range appears in B2's candidate set. | Fragment-level route acquisition / B2 input-scope gap; choosing among the same `SearchEvidence` cannot recover it. |

So among the five incomplete tasks, **two have a feasible required sibling inside B2's scope, two are blocked by entity selection, and one is blocked because the required fragment is absent from route evidence**. This is more specific than saying every B2 failure is a selector failure.

### Measurements of available selection signals

- **Query-symbol alignment:** For `a02`, both target-bearing siblings have exact identifier `Reset`; the nearby false-positive `SetTokenSource` documentation candidate also has exact `Reset`. A's lexical trace chose that documentation fragment with 5/9 informative query terms, over target siblings with 3/9 and 4/9. Exact symbol matching and token coverage therefore do not distinguish implementation from a nearby mention in this case.
- **Token alignment:** For `a03`, target ordinal 5 matches `fetch`, `stop`, and `tokens` (3/10 query terms), while B2's chosen ordinal 0 matches `buffered`, `input`, `stream`, and `tokens` (4/10). A raw token-count selector would favor the wrong sibling here too.
- **Route provenance:** Route mode and rank are present in the experimental trace, but B2's admission order uses fragment identity and baseline order, not route rank. For `a03`, ordinal 5 has vector rank 11 while chosen ordinal 0 has vector rank 39 in the same vector route, a plausible signal on this example. For `a02`, the relevant ordinal 3 has vector rank 17 while the unrelated rank-1 entity's chosen sibling has vector rank 11 in that route, so route rank can also favor an irrelevant entity. FTS and vector ranks/scores are not compared as one scale.
- **Structure:** B2's fragment inventory exposes only `fragment_id`, `range`, and `content_range`; route evidence adds route fields. Entity outlines list members such as `Reset()` and `Fetch(int n)`, but the outline is entity-level and has no per-fragment method/symbol ranges. It cannot directly map each sibling to a method boundary.
- **Complementarity:** In A's saved feature trace, target siblings for `a02/a03` add no unique query terms or exact identifiers beyond the rest of the baseline pack. Yet posthoc source-range scoring shows those siblings would cover the missing required steps. Token-set novelty does not capture that source-range complementarity.

These measurements show a better selector could plausibly recover `a02/a03`, but not with a proven reliable signal today. `a04/n11` require a wider entity pool; `n12` requires a wider fragment-evidence pool. The measured features are diagnostic only, not a claim that a combined selector would pass the gate.

## Designs and adjudication

Three distinct mechanisms were considered in `r4-evidence-projection-design-review-20261124.md`:

1. **One sibling by fragment-level route RRF.** Cheap and bounded, but carries route-rank weakness into fragment choice and cannot cover multiple branches.
2. **Fragment-level packing.** Allows multiple disjoint ranges under a shared budget; risks reducing entity diversity, increasing noise, and violating downstream assumptions. Prototype B preserved the baseline, but the binding 10-item cap left no free slots.
3. **Bounded source-structure expansion.** Add a containing method/declaration around a selected anchor. It does not recover nonlocal branches; earlier adjacent-neighbor Candidate C failed task-level validation, so a repeat of that policy was not warranted.

The initial Sol design review favored fragment-level packing; B1 showed that append-only packing could not operate with a full baseline. A later Sol review confirmed one bounded B2 allocation revision remained within the two-mechanism limit and specified one lowest-rank exchange, no new scorer, no gold selection, and a strict stop if the 6/10 gate failed. B2 follows that policy and fails the gate at 5/10. Sol also reviewed A's one-slot lexical substitution and required query-local evidence, unique informative token/identifier matching, tie-to-baseline behavior, no cross-route score/rank comparisons, all-ten replay, and preservation of `d05/d06/d07/n10`. The earlier A review reported 13 substitutions; the corrected overlap-guard replay changes 12. `final-sol-review-overlap-guard-v1.json` supersedes that stale count. Sol's final B2 review is in `final-sol-review-b2-v1.json`.

Reviewer records are in `r4-sol-design-review-20261124.json`, `projection-prototype-v1/design-review-a-v1.json`, `projection-prototype-v1/final-sol-review-v1.json`, `projection-prototype-v1/final-sol-review-overlap-guard-v1.json`, and `projection-prototype-v1/final-sol-review-b2-v1.json`. The corrected reviews confirm the local-neighbor Candidate C rejection for `val-03` as tested and B2's stop condition, but not that every structure-aware or multi-fragment design would fail. No raw task/trace maps the historical `C-NEW-03` label. Requested model/effort are recorded; runtime model identity and actual reasoning effort were not independently confirmed, and reviewer token usage was not exposed.

## Tests, gate, and cost

The isolated v3 `zg-engine` indexed-search library suite, run offline against cached native dependencies, finished with **57 passed, 0 failed, 1 ignored**; raw output is saved in `projection-prototype-v1/indexed-search-test-result-v1.txt`. It includes the overlap guard, empty/incomplete/irrelevant-evidence fallbacks, deterministic permutations, source-coordinate and packing checks, plus `reallocation_exchanges_only_the_lowest_item_for_a_disjoint_sibling`, which verifies full-pack exchange, retained baseline order/anchor, query-group coverage, overlap rejection, repeated entity ID, and deterministic output. A separate RAG pack-contract check validates two same-entity excerpts in `a02` against the frozen source lock and resolves both synthetic path/line citations; see `b2-duplicate-entity-pack-validation-v1.json`. This checks pack/citation resolution only, not answer quality.

Final corrected replay: `projection-prototype-v1/fragment-substitution-evaluation-overlap-guard-v1.json`.

Final B2 replay: `projection-prototype-v1/fragment-reallocation-evaluation-v1.json`.

| Measure | Baseline | Prototype A with overlap guard |
|---|---:|---:|
| Complete tasks | 4/10 | 4/10 |
| Required steps | 8/16 | 8/16 |
| Baseline complete tasks preserved | — | 4/4 |
| Items / UTF-8 bytes | 10 items per pack, 3.2–9.8 KB | 10 items per pack, under 24,576 bytes |
| Item cap / byte cap | pass | pass |
| Entity order / ranks / query groups | baseline | all preserved |
| Exact source slices | baseline | pass |
| New source overlap | none in baseline | none after guard |
| Untraced one-process ten-query wall time | 3.40 s | 3.64 s (+0.24 s; +7.1%) |
| Peak RSS in those one-run measurements | 472,528 KiB | 467,868 KiB (-4,660 KiB) |
| Selector body timing, separately instrumented | — | median 22,895.5 µs; mean 25,819.2 µs; max 53,253 µs |

| B2 measure | Baseline | B2 one-slot exchange |
|---|---:|---:|
| Complete tasks | 4/10 | 5/10 |
| Required steps | 8/16 | 9/16 |
| Baseline complete tasks preserved | — | 4/4 |
| All ten B2 invariants | — | pass |
| Untraced one-process ten-query wall time | 3.40 s | 3.73 s (+0.33 s; +9.7%) |
| Peak RSS in those one-run measurements | 472,528 KiB | 467,996 KiB (-4,532 KiB) |

Timings are one local CPU process per arm, include initialization, have no repeated warm-serving trial, and are not serving-latency claims. The A selector timing comes from a separate instrumented replay; trace serialization is excluded from that selector-body timing. B2's +1 completed task is a limited observed evidence-delivery gain, but B2 remains below the 6/10 gate. **Answerer, grader, and validation calls: 0.**

## Status vector

- `ENGINEERING_FIX_CONFIRMED`: the experimental overlap guard and one-exchange B2 mechanism satisfy their tested narrow invariants; the R4 evidence-projection issue is not fixed.
- `EVIDENCE_DELIVERY_GAIN`: **limited** — B2 rises from 4/10 to 5/10 and 8/16 to 9/16 on this development set, but fails the 6/10 gate.
- `ANSWER_GAIN_ON_DEVELOPMENT`: **not run**; offline evidence gate failed.
- `VALIDATED_GAIN`: **false / not run**.
- `HYPOTHESIS_REJECTED`: **A's lexical one-slot selector as tested; B2's bounded one-exchange policy fails its gate.** This does not reject all fragment-packing designs.
- Overall: **`HOLD_WITH_ROOT_CAUSE`**. No maintained code changed and no prototype should be promoted.

## Reproduction

From `/tmp/r4-evidence-projection-v3-20261124/rust`, use the matching v3 source and local cached ONNX runtime. `RefreshPolicy::Off` is hardcoded in the probe; do not change it for replay.

```sh
CARGO_TARGET_DIR=/home/masis/src/zvec-grep/.tmp/r4-projection-target \
CARGO_NET_OFFLINE=true \
ORT_DYLIB_PATH=/home/masis/src/zvec-grep/.tmp/r1-adapter-bundle-20261124/libonnxruntime.so.1 \
ORT_LIB_PATH=/home/masis/src/zvec-grep/.tmp/r4-ort-link ORT_PREFER_DYNAMIC_LINK=1 \
LD_LIBRARY_PATH=/home/masis/src/zvec-grep/.tmp/r1-adapter-bundle-20261124 \
cargo test --manifest-path Cargo.toml -p zg-engine --lib pipelines::indexed_search:: -- --nocapture
```

The probe source is added only in the patch. Baseline runs with experiment flags and trace-sink variables unset; A sets `ZVEC_R4_FRAGMENT_SUBSTITUTION=1`; B2 sets `ZVEC_R4_EVIDENCE_REALLOCATION=1`. All use the frozen `projection-prototype-v1/tasks.jsonl` and `/tmp/zvec-public-source-20261123` with `RefreshPolicy::Off`.

### Relation and method-owner follow-up

The subsequent bounded method-owner screen, R2 unknown-receiver guard, R1
integrity smoke, Sol reviews and final limitations are recorded in
[`method-owner-screen-v1/FINAL-REPORT.md`](method-owner-screen-v1/FINAL-REPORT.md).
The complete screen runner, preregistration, corrected v1/v2/v3 ledgers,
requests, receipts, negative controls, source fixtures and guard patch are in
[`method-owner-screen-v1/`](method-owner-screen-v1/). This diagnostic did not
change the baseline pack or pass the 6/10 gate; it does not supersede the
`HOLD_WITH_ROOT_CAUSE` decision.

## Artifact integrity and remaining work

`projection-prototype-v1/SHA256SUMS-final-v1.txt` records hashes for the frozen tasks/index, patch/source, baseline and candidate outputs, traces, costs, evaluators, reviews, and final reports. `prototype-r4-v3.patch` applies cleanly to the isolated v3 worktree. The v3 worktree is clean; root HEAD remains `663566546f03768b9b021c65eb3accc4aa0720a1`; the frozen index manifest hash is unchanged. Pre-existing dirty files in root were preserved.

Home-disk cleanup on 2026-11-24 moved 197 top-level zvec-grep build/test log
files into `.tmp/home-build-logs-20261031/`; `manifest.json` records every
original size and SHA-256. `models.txt` was excluded as unrelated Chromium
inventory. Two old Cargo build outputs were deleted: the 15-GiB
`~/.cache/zvec-grep/r4-evidence-20261119/target` and the 12-GiB
`~/.cache/zvec-grep/rust-target-recovered-20261105`. Receipts, source/corpus
artifacts, and all non-target experiment directories were retained. A minimal
R1 adapter bundle and its three binary hashes are kept in ignored
`.tmp/r1-adapter-bundle-20261124/`; the bundle was confirmed runnable with
`--help`. The older 5.4-GiB R1 target cache was reduced to that bundle; probe
outputs and experiment inputs were preserved. No snapshot or checkout was
moved, renamed or deleted.

Neither A nor B2 passes the answer-trial gate; no answer or validation gain is established. Sol directed stopping B2 at this point, so no third mechanism or parameter iteration is warranted within the two-mechanism limit. Further work should improve candidate/source-range observability and independently investigate entity cutoff and true route-acquisition misses before any maintained implementation.
