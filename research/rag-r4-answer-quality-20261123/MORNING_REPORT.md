# Overnight RAG and Indexed Retrieval Report

Run date: 2026-11-24
Final status: **HOLD**
Validation: **not run**

## Decisions

- **RAG prompt:** reject both prompt revisions. The frozen sufficient-pack baseline remains 9/12 fully correct. Prompt v1 scored 8/12 and lost `d05`. Prompt v2 scored 9/12, gained `a04`, and lost `d05`. It ties the baseline, fails the 11/12 threshold, fails preservation of all baseline-correct tasks, and lowers citation support from 1.833 to 1.750.
- **Indexed Semantic Retrieval:** keep `HOLD`. Offline ceilings were 4/10 complete answerable tasks from delivered R4 fragments, 8/10 from saved top-10 entity ranges, and 10/10 from any accepted range in a file appearing in those results. These ceilings did not establish a specific actionable method that recovers at least two tasks within the pack budget while preserving all four prior R4-complete tasks. Sol reviewer 1 returned `HOLD`.
- **Validation:** not authorized by the frozen gate. Sol reviewer 3 returned `HOLD`. No validation answer or grader calls were made.

## Development results

| Condition | Fully correct | Notes |
|---|---:|---|
| Lexical `rg` baseline | 3/12 | No unsupported claims or failed abstentions |
| R4 hybrid baseline | 0/12 | Two unsupported-claim cases and one failed abstention |
| Sufficient-pack baseline, original prompt | 9/12 | Two abstention controls passed |
| Prompt v1 on sufficient pack | 8/12 | Lost `d05`; zero unsupported claims and failed abstentions |
| Prompt v2 on sufficient pack | 9/12 | Gained `a04`, lost `d05`; no net gain |

The first prompt revision asked the agent to split answers by clause and cite each conclusion. Sol reviewer 2 found that it failed the gate and recommended one general revision, then stop. The second revision raised the count from v1's 8/12 to 9/12, but tied the original 9/12 baseline. It gained `a04` and lost `d05`, so it failed the preservation gate. It improved required-step score from 1.750 to 1.833, while citation score declined from 1.833 to 1.750.

## Sol review decisions

1. **Review 1:** `HOLD` on Indexed. Ceiling metrics show evidence availability, not an actionable selection method. The top-10 result ranges are not the complete adaptive candidate map, and file-level presence does not prove method reachability within budget.
2. **Review 2:** revise the prompt once offline, then use only the last development round. Preserve the incumbent until the result passes the frozen gates.
3. **Review 3:** `HOLD`. Prompt v2 failed accuracy, baseline preservation, citation, and Indexed actionable-recovery gates. Do not validate.

The final review checkpoint is recorded after the morning report is locked.

Sol's final review returned `REVISE` with three documentation corrections. This report now distinguishes v2's improvement over v1 from its tie against the original baseline, includes frozen artifact hashes and reproduction commands, and records reviewer usage as 83,264 reported tokens with input/output split unavailable. No fifth reviewer call was made.

The reusable `zg-bench rag-overnight` coordinator and `zg-bench review-overnight` command were implemented after the model run. Their ledger tests and answer-pair resume test passed. Dry-run verified Python 3.12, Codex authentication, 12-task baselines, writable output, and the configured limits. The recorded run was executed before the coordinator existed, so per-call journal replay was not exercised against those historical model calls.

## Usage

- New answer calls: 24 using `gpt-6-luna`, 529,904 input tokens and 8,359 output tokens.
- Development grading: 24 blinded task calls using `gpt-6.1-sol`, 786,519 input tokens and 14,016 output tokens.
- Sol reviewers: 4 calls, with 83,264 reported tokens used in aggregate. The Codex CLI output did not provide a reliable input/output token split for reviewer calls. Codex OAuth did not expose USD cost. No Astra calls were made.
- Validation usage: zero calls.

## Artifacts and reproducibility

- Offline ceiling: `overnight-offline-ceiling-v1.json`
- Prompt candidates: `overnight-rag-prompts-v1.json`, `overnight-rag-prompts-v2.json`
- Candidate answer and judge pairs: `overnight-rag-prompt-pairs-v1/`, `overnight-rag-prompt-pairs-v2/`
- Citation-refreshed reports: `overnight-rag-prompt-report-v2.json`, `overnight-rag-prompt-report-v4.json`
- Plan SHA-256: `5facee14e563fd5d031988f6f4a19a7d45b6228a48893cd6edfe5af7ee1c1`.
- Offline ceiling SHA-256: `500802f5a065c876fe29d54902e92348ba4a9216ec110cac4f4b1f45ab7ff1f7`.
- Prompt v1 SHA-256: `58c2878f04a6439fdbab91cf8240283739868fe4b6edbb9fcbb2f73b883f5426`. Prompt v2 SHA-256: `4a4db483bc0a067be0bcc7fb3a3aa300240fd0ee4d8045fe6e41967dbc881b62`.
- Paired run manifest hashes: v1 `e515b79490c9e721196630580e578deaaed93fd1a2527308815e6002c4632674`, v2 `56b41520c20c6f4d0c7925461b3bc7c625f59ccba1a3dfaf6f64d732842da0ea`.
- Citation-refreshed report hashes: v1 `a479d96b10a26a6b01b7500efd68e163fec7ba3e3efb01b7578ae998924f8cf5`, v2 `2807c94b2340b36406caf3fce2611c608be8505db049ccc95ec37d171cbbc9e3`.
- Reproduce answer runs with `PYTHONPATH=benchmarks/swe-qa-bench /tmp/rag-r4-upgrade-venv/bin/python -m zg_bench.swe_qa.run_rag_codex` using the matching `overnight-rag-prompt-datasets-v2` or `v3`, `overnight-rag-prompt-packs-v1`, and candidate-only profile. Reproduce grading with `python -m zg_bench.swe_qa judge-rag` using each paired directory, the frozen `rag-r4-v1` references/gold, source lock and corpus, `--profiles lexical-rg r4-hybrid sufficient-pack candidate --attempts 1 --completion-backend codex-cli --model gpt-6.1-sol`. Exact run IDs, inputs and output paths are recorded in each manifest and report.
- Each answer run and paired run has a manifest. Reports record per-task scores and grader usage.

USD cost is unavailable through Codex OAuth. All runs used the frozen corpus and source lock. The Indexed workspace search index was not rebuilt. No product retrieval defaults or API behavior changed.
