# Handoff — next session (2026-11-21, semantic evaluator pilot ready)

Single entry point. Read this, then
`rust/docs/r4-semantic-evaluator-pilot-20261121.md` (report: SE-01/SE-02,
C3 audit, pilot, review) and, for the earlier round,
`rust/docs/r4-semantic-evaluation-20261121.md`.

Status: **PILOT_READY — REAL ANSWER QUALITY NOT_MEASURED.** The evaluator
workflow can now represent and grade "correct but incomplete" and "stale
evidence" distinctly from "correct" and "wrong", but no production evaluator is
registered and no real Arm A/B/C answer quality exists. No `RAG_MVP_GO`, no
architecture winner.

## 1. Repository state right now

| Item | Value |
|---|---|
| Active worktree | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| HEAD | the handoff commit (parent `5b93300`) — **not pushed**, 6 commits ahead of `origin`: `069f6a8`, `8dd396c`, `bacfa36`, `75563f0`, `5b93300`, then this handoff |
| Other worktree | `/home/masis/src/zvec-grep` on `main` (`2d7d2ac`) |
| Worktrees | exactly two |
| Working tree | clean |
| Disk | 35G free (93%) |
| Registry | `benchmarks/rag-impact/cases/evaluators.json` still **empty** |

Snapshot of the round, if the worktree moves: `~/.cache/zvec-grep/r4-semantic-eval-20261121/` (code state `5b93300`, before this handoff commit; HEAD is only the handoff doc on top).

Rule that still holds: at most two local worktrees; push a branch before any
worktree removal. Nothing in this round was pushed, merged or released.

## 2. What this round finished

1. **SE-01 — correct-but-incomplete verdict.** The evaluation contract gained a
   required `omissions[]`. `PASS` requires `completeness = 2`, no omissions and
   no unsupported claims; `FAIL` is valid with an unsupported claim **or** an
   omission; `completeness = 0` requires an omission. Grader and `accept`
   enforce the same rules, so bypassing `accept` does not lift an incomplete
   answer.
2. **SE-02 — stale evidence fails closed.** A contentless pack item is re-read
   from the corpus only when its hash still matches the frozen pack hash; stale
   text is withheld (`stale_source`, no content) and a forged "current" packet
   is refused. No frozen hash at all → text withheld and marked
   `unverifiable_source`.
3. **C3 completeness audit.** `tools/audit_c3_anchoring.py`: 49 required steps
   over D01–D12, 29 addressable by the frozen token locator, 20 not (15
   no-identifier, 5 outside-gold), corpus bytes verified against the manifest.
   Proven: a D07 answer omitting step 3 still reaches 12/12 under a PASS
   evaluation. Gold/checklist/thresholds unchanged; the grader now reports
   `unmeasured_required_steps` (id + reason only), and the evaluator's
   `completeness` is the compensating control.
4. **Pilot.** `tools/r4_semantic_pilot.py`: five synthetic scenarios through the
   real packet/accept/grader — complete PASS, incomplete FAIL
   (`unsupported = []`, `omissions = ["Name"]`), wrong FAIL, wrong-citation
   FAIL, stale FAIL — asserted on the distinguishing fields, with a
   contrived-PASS counterfactual showing structure alone would accept the
   incomplete answer.
5. **Independent review** (DeepSeek Flash 4.1 High, one pass): no P0; 4 P1 + 6
   P2. All material findings fixed and pinned (PASS `completeness = 2`, C2 no
   longer drops on an omission-only FAIL, no-hash stale branch, pilot
   assertions). P2 residuals are stated in the report §7.

## 3. Environment (unchanged, still important)

```bash
export LD_LIBRARY_PATH=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt
export ZG_ORT_CACHE_DIR=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt
export CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/r4-evidence-20261119/target
```

**Never delete**: the `zvec-prebuilt/` and `ort-prebuilt/` above, the frozen
corpus `~/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`, and every `r4-*`
receipt/bundle directory. The two large `deps` (9.0G + 8.6G) are kept
deliberately; removing them frees ~17G and costs ~10 min of recompilation.

## 4. Reproduce / verify

```bash
cd /home/masis/src/zvec-grep-r4-evidence-20261119
C=/home/masis/.cache/zvec-grep/r4-semantic-eval-20261121

python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g   # 89/89
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator_selftest.py /tmp/se  # 57/57
python3 benchmarks/rag-impact/tools/r4_bench_driver_selftest.py \
  $CARGO_TARGET_DIR/debug/r4-adapter \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/h  # 79/79
python3 benchmarks/rag-impact/tools/r4_semantic_pilot.py --work $C/pilot  # 5/5
python3 benchmarks/rag-impact/tools/audit_c3_anchoring.py benchmarks/rag-impact \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --out $C/c3-audit.json
python3 benchmarks/rag-impact/tools/r4_semantic_e2e.py \
  --corpus /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --work $C/e2e
```

Expected: 89/89, 57/57, 79/79, pilot 5/5, audit 29/49 with 20 reported, E2E
exit 0 with 36 runs, 0 violations, 12/12 plan chains, 36/36 accepted
evaluations, 7/7 fixtures, no formal PASS without an evaluator.

Adapter tests (unchanged): `cargo test -p zg-r4-adapter` → 2 + 51 passing.

## 5. Owner decisions that unblock a real run

**D1 — register one semantic evaluator.** Candidate configs:
`benchmarks/rag-impact/cases/evaluator-candidates.json`. The prompt digest every
packet records and checks is
`4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55`
(`r4_semantic_evaluator.INSTRUCTIONS`, 2,974 bytes). Registration shape and the
updated procedure are in `cases/evaluators.json`; add `trusted: true` plus the
fingerprint, model/served identity and kind. The workflow never writes this.

**D2 — authorize private-source inference** if the evaluator is remote:
provider, exact model + served identity, endpoint, and the data classes in the
report §6. The packet never contains gold, the C3 checklist, the answerer
session or corpus outside delivered items/citations.

**D3 — run the real three-arm benchmark**: 12 cases × 3 arms through the driver
with `--backend openai-responses --authorized-inference`, then one grader
process per frozen pair with `--semantic` and the registry. Report structural
and semantic layers separately; a formal PASS needs both. Cost estimate for the
evaluator alone: ~$0.87 for 36 evaluations at the illustrative 2/8 USD per 1M
in/out rate card (~$0.024/evaluation; 331,949 estimated prompt tokens +
25,200 output).

## 6. Do not retry

- Changing gold, the C3 checklist or thresholds to make the 20 unanchored steps
  measurable; that is a rubric change for the owner, not a correction.
- Re-introducing an exclusive `end_line` convention: adapter packs are inclusive,
  and the old `end - 1` normalisation was a reproduced false negative.
- Accepting an evaluation without the five dimensions, or a PASS with
  `completeness < 2`; those were review findings and are pinned by fixtures.
- Treating the pilot judge or the loopback judge as answer-quality evidence.

## 7. Where the evidence lives

| What | Where |
|---|---|
| SE-01/SE-02, C3 audit, pilot, review | `rust/docs/r4-semantic-evaluator-pilot-20261121.md` |
| Earlier round (packet/accept, W1–W13) | `rust/docs/r4-semantic-evaluation-20261121.md` |
| Selftest / pilot / audit / E2E receipts | `~/.cache/zvec-grep/r4-semantic-eval-20261121/` |
| Sanitized delta bundle + zip | `~/.cache/zvec-grep/r4-semantic-eval-20261121/bundle-pilot-final/` / `r4-semantic-pilot-final-20261121.zip` (sha256 `fb3268dad8ef8e736482f85932a7177969a94706f3ac90be10890b465b66df3a`) |
| Evaluator/pilot/audit/E2E tools | `benchmarks/rag-impact/tools/` |
| Frozen corpus (read-only) | `~/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` |

## 8. First thing next session

Do not add infrastructure. Either get D1+D2 from the owner and run D3, or stop
at the authorization gate. The next milestone is **measured Arm A/B/C answer
quality**, not more schema.


## Continuation update — 2026-11-22 (real-agent RAG evaluation)

The evaluator and benchmark remain **PILOT_READY at the harness level; real answer quality NOT_MEASURED**. No private inference was run. The current gate report is rust/docs/r4-real-agent-rag-evaluation-gate-20261122.md; the owner decision form is rust/docs/r4-real-agent-rag-approval-package-20261122.md.

Readiness checks found that the frozen index and local Potion vector route execute on a disposable copy; the original cache corpus is read-only in this sandbox. The unrelated main workspace FTS index still fails to open. A complete Potion artifact is present in an existing local experiment cache, so the older “missing artifact” note is stale for this host, though the exact selected artifact path was not logged.

A mock test reproduced an absolute corpus-root leak in an adapter error sent as a model tool result. The boundary now replaces corpus and session roots before sending tool output; local transcripts retain the original. Fix checkpoint: 360c797. Offline checks after the fix: grader 89/89, evaluator 57/57, driver 79/79, synthetic pilot 5/5 and synthetic E2E exit 0. These remain harness results only.

Fresh-context review found the real remaining blockers: D1 evaluator identity/trust, D2 private-source inference approval, verified retention, and an enforceable all-in cost ceiling. The existing GPT-4.1 evaluator candidate is deprecated. No production evaluator is registered.

**Next action:** wait for the owner to fill/approve the decision form, including an all-in USD cap. Before any private request, implement and verify request/output-token bounds and a pre-call budget guard. Then run only the fixed 3×3 development pilot; proceed to full 12×3 only if the preregistered operational gate passes. Keep H01–H08 sealed. Do not treat synthetic E2E as answer-quality evidence.


## Continuation update — native Codex subagent pilot readiness (2026-11-22)

The current checkpoint remains 135844ed before this handoff-only update. Native Codex delegation was verified with a fresh-context reviewer using sanitized pilot information only. No HRM2.0 case, source, evidence, gold or checklist was sent to a subagent; no answerer/evaluator or Responses API call was run.

Tool availability: native rg, the collaboration spawn interface, R4 adapter CLI, and MCP zvec_grep_search are present. The adapter exposes search/symbol/facts/evidence/verify/workflow/status. The host has no zvec_grep_rg MCP tool; the current workspace FTS failure remains separate from the frozen R4 index.

The prepared exploratory pilot uses D01/D06/D12 × Arms A/B/C, nine fresh answerer contexts and three separate blind Arm-B plans. Evaluators would run only after all answers, one packet at a time, without gold/checklist/peer outputs. The frozen budgets remain targets; do not claim they are enforced uniformly by native subagents.

Key limitation: this session's subagent interface provides no per-agent tool allowlist/filesystem root, and the parent cannot independently see complete per-tool traces, token usage, cost or served-model fingerprint. Fresh context is not filesystem isolation. Arm A shell operations and unmediated Arm C activity are not covered by the R4 adapter ledger. Any pilot under these conditions is exploratory, not formal trusted evaluation. Full sanitized readiness and reviewer findings are in rust/docs/r4-codex-subagent-pilot-readiness-20261122.md.

**Stop here:** no private inputs go to subagents until the owner explicitly approves that data flow. Do not poll approval fields or use Responses API. Preserve H01–H08 and do not claim arm superiority from a three-case exploratory pilot.
