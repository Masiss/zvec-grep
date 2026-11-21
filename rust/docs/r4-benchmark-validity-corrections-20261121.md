# R4 final benchmark-validity corrections (2026-11-21)

Base checkpoint `43565f5` (branch `work/r4-evidence-representation-20261119`,
local, unpushed) → HEAD at the end of this mission.

Entry verdict from the independent review of `r4-benchmark-readiness-20261121.zip`
was **PARTIAL with confirmed benchmark-validity defects**. This document reports
what was reproduced, what was fixed, and what remains outside the harness.

No frozen target, case, gold span, checklist, threshold, budget, rubric
definition or historical baseline was changed. No embedding, index, ranking or
public contract was touched. No private source left the machine and no real
provider was called.

## 1. Executive verdict

**READY_FOR_AUTHORIZED_INFERENCE — SEMANTIC_EVALUATION_PENDING.**

The harness can no longer award a formal `PASS` to an answer merely because its
deterministic scores are high: a formal PASS now requires an independent semantic
verdict *with a rationale and evaluator identity*, on top of complete structural
criteria. Without that evaluator every case is honestly `NOT_MEASURED`.

The remaining gap is not implementation: it is a missing independent semantic
evaluator and the owner's private-source inference authorization.

This is not `QUALIFIED`, not `RAG_MVP_GO`, and picks no architecture winner.

## 2. Checkpoint

| Item | Value |
|---|---|
| Base | `43565f5` |
| Branch | `work/r4-evidence-representation-20261119` |
| Push | not performed |
| Worktree at handoff | clean |

## 3. Confirmed findings, reproduced then fixed

### 3.1 False PASS in grading (CRITICAL)

Reproduced exactly as reported: an answer asserting *"this method silently
deletes every employee and has no switch"* with a real, verified, in-pack
citation scored a full 12/12 and a formal `PASS`, because citation structure was
treated as support.

Fix — three layers that cannot collapse into one another:

- **structural**: does each claim carry a citation that resolves to a verified
  item of *this* pack, with a matching source hash; are required steps present
  and cited; did the answerer declare gaps.
- **semantic**: does the source support the claim. Supplied only by an external
  evaluator returning `PASS|FAIL` + non-empty rationale + evaluator identity.
  Anything else is `INVALID`, never PASS.
- **formal**: `PASS` requires structural completeness **and** a semantic PASS
  and no zero on any criterion. No evaluator → `NOT_MEASURED`. Invalid
  evaluation → `UNGRADABLE`. Zero on any criterion → `FAIL`.

Structural results stay available as a diagnostic
(`structural_measured_total`, `structural_complete`, `structural_missing`) and
are never presented as the graded verdict.

### 3.2 C5 honest-gap grading (HIGH)

`analyse_gaps()` merged `pack.gaps` into the answerer's declarations, so an
engine gap silently counted as the answerer telling the user.

Fix: the engine gap is the *obligation*; only the answerer's own structured
`gaps`/`abstain` are declarations. Scoring now distinguishes: obligation +
declared (2), obligation + abstain without naming (1), obligation + silence (0),
no obligation (2), fabricated gap (1), abstention on a complete pack (0).

### 3.3 Real Arm B planner used the arm tool set (HIGH)

Reproduced: the real backend constructed `HttpTransport`, so the planning
request advertised `status, verify, workflow` instead of `emit_plan`.

Fix: `PlanTransport` (request construction, prompt, hints, strict `emit_plan`
schema) is now shared; `MockPlanTransport` and `HttpPlanTransport` differ only in
transport I/O. `validate_plan_request()` runs in every planner before the
request leaves the process; the loopback test server independently rejects a
request whose tools are not exactly `emit_plan`, whose prompt lacks the question
or the permitted hints, or which carries evaluator-only material.

### 3.4 Fail-closed provenance (HIGH)

Reproduced: a workflow item without a `verified` field defaulted to verified; a
missing sidecar was tolerated; pack selection used "last file in a glob";
citations without a source hash were accepted.

Fix — one enforced grading artifact contract:

1. answer sidecar and pack sidecar are mandatory (`require_sidecar`);
2. the answer artifact names the exact pack (`pack.sha256` + kind) and the
   grader refuses a mismatch — a swapped pack cannot be graded;
3. pack selection is the exact file name, or exactly one pack in `--packs`;
4. `verified` is `True` only when the representation says so; `observed`,
   `unverified` and *missing* are all not verified, and the state is reported;
5. every citation must carry the source `sha256` and it must equal the pack
   item's hash, not merely overlap it.

### 3.5 Frozen question identity (HIGH)

Reproduced: `--question-file` could send different text while the case identity
still claimed the frozen question.

Fix: `resolve_question()` hashes the exact text and classifies it
`frozen-case` / `override-diagnostic`. A mismatch is refused unless
`--allow-question-override` is passed, and the run is then explicitly
non-formal. The receipt, the answer artifact and the report carry the digest of
the text that was actually sent.

### 3.6 Final answer contract (HIGH)

Reproduced: a prose answer was silently graded as "no claims", which reads as a
quality failure.

Fix: every arm's tool set now ends with a strict `submit_answer` tool
(`answer`, `claims[{text, citations[{path,start_line,end_line,sha256}]}]`,
`gaps`, `abstain`). Validation rejects prose, malformed JSON, missing fields,
empty citations, path traversal, reversed ranges and `abstain` combined with
claims — all recorded as `INVALID_OUTPUT` and graded `UNGRADABLE`, never as a
wrong answer. The instructions state the contract and a run whose instructions
omit it is refused.

### 3.7 Source-read accounting (HIGH)

Three distinct holes, all reproduced:

- **search**: a file already in `read_files` was re-read even with no operation
  left, because `known` bypassed admission;
- **evidence**: admission reserved one operation per distinct file while
  execution opened one per served span, so a multi-span request could read more
  than it paid for;
- **facts**: a UTF-8 decode failure happened after the bytes were read but
  before accounting, so the ledger reported fewer accesses than occurred.

Fix: read accounting is recorded **at the moment of the open**
(`note_source_read`) and merged into the ledger on every exit path, success or
failure; pending reservations are visible to the next admission; evidence
admission is per served *span*; search admits an operation before opening,
including for known files; path resolution that never opens records nothing.
`ToolOutput` no longer carries self-reported read counters, so there is exactly
one source of truth. Every call's `read_ops_delta` sums to the session counter
(`history_read_ops_reconcile_with_the_ledger`), which is what the receipts use.

## 4. Before/after reproductions

| Finding | Before | After |
|---|---|---|
| Wrong-but-cited answer | 12/12, formal `PASS` | `NOT_MEASURED` without an evaluator; `FAIL` with a semantic FAIL |
| Pack gap, silent answer | C5 = 2 | C5 = 0 |
| Real planner tool set | `status, verify, workflow` | `emit_plan` only (server-enforced) |
| Missing `verified` field | treated as verified | not verified, state reported |
| Missing sidecar | graded | `UNGRADABLE` |
| Pack swapped after freeze | graded | `UNGRADABLE` (binding mismatch) |
| Citation without hash | accepted | invalid citation |
| `--question-file` override | silent | refused in formal mode; non-formal when allowed |
| Prose answer | graded as no claims | `INVALID_OUTPUT` → `UNGRADABLE` |
| Search with 0 ops left | still read the file | item dropped, typed gap, no read |
| 3 spans, 1 op | 1 op admitted, 3 reads | only paid-for spans served |
| UTF-8 decode failure | read not counted | read counted |

## 5. Evidence

| Check | Result |
|---|---|
| Adapter contract tests | **45/45** |
| `zg-engine` lib tests (adapter-only change) | 802/804 in parallel; the two `config::tests` failures are the known pre-existing collision on a shared config path (14/14 when run serially, each passes alone) |
| clippy (adapter, all targets) | no errors |
| Harness acceptance (`r4_bench_driver_selftest.py`) | **76 checks PASS** |
| Grader fixtures (`r4_grade_selftest.py`) | **32 checks PASS** |
| Synthetic three-arm E2E | **36/36 runs**, 0 violations, 0 egress findings |
| Answer artifacts | 36/36 `OK`, 36/36 bound to their pack |
| Read-delta reconciliation | 36/36 receipts reconcile |
| Formal PASS without an evaluator | **0** (by construction, asserted) |

Receipts: `/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v4/`
(`three-arm-summary.json`, `RECONCILIATION.json`, 36 session dirs).

## 6. The three-arm result, stated honestly

All 36 synthetic runs are `NOT_MEASURED`, because no semantic evaluator ran.
That is the designed outcome, not a failure: the mock answerer's claims are
pipeline probes, not investigation. Per-arm structural diagnostics (median
`read_ops`, bound packs, verification policy) are in `RECONCILIATION.json`.

The semantic layer is demonstrated on the same artifacts two ways:

| Case | Without evaluator | With evaluator fixture |
|---|---|---|
| D11 arm-c | `NOT_MEASURED` (structural 10/10 measured) | `PASS` |
| D04 arm-c | `NOT_MEASURED` (structural 6) | `FAIL` (structural zeros stand) |

A structural failure is never rescued by a passing evaluator, and a passing
evaluator never lifts an unevaluated structure to PASS.

## 7. Gate matrix

| Gate | Result |
|---|---|
| Structural ≠ semantic separation | **PASS** (0 formal PASS without evaluation) |
| C5 obligation vs declaration | **PASS** (6 fixtures) |
| Real plan contract | **PASS** (loopback, server-enforced) |
| Frozen question identity | **PASS** (4 fixtures) |
| Grading provenance fail-closed | **PASS** (8 fixtures) |
| Answer contract | **PASS** (7 fixtures) |
| Source-read accounting | **PASS** (7 boundary tests + receipts) |
| Adapter isolation | READY (mechanical); model-level NOT_MEASURED |
| Private-source egress | none observed (mock, source-free plan prompts) |
| Answer quality C1–C6/C3 | **NOT_MEASURED** — needs an evaluator and authorized inference |
| Bounded local ops warm p95 ≤ 1 s | PASS (status/verify p95 ≤ 13 ms) |
| Indexed search / workflow warm gates | FAIL as measured cold (process-per-call); reported, not adjusted |

## 8. Residual blockers

1. **Independent semantic evaluator** for C2 support and C4 intent. The
   interface, validation and formal-verdict rules exist; the judge does not.
   Until it exists, real runs will report `NOT_MEASURED`, which is correct.
2. **Owner authorization** for private-source inference: provider, exact
   model/served identity, endpoint, and the answerer's data flow (question,
   tool schemas, and every delivered span with its hash).
3. **Cold-start latency** against the frozen warm gates — a product-path issue,
   not a benchmark-validity one.

## 9. Exact next authorized action

After owner authorization: preregister the evaluator (identity, prompt, what it
sees, how disagreements are recorded), then run D01–D12 × 3 arms with
`--backend openai-responses --authorized-inference --grade`, and grade each
frozen pair in a separate process with `--semantic`. Report structural and
semantic layers separately; never collapse them.

## 10. Git

Branch `work/r4-evidence-representation-20261119`, base `43565f5`, commits
listed in the handoff. Not pushed, not merged, nothing released.
