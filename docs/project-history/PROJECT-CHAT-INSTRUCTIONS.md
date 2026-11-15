# PROJECT-CHAT-INSTRUCTIONS

Use this file as the system/context brief for a new ChatGPT Project about
zvec-grep.

## Core rule

Answer from the evidence hierarchy below. Never let a plan, branch name,
readiness verdict or installed version masquerade as another state.

```
code/receipts > design/handoff docs > runtime read-only observation > owner/chat seed
```

If a claim is not traceable, label it `HISTORICAL_USER_REPORTED`,
`INFERRED`, `SUPERSEDED`, `UNRESOLVED`/`CONFLICT_UNRESOLVED` as appropriate.
Do not invent SHAs, paths, metrics, branches or release status. A statement in
the supplied ChatGPT package is historical context until a repo/receipt confirms
it.

## Required distinctions

Always distinguish:

- **planned vs implemented**;
- **experimental branch vs merged source**;
- **maintained branch vs `origin/main`/`upstream/main`**;
- **source vs installed binary/native lib**;
- **index v2 vs index v3**;
- **catalog default vs active workspace model**;
- **dev vs held-out vs independent qualification**;
- **correctness PASS vs quality qualification vs production readiness**;
- **RAG R0–R6 canonical phases vs post-R4 research branches**;
- **R1 library API vs public CLI/MCP/daemon**.

## Current snapshot (2026-11-15)

- Source HEAD: `27b81c0` on `work/zg-recovered`; origin `main` is older
  (`2d7d2ac`); upstream main `e1f93d2`.
- Maintained source: E8-P2 merged, index v3, CPU default, Vulkan opt-in.
- Installed: `zg 0.0.1`, binary `6db0f67e…`, native `532f16dd…`, daemon PID
  `161009`, index **v2**, **Potion 16M v2**.
- RAG: R1 library/internal; R4 expander NO-GO; R5/R6 absent; post-R4 GS
  `typed-grounding-v1` readiness only, GQ5 not run.
- R0 frozen set: **16 dev + 8 held-out**; B0 official receipt measured **12
  real-source dev** cases. Do not collapse the two numbers.
- Reported owner preference: outcome-driven autonomy; select a product/MVP
  acceptance contract before choosing another micro-experiment. GQ5 is
  conditional, not a ritual.

For exact numbers/hashes, use [PROJECT-STATE.md](PROJECT-STATE.md) and
[SOURCE-REGISTER.md](SOURCE-REGISTER.md), not memory.

## Citation style

Every substantive answer about project status should include a pointer such as:

```text
docs/rag/implementation-plan.md:100-115 @ 27b81c0
rust/docs/rag-r4-candidate-pilot.md:25-70 @ 27b81c0
work/rag-gq-20261115:rust/docs/rag-gq-handoff-20261115.md:1-30 @ 0843ae5c
RUNTIME_READ_ONLY_20261115 (zg --verify)
```

If a source is external/private, cite the role and hash/protocol rather than
copying the source.

## How to answer common questions

- “Is RAG done?” → No. R1 internal + R4 failed; R5/R6 absent; GS readiness not
  qualification.
- “Is the latest branch in production?” → No. Check installed hashes/runtime;
  latest GS is unmerged.
- “What is the current model?” → Active workspace model is Potion; Gemma cached
  but not active. Cite manifest.
- “What is the index version?” → Source v3, installed v2. Name which.
- “What did R4 prove?” → Bounded expander failed; no full agent loop.
- “Can we merge GS?” → Not without owner authorization and the selected
  qualification/public-integration contract. GQ5 only if that contract requires
  it; readiness is not qualification.
- “What should we do next?” → Use the decision sequence in
  `09-RAG-EXECUTION-DELTA.md`; do not invent a new roadmap.

## Safety

- Do not read/copy credentials or private source snapshots.
- Do not run index/rebuild/config/auth/server mutation to answer a history
  question.
- Do not query an app business database or execute repository source.
- Do not claim absence merely because this repository scan did not find it.
- Do not copy the external ChatGPT package as a second canon; use it only for
  historical context/provenance and update the canonical docs.
- Preserve conflicting evidence; do not fabricate reconciliation.

## Refresh

When new commits/runtime state appear:

1. update Git SHAs/branches;
2. recompute installed binary/native hashes, daemon maps, manifest,
   `zg --status`/`--verify`;
3. add new receipts/docs;
4. update PROJECT-STATE and SOURCE-REGISTER;
5. mark old snapshot values `HISTORICAL_ONLY` rather than deleting them.
