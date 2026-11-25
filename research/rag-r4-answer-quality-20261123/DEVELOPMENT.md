# RAG R4 answer-quality run — development checkpoint

Date: 2026-11-23. Corpus: `public-csharp-source-20261123-v1`, locked by `research/public-source-evaluation-20261123/source.lock.json` (`214298e7…`). This checkpoint includes development answer trials and a dev-only candidate experiment. No validation answer trial has run.

## Development evidence results

The frozen development split has 12 tasks: 10 answerable and 2 abstention controls. Each method received the same question and the same 10-item / 24,576-byte pack caps. Evidence spans were checked against the locked source files. Coverage means the share of gold-required behavior steps whose accepted source range intersects the delivered pack.

| Evidence condition | Answerable tasks with all required spans | Mean required-step coverage | Abstention controls with empty pack | Retrieval time |
| --- | ---: | ---: | ---: | ---: |
| Lexical `rg` | 3/10 | 0.30 | 0/2 | mean 0.140 s |
| R4 hybrid | 4/10 | 0.50 | 0/2 | mean 1.292 s |
| Sufficient pack | 10/10 | 1.00 | 2/2 | generated from adjudicated ranges |

The sufficient pack is an oracle diagnostic assembled directly from the frozen gold-accepted ranges; its generation time is not comparable to retrieval latency. The lexical runner's RSS field is a process high-water mark accumulated over the task loop, while R4 records per-query process RSS, so those memory figures are not directly comparable. The evidence results are development diagnostics and do not establish answer quality or select a product intervention.

## Development answer results

Answers used the same host Codex CLI agent (`gpt-6-luna`), one trial per task and evidence condition. Harbor's in-container Codex client could not connect to the model service, so the runner used isolated, read-only host CLI invocations over hash-checked gold-blind instructions. Grading used blinded batches with `gpt-6.1-sol` through Codex CLI. USD cost is unavailable from the subscription OAuth interface; token counts and calls are recorded.

Initial three-profile grade (`development-answer-report-v3.json`): lexical `rg` was fully correct on 3/12 tasks (including both abstention controls), R4 on 0/12, and sufficient-pack on 9/12. Sufficient-pack still missed required answer steps on `a04`, `d06`, and `n11`, indicating answer-side completeness issues even with sufficient evidence. R4 produced two unsupported-claim cases. Deterministic citation refresh resolved all 29 lexical, 34 R4, and 25 sufficient-pack citations to evidence in the corresponding pack. The original answers used 849,700 input and 14,056 output tokens across conditions; the first grade used 12 calls, 386,393 input and 6,240 output tokens.

The failure diagnosis (`development-failure-diagnosis-v1.json`) found 4 R4 fragment-selection/retention misses and 4 route-file-hit/fragment-miss cases; lexical had 9 selection losses and 2 acquisition misses. One dev-only selection candidate alternated ranked R4 snippets and lexical windows, removed overlapping ranges, and respected the 10-item / 24,576-byte pack caps. The candidate stayed at 4/10 answerable tasks with complete source coverage, equal to R4; mean required-step evidence coverage was 0.50. Combined measured retrieval latency was 1.433 s.

To limit spend, only candidate answers were newly generated; baseline answers were reused after matching common-instruction hashes. Candidate used 301,221 input and 4,764 output tokens. The combined four-profile batch grade used 12 `gpt-6.1-sol` calls, 442,007 input and 8,017 output tokens. In that same grade, candidate was fully correct on 3/12 tasks, equal to lexical and below sufficient-pack (9/12); it did not improve by two tasks over either baseline. It also introduced one unsupported-claim case (`d05`). Candidate was rejected at the development gate. Its citation-refreshed report is `development-candidate-answer-report-v2.json`; the gate remains unevaluated for validation.

Candidate artifacts: `development-packs-candidate-v2/` (manifest SHA-256 `2430eb8d33710acdfb5d93cb9217550c2d618f14d250d2baab7f60061e2a9c3e`; records strategy/version and raw candidate input hashes), `development-candidate-answer-pairs-host-codex-v1/`, `development-answer-pairs-with-candidate-v1/` (manifest SHA-256 `cacc929a27681cfebc8a5afce9924a2450a89b33e1784c5636805d374ad7a0bf`), and report SHA-256 `b62b91afbb89091443875e086e2c48e7aa99eed5180168728cdee30210b254e5`.

## Reproducibility artifacts

- Prompts: `development-prompts.json`; SHA-256 `4769a1eb44c8d39762a9566becb7a800b353bffdcba1ab583dbc6eef0bb9295a`.
- Lexical outputs and metrics: `development-lexical-rg/`; run manifest SHA-256 `635fbb77b8c37e7e5108b25ce22836e479089fa8b76298593b61bbe73deca77e`.
- R4 hybrid and route-pool outputs: `development-r4-hybrid/`; run manifest SHA-256 `a3d55dae1670699d37fb369104d2bf768fc6539789b8d954b53fe93831fb5f9e`.
- Normalized, source-verified three-profile packs: `development-packs-v8/`; manifest SHA-256 `e4ac6005b9ad7f1407f80ad1d50b6680027a94f90d2a04e2e08c46b01ab296af`.
- Matched gold-blind Harbor datasets: `development-harbor-v6/`; dataset manifest SHA-256 `2d0cfd4143220626f03d3a67190dd414468ed98573193c9593e0cae0870a51ef`.

`development-harbor-v6/datasets/` contains 12 task IDs under each of the three conditions. The common question/instruction prefix is hash-checked across conditions; only evidence differs. Gold, references, task type labels, and condition names are absent from task instructions and task metadata. The sufficient pack is author-curated from gold ranges; separate human semantic review has not been evidenced.

## Answer-run preflight

An earlier paired Harbor attempt with `custom-openai/glm-5.2` failed because Harbor's Codex container could not connect to the model service; no answer results from that failed job were used. The validated host Codex run above produced the development answer results. Validation has not run: the only tested candidate failed the development gate, and validation remains sealed against tuning.
