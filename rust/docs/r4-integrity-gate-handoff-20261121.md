# Handoff — R4 integrity gate & semantic-evaluation preparation (2026-11-21)

Status: **READY_FOR_AUTHORIZED_INFERENCE — SEMANTIC_EVALUATION_PENDING**.
Answer quality **NOT_MEASURED** (no independent evaluator exists yet).
Not `QUALIFIED`, not `RAG_MVP_GO`, no architecture winner.

## 1. Where things are

| Item | Value |
|---|---|
| Repo | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| Base checkpoint | `77e681e` |
| HEAD | `60160c0` — pushed, tracks `origin/work/r4-evidence-representation-20261119` |
| Worktree | clean |
| Report | `rust/docs/r4-integrity-gate-20261121.md` |
| Review bundle | `/home/masis/.cache/zvec-grep/r4-integrity-gate-20261121/r4-integrity-gate-final-20261121.zip` (SHA-256 built from `bundle-final/`; the ZIP digest is reported with the handoff message because it changes whenever the manifest is regenerated) |
| Bundle integrity | `bundle-final/MANIFEST.sha256` (content-addressed; the ZIP is rebuilt from it) |
| Synthetic receipts | `/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v6/` |
| Runner + summarizer | `run_three_arm_v5.py` (writes `three-arm-v6`), summary inside its JSON |

## 2. Frozen identities (unchanged)

| Artifact | Identity |
|---|---|
| Corpus | `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` (822 files) |
| Cases | `cases/dev.json` sha256 `27626109…` |
| Rubric | `r4-task-correctness-contract-20261118.md` sha256 `330c796d…` |
| Evaluator materials | `cases/c3-steps.json` sha256 `7a84ab69…` |
| Evaluator registry | `cases/evaluators.json` — **empty**: no evaluator is trusted yet |
| Embedding / index | potion-code-16m-v2, unchanged; no rebuild |

## 3. Verified in this mission

- Adapter contract tests: **51/51** (+2 internal accounting tests) (boundary accounting, failure injection,
  admission-vs-observed guard).
- `zg-engine` lib tests: **804/804** serially (the two `config::tests` collide on
  a shared path when run in parallel — pre-existing, unrelated).
- Harness acceptance: **79 checks**.
- Grader fixtures: **56 checks** (every required negative control).
- Synthetic three-arm E2E: **36/36 runs**, 0 violations, 36/36 answers `OK`,
  36/36 packs bound, 36/36 read deltas reconcile, **12/12 arm-b plan chains
  closed**, **0 formal PASS without a semantic evaluator**.

## 4. What an evaluator must do (the only remaining dependency)

1. Register it in `cases/evaluators.json` (`trusted: true`) after the owner
   approves its identity and prompt.
2. It receives only the frozen answer, the frozen pack and the evaluator-only
   materials — never gold spans, never the answerer's session.
3. It returns `{case, evaluator_id, verdict, rationale, inputs{case,
   answer_sha256, pack_sha256, question_sha256, rubric_sha256},
   supported_claims[], unsupported_claims[]}` with **every** claim judged once.

Without that, every run is `NOT_MEASURED` by design.

## 5. Exact next authorized action

Owner decision (1): register/authorize a semantic evaluator.
Owner decision (2): authorize private-source inference (provider, exact model and
served identity, endpoint, answerer data flow).

Then: run D01–D12 × 3 arms with `--backend openai-responses
--authorized-inference --grade`, and grade each frozen pair in its own process
with `--semantic <evaluation> --registry cases/evaluators.json`. Report the
structural and semantic layers separately; a formal PASS requires both.

Out of scope: ranking/embedding/index changes, held-out H01–H08, GQ5/R5/R6,
merge, release.

Next-session entry point: `rust/docs/r4-next-session-handoff-20261121.md`.
