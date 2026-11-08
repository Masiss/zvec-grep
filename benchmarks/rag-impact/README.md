# RAG Impact — R0/R1 artifacts

Status: **R0 FROZEN; official B0 measured; candidate pending. Targets, corpus and all 16 development cases frozen; D13–D16 fixtures shape-validated. R1 contract and selected real-source slice PASS.**

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
- R0 is frozen: owner targets, the B0 corpus, all 16 development cases and the
  official B0 receipt are recorded. D13–D16 fixture shapes are validated; their
  engine behavior is pending R2/R4. Candidate measurement is not yet run.
- R2 bounded C# facts are implemented (`rust/docs/rag-r2-csharp-facts.md`);
  synthetic and real-case C# fact gates PASS; SQL-dependent cases are R3.
- Official B0 (`results/b0-official.md`): dev recall@10 0.4722, held-out 0.3958,
  search p95 63.4 ms, idle PSS 571,800 KiB, peak PSS 769,113 KiB, 0
  RESOURCE_BUSY, cold index median 6.31 s. Read the limitations before reusing
  these numbers; task correctness is a deterministic evidence proxy.
- Targets are blind and frozen before measurement. They are not adjusted after
  seeing B0 or candidate results; hard gates and optimization goals are separated
  in `PREREGISTRATION.md`.

## Files

| File | Purpose |
|---|---|
| `scope-manifest.json` | Selected projects/anchors, current observed HEAD, source hashes, D01–D12 source spans, frozen target/corpus references. |
| `PREREGISTRATION.md` | Owner targets, hard gates vs optimization goals, B0 definition, metric formulas and measurement protocol. |
| `corpus-manifest.json` | Frozen B0 corpus: 822 files, 5,400,637 bytes, with per-file SHA-256; produced by `tools/freeze_corpus.py`. |
| `REPORT-CONTRACT.md` | Three conclusion classes, citation rules, budget stop conditions and forbidden behavior. |
| `cases/dev.json` | The 16 frozen development cases; D01–D12 facts/claim limits and D13–D16 synthetic fixtures. |
| `cases/synthetic/` | D13–D16 synthetic fixtures and expected outcomes; shape-validated, engine behavior pending R2/R4. |
| `cases/heldout.json` | Reviewer-authored H01–H08 with source hashes/spans; no candidate measurement has run. |
| `results/r1-pilot-slice.json` | Bounded real-source evidence packs and revalidation receipt for D02/D07 plus negative twins. |
| `results/b0-official.json` / `.md` | Official B0 baseline receipt and its limitations. |
| `schema/evidence-pack.schema.json` | JSON shape for R1 evidence packs. |
| `REPORT-CONTRACT.md` | Three conclusion classes and citation rules for an external agent. |

## R0 remaining checklist

1. **DONE** — revalidated D01–D12 facts/claim limits against the observed HEAD;
   source hashes and spans match and the cases are frozen.
2. **DONE (shape)** — D13–D16 synthetic fixtures authored and shape-validated;
   engine behavior against these fixtures remains an R2/R4 gate.
3. **DONE** — owner accepted and froze the numeric targets on 2026-11-08; see
   `PREREGISTRATION.md`. Targets are not adjusted after measurement.
4. **DONE** — official B0 measured with the frozen corpus, cases and protocol;
   receipt and limitations in `results/b0-official.md`.

After those gates, R0 can be frozen. The R1 contract and current evidence-pack
smoke test do not evaluate retrieval quality or performance.

Do not use `NewSolution` or an unselected `_old` SQL definition as authority.
No database, MSBuild/restore, code execution or live deployment inference.
