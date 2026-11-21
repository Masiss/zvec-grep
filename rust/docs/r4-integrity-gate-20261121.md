# R4 integrity gate & semantic-evaluation preparation (2026-11-21)

Base `77e681e` (branch `work/r4-evidence-representation-20261119`, local,
unpushed) → HEAD at the end of this mission.

Three groups of findings from the independent review of
`r4-validity-corrections-20261121.zip` were reproduced, fixed and verified:

1. semantic grading consistency and provenance;
2. source-read accounting at the actual I/O boundary;
3. Arm B plan provenance across the whole execution chain.

No frozen target, case, gold span, checklist, rubric definition, budget or
baseline changed. No embedding, index, ranking or public contract was touched.
No private source left the machine.

## 1. Executive verdict

**READY_FOR_AUTHORIZED_INFERENCE — SEMANTIC_EVALUATION_PENDING.**

A formal verdict now requires all of: a complete structural evaluation, a
semantic evaluation from a *registered* evaluator, that evaluation bound to the
exact answer/pack/question/rubric digests being graded, internally consistent
claim judgements, and a formal (non-overridden) case identity. Any missing piece
yields `UNGRADABLE` or `NOT_MEASURED` — never `PASS`.

Source reads are counted where they actually happen and cannot be lost on
failure paths; Arm B's plan identity closes from generation to the workflow pack
in 12/12 synthetic runs.

## 2. Findings: reproduced, fixed

### 2.1 Semantic verdict could contradict its own judgements (CRITICAL)

Reproduced: an evaluation with `"verdict": "PASS"` and `"unsupported_claims": [0]`
still produced a formal `PASS` (C2 = 2), because `unsupported_claims` only
affected scoring when the verdict was `FAIL`.

Fixed — the semantics contract is now explicit and enforced:

| Rule | Result if violated |
|---|---|
| verdict ∈ {PASS, FAIL} | INVALID |
| non-empty rationale | INVALID |
| evaluator identity present | INVALID |
| `PASS` with any unsupported claim | INVALID |
| `FAIL` without an unsupported claim | INVALID |
| every claim judged exactly once (`supported ∪ unsupported`) | INVALID |
| a claim cannot be both supported and unsupported | INVALID |

`INVALID` maps to `UNGRADABLE`; it can never become `PASS`.

### 2.2 Evaluator authority was caller-asserted (CRITICAL)

Reproduced: any `evaluator_id` string, including `totally-unknown-evaluator`,
was accepted as sufficient identity.

Fixed: `benchmarks/rag-impact/cases/evaluators.json` is a trusted registry
(`evaluators.<id>.trusted = true`, empty by default) with a documented
registration procedure. An unregistered, forged or untrusted identity is
`INVALID`. The answerer cannot mint a judge.

### 2.3 Evaluation was not bound to the graded inputs (CRITICAL)

Reproduced: an evaluation carried no reference to the artifacts it judged.

Fixed: an evaluation must name `inputs.{case, answer_sha256, pack_sha256,
question_sha256, rubric_sha256}`, and every value must equal what the grader is
actually scoring. A missing field is rejected; a mismatch is rejected with the
specific field. Consequence: a favourable verdict cannot be replayed onto a
different answer, and swapping the answer or pack after evaluation invalidates it.

### 2.4 Non-formal (diagnostic) runs received formal verdicts (HIGH)

Reproduced: `question.source = override-diagnostic`, `question.formal = false`
plus a semantic PASS produced a formal `PASS`.

Fixed: the grader itself now resolves the frozen question digest from
`cases/dev.json` and requires `question.formal is True` **and** a matching
digest. Otherwise `formal_eligible = false` with an explicit `ineligibility`
reason, the verdict is `NOT_MEASURED`, and the artifact can never be reported as
a frozen benchmark result. Editing `formal: false` to `true` does not help: the
digest check still fails.

### 2.5 Source reads were inferred, not observed (HIGH)

Reproduced: reads were recorded after engine operations returned, so
`assemble_spans` reading a file whose span was then dropped (stale hash, bad
range), `revalidate_spans` opening items before failing, and workflow reads
performed before an engine error could all be under-counted.

Fixed at the I/O boundary, three ways:

- the engine exposes `assemble_spans_observed` and `revalidate_spans_observed`
  (plus `revalidate_observed`); the existing public functions delegate with a
  no-op observer, so no production contract changed;
- the adapter's `EngineSource::read` records every workflow read attempt before
  opening, so a later engine failure keeps them;
- the adapter counts exactly those observed opens, and fails closed
  (`read_accounting_mismatch`) if the engine's own counter exceeds what the
  boundary saw, or if verification opens a file that is not in its pack.

Admission stays ahead of access: evidence reserves one operation per distinct
request path (an upper bound on the assembler's per-file cache), search admits
before opening a known file, verify reserves for every item it will re-open, and
workflow's engine budget is derived from what remains.

### 2.6 Arm B plan provenance was missing from the receipts (MEDIUM)

Reproduced: 12/12 Arm B rows in the previous summary carried
`plan_sha256: null`, because the value lived only in the CLI report and not in
the driver's own report.

Fixed: `drive()` computes the digest of the exact plan file it hands to the
adapter, and after the call compares it with the `plan.sha256` recorded inside
the workflow pack, appending a violation if the chain does not close. The CLI
additionally checks `generated == handed-to-adapter == pack` and exits with a
contract error otherwise. Synthetic runs now show 12/12 closed chains, and plan
generation usage (requests, tokens, latency) is reported separately per case.

## 3. Negative controls (executed independently)

Grader fixtures (`r4_grade_selftest.py`, 46 checks) include every required
control, each with its expected verdict fixed in the test before execution:

| Control | Expected | Result |
|---|---|---|
| Semantic PASS + unsupported claim | UNGRADABLE | PASS |
| Semantic PASS + invalid citation support | no formal PASS | PASS |
| Semantic PASS + unjudged claim | UNGRADABLE | PASS |
| Semantic FAIL + unsupported claim | FAIL | PASS |
| Missing semantic evaluator | NOT_MEASURED | PASS |
| Forged / unknown / untrusted evaluator id | UNGRADABLE | PASS |
| Wrong answer / pack / question / rubric digest | UNGRADABLE | PASS |
| Wrong case identity in evaluation | UNGRADABLE | PASS |
| Diagnostic question in a formal run | NOT_MEASURED | PASS |
| Replayed evaluation onto another answer | UNGRADABLE | PASS |
| Evaluation binding no inputs | UNGRADABLE | PASS |
| Valid synthetic PASS | PASS | PASS |
| Valid synthetic FAIL | FAIL | PASS |

Harness controls (`r4_bench_driver_selftest.py`, 79 checks) include the
end-to-end wrong-but-cited answer (formal `NOT_MEASURED`, never PASS), the
contradictory evaluation over real artifacts (never PASS) and the unregistered
evaluator (UNGRADABLE).

## 4. Source-read failure injection

Adapter contract tests (48) now include, with the ledger checked against the
observed opens rather than only a final total:

1. fail before any read → no invented read (path-resolution case asserts 0);
2. read one file then fail → the read is kept (decode failure asserts 1);
3. several files, one deleted → only the readable file counts, the deleted one
   was never opened (asserts 1, not 2);
4. repeated reads of a known file → the operation counter increases each time;
5. distinct-file budget enforced independently of the operation counter;
6. exactly one operation left → the operation is admitted;
7. no operation left → the operation is refused before any source access;
8. evidence read but not served (stale expected hash) → counted;
9. verification re-opening a changed file → both opens counted, `verified=false`;
10. workflow reads are recorded at `EngineSource::read`, and the call fails
    closed if the engine reports more than the boundary observed;
11. facts decode failure → counted;
12. crash/cancellation → terminal-invalidated, budget stays consumed.

Per-call `read_ops_delta` still sums to the session counter in 36/36 receipts.

## 5. Three-arm synthetic E2E

Real controller, real adapter, Responses-API payloads, terminal
`submit_answer`, frozen question identity, boundary-accounted reads,
independent grader process.

| Metric | Result |
|---|---|
| Runs | 36 (12 authored cases × 3 arms) |
| Protocol violations | 0 |
| Answers `OK` | 36/36 |
| Packs bound to answer | 36/36 |
| Read-delta reconciliation | 36/36 |
| Arm B plan chain closed | 12/12 |
| Formal verdicts without an evaluator | 36 × `NOT_MEASURED` |
| Formal PASS | 0 |

With a *fixture* evaluator applied to the same artifacts (clearly labelled as a
harness fixture, not real semantic grading): 5 PASS, 1 PARTIAL, 18 FAIL, 12
NOT_MEASURED — arm A stays `NOT_MEASURED` because it has no verification
capability (`provenance_blocked`), which is a capability statement, not a
reasoning judgement.

Receipts: `/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v5/`.

## 6. Gate matrix

| Gate | Result |
|---|---|
| Semantic consistency (verdict vs judgements) | PASS |
| Evaluator authority (registry) | PASS |
| Evaluation ↔ frozen input binding | PASS |
| Non-formal artifacts cannot be formally graded | PASS |
| Reads accounted at the I/O boundary | PASS |
| Admission before source access | PASS |
| Plan chain (generate → CLI → pack → receipt) | PASS |
| Three-arm synthetic E2E | PASS |
| Answer quality | NOT_MEASURED (no evaluator) |
| Real provider readiness | BLOCKED (owner authorization) |
| Resource gates | unchanged; cold latency reported as measured |

## 7. Residual limitations

1. **No independent semantic evaluator exists yet.** The contract, registry,
   binding and consistency rules are implemented and fixture-tested; until an
   owner-approved evaluator is registered, every real case will be
   `NOT_MEASURED`. This is intentional.
2. **Workflow failure after partial engine reads** is covered structurally by
   the boundary observer and the mismatch guard, but no deterministic synthetic
   case forces the engine to fail mid-expansion; the guard is the safety net and
   is exercised only on its success path.
3. **Cold-start latency** (process-per-call) still exceeds the frozen warm gates;
   reported as measured, not adjusted.

## 8. Exact owner decisions still required

1. **Register a semantic evaluator** (identity, model/version or named human,
   prompt, artifacts it may read) in `cases/evaluators.json`, or authorize one.
2. **Authorize private-source inference**: provider, exact model and served
   identity, endpoint, and the answerer's data flow.

## 9. Git

Branch `work/r4-evidence-representation-20261119`, base `77e681e`, commits in
the handoff. Not pushed, not merged, nothing released.
