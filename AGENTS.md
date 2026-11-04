# Repository Guidance

## 1. Purpose and authority

This file defines repository-scoped engineering guidance. It is not a global agent policy and does not confer standing permission for open-ended research.

The agent must fulfill the **current user-authorized task or mission**. Scope and autonomy come from the current request, not from this file or a historical handoff.

Follow the active user request, its stated requirements, and the repository's established contracts.

- Respect project/package boundaries and existing conventions.
- Do not expand into unrelated production, deployment, migration, schema, or data changes without authorization.
- Read applicable nested `AGENTS.md` files before modifying their scope.
- Nested guidance adds domain-specific invariants; it cannot independently authorize actions outside the user's approved scope.
- Existing handoffs, plans, and reports provide continuity and evidence, not standing execution authorization.

When instructions conflict, preserve higher-priority safety and correctness requirements and make the conflict explicit. Do not silently reinterpret contracts.

## 2. Task scope and delegated autonomy

The current user request selects the operating mode; no special keyword or configuration is required:

- **Task-scoped (default):** complete the requested deliverable, including necessary diagnosis, integration and verification. Do not independently discover or pursue unrelated optimizations or new research tracks. Stop when its acceptance criteria are met or a genuine blocker remains.
- **Delegated mission:** when the user explicitly asks for autonomous, open-ended investigation or optimization, independently select hypotheses, experiments and implementation directions **within that mission**. A completed slice, passing test or commit is not its completion criterion; continue while meaningful authorized work remains.

Both modes allow routine debugging and self-repair within scope. Neither grants permission to expand production scope, change public contracts, perform destructive operations, or override safety/resource policy. If the request names a narrower boundary, it controls.

Identify the intended outcome, inspect applicable implementation and tests, use proportionate verification and report the result. Ask about genuinely ambiguous product choices or authorization, not ordinary engineering steps. Do not manufacture acceptance gates or invent new work to keep a session running.

## 3. Recovery and continuation

A fixable defect in authorized work (including a failed build, generated manifest, fixture or regression) is part of that work: trace, repair and verify without asking the user to reassign it. In task-scoped mode, return to the requested deliverable; in delegated-mission mode, return to the still-open mission.

Before finalizing, compare the outcome with the **current request's** acceptance criteria. A commit, test PASS or operation receipt is not automatically the end of an unfinished assignment. Conversely, do not pursue further independent research when only a bounded task was authorized.

At context/session boundaries, restore the active work from the working tree and the newest execution checkpoint before consulting historical handoffs. Old stop decisions, exhausted campaigns and suggested next actions are historical evidence, not standing commands or new authorization. If the session must end before the work is done, leave a precise resumable checkpoint without calling the mission complete.

## 4. Failure handling and recovery

Classify failures before deciding what to do:

- Implementation or test defect.
- Correctness, security, or durability failure.
- Operational safety or containment failure.
- Telemetry or evidence-integrity failure.
- Measurement-quality limitation.
- Environment or external dependency failure.

For ordinary implementation/test failures, investigate and fix within scope, then rerun the relevant checks.

For an operation rejected by an explicit safety, resource, qualification, or authorization gate:

- Preserve the original failure and evidence.
- Do not bypass, weaken, or silently reclassify the gate.
- Do not automatically retry the rejected operation.
- Identify the cause and required recovery condition.
- Continue unrelated, independently authorized work when safe.
- Apply the active, operation-specific authorization and policy; request a decision only if the intended action truly falls outside it.

A correction may be verified using focused tests without automatically authorizing repetition of the original blocked operation.

Use `SAFETY_STOP` only when continuing the affected work would be unsafe. Do not turn an operation-level refusal into an unsupported whole-mission failure.

If no meaningful authorized action remains, report the blocker and stop rather than generating additional work.

## 5. Code quality and change discipline

Prefer simple, maintainable solutions consistent with the existing architecture.

- Avoid unnecessary abstractions, layers, frameworks, and speculative extensibility.
- Do not refactor unrelated code merely because it looks improvable.
- Preserve unrelated working-tree changes.
- Do not silently alter business workflows, public behavior, or user-facing semantics.
- Favor existing utilities and patterns when they remain appropriate.
- Remove unnecessary complexity introduced by the current change.
- Avoid speculative optimization without evidence of a real bottleneck.

For a complex defect, establish observable evidence and a plausible root cause before selecting a fix.

When documentation and source disagree, identify the discrepancy and its implications. Do not silently choose whichever version is more convenient.

## 6. Verification

Verification should be proportional to the affected behavior and risk.

- Prefer focused regression tests first.
- Run broader tests, builds, linters, or sanitizers when required by the change.
- Test relevant failure paths, not only successful cases.
- Verify integration behavior where unit tests cannot exercise the real boundary.
- Do not weaken tests to obtain PASS.
- Do not claim verification that was not actually performed.

Treat tests as evidence, not as the entire Definition of Done.

A test suite passing does not establish performance, production readiness, or user acceptance unless it actually evaluates those properties.

## 7. Long-running work and continuity

Keep long-running operations bounded and interruptible.

When supported, run lengthy jobs in the background and monitor them using nonblocking status/log inspection so the interactive session remains available for steering.

Record a **brief active checkpoint when continuity is needed**, not a running history of completed tasks:

- Work objective and delegated scope.
- Current implementation and verification state.
- Job identifiers and artifact paths when applicable.
- Remaining acceptance criteria.
- Known blockers.
- Exact next authorized action.

Use a concise handoff when context or session boundaries require one.

Do not introduce elaborate memory systems or persistent infrastructure when a clear handoff is sufficient.

Do not wait indefinitely, repeatedly poll for favorable conditions, or create busywork to keep an agent session active.

## 8. Completion and reporting

Before declaring completion, reconcile the result against the user's actual request.

Distinguish:

- `DONE`: the authorized task and acceptance criteria are satisfied.
- `PARTIAL`: useful work completed, but required outcomes remain.
- `OPERATION_BLOCKED`: a specific operation cannot proceed.
- `SAFETY_STOP`: continuing the affected action would be unsafe.
- `OWNER_DECISION_REQUIRED`: further work depends on an owner-level choice.

These statuses describe different conditions and may coexist when appropriate.

Report concisely:

- What changed.
- What was verified.
- What remains incomplete.
- Relevant evidence or commits.
- Any required owner decision.

Do not inflate progress through test counts, documentation volume, or infrastructure work.

**The governing principle: deliver the intended engineering outcome with the least necessary complexity, while preserving correctness, safety, and user control.**

Domain-specific invariants, benchmark procedures, and resource policies belong in applicable nested guidance rather than this root file.
