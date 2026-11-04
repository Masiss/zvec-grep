# RAG Impact — R0/R1 artifacts

Status: **R0 IN PROGRESS; R1 contract and selected real-source slice PASS.**

- The read-only pilot exists at `/home/masis/itc/Ricons/HRM2.0`; checkout root
  `/home/masis/itc/Ricons/`, observed HEAD
  `68a53a6865b81d05745c4aa47967204e8e128996`. The selected tracked anchors are
  clean and byte-identical to the previously declared HEAD. Unrelated modified
  and untracked checkout files are excluded.
- Selected project/source hashes are recorded in `scope-manifest.json`.
  H01–H08 were authored by an independent Sol 6.1 reviewer from selected source
  anchors, then parent-validated against hashes, spans and snippets. Reviewer
  was given excluded-topic themes to prevent overlap, so it was independent of
  candidate tuning but not blind to all dev topics.
- R1 is implemented in `rust/crates/zg-engine/src/api/evidence.rs`. The focused
  8-test contract suite passes. A real-source pack for D02/D07 and negative
  twins D04/D06/D12 passed assembly and revalidation; receipt:
  `results/r1-pilot-slice.json`.
- R0 remains incomplete: D01–D12 source refs/spans are pinned, but detailed
  facts/claim limits need review; D13–D16 synthetic fixtures are not authored;
  numeric targets in `docs/rag/implementation-plan.md` §9 need owner acceptance
  before candidate measurement.

## Files

| File | Purpose |
|---|---|
| `scope-manifest.json` | Selected projects/anchors, current observed HEAD, source hashes, and D01–D12 source spans. |
| `cases/dev.json` | The 16 development cases, re-anchored but still unfrozen pending exact gold refs and synthetic fixtures. |
| `cases/heldout.json` | Reviewer-authored H01–H08 with source hashes/spans; no candidate measurement has run. |
| `results/r1-pilot-slice.json` | Bounded real-source evidence packs and revalidation receipt for D02/D07 plus negative twins. |
| `schema/evidence-pack.schema.json` | JSON shape for R1 evidence packs. |
| `REPORT-CONTRACT.md` | Three conclusion classes and citation rules for an external agent. |

## R0 remaining checklist

1. Revalidate detailed facts/claim limits for D01–D12 against the observed
   HEAD (source hashes and spans are already pinned).
2. Author and test explicit synthetic fixtures for D13–D16; do not pass them on
   descriptive expected text alone.
3. Accept or adjust the numeric targets in `docs/rag/implementation-plan.md` §9
   **before** any candidate measurement.

After those gates, R0 can be frozen. The R1 contract and current evidence-pack
smoke test do not evaluate retrieval quality or performance.

Do not use `NewSolution` or an unselected `_old` SQL definition as authority.
No database, MSBuild/restore, code execution or live deployment inference.
