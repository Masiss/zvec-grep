# HRM2.0 source-discovery evaluation protocol

Status: protocol frozen; D01–D12 provenance and source-span gold verified; initial local `rg` baseline measured. No candidate code was optimized.
Date: 2026-11-22.

## Scope

- Source checkout was clean at intake. Its exact revision is retained only in local-only receipts and is withheld from the shared review bundle.
- Development cases: D01–D12 only. H01–H08 are excluded.
- No production source, index, embedding, default, API, or benchmark changes.
- All source processing stays local. Only aggregate counts and opaque case IDs may leave the source checkout.
- For this targeted lookup, the user explicitly permits non-sensitive repository-relative artifact paths (including the case manifest), the named Git ref, their digests, match states, and sanitized diagnostics in the report. HRM source paths, source revision, identifiers, snippets, and raw receipts remain local-only.

## Frozen comparison

1. `rg` literal/lexical search is the baseline.
2. Roslyn `MSBuildWorkspace` is the semantic reference only for projects that load and produce sufficiently complete compilations. Syntax-only or error-heavy compilations do not count as semantic ground truth.
3. Existing zvec retrieval is a candidate only if an already-existing local index can be queried read-only. No index repair, rebuild, or refresh is permitted.
4. Do not tune any candidate or change the task set after seeing per-task scores.

## Scoring

- Entry points and declarations: exact symbol identity and source-span coverage; report extra matches separately.
- Partial types: expected declaration locations found / expected locations, including every partial declaration.
- Caller relationships: exact caller→callee symbol pairs; report TP, FP, FN, and unresolved target-site coverage separately. Resolved-edge precision is not coverage.
- Report per-task false positives/noise, bytes, tool calls, wall time, and peak RSS.
- Do not use answer quality as a proxy for retrieval success.

## Freeze integrity gate

Before candidate scoring, require a verifiable manifest for all D01–D12 prompts and expected outcomes, plus hashes for the source revision and task/gold files. If that manifest is unavailable, report capability diagnostics only and leave the task-level decision `INCONCLUSIVE`. Do not substitute synthetic tasks or open H01–H08.

## Provenance lock — resolved from the R4 ref

- `benchmarks/rag-impact/cases/dev.json` is present at `work/r4-evidence-representation-20261119` and matches SHA-256 `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311`.
- The frozen file has 16 cases; D01–D12 are all present, marked frozen, and have nonempty questions and expected-evidence descriptions. Their source references contain 22 file refs and 25 line-range spans.
- The manifest source revision differs from the current checkout HEAD. Its frozen commit exists locally. All 22 referenced-file hashes match both that frozen revision and current HEAD, and all 25 spans are in bounds at both revisions. Exact source revision and source paths remain in local-only receipts.
- The source-reference schema gives exact path/hash/line-span evidence. It does not provide structured declaration identities or caller→callee edge pairs. Do not score those as gold from prose alone.
- The C1–C6 task-correctness contract and the C3 checklist match their supplied historical hashes. The C1–C6 contract is the rubric; the separate `cases/rubric.json` is not that historical rubric artifact.

## Frozen local measurement

The initial local measurements after provenance verification are limited to the existing tools:

1. `rg` literal search over code-style identifiers extracted locally from each frozen question; use all distinct anchors, case-insensitive, fixed-string, whole-word, C# and SQL files only. The identifier pattern is `(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]*|[A-Z][A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*|[A-Za-z_]*\d[A-Za-z0-9_]*)(?![A-Za-z0-9_])`.
2. `rg` lexical-term scan over the question's distinct lowercased non-stopword terms of length at least four; use fixed-string whole-word search and keep this result separate as a noise/recall diagnostic. The stopword set and actual query terms stay in local-only receipts.
3. Roslyn/MSBuildWorkspace on the source root declared by the frozen manifest, with no package restore. Treat only compilations without relevant errors as a semantic reference.

The sanitized `rg` command shape is `rg --no-heading --line-number --color never --ignore-case --fixed-strings --word-regexp --glob '*.cs' --glob '*.sql' --glob '!**/bin/**' --glob '!**/obj/**' --glob '!**/H01/**' ... --glob '!**/H08/**' <QUESTION_DERIVED_TERM> <SOURCE_ROOT>`. Raw terms, root, and result lines remain local-only.

For the two `rg` arms, deduplicate hit lines within each D case; aggregate unique-line counts sum those per-case counts. Score annotated source-span overlap, missed gold spans, output bytes, wall time, and peak RSS per D case. Report unmatched output as noise; do not call it false-positive gold without adjudication. Declaration identity, caller-edge correctness, and answer quality remain separate measures requiring independent adjudication because the frozen case schema has no structured gold for them. No source/code candidate optimization will precede these baseline runs. These are deterministic local lexical diagnostics, not the complete preregistered three-arm experiment or a blind agent trial.

## Budgeted evidence-pack comparison

Use the canonical workflow budget from the R4 preregistration: at most 6 tool calls, 3 rounds, and 32 distinct source reads per case; final pack at most 24,000 bytes, cumulative tool content at most 64,000 bytes, and at most 10 evidence items. For this paired comparison, freeze a 36,000-byte maximum for the single `rg` result stream and a 24,000-byte maximum for each arm's final pack. `rg --sort path` fixes result ordering. The paired arms are evaluated separately under that same budget; the shared query/result stream is counted once per arm. The packer may open no more than 10 distinct source files, uses no more than 10 evidence items, and makes one query round plus one packing round (two measured tool operations; scoring is post hoc). Count files actually opened to construct the pack as source reads; report the broader corpus scan separately. Per arm, measured cumulative content is the actual query/result bytes plus final pack bytes and must remain at or below 64,000 bytes; the unused 4,000-byte margin covers bounded call metadata. If any cap is reached mid-record, discard that partial record and stop adding evidence.


The query is the frozen case's distinct lexical terms (lowercased, non-stopword, length ≥4) only. No gold fields may be read before both arms' packs are fixed and hashed. Arm (a) takes first occurrences of distinct files in `rg` result order. Arm (b) ranks distinct files by descending number of distinct query terms matched, then descending sum of inverse document frequencies `log((N+1)/(df+1))` over matched terms, then original first-hit order; `N` is the number of distinct files represented in the capped result and `df` counts those files containing each term. Within a selected file, both arms include the matching result lines and the same bounded context (one preceding and one following source line per hit), deduplicate line ranges, and serialize evidence with path, line numbers, and text. They may skip a file that cannot fit as a whole item, then consider the next ranked file. These deterministic rules and caps are frozen before scoring. Run the same packer on one public negative-control corpus with no gold, and do not use it as a substitute for D-case results. Report delivered annotated-span reachability, missed spans, pack/raw bytes, tool calls, files opened, latency, RSS, and unmatched output lines as unadjudicated noise. Do not claim answer quality or declaration/edge correctness from this comparison.

### Follow-up diagnostic: compact acquisition

The first capped-result run returned zero gold spans in raw `rg` output for all 12 tasks, so its primary failure was acquisition under output truncation; ranking could not help. A separate exploratory follow-up tests that diagnosis without changing the original result: retain the frozen question terms, caps, file ranking, line context, and scoring rule, but add `rg --only-matching` so the capped stream contains matching tokens with file/line coordinates rather than full source lines. This is explicitly post-result exploratory evidence, not a replacement for or pooled estimate with the first comparison. Freeze/hashes for the compact-stream packs must precede scoring. A source file is opened only after ranking, at most 10 per arm. The same public negative-control corpus is run without gold.

The initial compact-stream attempt was invalid as a compact-output test: `rg --json --only-matching` still included full source lines in each JSON record. Those outputs are excluded from findings. The corrected diagnostic streams `rg --json` records through a local projector which emits only relative path, line number, and matched substrings; the 36,000-byte cap applies to those projected records delivered by the query operation. A second variant adds `--max-count 1` per file. Both are separate exploratory results, retain the frozen terms and pack rules, and use gold only after packs are fixed. Stop after these corrected diagnostics; do not tune further against D-case spans.
