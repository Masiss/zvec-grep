# HRM2.0 source-discovery reality check

Status: D01–D12 provenance verified; budgeted local evidence-pack comparison completed. No production/source-code change or model answer-quality run was made.

## Provenance and gold

Read-only lookup in Git ref work/r4-evidence-representation-20261119 found:

- benchmarks/rag-impact/cases/dev.json, SHA-256 27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311.
- rust/docs/r4-task-correctness-c3-checklist-20261120.md, SHA-256 2bd4c63dff8b6638cad7db72a37ceeaaa6fd1031acf5f0cd15e3ed92ae8751bb.
- rust/docs/r4-task-correctness-contract-20261118.md (the C1–C6 rubric), SHA-256 330c796d0e51ed5e02d8ca02f34a763dac06f6abd055eb4eee3ecc8488fe5e15.
- benchmarks/rag-impact/PREREGISTRATION.md is present with SHA-256 58e4592b54abe11a3f7c89e22625e654d6d90721b24140df10db9cec592b0450; rust/docs/r4-three-arm-preregistration-20261120.md is present with SHA-256 5d7cc900d3f4ade420ac144734c4389436e9db2d275c8c75e04c2d0fef44e067.
- The supplied C1–C6 digest matches the correctness-contract document. The separate benchmarks/rag-impact/cases/rubric.json has SHA-256 03bacb2626af560fcb0f1d012637cdacdecb50bb3428c78f8afbddaafe9432dc and is not that contract artifact.
- The R4 linked worktree is present and clean. Checked archives docs/research/bundles/source-discovery-autonomous-20261122-review.zip and research/hrm2-source-discovery-reality-check/review-bundle.zip; neither contains the D-case manifest or gold. The canonical files are available directly from the R4 Git ref.

All 12 D cases are marked frozen and have nonempty questions and expected-evidence descriptions. Their source_refs contain 22 file references and 25 line-range spans. The manifest's frozen source revision differs from the current checkout revision, but its frozen commit is available locally; every referenced file hash matches at both revisions, and every span is in bounds at both. H01–H08 were not opened or used.

This is source-span gold: each reference supplies a source file, file hash, and one or more line ranges. The case schema has prose expected_evidence but no structured declaration identities or caller→callee pairs. Source-span reachability is directly scoreable. Declaration coverage, caller-edge correctness, exhaustive false positives, and answer quality require independent adjudication.

The C1–C6 contract and C3 checklist describe correctness review criteria. Timing, RSS, bytes, query count, and exact gold-span overlap are directly measurable. Do not infer answer correctness or relation accuracy from retrieval overlap.

## Frozen rg baseline

Queries were derived locally from each frozen question only; expected evidence and source refs were not used as query terms.

- Identifier arm: distinct code-style tokens from the question; 10 queries across 7 cases.
- Lexical arm: distinct non-stopword terms of at least four characters; 51 queries across all 12 cases.
- Both used case-insensitive fixed-string whole-word rg -n, searching C# and SQL files and excluding build output and H01–H08 paths.
- A gold span counts as reached when a returned line is in its referenced file and within the frozen line range. Unmatched lines below are output noise relative to the provided spans, not adjudicated false positives. Output bytes sum each query's raw output, so repeated lines from different terms count more than once. Unique hit lines are deduplicated within each case and then summed; they are not globally deduplicated.

| Case | Gold spans | Identifier q / spans | Identifier lines / bytes | ID time | Lexical q / spans | Lexical lines / bytes | Lexical time |
|---|---:|---:|---:|---:|---:|---:|---:|
| D01 | 2 | 1 / 1 | 14,254 / 2,202,612 | 0.03 s | 4 / 2 | 3,790 / 792,262 | 0.05 s |
| D02 | 6 | 0 / 0 | 0 / 0 | 0.00 s | 2 / 0 | 720 / 140,907 | 0.04 s |
| D03 | 1 | 1 / 1 | 2,500 / 368,531 | 0.02 s | 5 / 1 | 6,219 / 940,083 | 0.10 s |
| D04 | 1 | 1 / 1 | 3 / 447 | 0.01 s | 4 / 1 | 8,712 / 1,448,256 | 0.04 s |
| D05 | 1 | 0 / 0 | 0 / 0 | 0.00 s | 8 / 0 | 7,659 / 1,275,794 | 0.13 s |
| D06 | 3 | 0 / 0 | 0 / 0 | 0.00 s | 4 / 2 | 4,453 / 888,944 | 0.08 s |
| D07 | 3 | 0 / 0 | 0 / 0 | 0.00 s | 6 / 1 | 5,830 / 1,140,880 | 0.12 s |
| D08 | 2 | 1 / 2 | 7 / 1,024 | 0.02 s | 3 / 2 | 4,526 / 797,898 | 0.06 s |
| D09 | 1 | 2 / 0 | 2,520 / 371,916 | 0.03 s | 2 / 0 | 2,520 / 371,916 | 0.04 s |
| D10 | 2 | 3 / 1 | 6,279 / 827,320 | 0.07 s | 4 / 1 | 4,624 / 622,243 | 0.08 s |
| D11 | 1 | 1 / 1 | 1 / 164 | 0.02 s | 3 / 1 | 511 / 110,926 | 0.06 s |
| D12 | 2 | 0 / 0 | 0 / 0 | 0.00 s | 6 / 0 | 1,972 / 378,319 | 0.11 s |

Aggregate: identifier search reached 7/25 annotated spans (28%) with 25,564 per-case-deduplicated hit lines and 3.77 MB output; lexical-term search reached 11/25 (44%) with 51,536 per-case-deduplicated hit lines and 8.91 MB output. Median per-case time was 0.015 s and 0.07 s respectively; maximum measured RSS was 11,080 KiB and 11,980 KiB. Lexical terms improved annotated-span reachability but generated substantial noise. These are not semantic recall, false-positive rates, answer quality, or blind-agent scores.

Raw per-query outputs, question-derived terms, HRM source paths, exact source revision, and source-file hashes are stored only in local authorized receipts. Shared non-sensitive repo-relative artifact paths, the named ref, manifest/rubric digests, match states, and sanitized diagnostics are included under the user's explicit allowlist.

## Roslyn capability on the declared source root

The source root has 4,656 C# files, 51 project files, and one solution. Roslyn MSBuildWorkspace ran without restore/downloads:

- 58 solution entries; 45 projects loaded and compiled.
- 3,269 documents/syntax trees; 82,391 invocation sites, 9,038 symbol-bound.
- 367,460 aggregate compilation errors, 162 warnings, and 241 workspace failure/warning events.

This supports partial project-aware navigation but is not a whole-solution semantic oracle. No caller-edge correctness score is available because the frozen cases do not encode edge gold.

## Budgeted delivered-evidence comparison

The R4 contract budget was applied per case: ≤6 calls, ≤3 rounds, ≤32 opened source files, ≤10 evidence items, ≤24,000 bytes per pack, and ≤64,000 bytes of query output plus pack. The frozen question-only lexical terms were used; output order was fixed by `rg --sort path`. Each arm used one search round and one pack round (2 measured operations), opened 10 files per case, and produced 10 items. Corpus scanning covered 4,656 C# files plus SQL files. Packs and raw source outputs remain only in local private receipts.

Three non-pooled comparisons were run. The first capped raw `rg` stream returned full source lines; both first-hit and term-overlap/rarity ranking reached 0/25 spans. Two exploratory follow-ups used a streaming local projector that emitted only path, line, and matched tokens before the same 36,000-byte cap. One used first-hit order; the other added `--max-count 1` per file. The latter reached 0/25. The compact term-overlap/rarity arm reached 1/25 (one span in D11); its first-hit baseline remained 0/25. The earlier invalid `rg --json --only-matching` attempt was excluded because JSON retained full source lines.

| Case | Gold spans | Full-line cap: raw reachable | Compact first-hit pack | Compact ranked pack | Diverse compact ranked pack |
|---|---:|---:|---:|---:|---:|
| D01 | 2 | 0 | 0 | 0 | 0 |
| D02 | 6 | 0 | 0 | 0 | 0 |
| D03 | 1 | 0 | 0 | 0 | 0 |
| D04 | 1 | 0 | 0 | 0 | 0 |
| D05 | 1 | 0 | 0 | 0 | 0 |
| D06 | 3 | 0 | 0 | 0 | 0 |
| D07 | 3 | 0 | 0 | 0 | 0 |
| D08 | 2 | 0 | 0 | 0 | 0 |
| D09 | 1 | 0 | 0 | 0 | 0 |
| D10 | 2 | 0 | 0 | 0 | 0 |
| D11 | 1 | 0 | 0 | 1 | 0 |
| D12 | 2 | 0 | 0 | 0 | 0 |
| **Total** | **25** | **0** | **0** | **1** | **0** |

Across the original capped raw stream, query results themselves reached 0/25 spans. In the compact stream, query acquisition reached 1/25 in D11; ranked packing delivered that same one span. Thus the other 24 misses are acquisition failures under these query/output caps, not ranking misses. The original unbounded lexical arm reached 11/25 spans but produced 8.91 MB; the capped evidence-pack workflow did not recover any case that had zero spans in the unbounded identifier and lexical baselines (D02, D05, D09, D12).

| Arm (per 12 cases) | Query output bytes (sum) | Pack bytes (sum; median; max) | Max query + pack | Median / max latency per case | Batch peak RSS | Delivered spans | Unadjudicated non-gold pack lines |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full-line, first-hit | 429,944 | 80,167; 6,267; 9,410 | 45,254 | 0.0136 / 0.0215 s | 22,904 KiB | 0/25 | 893 |
| Full-line, ranked | 429,944 | 100,990; 8,476; 10,350 | 46,295 | 0.0136 / 0.0215 s | 22,904 KiB | 0/25 | 1,016 |
| Compact, first-hit | 431,400 | 80,167; 6,267; 9,410 | 45,384 | 0.0237 / 0.0368 s | 23,148 KiB | 0/25 | 893 |
| Compact, ranked | 431,400 | 163,230; 11,985; 23,477 | 59,451 | 0.0237 / 0.0368 s | 23,148 KiB | 1/25 | 1,417 |
| Compact + per-file max-count, ranked | 416,909 | 42,153; 3,490; 4,748 | 40,719 | 0.0257 / 0.0803 s | 22,964 KiB | 0/25 | 360 |

The unadjudicated non-gold counts are pack lines outside annotated gold ranges; they are noise indicators, not proven false positives. Compact ranked packing added one span while increasing pack size and noise relative to the matched first-hit arm. The per-file diversity cap reduced noise and bytes but did not reach a gold span. Every per-case cap passed; max packed bytes were 23,477, max cumulative content 59,451, and source reads were 10 per arm/case (120 across each arm). The public Avalonia negative control was processed with the same question-derived terms without gold; it yielded 1,400 compact match records / 114,769 projected bytes and median packs of roughly 2.5–3.0 KB. This detects that the query emits nontrivial public-code output but cannot score correctness.

No HRM source paths, questions, identifiers, snippets, or raw receipts are in this shared report. The per-case table reports only opaque case IDs and annotated-span counts.

## Decision and next step

**NO_GO for adopting or further developing this rg-first ranked-pack workflow.** Under the fixed per-case evidence budget, the best arm delivered 1/25 spans and did not make any previously evidence-empty case reachable. The single gained span was already reachable by unbounded lexical `rg`; the ranking arm also produced more pack noise and larger packs. The 24 missed spans were absent from the compact query stream, so evidence selection alone cannot solve the main acquisition failure. The simpler first-hit pack had no delivered spans.

This decision is scoped to the tested local lexical acquisition and packing workflow; it does not establish that all local-first discovery methods lack value. The existing Roslyn probe remains only partial capability evidence (45/58 solution entries loaded/compiled, with high aggregate errors); the frozen cases have no structured declaration/edge gold, so no valid Roslyn relation score or answer-quality comparison is available. There is no source diff or experimental implementation to adopt, so no product-code rollback is needed. Optional cleanup is to delete the one-off scripts and private receipts from local temporary storage. To revert repository documentation, remove this report/protocol bundle and this appended handoff section; preserve unrelated dirty artifacts. No production, source, index, embedding, defaults, API, or frozen benchmark state changed. Keep Tree-sitter C# at the prior NO_GO.
