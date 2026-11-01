# RAG R0/R1 — Freeze status and evidence contract

Status: **R0 OPERATION_BLOCKED; R1 PASS for the synthetic contract slice.**
Date: 2026-11-01. Follows `docs/rag/README.md`,
`docs/rag/implementation-plan.md` and `docs/rag/hrm2-pilot.md`.

## R0 — freeze

The declared pilot is `/home/masis/itc/Ricons/HRM2.0` under checkout root
`/home/masis/itc/Ricons/`. Neither path exists on this host. Without the
read-only checkout I cannot:

- verify HEAD `d5e8fbcdd49b48c208887096993074464c793cd7`;
- hash the selected `.csproj`/C#/SQL anchors;
- verify the line ranges from the pilot notes;
- freeze the 16 development cases or have a reviewer author H01–H08.

Artifacts are in `benchmarks/rag-impact/`: `scope-manifest.json` records the
blocker with `null` hashes, `cases/dev.json` keeps the proposed cases marked
unfrozen, and `cases/heldout.json` leaves the reviewer-owned holdout empty. No
hash, gold span or expected answer was fabricated.

**R0 gate: OPERATION_BLOCKED.** Restore the checkout, then fill hashes/spans and
complete the case review before any candidate measurement.

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
- Same relative names in different projects remain separate items.
- Empty request list is `empty`; a zero budget is `EvidenceError::InvalidBudget`
  (query error, not an empty result).

### Tests

`cargo test -p zg-engine --lib api::evidence` — **8 passed**:

- Unicode/codepoint boundaries and exact bytes;
- CRLF and EOF coordinates, locator mismatch;
- traversal, absolute path, symlink;
- stale hash, missing/renamed source;
- same name in two projects;
- snippet/item budget and dedup;
- empty result vs invalid-budget error;
- revalidation after source change.

### Limits

- This does not search, rank or parse C#/SQL; it validates evidence supplied by
  the caller or a later retrieval adapter.
- It does not decide project inclusion; the R0 manifest must supply project
  identity and scope.
- The pilot has not exercised it. R1 pilot validation remains pending R0.
- No cache/version key is persisted yet; callers revalidate per final answer.

## Next

1. Restore the read-only pilot and complete R0 freeze (hashes, spans, holdout).
2. Run D02/D07 plus negative twins D04/D06/D12 through the evidence pack.
3. Only then add the C# fact adapter (R2). Do not open a new optimization track.
