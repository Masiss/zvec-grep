# R4 correction & end-to-end validation — AR-01..AR-12 closure (2026-11-20)

Base `4d19b05` → correction HEAD `836aeec` (branch
`work/r4-evidence-representation-20261119`, local, unpushed). The previous
verdict `READY_FOR_BENCHMARK` was corrected by owner-side review to
`PARTIAL — NOT_READY_FOR_FORMAL_BENCHMARK`; this mission addressed the
review findings, re-qualified the harness end-to-end and froze the
three-arm preregistration. No frozen case, gold, checklist, threshold,
corpus, index or model was changed.

## 1. AR-01..AR-12 closure matrix

| Finding | Disposition | Root cause / evidence | Correction | Verification | Residual risk |
|---|---|---|---|---|---|
| **AR-01** driver only had `MockAnswerer` | Confirmed, fixed | Real inference interface absent. | `OpenAIResponsesBackend` implements the openai-responses client with tool-calling mediation; records requested/served model identity, fingerprint, tokens, latency; refuses to run without `--authorized-inference`. | Mock-transport loop: legal search executed; unknown + malformed tool calls are violations; served-model identity recorded; final answer frozen+hashed; real backend refuses without the authorization flag. | **MOCK_VERIFIED only** — real provider behavior untested until the privacy gate opens. |
| **AR-02** workflow re-granted a full budget per invocation | Confirmed, fixed | `workflow::run` got a fresh `WorkflowBudget` each call. | Budgets derive from the *remaining* session pool: shared six-call pool (`internal_calls` counter), remaining reads/files/rounds. | `engine_managed_work_never_regrants_the_shared_call_pool`; binary control: first workflow consumes 1+5=6, second refuses `no_calls_left`. | None known. |
| **AR-03** unbounded free-slot verify | Confirmed, fixed (design) | Free verify with no bound. | Verify is bounded by full byte accounting + the hard 64 KiB delivered cap and the read-op accounting (revalidation re-opens pack files, counted); no once-per-pack exception (a once-per-pack gate would have *hidden* post-verification staleness — rejected deliberately). | Budget-exhaustion control: verify still possible after all six slots; repeated verify bytes accumulate into the same cap. | Repeated clean verification costs bytes; acceptable. |
| **AR-04** stale verification state persisted | Confirmed, fixed | `verified=true` survived a failed revalidation. | Revalidation failure clears the persisted `verified` state; history keeps the event. | `evidence_revalidate_detects_source_change_and_marks_pack` (mutate → verify → persisted false). | None known. |
| **AR-05** crash accounting gap | Confirmed, fixed | Reservation/commit not persisted before work. | Admission is a persisted reservation (`calls_used+1`, `in_flight`) before execution; a crash between reservation and commit makes the session `terminal: invalidated_by_crash` with the budget consumed; committed-but-unpublished windows over-account (fail-closed direction). | Binary kill sweep: 1s/2s/4s kills leave `calls_used=1, in_flight` and refuse further investigation; Rust sweep covers pre/post-reservation/commit windows (12 kill points). | A crash mid-call invalidates that case run — accepted for benchmark validity. |
| **AR-06** delivered-file count substituted for actual source reads | Confirmed, fixed | Hashing/reading not accounted as reads. | Separate `read_files` (≤32) and `read_ops` (≤64) accounting at the actual access points: search hashes only FRESH admitted items; facts counts its open; evidence counts pack-item and stale-gap file opens; verify counts revalidation opens; symbol is metadata-only (no source access). | Budget displays `read_files_left`; search on 37 files delivers exactly 32 with explicit gaps; receipts show read_ops. | Read-*op* cap 64 is an adapter accounting bound; the frozen contract's per-case read semantics remain the workflow stats for Arm B. |
| **AR-07** validator accepted invalid paths/types | Confirmed, fixed | PATH_RE allowed `..` segments; Python bool is an int. | Strict segment check (`.`/`..` rejected), `type(x) is int` for ints, cross-arm tool rejection, answerer `round` keys are violations, unknown-arg rejection. | Selftest adversarial suite: 11 policies (shell, injection, scope-traversal `a/../../outside`, `a/../b.cs`, absolute, bool round, float limit, unknown arg, cross-arm workflow, 11-item evidence) all rejected with zero state change. | None known at the controller boundary. |
| **AR-08** pack identity/publication (+2048 allowance, phantom risk) | Confirmed, fixed | Publication decided by estimate, identity unchecked on load. | Two-phase commit: pack persisted only after the final envelope fits (no fixed allowance); `load_pack` verifies the stored bytes against the recorded SHA-256 (tamper fail-closed). | `tampered_pack_file_fails_closed_on_verify`; `evidence_refused_for_budget_stores_no_phantom_pack`; budget-block exactness control. | None known. |
| **AR-09** arm-b control identity (`run` vs `run_with_plan`) | Confirmed, fixed | The corrected baseline32 (0.4861) is plan-based; the adapter called the weaker no-plan workflow. | Arm B accepts a preregistered blind plan (`--plan-file`, `run_with_plan`); preregistration fixes B-plan (fresh blind plan, accounted) as primary, frozen-plan reproduction and no-plan as explicitly labeled diagnostics. | Real-corpus receipt (D02, frozen blind plan): 10 verified spans, 23.8 KiB selected, reads 32/32, shared pool 6/6, wall 5.5 s; plan provenance asserted in the define stage. | Reproduced selection differs from historical bytes (post-E2 containment code); numbers are reported as reproduced, never tuned back to 0.4861. |
| **AR-10** workflow not validated / budget bypass via nesting | Confirmed, fixed | Driver contract lacked `workflow`; nested re-grant (see AR-02). | `workflow` added to the arm-b contract (question ≤2000 chars); shared pool accounting. | Cross-arm policy rejected under arm-c/a; arm-b mock run accounted 1 external + 5 internal calls. | None known. |
| **AR-11** round enforcement trusted answerer declaration | Confirmed, fixed | `--round` was self-reported metadata. | Rounds are controller-owned (one model turn = one round, cap 3); answerer round keys are contract violations; adapter still bounds rounds non-decreasing. | Selftest: `bool-round` policy rejected; three-arm mock run shows controller rounds 3/2/3. | Round semantics remain the declared interpretation of the R0 stage notion (preregistered, not post-hoc). |
| **AR-12** provenance mislabel (current hash on stale indexed snippet) | Confirmed, fixed | Search attached the current file hash to possibly-stale indexed content. | Only FRESH items get a current-file hash (and only when the read budget admits the file); stale items are delivered flagged `possibly_stale` without a hash; observed/verified semantics unchanged in evidence. | Contract suite asserts fresh-only hashing and stale flags; read-budget gaps never hash unread files. | Overload ambiguity remains the answerer's responsibility (symbol/facts list all matching declarations). |

Two additional defects were found and fixed during correction work
(both caught by the suite, not by review):
- The delivered-byte fixpoint had an unbounded variant that could spin
  (digits oscillate); bounded to 8 iterations with a conservative exit,
  and the same fixpoint is used on every envelope path (success, degrade,
  pack commit, error).
- The facts degrade decision measured the compact form while delivering
  the pretty form (the audit's compact/pretty defect class, residual
  instance) — now measured on the delivered (pretty) form; the whole-file
  facts dump on the real corpus degrades to an explicit summary gap.

## 2. Verification states

- **IMPLEMENTED + TESTED_PASS**: adapter corrections; 34/34 contract
  tests (incl. crash-reservation sweep, shared-pool, tamper, exact
  budget-block, plan provenance); engine default suite 802–803/803 with
  the documented pre-existing `config::tests` parallel-lock flake (passes
  in isolation; unrelated to this worktree).
- **MOCK_VERIFIED**: provider backend mediation loop, answer freeze
  hashing, real-backend authorization refusal; three-arm end-to-end
  harness (A/B/C scripted policies: all completed under budget, rounds
  controller-owned, arm-b internal accounting 1+5=6, arm-c verified pack,
  answers frozen + SHA-256 verified).
- **READY_FOR_AUTHORIZED_INFERENCE**: harness + preregistration
  (`r4-three-arm-preregistration-20261120.md`) are frozen and executable.
- **OPERATION_BLOCKED**: the real three-arm evaluation awaits the
  owner's private-source egress authorization for the identified
  `openai` endpoint (model identity fixed per §3 of the
  preregistration); push authorization remains outstanding.
- **NOT_MEASURED**: answer quality C1–C6/C3; real-model isolation
  quality; real provider identity/latency/tokens.

## 3. Resource measurements (post-correction, warm, n=5, fresh session each)

status 0.01 s; rg 0.03 s; facts (honest summary path) 0.03 s; evidence
0.01 s; verify 0.01 s; indexed FTS 0.50 s; hybrid 1.23–1.35 s; Arm B
workflow (question-only, fresh session) median 3.1 s. Per-call budget
fixpoint adds no measurable latency. Gate matrix unchanged from the
preregistration §6: bounded ops + FTS PASS the warm ≤1 s gate;
hybrid/vector and the workflow investigation wall FAIL as measured
(process-per-call model) — recorded, not adjusted.

## 4. Bundle provenance note

The earlier review bundle (`git/identities.txt` = `ac7610d`) was built
before the final handoff docs commit (`4d19b05`, docs-only: 2 files).
The delta bundle for this correction references `4d19b05..836aeec` and
supersedes nothing already reviewed.
