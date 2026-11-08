# RAG R0/R1 — Pilot freeze and evidence contract

Status: **R0 FROZEN; official B0 measured; candidate pending. Targets, B0 corpus
and all 16 development cases frozen; D13–D16 fixtures shape-validated. R1 PASS
for the contract and selected real-source slice.**
Date: 2026-11-08. Follows `docs/rag/README.md`,
`docs/rag/implementation-plan.md` and `docs/rag/hrm2-pilot.md`.

Owner blind targets were frozen on 2026-11-08 in
[`../../benchmarks/rag-impact/PREREGISTRATION.md`](../../benchmarks/rag-impact/PREREGISTRATION.md).
The frozen B0 corpus is
[`../../benchmarks/rag-impact/corpus-manifest.json`](../../benchmarks/rag-impact/corpus-manifest.json)
(822 files, 5,400,637 bytes). Targets are not adjusted after B0 or candidate
measurement; hard gates and optimization goals are listed separately.

## R0 — current freeze state

The declared pilot exists at `/home/masis/itc/Ricons/HRM2.0`, under Git root
`/home/masis/itc/Ricons/`. Observed HEAD is
`68a53a6865b81d05745c4aa47967204e8e128996`, different from the originally
planned `d5e8fbcdd49b48c208887096993074464c793cd7`. All selected tracked anchor
files are clean and byte-identical between those HEADs. The surrounding tree
has unrelated dirty/untracked files, explicitly excluded from this pilot.

`benchmarks/rag-impact/scope-manifest.json` records the observed root/HEAD,
SHA-256 for every selected project/source anchor, and exact spans for D01–D12.
It intentionally does not claim R0 frozen: detailed facts/claim limits for
D01–D12 remain under review, D13–D16 synthetic fixtures are not authored, and
the owner has not accepted or adjusted the numeric targets in
`docs/rag/implementation-plan.md` §9. No candidate quality or performance
measurement has been run.

An independent Sol 6.1 reviewer authored H01–H08 from selected source anchors;
parent verification confirms all hashes, line spans and 45 quoted snippets.
The reviewer did not read the dev-case file or candidate artifacts. Parent
provided excluded-topic themes to avoid duplication, so the author was
independent of candidate tuning but was not blind to all development topics.
No candidate measurement was performed.

**R0 gate: FROZEN.** D01–D12 facts/claim limits are reviewed and frozen; owner
targets are accepted; the B0 corpus is frozen; D13–D16 synthetic fixtures are
authored and shape-validated; official B0 is measured. D13–D16 engine behavior
remains an R2/R4 gate before claiming those relation cases pass.

Official B0 (development/held-out): recall@10 0.4722 / 0.3958; search p95
63.4 ms; idle PSS 571,800 KiB; peak PSS 769,113 KiB; 0 RESOURCE_BUSY; cold index
median 6.31 s. Task correctness is a deterministic evidence proxy, not answer
quality. Receipt: `benchmarks/rag-impact/results/b0-official.md`.

## R1 — evidence/source pack

Implemented in `rust/crates/zg-engine/src/api/evidence.rs` (`pub mod
api::evidence`). It is an internal engine contract; no MCP/CLI response shape or
search default changed.

### API

- `EvidenceRequest` — project, relative path, expected whole-file SHA-256,
  half-open UTF-8 byte range, optional expected start/end line.
- `EvidenceBudget` — `max_bytes`, `max_items`.
- `assemble(root, requests, budget) -> Result<EvidencePack, EvidenceError>`.
- `EvidencePack` — schema version, `ok`/`partial`/`empty`/`failed` status,
  validated `EvidenceItem`s, typed `EvidenceGap`s and a dedup count.
- `revalidate(root, pack)` — rechecks hashes and ranges before a final answer.

Items reuse `ContentRange::Text` (1-based lines, 0-based UTF-8 byte columns) and
carry the source hash. Files are read and hashed once per canonical path; exact
duplicate requests are collapsed.

### Fail-closed behavior

- `../`, absolute paths, symlinks, canonical escapes and missing files are gaps.
- Hash mismatch is `stale_hash`; a locator mismatch is `stale_coordinates`.
- Invalid UTF-8 and ranges that split a codepoint are gaps.
- Snippet/item budget overflow is a gap; the pack never silently truncates.
- A source changed after assembly fails `revalidate`.
- Same relative names in different projects stay separate items.
- Empty request list is `empty`; a zero budget is `EvidenceError::InvalidBudget`
  (query error, not an empty result).

### Verification and pilot slice

- `cd rust && TMPDIR=/home/masis/src/zvec-grep/.tmp cargo test -p zg-engine --lib api::evidence`
  — **8 passed, 0 failed**. Build emitted unrelated dead-code/unused-import
  warnings in `storage`.
- A temporary integration harness exercised D02, D07 and negative twins D04,
  D06, D12 against the real selected HRM2.0 checkout, with 24 KiB per-case
  evidence budgets. All five `assemble` packs were `ok`; all 14 evidence items
  passed `revalidate`. Receipt: `benchmarks/rag-impact/results/r1-pilot-slice.json`.
  The temporary harness was removed after execution.
- A first pilot run exposed a malformed 65-character S1 hash in the manifest;
  corrected against the source and re-ran successfully. This is recorded as
  validation history, not hidden.

### Limits

- This does not search, rank or parse C#/SQL; it validates evidence supplied by
  the caller or a later retrieval adapter.
- It does not decide project inclusion; R0 supplies project identity and scope.
- These packs validate selected citations, not retrieval quality, answer quality,
  performance, deployment state, database behavior, or complete impact.
- No cache/version key is persisted yet; callers revalidate per final answer.

## Next

1. R2 bounded C# facts are implemented and pass both the synthetic slice and the
   real-case gate for the C# fact subset (D01–D06/D08/D10/D11): see
   [`rag-r2-csharp-facts.md`](rag-r2-csharp-facts.md). D07/D09/D12 SQL facts are
   R3; D15 revalidation is R4.
2. B0 is measured and frozen; use the receipt as the baseline for candidate
   comparison. Do not adjust targets or the corpus after seeing candidate output.
3. R3 SQL facts are implemented and the real selected-anchor gate passes:
   see [`rag-r3-sql-facts.md`](rag-r3-sql-facts.md).
4. R4 bounded expansion pilot **FAILED** the frozen benefit targets: recall
   regressed, reads increased, only tokens improved. No candidate GO; keep the
   R1 lightweight mode. See [`rag-r4-candidate-pilot.md`](rag-r4-candidate-pilot.md).
5. Keep the held-out split uninspected by the implementer; run it mechanically
   and report it separately from the development split.

## Evidence

- `benchmarks/rag-impact/scope-manifest.json`
- `benchmarks/rag-impact/cases/dev.json`
- `benchmarks/rag-impact/cases/heldout.json`
- `benchmarks/rag-impact/results/r1-pilot-slice.json`
- `benchmarks/rag-impact/results/r1-contract.json`
- `benchmarks/rag-impact/README.md`
- `docs/rag/implementation-plan.md`

No R2 implementation or production rollout is authorized by this checkpoint.
```