"""Paired answer judging for lexical, R4, and sufficient-pack evidence."""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any, Sequence

from zg_bench.core.errors import SweQaError
from zg_bench.core.protocol import JUDGE_MODELS
from zg_bench.engines.codex import codex_completion
from zg_bench.engines.judge import (
    CODEX_RAG_JUDGE_MODELS,
    Completion,
    default_completion,
    judge_generation_metadata,
    judge_rag_candidates,
    rag_judge_label,
)
from zg_bench.engines.registry import resolve_opencode_model
from zg_bench.metrics.numbers import mean_or_none

from .pairs import load_pairs, load_references
from .rag_evidence import (
    audit_answer_citations,
    score_required_steps,
    validate_trial_navigation_usage,
    validate_source_pack,
)

RAG_PROFILE_NAMES = ("lexical-rg", "r4-hybrid", "sufficient-pack")
BASELINE_PROFILE = "lexical-rg"
RAG_CANDIDATE_PROFILE = "candidate"
_BASELINE_PROFILES = ("lexical-rg", "r4-hybrid")
RAG_JUDGE_MODELS = (*JUDGE_MODELS, *CODEX_RAG_JUDGE_MODELS)


def refresh_rag_citation_audits(
    *,
    report_path: Path,
    pairs_root: Path,
    source_lock_path: Path,
    corpus_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Recompute deterministic citation resolution without making model calls."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    profile_names = tuple(report.get("profiles", []))
    cases = report.get("cases")
    if not profile_names or not isinstance(cases, list):
        raise SweQaError("RAG report is missing profile names or cases")
    expected = [case.get("task_id") for case in cases if isinstance(case, dict)]
    if len(expected) != len(cases) or any(not isinstance(task_id, str) for task_id in expected):
        raise SweQaError("RAG report has invalid task IDs")
    pairs = load_pairs(pairs_root, expected, profile_names)
    source_lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    for case in cases:
        task_id = case["task_id"]
        for profile_name in profile_names:
            evidence_rows = case["evidence"][profile_name]
            input_trials = pairs[task_id]["profiles"][profile_name]["trials"]
            evidence_by_index = {
                row["trial_index"]: row for row in evidence_rows
            }
            for input_trial in input_trials:
                evidence_row = evidence_by_index.get(input_trial["trial_index"])
                if evidence_row is None:
                    raise SweQaError(
                        f"RAG report is missing citation record for {task_id}/{profile_name}"
                    )
                verified = validate_source_pack(
                    input_trial["evidence_items"],
                    source_root=corpus_root,
                    source_lock=source_lock,
                )
                evidence_row["citations"] = audit_answer_citations(
                    input_trial["answer"], verified
                )
    for profile_name in profile_names:
        metrics = [
            trial
            for task_id in expected
            for trial in pairs[task_id]["profiles"][profile_name]["trials"]
        ]
        aggregate = report["aggregates"][profile_name]
        aggregate.update(
            {
                "mean_input_tokens": sum(row["input_tokens"] for row in metrics) / len(metrics),
                "mean_output_tokens": sum(row["output_tokens"] for row in metrics) / len(metrics),
                "total_input_tokens": sum(row["input_tokens"] for row in metrics),
                "total_output_tokens": sum(row["output_tokens"] for row in metrics),
            }
        )
    report["citation_audit_policy"] = (
        "Exact source paths or unique path suffixes resolve; ambiguous suffixes fail."
    )
    report["citation_audit_source_lock_sha256"] = hashlib.sha256(
        source_lock_path.read_bytes()
    ).hexdigest()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def judge_rag_pairs(
    *,
    pairs_root: Path,
    references_path: Path,
    gold_path: Path,
    source_lock_path: Path,
    corpus_root: Path,
    output_path: Path,
    expected: Sequence[str],
    profile_names: Sequence[str] = RAG_PROFILE_NAMES,
    completion_backend: str = "litellm",
    completion_fn: Completion | None = None,
    attempts: int = 3,
    model: str = "glm-5.2",
) -> dict[str, Any]:
    """Judge the three frozen RAG evidence conditions on identical task pairs."""
    if attempts < 1 or attempts > 5:
        raise SweQaError("judge attempts must be between 1 and 5")
    profile_names = tuple(profile_names)
    allowed_profiles = (RAG_PROFILE_NAMES, (*RAG_PROFILE_NAMES, RAG_CANDIDATE_PROFILE))
    if profile_names not in allowed_profiles:
        raise SweQaError(
            "RAG profiles must be lexical-rg, r4-hybrid, sufficient-pack, "
            "optionally followed by candidate"
        )
    pairs = load_pairs(pairs_root, expected, profile_names)
    references = load_references(references_path)
    gold_root = json.loads(gold_path.read_text(encoding="utf-8"))
    source_lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    gold_rows = gold_root.get("tasks")
    if not isinstance(gold_rows, list):
        raise SweQaError("RAG gold tasks must be an array")
    gold_by_id = {row.get("task_id"): row for row in gold_rows if isinstance(row, dict)}
    missing = [task_id for task_id in expected if task_id not in references]
    if missing:
        raise SweQaError("RAG judge is missing reference(s): " + ", ".join(missing))
    missing_gold = [task_id for task_id in expected if task_id not in gold_by_id]
    if missing_gold:
        raise SweQaError("RAG judge is missing gold task(s): " + ", ".join(missing_gold))
    if completion_backend == "codex-cli":
        if model not in CODEX_RAG_JUDGE_MODELS:
            raise SweQaError("codex-cli backend requires a supported Codex RAG judge model")
        api_key, api_base = "codex-cli-auth", "codex-cli"
        completion_fn = completion_fn or codex_completion(model)
    elif completion_backend == "litellm":
        if model in CODEX_RAG_JUDGE_MODELS:
            raise SweQaError("Codex RAG judge models require completion_backend=codex-cli")
        try:
            runtime = resolve_opencode_model(model, require_credentials=True)
        except ValueError as error:
            raise SweQaError(str(error)) from error
        if runtime.api_key is None:
            raise SweQaError("RAG judge credentials are unavailable")
        api_key, api_base = runtime.api_key, runtime.configuration.base_url
        completion_fn = completion_fn or default_completion()
    else:
        raise SweQaError("completion_backend must be litellm or codex-cli")

    cases: list[dict[str, Any]] = []
    usage = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }
    input_tokens_complete = True
    output_tokens_complete = True
    cost_complete = True
    for task_id in expected:
        pair = pairs[task_id]
        profiles: dict[str, Any] = {}
        evidence_results: dict[str, list[dict[str, Any]]] = {}
        prepared_by_profile: dict[str, list[dict[str, Any]]] = {}
        trial_results_by_profile: dict[str, list[dict[str, Any]]] = {
            profile_name: [] for profile_name in profile_names
        }
        for profile_name in profile_names:
            input_trials = pair["profiles"][profile_name]["trials"]
            profile_evidence = []
            prepared_trials = []
            for input_trial in input_trials:
                navigation_usage = validate_trial_navigation_usage(
                    input_trial.get("navigation_usage"), input_trial["tool_calls"]
                )
                evidence_items = input_trial.get("evidence_items")
                if evidence_items is None:
                    raise SweQaError(f"{task_id} {profile_name} trial lacks evidence_items")
                verified = validate_source_pack(
                    evidence_items, source_root=corpus_root, source_lock=source_lock
                )
                coverage = score_required_steps(gold_by_id[task_id], verified)
                citations = audit_answer_citations(
                    input_trial["answer"], verified
                )
                prepared_trials.append(
                    {
                        "input_trial": input_trial,
                        "evidence_items": evidence_items,
                        "verified": verified,
                        "coverage": coverage,
                        "citations": citations,
                    }
                )
                profile_evidence.append(
                    {
                        "trial_index": input_trial["trial_index"],
                        "source_bytes": verified["source_bytes"],
                        "navigation_usage": navigation_usage,
                        "covered_steps": coverage["covered_steps"],
                        "required_steps": coverage["required_steps"],
                        "evidence_complete": coverage["complete"],
                        "abstention_pack_empty": coverage["abstention_pack_empty"],
                        "citations": citations,
                    }
                )
            evidence_results[profile_name] = profile_evidence
            prepared_by_profile[profile_name] = prepared_trials

        trial_counts = {len(rows) for rows in prepared_by_profile.values()}
        if len(trial_counts) != 1:
            raise SweQaError(f"{task_id} RAG profiles have different trial counts")
        batch_usage_rows = []
        for trial_index in range(next(iter(trial_counts))):
            # Stable hash ordering hides the evidence-profile name from the grader.
            ordered_profiles = sorted(
                profile_names,
                key=lambda profile: hashlib.sha256(
                    f"{task_id}:{trial_index + 1}:{profile}:blind-v1".encode()
                ).hexdigest(),
            )
            profile_by_candidate_id = {
                f"answer-{index + 1}": profile
                for index, profile in enumerate(ordered_profiles)
            }
            candidates = []
            for candidate_id, profile_name in profile_by_candidate_id.items():
                prepared = prepared_by_profile[profile_name][trial_index]
                input_trial = prepared["input_trial"]
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "answer": input_trial["answer"],
                        "evidence_items": prepared["evidence_items"],
                    }
                )
            judge_started = perf_counter()
            graded_batch = judge_rag_candidates(
                completion_fn=completion_fn,
                api_key=api_key,
                api_base=api_base,
                question=references[task_id]["question"],
                required_steps=gold_by_id[task_id]["required_steps"],
                accepted_source_ranges=gold_by_id[task_id]["accepted_source_ranges"],
                candidates=candidates,
                attempts=attempts,
                model=model,
            )
            judge_wall_seconds = perf_counter() - judge_started
            batch_usage = graded_batch["usage"]
            batch_usage_rows.append(batch_usage)
            usage["calls"] += batch_usage["calls"]
            if batch_usage["input_tokens"] is None:
                input_tokens_complete = False
            else:
                usage["input_tokens"] += batch_usage["input_tokens"]
            if batch_usage["output_tokens"] is None:
                output_tokens_complete = False
            else:
                usage["output_tokens"] += batch_usage["output_tokens"]
            if batch_usage["cost_usd"] is None:
                cost_complete = False
            else:
                usage["cost_usd"] += batch_usage["cost_usd"]

            for candidate_id, profile_name in profile_by_candidate_id.items():
                scores = graded_batch["scores_by_id"][candidate_id]
                prepared = prepared_by_profile[profile_name][trial_index]
                trial_results_by_profile[profile_name].append(
                    {
                        "judge": {
                            "label": graded_batch["label"],
                            "model": model,
                            "scores": scores,
                            "total": graded_batch["totals_by_id"][candidate_id],
                            "shared_batch": True,
                        },
                        "metrics": prepared["input_trial"],
                        "judge_wall_seconds": judge_wall_seconds,
                    }
                )

        for profile_name in profile_names:
            trial_results = trial_results_by_profile[profile_name]
            score_keys = trial_results[0]["judge"]["scores"].keys()
            scores = {
                key: fmean(result["judge"]["scores"][key] for result in trial_results)
                for key in score_keys
            }
            profiles[profile_name] = {
                "trial_count": len(trial_results),
                "judge": {
                    "label": rag_judge_label(model),
                    "model": model,
                    "scores": scores,
                    "total": fmean(result["judge"]["total"] for result in trial_results),
                    "usage": {
                        "attribution": "shared per-task blinded candidate batch",
                        "batch_count": len(batch_usage_rows),
                    },
                    "mean_wall_seconds": fmean(
                        result["judge_wall_seconds"] for result in trial_results
                    ),
                },
                "metrics": {
                    key: fmean(float(result["metrics"][key]) for result in trial_results)
                    for key in ("input_tokens", "output_tokens", "tool_calls", "agent_wall_seconds")
                },
            }
            profiles[profile_name]["metrics"].update(
                {
                    "retrieval_latency_seconds": mean_or_none(
                        [
                            _optional_nonnegative(
                                result["metrics"].get("retrieval_latency_seconds"),
                                f"{task_id} {profile_name} retrieval_latency_seconds",
                            )
                            for result in trial_results
                        ]
                    ),
                    "peak_rss_kib": mean_or_none(
                        [
                            _optional_nonnegative(
                                result["metrics"].get("peak_rss_kib"),
                                f"{task_id} {profile_name} peak_rss_kib",
                            )
                            for result in trial_results
                        ]
                    ),
                }
            )
            profiles[profile_name]["metrics"]["cost_usd"] = mean_or_none(
                [result["metrics"].get("cost_usd") for result in trial_results]
            )
        baseline = profiles[BASELINE_PROFILE]
        comparisons = {}
        for name in profile_names[1:]:
            candidate = profiles[name]
            comparisons[name] = {
                "judge_total_delta": candidate["judge"]["total"] - baseline["judge"]["total"],
                "correctness_delta": candidate["judge"]["scores"]["correctness"] - baseline["judge"]["scores"]["correctness"],
                "required_step_coverage_delta": candidate["judge"]["scores"]["required_step_coverage"] - baseline["judge"]["scores"]["required_step_coverage"],
                "citation_support_delta": candidate["judge"]["scores"]["citation_support"] - baseline["judge"]["scores"]["citation_support"],
                "input_tokens_delta": candidate["metrics"]["input_tokens"] - baseline["metrics"]["input_tokens"],
                "tool_calls_delta": candidate["metrics"]["tool_calls"] - baseline["metrics"]["tool_calls"],
                "agent_wall_seconds_delta": candidate["metrics"]["agent_wall_seconds"] - baseline["metrics"]["agent_wall_seconds"],
                "retrieval_latency_seconds_delta": _difference(
                    candidate["metrics"]["retrieval_latency_seconds"],
                    baseline["metrics"]["retrieval_latency_seconds"],
                ),
                "peak_rss_kib_delta": _difference(
                    candidate["metrics"]["peak_rss_kib"],
                    baseline["metrics"]["peak_rss_kib"],
                ),
                "cost_usd_delta": _difference(candidate["metrics"]["cost_usd"], baseline["metrics"]["cost_usd"]),
            }
        cases_judge_usage = {
            "calls": sum(row["calls"] for row in batch_usage_rows),
            "input_tokens": (
                sum(row["input_tokens"] or 0 for row in batch_usage_rows)
                if all(row["input_tokens"] is not None for row in batch_usage_rows)
                else None
            ),
            "output_tokens": (
                sum(row["output_tokens"] or 0 for row in batch_usage_rows)
                if all(row["output_tokens"] is not None for row in batch_usage_rows)
                else None
            ),
            "cost_usd": (
                None
                if any(row["cost_usd"] is None for row in batch_usage_rows)
                else sum(float(row["cost_usd"]) for row in batch_usage_rows)
            ),
        }
        cases.append({
            "task_id": task_id,
            "profiles": profiles,
            "evidence": evidence_results,
            "judge_batch_usage": cases_judge_usage,
            "comparisons_to_lexical": comparisons,
        })

    if not input_tokens_complete:
        usage["input_tokens"] = None
    if not output_tokens_complete:
        usage["output_tokens"] = None
    if not cost_complete:
        usage["cost_usd"] = None
    aggregates = {
        name: {
            "task_count": len(cases),
            "mean_judge_total": fmean(case["profiles"][name]["judge"]["total"] for case in cases),
            "mean_correctness": fmean(case["profiles"][name]["judge"]["scores"]["correctness"] for case in cases),
            "mean_required_step_coverage_score": fmean(case["profiles"][name]["judge"]["scores"]["required_step_coverage"] for case in cases),
            "mean_citation_support_score": fmean(case["profiles"][name]["judge"]["scores"]["citation_support"] for case in cases),
            "mean_input_tokens": fmean(case["profiles"][name]["metrics"]["input_tokens"] for case in cases),
            "mean_output_tokens": fmean(case["profiles"][name]["metrics"]["output_tokens"] for case in cases),
            "total_input_tokens": sum(
                case["profiles"][name]["metrics"]["input_tokens"] for case in cases
            ),
            "total_output_tokens": sum(
                case["profiles"][name]["metrics"]["output_tokens"] for case in cases
            ),
            "mean_tool_calls": fmean(case["profiles"][name]["metrics"]["tool_calls"] for case in cases),
            "mean_agent_wall_seconds": fmean(case["profiles"][name]["metrics"]["agent_wall_seconds"] for case in cases),
            "mean_retrieval_latency_seconds": mean_or_none(
                [case["profiles"][name]["metrics"]["retrieval_latency_seconds"] for case in cases]
            ),
            "mean_peak_rss_kib": mean_or_none(
                [case["profiles"][name]["metrics"]["peak_rss_kib"] for case in cases]
            ),
            "mean_judge_wall_seconds": fmean(
                case["profiles"][name]["judge"]["mean_wall_seconds"] for case in cases
            ),
            "mean_required_step_coverage": fmean(
                trial["covered_steps"] / trial["required_steps"]
                for case in cases for trial in case["evidence"][name]
                if trial["required_steps"]
            ),
            "complete_evidence_trial_count": sum(
                trial["evidence_complete"] for case in cases for trial in case["evidence"][name]
            ),
            "trial_count": sum(len(case["evidence"][name]) for case in cases),
        }
        for name in profile_names
    }
    gate = _validation_gate(cases, gold_by_id, profile_names)
    report = {
        "schema_version": 1,
        "protocol": (
            "rag-answer-intervention-v1"
            if RAG_CANDIDATE_PROFILE in profile_names
            else "rag-answer-triad-v1"
        ),
        "judge": {
            "label": rag_judge_label(model),
            "model": model,
            "backend": completion_backend,
            **judge_generation_metadata(model),
            "usage": usage,
        },
        "profiles": list(profile_names),
        "cases": cases,
        "aggregates": aggregates,
        "validation_gate": gate,
        "limitations": [
            "The model grader evaluates citation support against the exact verified source pack. Deterministic citation auditing also confirms cited path and line inclusion.",
            "RAG rubric scores and deterministic evidence-span coverage remain separate report fields.",
            *(
                ["Codex OAuth usage is reported in tokens; per-request USD cost is unavailable."]
                if completion_backend == "codex-cli"
                else []
            ),
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _validation_gate(
    cases: list[dict[str, Any]],
    gold_by_id: dict[str, dict[str, Any]],
    profile_names: Sequence[str],
) -> dict[str, Any]:
    validation_cases = [
        case for case in cases if gold_by_id[case["task_id"]]["split"] == "validation"
    ]
    result: dict[str, Any] = {
        "validation_task_count": len(validation_cases),
        "candidate_profile": RAG_CANDIDATE_PROFILE,
        "required_task_gain": 2,
        "comparison_to_baselines": {},
        "evaluated": False,
        "passed": None,
    }
    if RAG_CANDIDATE_PROFILE not in profile_names:
        result["status"] = "not_evaluated_no_intervention_candidate"
        return result
    if len(validation_cases) != 12:
        result["status"] = "not_evaluated_until_all_12_validation_tasks_are_judged"
        return result

    comparisons = {}
    for baseline_name in _BASELINE_PROFILES:
        baseline_correct = {
            case["task_id"]
            for case in validation_cases
            if _fully_correct(case, baseline_name)
        }
        candidate_correct = {
            case["task_id"]
            for case in validation_cases
            if _fully_correct(case, RAG_CANDIDATE_PROFILE)
        }
        baseline_failed_abstentions = sum(
            _failed_abstention(case, baseline_name, gold_by_id[case["task_id"]])
            for case in validation_cases
        )
        candidate_failed_abstentions = sum(
            _failed_abstention(
                case, RAG_CANDIDATE_PROFILE, gold_by_id[case["task_id"]]
            )
            for case in validation_cases
        )
        baseline_unsupported_claim_cases = sum(
            _has_unsupported_claims(case, baseline_name) for case in validation_cases
        )
        candidate_unsupported_claim_cases = sum(
            _has_unsupported_claims(case, RAG_CANDIDATE_PROFILE)
            for case in validation_cases
        )
        gain = len(candidate_correct) - len(baseline_correct)
        preserved = baseline_correct.issubset(candidate_correct)
        no_failed_abstention_increase = (
            candidate_failed_abstentions <= baseline_failed_abstentions
        )
        no_unsupported_claim_increase = (
            candidate_unsupported_claim_cases <= baseline_unsupported_claim_cases
        )
        comparisons[baseline_name] = {
            "baseline_fully_correct_tasks": sorted(baseline_correct),
            "candidate_fully_correct_tasks": sorted(candidate_correct),
            "fully_correct_gain": gain,
            "preserved_baseline_correct_tasks": preserved,
            "baseline_failed_abstentions": baseline_failed_abstentions,
            "candidate_failed_abstentions": candidate_failed_abstentions,
            "no_increase_in_failed_abstentions": no_failed_abstention_increase,
            "baseline_unsupported_claim_cases": baseline_unsupported_claim_cases,
            "candidate_unsupported_claim_cases": candidate_unsupported_claim_cases,
            "no_increase_in_unsupported_claims": no_unsupported_claim_increase,
            "passes_validation_gate": (
                gain >= 2
                and preserved
                and no_failed_abstention_increase
                and no_unsupported_claim_increase
            ),
        }
    result.update(
        {
            "comparison_to_baselines": comparisons,
            "evaluated": True,
            "status": "evaluated",
            "passed": all(
                comparison["passes_validation_gate"]
                for comparison in comparisons.values()
            ),
        }
    )
    return result


def _difference(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _optional_nonnegative(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweQaError(f"{label} must be numeric")
    if not math.isfinite(float(value)) or value < 0:
        raise SweQaError(f"{label} must be finite and non-negative")
    return float(value)


def _fully_correct(case: dict[str, Any], profile: str) -> bool:
    scores = case["profiles"][profile]["judge"]["scores"]
    keys = (
        "correctness",
        "required_step_coverage",
        "citation_support",
        "source_deployment_discipline",
        "honest_gaps",
        "unsupported_claims",
    )
    return all(scores[key] == 2 for key in keys)


def _has_unsupported_claims(case: dict[str, Any], profile: str) -> bool:
    return case["profiles"][profile]["judge"]["scores"]["unsupported_claims"] < 2


def _failed_abstention(
    case: dict[str, Any], profile: str, gold_task: dict[str, Any]
) -> bool:
    if gold_task["outcome"] != "abstain":
        return False
    scores = case["profiles"][profile]["judge"]["scores"]
    return scores["honest_gaps"] < 2 or scores["unsupported_claims"] < 2
