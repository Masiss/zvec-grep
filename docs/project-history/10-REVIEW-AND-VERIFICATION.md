# 10 — Independent Review and Negative Controls

This review was performed read-only against the final documentation tree and the
repository/runtime snapshot. It is not a product qualification.

## 1. Inventory checks

| Check | Result |
|---|---|
| Required docs 00–09 + state/register/instructions present | PASS (13 Markdown files) |
| Relative Markdown links resolve | PASS (0 missing) |
| No mandatory domain omitted | PASS (coverage matrix in `00-START-HERE.md`) |
| All RAG branches discovered locally and remotely | PASS |
| All release/rollback tags named in docs exist | PASS |
| Post-R4 branch tips/bases checked with `git rev-parse`/`merge-base` | PASS, with local/published requal distinction documented |
| Current installed binary/native hashes match PROJECT-STATE | PASS |
| Current active index version/model match PROJECT-STATE | PASS |
| Current daemon command/loaded lib match PROJECT-STATE | PASS |

## 2. Negative controls

These are deliberate counterexamples; a knowledge base that fails them is
overclaiming.

### N1 — “R1 is a public RAG API”

Rejected. `rust/docs/rag-r1-release-gates.md:34` says no CLI/MCP/daemon
consumer; R1 is a public **engine library** API, not public product surface.

### N2 — “GS is qualified / production-ready”

Rejected. `work/rag-gs-20261115` is `READY_FOR_INDEPENDENT_QUALIFICATION`;
GQ5 has not run; GQ4 is observed/tiny; no public integration. Readiness is not
qualification.

### N3 — “Installed index is v3”

Rejected. `.zvec-grep/manifest.json` has `indexVersion: 2`; source has v3.
`zg --verify` PASS does not change the version.

### N4 — “E8-P2 is installed”

Rejected. E8-P2 merged into source `work/zg-recovered`; installed binary SHA
`6db0…`, native `532f…`, index v2. Source has v3. The E8-P2 docs explicitly
say no installed binary/daemon change.

### N5 — “R5/R6 are done because there are post-R4 branches”

Rejected. The canonical plan `R5`/`R6` are paired qualification and opt-in
release/rollback. No such implementation/receipt exists. Post-R4 branches are a
different research track.

### N6 — “E4 proves FTS depends on the model”

Rejected. E5 reproduced the harness contamination; the E4 FTS claim is
retracted. Pure FTS is model-independent on identical fragment tables.

### N7 — “RefInt selected 22/25 required spans”

Rejected. 22/25 is **candidate union**, not selected spans or Recall@10.
RefInt selected 10/25 dev / 8/20 held-out.

### N8 — “The latest branch is main”

Rejected. `work/rag-gs` is an experimental branch; `work/zg-recovered` is the
maintained branch; `origin/main` is older. No research branch was merged.

### N9 — “The active model is Gemma”

Rejected. Gemma is cached; active workspace manifest is Potion 16M v2.

### N10 — “R4 is complete because an expander exists”

Rejected. Canonical R4 includes a multi-turn agent loop and public integration;
the expander failed and the full loop was never implemented.

## 3. Conflicts handled

| Conflict | Handling |
|---|---|
| `work/rag-requal` local `1d8f57a` vs published `f93d0499` | Both recorded; use published for publication, local for content; S1 branch shares local tip |
| R4 plan vs R4 expander naming | Canonical plan preserved; expander labeled subset/experiment |
| R4 B0 0.4722 vs fixed 0.3750 | Labeled oracle vs deployable |
| E3/E4 route tables | Marked invalid by E5/E6; valid resource/gold evidence retained |
| R1 “public engine API” vs no public consumer | Split into library/public-product states |
| Source v3 vs installed v2 | Both recorded as separate identities |
| `docs/rag` PROPOSED vs implemented work | Recorded as governance drift, not rewritten as plan fact |

No conflict was “resolved” by inventing a new number or deleting the losing
source.

## 4. Evidence integrity

- Every major numeric verdict in `05`/`06` is tied to a named Rust doc or branch
  handoff; external cold receipts are referenced by path/role, not copied.
- Historical invalidation is prominent in `04` and `05`.
- Dev/held-out distinction is retained; no held-out reuse is presented as
  independent generalization.
- Installed/runtime claims are marked `VERIFIED_READ_ONLY`.
- Missing/unavailable evidence is listed in `SOURCE-REGISTER.md` §10.

## 5. Known residual gaps

- No exhaustive re-hash of every external `~/.cache` receipt.
- No human review of RAG labels/claims.
- No live HRM2 DB/deployment validation.
- No full Node/TypeScript code audit beyond timeline/release docs.
- `docs/rag` itself was not edited (this is a docs-only history branch); the
  discrepancy is documented, not silently fixed.
- Native HNSW/exact zvec internals are delegated to the pinned backend; this KB
  does not claim independent verification of every ANN parameter.

## 6. Review verdict

**PASS for a navigable, evidence-annotated project history and RAG status KB.**
It is **not** a claim that zvec-grep RAG is production-ready, that R1 is public,
that R4/R5/R6 are complete, or that source and installed identities match.

## 7. Evidence anchors

- All files in this directory.
- `SOURCE-REGISTER.md` §§2–10.
- Runtime read-only commands in `PROJECT-STATE.md`.
- Branch tips from `git rev-parse`/`git ls-remote`.
- Canonical plan `docs/rag/implementation-plan.md`.
