# Documentation changelog

This file records changes to the **knowledge base**, not changes to the
zvec-grep engine. Historical plans/reports are not rewritten here.

## 2026-11-15 — consolidation of the ChatGPT context package

**Input:** `/home/masis/downloads/zvec-grep-project-context-20261115/`
(13 files; package manifest sha256
`17d6fd04805413c5e993eab0c2d48fc3c4c32d5afa0f4e28a316ba664b89252d`;
master handoff sha256 `8a3fe79c…e8ec3d`). Treated as a **historical
supplement**, not as a higher-priority source.

**Original-plan integrity:** the three `source-docs/` RAG files are byte-identical
to `docs/rag/README.md`, `implementation-plan.md` and `hrm2-pilot.md`
(sha256 `ceea9057…`, `28d8d210…`, `4c72b317…`). They remain historical
PROPOSED plans.

**Added to canonical docs:**

- `00`: capability-status vocabulary (`PROPOSED` → `PUBLIC_EXPOSED`) and the
  rule that a higher layer is never inferred from a lower one;
- `01/03`: confirmed FTS+scalar+vector/HNSW structural-fragment coupling and
  its format-change implication; F6 snapshot-cursor and strict-freshness open
  risks;
- `02/05`: exact R0 freeze shape (**16 dev + 8 held-out**; B0 measured 12
  real-source dev cases), plus verified foundation/E1/E2/E3 branch SHAs;
- `04`: No-op Fast Path measurements/safety, Foundation G1–G4/G5 failure
  lessons, E1 arm table, E2-C NO-GO and measurement variants, E7 scaling
  slices, E8-P1 vs E8-P2 run variants, and the **design-only** distillation
  discussion (no trained student);
- `06`: H-A/H-B/H-C discovery side experiments and GQ dev/held-out gate detail;
- `08`: owner-reported outcome-driven autonomy, legacy dead-code vs liveness
  separation, verbal-replacement/no-execution record, GQ5-as-conditional;
- `09`: execution-contract decision before technique choices; GQ5 only when the
  selected MVP requires it;
- `SOURCE-REGISTER`: external-package provenance, original-plan hashes, and
  explicit conflict rows for 16-vs-12 cases, S1b 56/5-vs-57/10, no-op/E2/E8
  measurement variants, and test-count differences;
- `10`: additional negative controls and reconciliation checks.

**Not changed:** source, engine, index schema, ranking, model default, RAG
research branches, installed binary/daemon/index, original plans, receipts.
No GQ5, merge, release or promotion was performed.

## 2026-11-15 — handoff scope cleanup

The resumable handoff was moved out of this documentation KB. It is maintained
in the source-main worktree as `HANDOFF-20261115-NEXT.md`. This KB remains the
canonical **documentation package**; the handoff is an engineering/session
artifact and should not be part of the portable documentation set.

## 2026-11-15 — initial knowledge base

Commit `70036c66a20756e1ee8ebcea587f2a8f9169d97b` created
`docs/project-history/` (00–10, state, source register, project instructions)
from Git/source/receipts/runtime archaeology.
