# E2 preregistration amendment 1 — enclosing evidence context (2026-11-19)

Status: **FROZEN BEFORE E2 RUN.** This versioned amendment supersedes only the
E2 candidate/selection scope paragraph in
`r4-e2-prereg-20261119.md`. E2 has not been run; no result informed this
amendment. It does not change the frozen D01–D12 set, thresholds, call/read/
byte caps, index, model, query plan, or the three-experiment ceiling.

## New evidence requiring the amendment

E1 failed both numeric thresholds (7/25, macro 0.4028) and the context audit.
For frozen D01 (“Form 1, update thông tin cá nhân đi qua đâu?”), the selected
controller excerpt was `EmployeeController.cs:524–544`. It shows a later lock
check but omits the `Form == "1"` / `Perform` branch at the beginning of the
gold C1 span (`:459–620`); the required C2 personal-update declaration
(`EmployeeCommand.cs:215–285`) was not selected. Any-overlap recall counted the
controller excerpt as covering C1, but the excerpt is insufficient for the
frozen task. This is a critical-context failure, not merely a low score.

The same trace has a structured hybrid entity range
`EmployeeController.cs:459–622` with a narrower content range `:570–605`;
lexical hits such as `Form` at `:464`/`:530` have no entity ID. E1 projected
both to a content/line window and discarded the enclosing structural relation
needed to restore method-entry context. The exact raw trace is preserved in
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/r4-e1-20261119/attempt-01/`.

## Amended E2 hypothesis and single representation change

Keep the retrieved fragment/line as the **candidate relevance anchor and
provenance unit**, but resolve the smallest structured entity range in the
same file that contains that anchor. Use that enclosing entity range only for
(a) typed-reference extraction and (b) rank-time declaration expansion and
evidence compaction. If no structured range contains a candidate, retain the
current fragment/line behavior and record that limitation in the trace. The
candidate's search route/rank remains attached when a containing declaration
is expanded; the selected output is still source-read and hash-verified.

This is one separation-of-roles hypothesis: precise retrieval anchor versus
structured declaration context. It does **not** add index entities, create a
second duplicate pool entry, or change query allocation, ranking weights,
selection quotas, or budgets. It should restore useful parent-scope identifiers
without handing an isolated body snippet to an external agent. The additive
`EvidenceSource` method defaults to no enclosing range, preserving other
backends. No public MCP/CLI, persistence, or external-provider changes.

## Acceptance and stop condition (unchanged)

Use the procedure and exact thresholds from E2 preregistration: one D01–D12 run;
selected ≥12/25 and macro ≥0.60; no resource/citation/hash violation; manually
audit required branch, guard, call, and transaction context. Enforce ≤3 rounds,
≤6 calls, ≤32 distinct source files, ≤24,576 selected pack bytes, and ≤65,536
workflow source-content-plus-gap bytes. Serialize per-case enclosing range,
fragment anchor, query strings, selected source hash/verification, and gaps.

Failure of either score threshold or any critical-context/hash/cap check rejects
E2. Preserve the raw receipt and stop unless an evidence-supported, separately
preregistered third hypothesis remains within the authorized maximum. Do not
lower thresholds, tune from the E2 result, or claim external-agent correctness;
the independent task-correctness rubric remains NOT_MEASURED/BLOCKED.

This amendment was written after the manual E1 audit and before E2 code is
completed or run. The first E2 preregistration remains preserved as historical
text; this amendment governs execution.
