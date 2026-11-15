# HANDOFF — next session (source main)

**Date:** 2026-11-15  
**Status:** `DONE_DOCUMENTATION`  
**Next:** `OWNER_DECISION_REQUIRED` — choose the RAG MVP/acceptance contract; do
not run GQ5 as a ritual.  
**Scope:** source-main continuation. Documentation/forensics only; no engine,
index, model, RAG runtime, installed binary, daemon or production index was
changed.

## 1. Why this handoff lives here

The canonical documentation package is a **separate, portable artifact**:

- branch `docs/project-history-20261115`;
- entrypoint `docs/project-history/00-START-HERE.md` on that branch;
- it is **not merged** into source main and is not part of this checkout.

This file is the engineering/session checkpoint for the **source-main** line.
It records the documentation consolidation state and the safe next actions.

## 2. Git and runtime state

| Item | Value |
|---|---|
| Source branch | `work/zg-recovered` |
| Source HEAD | `27b81c0e0472644820955fd69d1a080fb76b16ca` |
| Source status | clean before this handoff file |
| Docs branch | `docs/project-history-20261115` |
| Docs cleanup tip | `1e8a9be5285fc5e8d5e03bd307c59f829560b987` |
| Docs content base | `5deaf2583f3d97400753664f1d3fc3a7703c3486` |
| Merge | docs branch **not merged** into source |
| Installed CLI | `zg 0.0.1`, binary `6db0f67e…` |
| Native lib | `532f16dd…` |
| Daemon | PID `161009`, ready on `127.0.0.1:7999` |
| Active index | **v2**, Potion Code 16M v2, 256d |
| Source index | **v3** after E8-P2 |

Do not infer `INSTALLED` or `ACTIVE_RUNTIME` from `MERGED_INTERNAL`. The
installed pair is older than source HEAD.

## 3. Canonical documentation location

Git is authoritative. To read/upload the KB:

```text
docs/project-history/00-START-HERE.md   # on docs/project-history-20261115
```

Current extracted copy:

```text
/home/masis/.cache/zvec-grep/project-history-20261115/consolidated/extracted/project-history
```

ZIP:

```text
/home/masis/.cache/zvec-grep/project-history-20261115/consolidated/project-history-consolidated-20261115.zip
```

The older path
`/home/masis/.cache/zvec-grep/project-history-20261115/extracted/project-history`
is **stale** (pre-consolidation and still contains the removed handoff); do not
use it as canonical.

## 4. What was consolidated

The external ChatGPT package
`/home/masis/downloads/zvec-grep-project-context-20261115/` was treated as a
**historical supplement**, not a higher-priority source. Its package manifest
hash is `17d6fd04…89252d`; its master handoff is `8a3fe79c…e8ec3d`.

The three RAG `source-docs/` files were verified **byte-identical** to the repo
originals (`ceea9057…`, `28d8d210…`, `4c72b317…`).

Only the existing canonical docs were modified; no parallel knowledge base was
created. `CHANGELOG.md` records the changes.

The resumable handoff was then moved out of that documentation package into this
source-main file, because the docs package is a separate portable artifact.

## 5. Corrections that matter

- **R0 cardinality:** frozen set is **16 dev (D01–D16) + 8 held-out**; the B0
  official receipt measured only **12 real-source dev cases**. “12” is a
  measured subset, not the frozen set.
- **No-op:** ~0.591→0.047 s dense, 0.575→0.062 s mixed, RSS 281→62 MiB, zero
  writes. `published_state_current` prevents false no-op after native commit
  before manifest publication.
- **G1–G4:** microbench gains, no end-to-end win; G5 streaming hash slower and
  rolled back.
- **E1:** bounded arms A4/B2/B4/B8/B16 recorded; B8 inconclusive; E1 remains
  experimental.
- **E2:** E2-C cross-batch coalescing NO-GO; measurement variants differ by
  workload/run and must not be averaged.
- **E8:** E8-P2 3.65× is the merged receipt; supplemental 4.03× is another
  run/build, not a replacement.
- **Distillation:** design discussion only; no student/training artifact.
- **Post-R4:** H-A/H-B FAIL, H-C deferred; GQ dev 8/10 groups, role 0.533,
  misleading 6, abstention 4/6, negative safety 0/1; GQ-R Gate A PASS but GQ4
  NO_GO; GS `typed-grounding-v1` is readiness only, GQ5 not run.
- **Architecture:** structural fragment carries FTS + scalar + vector/HNSW;
  changing vector cardinality is an index-format/retrieval-contract change.
  F6 snapshot-cursor and same-size/mtime freshness risks remain open.

## 6. Conflicts and unresolved items

| Conflict | Handling |
|---|---|
| 16 frozen dev vs 12 measured | Both retained; label measured subset. |
| S1b supplement 56 units/5 gold vs receipt | Receipt: 57 units/10 selected/47 non-gold; supplement number not used. |
| No-op/E2/E8 measurement variants | Kept as historical variants; no averaging. |
| Test totals 765/770/824 | Different suites/builds; `UNRESOLVED` if one total is required. |
| `docs/rag` PROPOSED vs post-R4 work | Original plan preserved; post-R4 is a separate research track. |
| Source v3 vs installed v2 | Separate identities; no auto-migration. |
| Binary replacement chat vs execution | `HISTORICAL_USER_REPORTED / NO_ACTION`; not executed. |

Still unverified: raw ChatGPT reports not re-run; external receipts not
exhaustively re-hashed; human-independent RAG gold/adjudication absent; no
GQ5; no full R4 loop/public integration; no R5/R6; no live HRM2 DB authority.

## 7. Owner-reported preferences

These are historical user reports, not production authorization:

- outcome-driven autonomy: define objective/acceptance/authority/dependencies,
  then run bounded phases; owner gates product/public-contract/safety/budget/
  production decisions;
- legacy dead-code exclusion is not compiler liveness; commented/inactive code
  cannot create `VERIFIED` bindings;
- GQ5 only if the selected MVP/qualification contract needs it;
- R1 library-only is a legitimate fallback;
- do not replace installed binary/index without release gates, backup,
  rollback and post-install verification.

## 8. Next session — safe first actions

```bash
cd /home/masis/src/zvec-grep
git status --short --branch
git rev-parse HEAD
git ls-remote origin refs/heads/docs/project-history-20261115
sha256sum .zvec-grep/manifest.json
$HOME/.local/bin/zg --version
$HOME/.local/bin/zg --server status
```

If source/runtime has drifted, update `PROJECT-STATE.md` and
`SOURCE-REGISTER.md` in the docs branch before answering “current” questions.

Then choose **one** direction:

1. R1 product shape: library-only, public CLI/MCP evidence, or no RAG.
2. Write the execution contract/acceptance before choosing techniques.
3. If R4 is resumed, preregister retrieval/discovery + full agent loop; do not
   continue selection micro-tuning.
4. Run/decline GQ5 based on the chosen contract, not by default.
5. R5/R6 only after a working R4 and public R1.

## 9. Hard constraints

- Do not merge docs branch to source main without instruction.
- Do not modify engine/index/model/ranking/defaults.
- Do not rebuild/replace installed binary, daemon or production index.
- Do not run GQ5, release, promote or migration.
- Preserve original plans, historical reports, receipts and rollback pair.
- Do not turn a chat decision into an executed production decision.
- Do not use the stale extracted folder as canonical.

## 10. Terminal state

```text
DOCUMENTATION: DONE (separate docs branch)
SOURCE MAIN:   27b81c0 + this handoff file
PRODUCTION:    UNCHANGED
GQ5:           NOT RUN
MERGE:         NOT DONE
NEXT:          OWNER DECISION / EXECUTION CONTRACT
```

For history/architecture/RAG questions, use the canonical KB; raw receipts are
only needed for a new claim or a new qualification. Do not reuse GQ/GQ2/GQ3/GQ4
as independent generalization.
