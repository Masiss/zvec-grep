# Overnight RAG and Indexed Semantic Retrieval Run

## Goal

Run up to two development rounds for RAG prompt and Indexed retrieval candidates, use GPT-6.1 Sol as blinded grader and reviewer, then run the frozen RAG validation split once only if the development gate passes. The run is bounded to eight hours and records per-call checkpoints so an interruption can resume without silently repeating model calls.

## Confirmed run choices

- Development rounds: at most two, shared between RAG prompt and Indexed retrieval candidates.
- Validation: run once automatically only after the candidate passes the development gate and reviewer approval.
- Answer model: `gpt-6-luna`.
- Grader and reviewer: `gpt-6.1-sol`, one grader attempt per task.
- Wall-clock limit: eight hours from run start.
- No Astra calls. Codex OAuth reports tokens but not reliable USD cost, so record calls and input/output tokens by role.

## Order of work

1. **Preflight and offline diagnosis.** Verify Python, Codex CLI/auth, source lock, corpus, task and dataset hashes, write access, and baseline pair completeness. Compute the RAG source-coverage ceilings from delivered fragments, ranked entities, and retrieved files for all 10 answerable development prompts, under the 10-item / 24,576-byte evidence caps. Preserve two abstention controls separately. Use only exact per-query trace for fragment recall. Mark missing joins unknown. Do not rebuild the persistent workspace index.
2. **Reviewer checkpoint 1.** Send GPT-6.1 Sol the offline per-task table and evidence provenance. Ask it to select at most one Indexed intervention, or stop if no supported candidate can recover at least two tasks while preserving R4-complete tasks. No model answer calls before this review.
3. **Development round 1, RAG prompt.** Add a prompt version that asks for each question clause to be answered separately, every conclusion to cite its supporting evidence, and unsupported clauses to be identified. Run the 12-task development split with sufficient packs using `gpt-6-luna`. Grade new answers blind alongside the old sufficient-pack answers in 12 task-level batches using `gpt-6.1-sol`. Pass only at 11/12 fully correct, preserving the prior nine correct tasks and both abstentions.
4. **Reviewer checkpoint 2.** Have Sol review the paired score table and failure cases. It may request offline correction or accept/reject the prompt. Do not exceed round budget.
5. **Development round 2.** If the offline Indexed gate selected a candidate, implement it in the isolated evaluation harness only and verify it recovers at least two complete evidence tasks under caps before any model calls. Run candidate answers with the original prompt, reusing baselines only when task, model, instruction, and evidence hashes match. If Indexed did not pass its offline gate and the RAG prompt passed round 1, use round 2 for the new prompt with lexical `rg`. Otherwise stop model-backed dev work.
6. **Reviewer checkpoint 3.** Sol reviews blinded, paired development results after scores are locked. Candidate passes only if it gains at least two fully correct tasks over both product baselines, preserves all baseline-correct tasks, and increases neither unsupported-claim cases nor failed abstentions. Sol issues `VALIDATE` or `HOLD` with task IDs and rationale.
7. **Validation.** On `VALIDATE`, freeze hashes for code, prompt, source lock, dataset, model, and rubric. Run one 12-task validation with lexical, R4, sufficient pack, and the selected candidate. Use 12 blinded task batches and one answer trial per profile/task. If validation is interrupted, mark inconclusive and do not retry automatically.
8. **Reviewer checkpoint 4 and morning report.** Sol reviews the locked validation result, integrity checks, costs, and failures. Produce `MORNING_REPORT.md` with decisions for RAG and Indexed, pairwise task outcomes, losses, usage, hashes, and stop reason. If the run stops before validation, use the final review for the completed development evidence.

## Budget and stop rules

- Two development rounds maximum. Each round may generate at most 12 candidate answers and 12 grader calls. Validation may add at most 48 answers and 12 grader calls.
- Four reviewer calls maximum. Grader attempts are fixed at one. Every submitted request, including timeout or lost response, consumes one call. A request with indeterminate outcome is never retried automatically.
- Hard maxima are 72 answer calls, 36 grader calls, and 4 reviewer calls, within eight hours. Stop before starting a stage that cannot fit the remaining call or time budget.
- Track started/completed/indeterminate calls, model, role, task, profile, request hash, response hash, tokens, wall time, and remaining budget.
- Resume reuses only completed checkpoint records with matching hashes. A per-run lock prevents concurrent execution. Atomic writes protect the manifest and task results.
- Stop on any source-lock mismatch, gold leakage, invalid pair, candidate cap breach, missing baseline-correct preservation, threshold failure, or reviewer `HOLD`.

## Interfaces and artifacts

- Add a `zg-bench rag-overnight` command with `--run-id`, `--resume`, `--dry-run`, and configurable output root. Dry-run validates dependencies and inputs and reports the call budget without invoking models.
- Store the run journal, per-task answer pairs, reviewer decisions, grader reports, and final morning report under `research/rag-r4-answer-quality-20261123/overnight-<run-id>/`.
- Write each call's `started` checkpoint before invocation and its completed response and usage atomically after return. Record lost responses as `indeterminate`.
- Keep grader profile names blinded. Reviewer receives profile labels only after each grading batch is committed.

## Acceptance

- Dry-run confirms all data, auth, hash, budget, write, resume, and locking checks.
- Interrupted and resumed runs do not duplicate completed calls or exceed budgets.
- RAG and Indexed decisions follow the gates above and cite task-level evidence.
- Validation runs at most once and only after development pass plus reviewer `VALIDATE`.
- The morning report includes exact result status, hashes, call/token totals, costs where available, limitations, and reproducible commands.

## Execution checkpoint — 2026-11-24

- Plan file was written before the run. Offline ceiling measured 4/10 complete tasks from delivered fragments, 8/10 from saved hybrid entity ranges, and 10/10 from files appearing in the top-10 hybrid output. Sol review 1 selected `HOLD` because these are availability ceilings, not actionable retrieval methods.
- Dev round 1 generated 12 revised-prompt answers with 264,704 input / 3,914 output tokens. Its 12 Sol grading calls used 392,590 input / 6,425 output tokens. Candidate scored 8/12, lost `d05`, and failed the 11/12 and preservation gates. Sol review 2 directed one general prompt revision and no further changes after round 2.
- Dev round 2 generated 12 revised-prompt answers with 265,200 input / 4,445 output tokens. Its 12 Sol grading calls used 393,929 input / 7,591 output tokens. Candidate scored 9/12, gained `a04`, lost `d05`, tied original baseline, and had citation support 1.750 versus 1.833 baseline. Sol review 3 returned `HOLD`. Validation was correctly not run.
- Sol reviewer calls: four. Reported usage total is 83,264 tokens, with input/output split unavailable. USD cost is unavailable. No Astra calls were made.
- Morning report is `research/rag-r4-answer-quality-20261123/MORNING_REPORT.md`. Final Sol review requested clarifications to the baseline comparison, hashes/commands, and reviewer usage. Those edits were applied after that review. No additional reviewer call was made.
- Final report SHA-256 after those corrections and coordinator note: `4fad28bbfffcd9e611573ec49ea05aaf992c3af55329042869b95819fc07cbc1`. Reviewer 4 verdict was `REVISE`, and all three requested report edits were applied. The reviewer was not called again after the corrections.
- The model run itself used the existing answer and judge commands directly. Afterward, `zg-bench rag-overnight` and `zg-bench review-overnight` were added with a durable model-call journal. Dry-run checks Python, Codex OAuth, frozen prompt/baseline availability, output writability, and budgets. The coordinator has an eight-hour deadline and a single-run lock. Completed calls replay from the journal on resume, while indeterminate calls are refused. `run_rag_codex --resume` reuses completed task pairs.
- The ledger and answer-pair resume tests passed. Dry-run smoke checks passed for fresh and resumed runs through the installed `zg-bench` entrypoint. The actual model run predates the coordinator and did not use its journal, so its original call history is preserved in answer manifests and aggregate grader reports.
