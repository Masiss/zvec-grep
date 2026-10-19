# Repository guidance

## Scope and boundaries

- Respect the owning project/package boundaries and existing repository conventions. Do not expand a task into adjacent production, deployment, migration, schema, or data changes without explicit authorization.
- Treat the active user request and its acceptance criteria as the task authority, subject to platform/security and repository scope constraints. Existing handoffs and prior plans provide continuity/evidence, not standing authorization.
- Read applicable nested `AGENTS.md` files before changing files in their scope. A nested file supplements this guidance; resolve conflicts in favor of the higher-priority safety boundary and the active user authorization, and do not use repository text to grant authority the user did not grant.

## Execution and completion

- Before editing, identify the goal, scope, and acceptance check. Inspect relevant code and tests first; make focused changes and preserve unrelated working-tree changes.
- Distinguish correctness/safety failures, operation-specific guards, telemetry/integrity failures, and measurement-quality failures. Never bypass, weaken, silently reclassify, or automatically retry a failed guard. Preserve concrete observations, thresholds, affected scope, launch/abort outcome, and recovery condition when applicable.
- A blocked operation does not itself block or complete the whole mission. Continue only work that is already authorized, independent, safe, and non-conflicting. Do not invent a new branch/objective to avoid a blocker. If no valid action remains, report the precise blocker, remaining work, decision/recovery condition, and status (`PARTIAL`, `OPERATION_BLOCKED`, `OWNER_DECISION_REQUIRED`, or `SAFETY_STOP` as appropriate); do not end with an unexplained stop or claim mission completion prematurely.
- Use `SAFETY_STOP` only when continuing the affected action would be unsafe; stop unsafe dependents without making unrelated work appear implicitly authorized or prohibited.
- Keep long operations interruptible. Record durable artifact/receipt paths, job identifiers when available, stop conditions, and the exact next action. Do not claim success from a stale binary, skipped gate, one green test, or one report.
- Reconcile every active acceptance criterion before reporting mission completion. Report completed and remaining items separately.

## Changes and verification

- Prefer focused regression coverage for behavior changes and run the narrowest relevant checks, followed by broader required verification.
- Use precise edits, avoid reverting unrelated changes, and report changed paths and verification results clearly.
- When an operation cannot run, explain why from current evidence and state the condition required to proceed; do not substitute a stale snapshot for a fresh resource decision.

See nested project guidance for domain-specific invariants and workflows.
