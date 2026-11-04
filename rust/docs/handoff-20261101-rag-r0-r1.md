# Handoff — RAG R0/R1 (updated 2026-11-04)

Storage integrity is closed; do not reopen it or start unrelated optimization.
Active scope is completing the R0 source/case freeze and R1 evidence validation.

## State

| Item | State |
|---|---|
| Storage integrity W4 | **PASS; closed; installed locally** (`ed8d23d`) |
| RAG R1 evidence contract | **PASS**: 8 focused tests on synthetic fixtures |
| R1 pilot slice | **PASS**: D02/D07 and negative twins D04/D06/D12; 5 packs / 14 items assembled and revalidated |
| R0 source checkout | **Available and re-anchored**; observed HEAD differs from planned HEAD, selected anchors are clean and unchanged |
| R0 development split | **INCOMPLETE**: D01–D12 exact refs/facts not all frozen; D13–D16 synthetic fixtures not authored |
| R0 held-out split | H01–H08 independently authored and parent-validated; reviewer disclosure recorded |
| Numeric targets | **OWNER DECISION REQUIRED** before candidate measurement |
| Candidate quality/performance measurement | **Not run** |

## Source checkout and scope

- Pilot root: `/home/masis/itc/Ricons/HRM2.0`
- Git root: `/home/masis/itc/Ricons`
- Observed HEAD: `68a53a6865b81d05745c4aa47967204e8e128996`
- Planned HEAD in the original prereg: `d5e8fbcdd49b48c208887096993074464c793cd7`.
  The selected tracked anchor files compare byte-identically at both revisions;
  manifest hashes pin the observed HEAD.
- Checkout has unrelated modified/untracked files (`HRM2.0/DB/cc.txt`,
  `HRM2.0/Database/`, and root `Database_EVIDENCE_*` docs). They are excluded;
  do not read/use them for the pilot or modify them.
- No DB access, build/restore, code execution in the HRM2.0 project, deployment
  inference, source writes, index writes, or live changes occurred.

## Artifacts updated

- `benchmarks/rag-impact/scope-manifest.json` — observed root/HEAD and hashes
  for selected projects and source anchors. One initial S1 checksum was
  malformed (65 chars), detected by real R1 assembly, corrected to the exact
  64-character SHA-256, then revalidated.
- `benchmarks/rag-impact/cases/heldout.json` — H01–H08 authored by a fresh
  `openai-codex/gpt-6.1-sol` reviewer from selected anchors. The reviewer did
  not inspect dev cases or candidate artifacts. Parent supplied excluded-topic
  themes to reduce overlap, so this was independent of candidate tuning but not
  fully blind to all dev topics. Parent validated 8 case records, 45 snippets,
  hashes and spans against the current files. No measurement was run.
- `benchmarks/rag-impact/cases/dev.json` — current HEAD/status recorded;
  proposals remain unfrozen until gold refs/facts and synthetic fixtures are
  complete.
- `benchmarks/rag-impact/results/r1-pilot-slice.json` — five real-source packs,
  14 items, all status `ok`, all revalidated, per-case 24 KiB budget.
- `benchmarks/rag-impact/README.md` and `rust/docs/rag-r0-r1.md` updated.
- Temporary R1 integration test was removed after execution.

## Verification

```sh
cd /home/masis/src/zvec-grep/rust
export TMPDIR=/home/masis/src/zvec-grep/.tmp
cargo test -p zg-engine --lib api::evidence
```

Result: **8 passed, 0 failed**. Existing `storage` unused/dead-code warnings
remain. A temporary integration harness also passed:
`cargo test -p zg-engine --test rag_pilot_r1_tmp` — **1 passed**; test source
removed, receipt retained at `benchmarks/rag-impact/results/r1-pilot-slice.json`.

The first real-source run failed closed on S1's malformed expected hash; the
mismatch was corrected and the focused rerun passed. Do not erase this defect
from the evidence history.

## Exact next authorized work

1. Revalidate all D01–D12 facts, add exact source refs/spans/hash and forbidden
   claims; distinguish explicit gaps from unsupported negative claims.
2. Author bounded synthetic fixtures and expected outcomes for D13–D16.
3. Ask owner to accept or adjust the proposed targets in
   `docs/rag/implementation-plan.md` §9. Until that decision, do not run
   candidate quality/performance measurements.
4. Once those gates are complete, mark R0 frozen and only then execute the next
   preregistered candidate-evaluation slice. R2 is not authorized yet.

## V2 recovery port (2026-11-04)

The R1 evidence API and RAG artifacts are being ported to the released V2
recovery branch. The historical receipt above records the original V1 run; it
is not evidence that the Rust tests or real-source pilot were rerun on V2. A
V2-focused test run validates only the portable evidence API against V2's
`ContentRange` coordinate representation. No HRM2.0 source was read or
revalidated during this port, no pilot receipt was regenerated, and no R0
freeze or candidate measurement is claimed.

## Guardrails retained

- Do not use `NewSolution`, unselected `_old` SQL, untracked `Database/`, or
  generated evidence docs as authority.
- Do not infer deployed SQL, DB connection, HRM2.0 production state, real-person
  records, or complete impact from source.
- Do not upgrade zvec 0.8.0, deploy/push, edit/delete live indexes, or use the
  installed binary/library for this source-only pilot.
- Preserve unrelated workspace modifications and historical benchmark receipts.
