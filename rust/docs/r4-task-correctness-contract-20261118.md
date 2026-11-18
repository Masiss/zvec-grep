# R4 task-correctness contract (frozen rubric)

Status: **rubric and protocol frozen; grading NOT_MEASURED** — no external-agent
execution with a valid grader was run in this mission, so nothing here is
reported as an empirical result.

Purpose: separate *evidence retrieval* quality (Recall@10, measured) from
*answer* quality (this rubric, unmeasured), so an MVP claim can never rest on
retrieval alone.

## Inputs each case is graded against

1. The frozen dev question (D01–D12), unchanged.
2. The verified evidence pack the engine returned for that case (≤24 KiB,
   hash-verified, with provenance), **not** the gold spans.
3. The frozen gold spans, used by the grader only to decide whether a required
   fact was reachable at all.

## Rubric (per case, 0–2 per criterion)

| # | Criterion | 0 | 1 | 2 |
|---|---|---|---|---|
| C1 | **Answerable from the pack** — an answer can be derived from delivered evidence alone | pack lacks any support | partial support | fully supported |
| C2 | **Citation support** — every claim is backed by a cited, verified span (not merely a valid hash/range) | unsupported claims | some claims unsupported | all claims supported |
| C3 | **Chain completeness** — controller → command/executor → DbContext/SaveChanges → SQL object, as far as the question requires | a required step missing | step implied but not cited | every required step cited |
| C4 | **Source-vs-deployment discipline** — no SQL/EF definition asserted as live runtime behaviour | one such claim | ambiguous wording | strictly source-visible |
| C5 | **Honest gaps/abstention** — missing, stale or out-of-budget evidence is declared, not papered over | invented support | partial declaration | fully declared |
| C6 | **No false negatives** — facts present in the pack are not omitted from the answer | >1 omission | one omission | none |

Scores: **12 = PASS**, 8–11 = PARTIAL, ≤7 = FAIL per case. MVP requires the mean
of C2, C4, C5 ≥ 1.8 **and** mean total ≥ 10.0, with no case at 0 on C2/C4/C5.

## Protocol (to be run by an external agent, not by the engine)

1. The agent receives only the question + the verified pack (paths, hashes,
   ranges, content). It never sees gold.
2. The agent answers, citing spans from the pack; abstains when unsupported.
3. A separate grading pass (different session) scores C1–C6 against the gold
   spans, which are released to the grader only.
4. Both artefacts (answer + score sheet) are frozen and hashed per case before
   any aggregate is computed.
5. Aggregate reporting keeps retrieval metrics and rubric scores in separate
   tables; no combined "MVP score" is derived.

## Fixtures prepared (no results)

- 12 D-case slots with their verified packs from the best arm run
  (`r4b-paired-20261116.json`), ready to be handed to an agent.
- Adversarial fixtures for C4/C5: a SQL definition whose proc is invoked only
  via a dynamic string literal (D12 class), and a case whose gold lives outside
  the pack (D02 class) where the correct behaviour is an explicit gap.
- Negative control: a question with no matching evidence at all, where any
  citation is a C2 violation by construction.

## Known limits of this rubric

- It cannot be scored by the engine itself; a self-graded answer is not evidence.
- C3 depends on a human/agent judgement of "required step" per question; the
  per-question required-step lists are not yet frozen (this is the next concrete
  prerequisite for running the protocol).
- Because D01–D12 have been used for development, passing this rubric on them
  would be development evidence, not generalization.
