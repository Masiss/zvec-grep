# E2 preregistration amendment 2 — candidate-to-declaration containment (2026-11-19)

Status: **FROZEN BEFORE REPAIR AND ATTEMPT 02.** E2 attempt 01 is preserved
unchanged at
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/r4-e2-20261119/attempt-01/`.
It is **not an acceptance-qualified E2 result** because trace review found an
implementation defect; its output remains diagnostic evidence. This amendment
authorizes one defect-corrected E2 attempt on the same frozen inputs. It is not
a new hypothesis or a fourth experiment; the three-implementation-experiment
ceiling remains unchanged.

## Defect evidence

Attempt 01 returned the enclosing structured range
`EmployeeCommand.cs:14–1038` for a D01 fragment candidate at `:200–238`. That
range is the containing class, not the containing method. `State::rank` then
expanded every parsed method inside the class under the same fragment's
provenance instead of restricting expansion to the declaration containing the
fragment. D01's final verified plan pack contained C1
`EmployeeController.cs:459–622` but no selected C2 evidence
`EmployeeCommand.cs:215–285`; the class-level expansion permitted unrelated
methods in the same class to compete. This violates the amended E2's
candidate-anchor/context relation. The scored output was also below acceptance
(7/25 selected, macro 0.4028, pool overlap 12/25), identical to E1's metric.

Raw trace records both the candidate/context pair and final verified selected
spans. No index/model/plan/gold input changed. E2 attempt 01's caps were met,
but that does not repair the candidate-to-declaration defect.

## Bounded correction

When an enclosing range differs from the fragment candidate, resolve a parsed
C# method or SQL procedure that contains that candidate and expand only that
declaration, even if the enclosing class/context is under the generic width
limit. If no containing declaration can be parsed, retain the enclosing-range
fallback and emit the existing gap/trace evidence; do not fan one fragment out
to unrelated sibling declarations. Candidates without separate context keep
the pre-E2 behavior.

Add regressions for a fragment inside a multi-method C# class and a fragment
inside a multi-procedure SQL file. Preserve fragment provenance, source hash
verification, all thresholds and budgets, plan/index/model identity, and
manual context audit. Attempt 02 is the one valid E2 measurement. If this
correction fails focused tests, caps, hashes, or critical-context audit, stop;
do not retune from the output. Do not launch a third implementation hypothesis
without first documenting independent evidence and a new preregistration.

---

This amendment repairs the amended E2 mechanism; it does not alter its goal of
separating precise candidate relevance anchors from enclosing structural
declaration context.
