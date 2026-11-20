# R4 benchmark readiness — final contract corrections (2026-11-21)

Base checkpoint `eda88b1` (branch `work/r4-evidence-representation-20261119`,
local, unpushed) → HEAD at the end of this mission. Independent review verdict
at entry was **PARTIAL — NOT_READY_FOR_FORMAL_BENCHMARK**; this document reports
what was reproduced, fixed, and verified against that list, and what remains
gated.

Nothing here changes a frozen benchmark target, case, gold span, checklist,
threshold or acceptance criterion. Retrieval ranking, index schema, embedding,
granularity and public contracts were not touched. No private source left the
machine and no real provider was called.

## 1. Executive verdict

**READY_FOR_AUTHORIZED_INFERENCE** (harness, accounting, grading, provenance),
with real private-source inference still **OPERATION_BLOCKED** on owner
authorization.

Evidence: 36/36 synthetic three-arm runs (12 authored cases × 3 arms, zero
protocol violations, zero plan-egress findings), 38/38 adapter contract tests,
38/38 harness self-test checks, 15/15 grader fixture checks, and a reconciliation
computed only from raw receipts (`RECONCILIATION.json`).

This is **not** `QUALIFIED`, not `RAG_MVP_GO`, and not an answer-quality result:
the synthetic answerer is a deterministic mock, so the C1–C6 scores measure
pipeline behaviour, not investigation quality.

## 2. Checkpoint and changes

| Item | Value |
|---|---|
| Base | `eda88b1` |
| Branch | `work/r4-evidence-representation-20261119` |
| Push | not performed (owner-only) |
| Worktree at handoff | clean |

Changed in this mission:

- `rust/crates/zg-r4-adapter/src/lib.rs` — source-read admission on every
  read path (facts/evidence/verify/search/workflow), exact read-op accounting,
  plan provenance in workflow results.
- `rust/crates/zg-r4-adapter/src/bin/r4-adapter.rs` — plan file digest
  (`plan_sha256`) recorded from the exact frozen bytes.
- `rust/crates/zg-r4-adapter/tests/contract.rs` — budget-boundary and
  single-open regression tests (38 total).
- `benchmarks/rag-impact/tools/r4_tool_schema.py` — strict tool + plan schemas,
  independent strict validator, wire normalisation.
- `benchmarks/rag-impact/tools/r4_bench_driver.py` — strict-schema enforcement,
  plan generation over the wire, frozen/hashed plan provenance, answer+pack
  freezing with sidecars, separate-process grading, explicit verification
  policy per arm.
- `benchmarks/rag-impact/tools/r4_grade.py` — independent C1–C6/C3 grader.
- `benchmarks/rag-impact/tools/r4_grade_selftest.py` — grader fixtures.
- `benchmarks/rag-impact/tools/r4_bench_driver_selftest.py` — end-to-end
  acceptance checks.
- `benchmarks/rag-impact/cases/c3-steps.json` — evaluator-only materials
  generated **verbatim** from the frozen rubric and checklist (hashes pinned).

## 3. Defects: confirmed, fixed, rejected

### Confirmed and fixed

| # | Defect | Reproduction before | After |
|---|---|---|---|
| 1 | Source reads were not admitted against the read budget; a `facts` call opened the file twice but recorded one op | a facts call at `read_ops = 63` still opened the file; `read_ops` under-counted | admission happens before any open on every path; the 64-op boundary admits exactly the last op and refuses the next (`read_budget_boundary_admits_the_last_op_and_refuses_the_next`) |
| 2 | `verify` could re-open pack items without accounting, and could partially read then fail | verify at the boundary read some items and errored midway | all re-opened items are admitted first; a refused verification performs no read |
| 3 | Arm B could run without a plan and silently fall back to the weaker question-only control (AR-09 identity loss) | `--plan-file` optional, no provenance anywhere | fresh blind plan generated over the wire contract, validated against a strict schema, frozen + hashed **before** execution, digest recorded in the workflow pack; question-only requires explicit `--allow-no-plan` and is labelled diagnostic |
| 4 | No grading pipeline existed; "C1–C6/C3" was a checklist with no executable evaluation | grading was `null` | independent grader computes all six criteria from artefacts, refuses stale/tampered/missing inputs, and cannot PASS an unsupported answer |
| 5 | Tool schemas were not verified against strict-mode rules | parameters were hand-built and untested | one source of truth plus an independent validator; a permissive or untyped schema fails the test suite |
| 6 | Receipts could not show whether an arm's answer was graded against a verified pack | packs were not frozen next to answers | answer and pack are frozen with sidecar hashes; the grader re-verifies both |
| 7 | Arm A's "no pack" was ambiguous in receipts | no pack, no policy | arm A freezes its delivered excerpts as an explicitly **unverified** pack and the receipt records `policy: no_verification_tool`; C2 penalises it |

### Rejected (not defects)

- `distinct_files ≠ read_files` for arm B: `read_files` counts files the engine
  opened, `distinct_files` counts files present in the delivered pack after the
  byte cap. Different quantities, both correct, now documented.
- Search/workflow latency above the warm gates: the synthetic run is
  process-per-call and cold, including model load. Reported as measured, not
  adjusted (preregistration §6).

## 4. Strict tool schema verification

- One definition per tool (`r4_tool_schema.py`); payloads are built from it.
- `strict_violations()` independently re-checks every generated schema:
  `additionalProperties: false` on every object, every property in `required`,
  explicit `null` unions for optionals, no free-form objects.
- Self-test asserts both directions: valid schemas produce **no** violations,
  and a smuggled property / opened root object **does** produce violations.
- Malformed arguments never reach the adapter: unknown tool, bool-as-int,
  path traversal, cross-arm tool, mixed address modes, reversed line spans are
  all rejected at the trusted boundary (each has a negative control).

## 5. Grading C1–C6/C3: implementation and limits

`r4_grade.py` runs in its own process and receives only: the frozen answer
(hash-checked), the frozen pack (hash-checked), the evaluator-only materials,
and the corpus (for C3 anchoring). It never receives the answerer's session,
gold answers or the checklist's expected outcomes.

Deterministic dimensions:

| Criterion | How it is computed | Status |
|---|---|---|
| C1 answerability | fraction of required steps present in the pack | MEASURED |
| C2 citation support | every claim must carry a citation resolving to a **verified** pack span with matching hash | MEASURED (structure); semantic support flagged `NEEDS_EVALUATOR` |
| C3 chain completeness | fraction of pack-supported required steps actually cited | MEASURED |
| C4 source vs deployment | conservative lexical detector for runtime/deployment assertions without a hedge | HEURISTIC (downgrade only) |
| C5 honest gaps | required steps absent from the pack must be declared/abstained | MEASURED |
| C6 false negatives | pack-supported required steps omitted from the answer | MEASURED |

Required steps are anchored honestly: each frozen checklist row is parsed
verbatim, its backticked identifiers are located **inside that case's frozen
gold spans**, and only then is the step machine-checkable. Steps that cannot be
anchored are reported `UNADDRESSABLE` and excluded — never guessed.

Hard failures (`UNGRADABLE`, exit 3): missing answer, missing pack, sidecar hash
mismatch, tampered pack, missing rubric, unauthored gold.

Fixture evidence (`r4_grade_selftest.py`, 15 checks): a fully supported answer
scores 12/PASS; a partially cited answer is penalised on C6; an uncited claim and
an out-of-pack citation both zero C2; an answer declaring its gap gets C5 = 2
but cannot reach PASS on incomplete evidence; stale, tampered, missing-rubric and
missing-pack inputs are UNGRADABLE.

Limits, stated plainly: C2 semantics, C4 intent and any "is this reasoning
sound" judgement still need an independent evaluator; the grader bounds them, it
does not replace them.

## 6. Source-read accounting receipts

Raw ledger, 36 runs (`three-arm-v3/three-arm-summary.json`,
`RECONCILIATION.json`):

| Arm | read_files (min/median/max) | read_ops | delivered bytes (median) | distinct = read_files |
|---|---|---|---|---|
| A | 10 / 11 / 14 | 12–15 | 39,488 | yes |
| B | 17 / 20 / 32 | 25–42 | 13,725 | no (byte-cap truncation) |
| C | 7 / 9 / 10 | 9–12 | 31,358 | yes |

Boundary and single-open behaviour is pinned by contract tests, not by prose:
last admissible read succeeds, next is refused before the file is opened,
verification of N items needs N admission slots or reads nothing.

## 7. Arm B blind-plan provenance

- Requirement: `run_with_plan()` executed through the benchmark driver.
- The driver runs a **separate** Responses exchange with an `emit_plan` tool
  (strict schema, source-free prompt: question + folder-hint paths only).
- The plan is validated (`queries` non-empty, no unknown fields, path grammar,
  caps), frozen to `plans/plan-<case>-<digest>.json`, hashed by content, and
  passed as `--plan-file`.
- The adapter hashes the **exact file bytes** and records
  `plan: {source: "preregistered-plan", sha256: <file digest>, queries, symbols,
  scopes}` inside the workflow pack.
- Verified in the self-test: the pack's `plan.sha256` equals the frozen file
  digest; a generated invalid plan fails closed; a plan-less run requires
  `--allow-no-plan` and is labelled diagnostic.
- Mock plan generation derives queries/symbols/scopes from the question and
  hints only; no gold, checklist or prior answer is consulted.

## 8. Complete synthetic three-arm end-to-end run

12 authored cases × 3 arms = 36 runs through the same code path as real
inference (only the HTTP transport is replaced by an in-process Responses-API
mock). Mock transport = 0 socket calls; loopback HTTP transport is exercised
separately in the self-test.

| Arm | verified packs | verdicts (12 cases) | mean total | mean C2/C4/C5 |
|---|---|---|---|---|
| A (search only) | 0/12 (no verification tool, policy explicit) | FAIL 12 | 4.58 | 0.83 |
| B (plan workflow) | **12/12** | PASS 4, PARTIAL 8 | 9.33 | 2.00 |
| C (multi-tool evidence) | **12/12** | PASS 1, PARTIAL 1, FAIL 10 | 6.75 | 1.39 |

Protocol violations: **0**. Plan-stage egress findings: **0**. Rounds and the
6-call cumulative pool held in every run.

Reading these numbers honestly: the mock answerer cites what it receives and
declares gaps when the pack lacks them. Arm B wins here because a verified
workflow pack usually contains the required declarations, not because a model
reasoned better. This is a pipeline receipt, not an answer-quality result.

## 9. Resource / safety / quality gates

| Gate | Result | Note |
|---|---|---|
| Bounded local ops warm p95 ≤ 1 s | **PASS** | status/verify p95 0.2 ms / 13 ms |
| Indexed FTS warm ≤ 1 s | **FAIL as measured** | cold process-per-call p95 4.66 s, model load included |
| Workflow investigation ≤ 3 s | **FAIL as measured** | cold p50 11.8 s (warm reference 3.1–4.7 s from earlier receipts) |
| Call/file/byte budgets | **PASS** | 6-call pool, 32-file/64-op reads, 24 KiB pack, 64 KiB delivered |
| Round limit ≤ 3 | **PASS** | extra turns refused at the trusted boundary |
| Adapter isolation | **READY (mechanical)** | 7 adversarial controls; model-level isolation NOT_MEASURED |
| Private-source egress | **none observed** | mock = no socket; plan prompts source-free; answerer data flow recorded |
| Answer quality C1–C6/C3 | **NOT_MEASURED** | mock answerer only; real grader ready |

Latency gates bind the product path, not this comparison (preregistration §6);
they are reported, not adjusted.

## 10. Residual blockers

1. **Owner authorization** for private-source inference: provider, exact
   model/served identity, endpoint, and the answerer's data flow must be
   approved and preregistered before any real run.
2. **Evaluator** for C2 semantics / C4 intent: the deterministic grader bounds
   these but cannot replace an independent judge.
3. **Cold-start latency** vs the frozen warm gates: a proven product-path
   blocker, not a benchmark-validity blocker; no optimization was attempted
   here.

## 11. Exact next authorized action

After explicit owner authorization of provider + model identity + endpoint:
run the frozen D01–D12 in all three arms through
`benchmarks/rag-impact/tools/r4_bench_driver.py --backend openai-responses
--authorized-inference`, then grade each frozen pair with `r4_grade.py` in a
separate process, and reconcile the receipts with `RECONCILIATION.json`
semantics. Stop at the owner gate for any private-source payload if the
approved scope differs from what the answerer receives.

## 12. Git

Branch `work/r4-evidence-representation-20261119`, base `eda88b1`, commits
listed in the handoff. Not pushed, not merged, nothing released.
