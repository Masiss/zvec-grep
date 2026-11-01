# RAG Impact — R0/R1 artifacts

Status: **R0 OPERATION_BLOCKED; R1 evidence contract implemented and tested
with synthetic fixtures.**

- R0 needs the read-only pilot checkout. The declared path is
  `/home/masis/itc/Ricons/HRM2.0` (checkout root `/home/masis/itc/Ricons/`), but
  neither path exists on this host. Source hashes, line ranges and
  reviewer-selected held-out cases are therefore **not frozen**. Nothing here
  invents them.
- R1 is implemented in `rust/crates/zg-engine/src/api/evidence.rs`. It assembles
  a bounded, hash-checked pack and revalidates citations before a final answer.
  The pilot still has to exercise it once the checkout is restored.

## Files

| File | Purpose |
|---|---|
| `scope-manifest.json` | Selected projects/anchors, exclusions, declared HEAD, blocker. Hashes are `null` until the checkout exists. |
| `cases/dev.json` | The 16 proposed development cases from `docs/rag/hrm2-pilot.md`, marked unfrozen. |
| `cases/heldout.json` | H01–H08 placeholders. A reviewer independent of tuning must author and freeze them. |
| `schema/evidence-pack.schema.json` | JSON shape for R1 evidence packs. |
| `REPORT-CONTRACT.md` | Three conclusion classes and citation rules for an external agent. |

## R0 completion checklist

1. Restore/mount the read-only checkout at the declared path.
2. Verify HEAD `d5e8fbcdd49b48c208887096993074464c793cd7` (or record the new
   reviewed HEAD and re-anchor every path).
3. Fill every source hash and exact span in `scope-manifest.json`.
4. Revalidate the 16 development cases; record facts/spans and forbidden claims.
5. Have an independent reviewer author and freeze H01–H08.
6. Accept or adjust the numeric targets in `docs/rag/implementation-plan.md` §9
   **before** any candidate measurement.

Do not use `NewSolution` or an unselected `_old` SQL definition as authority.
No database, MSBuild/restore, code execution or live deployment inference.
