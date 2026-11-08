# R4 follow-up experiments — pre-registration

Status: **FROZEN BEFORE RUN. R4-A and R4-B only.**
Date: 2026-11-08. The original R0/B0/R4 receipts and targets remain frozen and
must not be edited. This is a new experiment on the same 12 development cases;
held-out is not inspected.

## Fixed inputs

- Frozen B0 receipt: `results/b0-official.json`.
- Frozen candidate failure attribution: `results/r4-failure-attribution.json`.
- Frozen corpus/cases: `corpus-manifest.json`, `cases/dev.json`.
- Source: HRM2.0 `68a53a6865b81d05745c4aa47967204e8e128996`, read-only.
- Targets, corpus, case gold and the original B0 values are unchanged.

## What is being tested

Both experiments are harness-level candidate changes; they do not add a model,
index format change, network call, DB access or source write.

### R4-A — multi-call seed retention + method-level ranking

- Use all six frozen B0 calls; do not choose one call.
- Score a unit by: method/entity containment, exact identifier/token overlap in
  the method/type name, path token overlap, span density and call diversity.
- Keep at most 5 fixed seeds, with at least one from each non-empty call where
  possible; reserve at least 5/10 slots for expansion.
- Expansion uses the committed C#/SQL facts and bounded helper; dynamic targets
  remain candidates.

### R4-B — file-cluster packing + corrected pack-level measurement

- Start from R4-A's ranked candidate items.
- Group by file, choose at most 3 files by evidence score, merge overlapping or
  adjacent spans, and cap the pack at 10 items.
- Compare both arms on the **same pack-level definitions**:
  - evidence files = distinct files represented in the 10-item pack;
  - evidence tokens = tokens of the pack content only;
  - recall = gold spans covered by the pack.
- This corrects the original candidate pilot's asymmetry (candidate pack files
  vs B0 post-search file reads). It is a diagnostic metric, not a redefinition
  of the owner's frozen targets.

## Acceptance for this follow-up

- Report R4-A and R4-B separately.
- Primary: Evidence Recall@10 on the 12 dev cases versus B0 best-call pack.
- Secondary: evidence files and tokens versus the same B0 pack.
- A win requires recall **above B0** and no material resource increase; the
  original +10pp target still governs promotion.
- No target/corpus change after results. No held-out run. No candidate GO unless
  all original hard gates and optimization goals are met.
- If either experiment fails, keep R1 lightweight and report FAIL.

## Outputs

- `results/candidate-r4a.json`
- `results/candidate-r4b.json`
- A short result note under `rust/docs/`.
