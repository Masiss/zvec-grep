# Public-source implementation retrieval protocol — 2026-11-23

**Status: COMPLETE — `NO_VALIDATED_GAIN`.** Source inventory, task split, adjudication, budgets, baseline strategies, metrics, and thresholds were frozen before the final run. The full nine-task historical prompt set was unavailable for overlap review, so any mechanism evidence remains exploratory.

## Frozen inputs

- Corpus lock: [`source.lock.json`](../../research/public-source-evaluation-20261123/source.lock.json), SHA-256 `214298e7f84f610a2a47acee48aa1f102f472bee6c9e8a76cdd95a6a9df7ebf2`.
- Corpus: all case-sensitive `.cs` archive members from ANTLR `2ac185af855ff39b4b8c41854b756767099c356f`, Dapper `eb47546a408bbf6c178bda4f04dc205bc51ffbfe`, and Newtonsoft.Json `0a2e291c0d9c0c7675d445703e51750363a549ef`; 1,279 source files. No other file types, default ignores, or repository-specific exclusions.
- The exact source corpus is recreated with `python3 research/public-source-evaluation-20261123/materialize_corpus.py <new-directory>`. It verifies archive hashes, every included file hash/size, and the final path inventory.
- Development: four authored tasks, six accepted source ranges, SHA-256 `68929109ec794df4eda7d017cceb79541728829f1a43bffdd48771de983b47f7`. Dev-03 was narrowed to the ordinary list-expansion path when array parameters are unsupported, then all development methods were rerun against that revised split before any candidate policy.
- Validation: ten prompts, SHA-256 `50d5b5628c69b4c9c321984407e908a0f46092b728ba4aebb8ac27d491837dd6`; author seal `0497e8574927f37a74614381a7bdd0ca324790d65e411daeb5d967e50d4671d1`; independent adjudication seal has nine answerable tasks, one `ABSTAIN_REQUIRED` control, and 18 accepted ranges. The private gold SHA-256 is `450253d1c1e7c3e2409ea42ccf48749ad83c236784ba29f4160dae47a7688181`.
- Task author, gold adjudicator, and retrieval engineer worked in separate agent contexts. The final runner received the validation prompt file by path; the retrieval engineer did not inspect prompt text, raw validation outputs, or private gold. Only hashes and sanitized adjudication aggregates were reviewed. The filesystem is shared, so separation is procedural rather than enforced by access control.
- Historical task overlap audit is partial: reachable frozen records omitted the prompt/target text for the complete nine-task diagnostic set. Do not describe this split as fully contamination-cleared.

## Runtime and index

- Use the R4 experimental Rust search implementation, pinned to source commit `e7d0a89c27e199489ea4ad786c619211bad5ac2d`. The R4 code tree has no code changes from the report's cited `2bf2f1ef…` checkpoint; the intervening commit changes a Rust documentation handoff only.
- The CLI and adapter were built from this pinned source in an isolated clone at `/tmp/public-source-r4-experiment-20261123-v1`, with Rust/Cargo 1.98.1 and offline dependency resolution. The successful build used the already-cached zvec, ONNX Runtime 1.21.0, and llama runtime bundle; it did not fetch or install dependencies. `zg` SHA-256 is `92dea6a681ff0881341701b71fd5740ebb2564216423ba9a345656f0d796850c`; `r4-adapter` SHA-256 is `cd7b66620a49b771c05f21ec2c74f3a3dfa960c92854717fb2dde5d59aaf15c2`. Full build and native dependency receipts are in [`r4-index-build.json`](../../research/public-source-evaluation-20261123/receipts/r4-index-build.json). Do not use the installed v2 verifier or the old 392-file work-copy index as evaluation evidence.
- Build a new scratch index from the materialized 1,279-file corpus using local `potion-code-16m-v2` on CPU. Weight SHA-256: `75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c`. No remote inference, index repair, or protected-index access.
- A fresh v3 scratch index was built from the materialized corpus: generation `ed3d5c63-abb4-4c9c-8b4d-5b12daf5fee7`, 1,279/1,279 files, 0 failed, 22,096 entities. The read-only `--check-ready` status exited 0. The 135,458,663-byte index tree SHA-256 is `ed7006b519b0f1ab8ddd9c31fdc8d9d6fff6bd1222b0d65b495a8f56aab201f0`. The same generation was used for development and the single final validation run.

## Baseline methods

**Existing hybrid:** submit the question verbatim to the pinned R4 engine's default hybrid route, with `prefer_symbol=false`, refresh off, and a 10-item ranked limit. Do not add query expansions or manually supplied identifiers.

Capture the hybrid result with the R4 adapter's search-only `arm-a` profile; do not call its workflow, planning, or evidence tools. Pass a fixed path-exclusion marker that matches no corpus path, overriding the adapter's legacy benchmark exclusions so the frozen `.cs` corpus remains the actual search scope. The adapter's 64 KiB session cap is only an outer transport limit; the evaluation driver selects the ranked items that fit the 24,576 source-byte cap.

**Lexical `rg`:** tokenize the question into unique case-insensitive alphanumeric/underscore terms of length at least three; remove this fixed stop list: `a, an, and, are, as, at, be, been, being, by, can, could, did, do, does, for, from, had, has, have, how, if, in, into, is, it, its, may, might, of, on, or, should, that, the, their, then, there, these, this, to, was, were, what, when, where, which, while, who, why, will, with, would`. Search those terms as a case-insensitive, whole-word literal OR over `.cs` files with `rg`. Create 9-line windows centered on matching lines, merge overlapping or adjacent windows within a file, then rank by distinct query-term coverage in the window, distinct query-term coverage in the file, match count, path, and start line, in that order. Select the first ten windows that fit the source-byte cap. No stemming, synonyms, manual query rewrites, or task-specific terms.

**Candidate-pool diagnostic:** capture direct FTS and vector route results separately with the pinned CLI, up to 50 hits per route and task. Treat their union as a bounded route-level acquisition pool. It is not the engine's complete internal adaptive-recall map; do not claim that it is. The hybrid run's ranked results remain a separate artifact. This keeps the route-pool capture free of the adapter's per-session file-read budget.

## Budgets and measurement

- Maximum 10 delivered evidence items and 24,576 UTF-8 source-content bytes per task. Count exact source content only; report serialized output bytes separately. Skip an item that will exceed the remaining source-byte budget and continue in rank order; never truncate a source range.
- For every task and method, preserve candidate-pool hits, ranked hits, and final delivered path/ranges as separate raw artifacts. Distinguish relevant entity/file acquisition, relevant fragment acquisition, useful ranges, and context completeness.
- An adjudicator labels whether each delivered item supports the question, is irrelevant, or is actively misleading. Gold non-overlap alone is not a false-positive label.
- Measure end-to-end latency, peak RSS, delivered source bytes, serialized output bytes, and scratch-index size. Direct CLI execution has no persistent warm service; label repeated-process results as cold process / warm OS-cache where measurable.
- Run development baselines before design review. Do not query validation tasks until the single final evaluation, when hybrid, lexical, and the selected experimental method are each run once under the frozen protocol. Do not inspect validation outputs or scores before the adjudicator returns aggregates.

## Metrics and gates

- **Useful-source task:** adjudicator confirms at least one delivered implementation range materially supports the task.
- **Complete task:** every independently marked required range or required behavior is supported by delivered context. Report this separately from simple overlap.
- **Unique lexical value:** useful implementation evidence delivered by the indexed method that the lexical baseline did not deliver.
- **Development falsification gate:** proceed only if one experimental method improves useful-source task count by at least one over each baseline, preserves every baseline-complete development task, adds no adjudicated misleading evidence, and stays within hard budgets.
- **Validation success threshold:** the experimental method must add useful-source evidence on at least two answerable tasks over each baseline, add at least one complete task over each baseline, preserve every task complete under either baseline, and introduce no increase in false/irrelevant evidence on the controls. No statistical-significance claim is made at this sample size.
- **Cost gate:** to claim a validated source gain, median end-to-end latency and median peak RSS must each stay within 1.25× the hybrid baseline. Any source gain above that cost is reported as a trade-off and does not pass this gate. Hard item and source-byte limits always apply.
- If the development gate fails, stop the primary direction; at most one materially different reviewed alternative may be tried. If the final gate fails, report `NO_VALIDATED_GAIN`. Because the historical contamination audit is partial, even a numerical validation pass can support only `MECHANISM_CONFIRMED — GENERALIZATION_NOT_PROVEN` until independence is established.

## Change and review boundary

The first fresh-context Sol High challenge returned `REVISE_DESIGN`. The second fresh-context challenge returned `APPROVE_DESIGN` for testing C (bounded source-neighbor expansion) in the isolated clone; this approves the experiment only, not adoption. The sidecar records actual adaptive route calls/depths, pre-fusion entity-to-fragment evidence with route/rank/score/document identity, loaded evidence before entity cutoff, and final entity order/ranges. A four-task captured run and a no-capture control produced exactly equal delivered JSON items, content, and ranges. See [`indexed-source-delivery-design-evidence-20261123.md`](indexed-source-delivery-design-evidence-20261123.md) for exact development observations and the challenge findings.

Freeze C with the deterministic policy, byte accounting, range/hash requirements, and falsifiers recorded in the second challenge. Before final validation, test at most this one candidate; do not tune against validation or switch to A based on validation outcomes. The reviewers received no validation prompt or gold. Keep candidate changes in an isolated experimental clone; do not alter maintained defaults, public APIs, schemas, embeddings, production runtime, protected indexes, or frozen R0–R6 benchmarks.

## Final validation result

The single final run is recorded in [`final-validation-run-20261123.json`](../../research/public-source-evaluation-20261123/receipts/final-validation-run-20261123.json), with sanitized scores in [`final-validation-adjudication.json`](../../research/public-source-evaluation-20261123/adjudicator/final-validation-adjudication.json).

| Method | Answerable tasks with useful source | Complete tasks | Useful / irrelevant / misleading items |
|---|---:|---:|---:|
| R4 hybrid | 8/9 | 3/9 | 27 / 69 / 4 |
| Lexical `rg` | 8/9 | 3/9 | 24 / 68 / 3 |
| Candidate C | 8/9 | 3/9 | 28 / 68 / 4 |

C added useful evidence items on two answerable tasks over each baseline, but those tasks already had useful baseline evidence. Task-level usefulness and completeness did not increase. C preserved the three tasks complete under either baseline. On the abstention-required control, C matched R4 and had one more irrelevant item than lexical `rg`; no control threshold was met against both baselines. The frozen protocol gate therefore **failed**: C added no complete task over either baseline and missed the control condition. The separate adjudicator's stricter all-answerables-useful gate also failed at 8/9; it does not replace the preregistered gate.

Validation median R4 latency/RSS was 1.11 seconds / 426,918 KiB. The lexical runner median was 0.191 seconds / 76,416 KiB. The candidate packer took 0.31 seconds and 82,744 KiB across all ten tasks; integrated serving latency was not measured. Fresh-context Sol final review returned **`NOT_GENERALIZED`** and recommended stopping without promotion or further validation tuning. Final decision: **`HOLD`**. Definition of Done: **`NO_VALIDATED_GAIN`**.
