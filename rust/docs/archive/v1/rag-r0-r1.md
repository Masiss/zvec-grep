# RAG R0/R1 — Pilot freeze and evidence contract

Status: **R0 IN PROGRESS; R1 PASS for the contract and selected real-source slice.**
Date: 2026-11-04. Follows `docs/rag/README.md`,
`docs/rag/implementation-plan.md` and `docs/rag/hrm2-pilot.md`.

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

**R0 gate: INCOMPLETE.** Finish D01–D12 fact/claim-limit review and D13–D16
synthetic fixtures, then obtain owner acceptance of §9 targets before candidate
measurement.

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

1. Finish exact refs/facts for development cases D01–D12 and synthetic fixture
   definitions for D13–D16; keep them unfrozen until reviewed.
2. Ask the owner to accept or adjust `implementation-plan.md` §9 targets.
3. Do not run candidate quality/performance measurement until both R0 gates are
   satisfied. Only then consider the scoped C# fact adapter (R2); no graph store,
   background LLM, DB connection, build/restore or code execution.

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