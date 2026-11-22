# R4 native Codex-subagent pilot — results and limits

Date: 2026-11-22
Base checkpoint: bad498eb7e3c4ff928df734c8a36961883cb6931
Branch: work/r4-evidence-representation-20261119

## Decision

**INCONCLUSIVE for comparative RAG answer quality.** The exploratory pilot ran the three frozen development cases D01, D06 and D12 through Arms A/B/C using native Codex subagents. Six cells produced deterministic structural scores; all six failed. Three cells were ungradable. Three separate semantic judgments were schema-accepted locally, but none was trusted by the existing evaluator registry; the formal grader returned UNGRADABLE for those semantic evaluations. There is no defensible arm ranking or quality-improvement claim.

No Responses API call was made. No H01–H08 case was opened. HRM2.0 source, questions and evidence remain in a local temporary pilot workspace; this report contains no question text, source paths or excerpts.

## Requested model configuration and actual identity

- Answerers and Arm-B planners were spawned with requested model gpt-6-luna and reasoning_effort high.
- Evaluators were spawned with requested model gpt-6.2-sol and reasoning_effort high. The requested Sol evaluator was accepted; no fallback was used.
- The collaboration interface accepted these spawn arguments. Runtime context exposed only “Codex based on GPT-6”; neither the parent nor the agents could verify an exact served snapshot or actual reasoning setting. Record the values above as requested configuration only, not verified served identity.
- The production evaluator registry was not changed. Evaluator instruction digest: 4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55.

## Per-case results

Structural columns are C1/C2/C3/C4/C5/C6. A derived search pack is reconstructed only from captured tool results and is marked unverified; it is not an adapter-created pack.

| Case | Arm | Answer outcome | Adapter history ops / read ops / delivered bytes | Pack bytes / items | Structural C1–C6, total | Formal result |
|---|---|---|---:|---:|---|---|
| D01 | A | Abstained; one claim present | 4 (status + 3 search) / 22 / 65,347 | 37,855 / 19, derived | 0/0/0/2/0/2, 4/12 | NOT_MEASURED |
| D01 | B | Invalid answer schema (abstain field was a string) | 3 (workflow, status, verify) + 5 internal / 36 / 14,515 | 9,656 / 10, adapter pack | — | UNGRADABLE |
| D01 | C | Abstained | 5 (2 search, symbol, facts, symbol) / 13 / 44,377 | 23,117 / 10, derived, unverified | 0/0/0/2/0/2, 4/12 | NOT_MEASURED |
| D06 | A | Invalid/abstained after CLI syntax errors; no adapter receipt | 0 recorded adapter ops; agent reported 6 rejected attempts | none | — | UNGRADABLE |
| D06 | B | Abstained; completed on one recorded retry after an initial launch that created no session | 2 (workflow, verify) + 5 internal / 20 / 9,914 | 6,839 / 6, adapter pack | 0/0/0/2/2/2, 6/12 | FAIL |
| D06 | C | One claim; did not abstain | 6 (3 search, symbol, 2 facts) / 17 / 42,267 | 19,330 / 13, derived, unverified | 0/0/0/2/1/2, 5/12 | NOT_MEASURED |
| D12 | A | Abstained; one claim present | 4 (status + 3 search) / 11 / 26,169 | 16,793 / 11, derived | 0/0/0/0/0/2, 2/12 | NOT_MEASURED |
| D12 | B | Abstained | 3 (workflow, status, verify) + 5 internal / 33 / 12,427 | 8,562 / 5, adapter pack | 0/0/0/2/2/2, 6/12 | FAIL |
| D12 | C | Abstained; no pack or adapter receipt | No receipt; agent-reported 6 calls / 3 rounds, unverified | none | — | UNGRADABLE |

The frozen evidence-pack limit is 24 KiB and 10 items. Three reconstructed search packs exceeded the item/byte cap: D01/A (37,855 bytes, 19 items), D06/C (13 items), and D12/A (11 items). These cells are exploratory only and cannot be counted as budget-compliant comparisons. D01/C’s pack is unverified and derived after the answer from its captured search outputs. No source or index was modified; the disposable corpus/index copy matched the source inventory across 1,812 files before use.

## Evaluator results

Three evaluators received one opaque packet each after all answerers finished. Packets had the arm-profile field and artifact paths masked; the prompts contained no gold, C3 checklist, peer answer or arm label. Pack structure and evidence verification state could still reveal workflow differences.

| Opaque packet | Mapped cell | Exploratory judgement | Key dimensions |
|---|---|---|---|
| task-01 | D01/A | FAIL | claim correctness 2, citation support 2, completeness 0; one omission; no unsupported claim |
| task-02 | D06/C | FAIL | claim correctness 0, citation support 0, completeness 1; one unsupported claim and one omission |
| task-03 | D12/A | PASS | all five dimensions 2; no omissions or unsupported claims |

All three response files passed the local packet/response validator. The grader then rejected them as untrusted because the production registry is empty. The D12/A exploratory PASS does not override its structural 2/12 failure or its 11-item over-limit derived pack. It is not a formal PASS.

## Usage, latency, isolation and evidence limits

- No Responses API or separate API billing occurred.
- Native Codex exposed no per-agent token usage, cost, exact served model snapshot, exact reasoning setting, or dependable subagent start/end timing. Per-agent latency and model-token usage are therefore **not measured**.
- R4 adapter receipts record calls, reads and bytes for most sessions. D06/A has no adapter receipt; D12/C has no receipt; D06/B’s first launch created no session, and its successful retry is recorded separately.
- Answerers were given only their own question-only file and the temporary source/index root, and were instructed not to inspect benchmark materials or peer results. However, native subagents share the workspace and have no per-agent tool/filesystem ACL. The parent cannot independently verify every access. One answerer also echoed its assigned question in an internal status message; no answer, source excerpt, gold or checklist was included in that message, and no question text is reproduced here.
- The local R4 grader computed structural C1–C6 from frozen inputs. Semantic grades remain exploratory because the evaluator was not registered. The pilot is descriptive, not a controlled estimate of arm superiority.

## Independent review

A fresh-context OpenAI reviewer received only the sanitized aggregate above. It agreed the pilot is useful for workflow and artifact-format observations, not for comparative answer-quality claims. Its main findings:

1. Six gradeable structural cells all failed; the three other cells failed for distinct artifact/protocol reasons and must not be assigned fabricated zero quality scores.
2. Three pack artifacts breached limits; transcript-derived packs do not prove the exact evidence each answerer saw.
3. No semantic judgement is formal because evaluator trust is absent.
4. Shared workspace access, missing served-identity/token telemetry and unverified Arm-A/Arm-C traces prevent verified blinding and equal-cost/model claims.
5. The defensible conclusion is six observed structural failures, three cells without a structural grade, and zero trusted semantic verdicts.

## Artifacts, checkpoint and rollback

- Raw private pilot artifacts (answers, evidence traces, receipts, packets and evaluator responses) remain only in the local temporary pilot workspace. A local hash/size manifest is at raw-manifest.json; its SHA-256 is 2312c6bfda87489b1d65ae30353853f2b7e1656d36b21b0419f594bcdeed8535.
- Sanitized results are in this report. The canonical R4 handoff is updated at rust/docs/r4-next-session-handoff-20261121b.md.
- No source code, HRM2.0 source, frozen benchmark, index, embedding, production default or API changed in this pilot. No push, merge or release occurred.
- The only repository changes are this report and the handoff append. Roll back this documentation checkpoint with git revert <results-doc-commit>. The earlier local-root boundary fix remains at 360c797 and is unrelated to pilot data.
- Stop here. No further infrastructure hardening or additional cases are in scope for this run.
