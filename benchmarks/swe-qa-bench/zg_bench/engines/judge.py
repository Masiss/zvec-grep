"""Model calls, response parsing, retries, and bounded self-judge concurrency."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from zg_bench.core.errors import SweQaError
from zg_bench.core.protocol import (
    PROFILE_NAMES,
    SCORE_KEYS,
    judge_label,
    model_request_spec,
)
from zg_bench.metrics.usage import (
    SESSION_USAGE_METRICS,
    usage_scope,
)
from zg_bench.settings import (
    BENCHMARK_SEED,
)

DEFAULT_JUDGE_CONCURRENCY = 3


MAX_JUDGE_CONCURRENCY = 8


JUDGE_CONCURRENCY_ENV = "SWE_QA_JUDGE_CONCURRENCY"


Completion = Callable[..., Any]
RAG_SCORE_KEYS = (
    "correctness",
    "required_step_coverage",
    "citation_support",
    "source_deployment_discipline",
    "honest_gaps",
    "unsupported_claims",
)
CODEX_RAG_JUDGE_MODELS = ("gpt-6.1-sol",)


def rag_judge_label(model: str) -> str:
    if model in CODEX_RAG_JUDGE_MODELS:
        return f"{model}-rag-judge"
    return judge_label(model)


def judge_temperature(model: str) -> float:
    if model in CODEX_RAG_JUDGE_MODELS:
        return 0.0
    return model_request_spec(model).temperature


def judge_generation_metadata(model: str = "glm-5.2") -> dict[str, Any]:
    if model in CODEX_RAG_JUDGE_MODELS:
        return {
            "enable_thinking": True,
            "reasoning_effort": "high",
            "max_tokens": 2048,
            "response_format": None,
        }
    spec = model_request_spec(model)
    return {
        "enable_thinking": spec.enable_thinking,
        "reasoning_effort": spec.reasoning_effort,
        "max_tokens": spec.max_tokens,
        # The rubric prompt requests JSON; no API-enforced format is enabled.
        # Parsing and retries enforce valid scores for both supported models.
        "response_format": None,
    }


def judge_concurrency(value: int | None = None) -> int:
    raw_value: Any = value
    if raw_value is None:
        configured = os.environ.get(JUDGE_CONCURRENCY_ENV)
        raw_value = DEFAULT_JUDGE_CONCURRENCY if configured is None else configured
    if isinstance(raw_value, str):
        try:
            raw_value = int(raw_value.strip())
        except ValueError as error:
            raise SweQaError(
                f"{JUDGE_CONCURRENCY_ENV} must be an integer between 1 and "
                f"{MAX_JUDGE_CONCURRENCY}"
            ) from error
    if (
        isinstance(raw_value, bool)
        or not isinstance(raw_value, int)
        or not 1 <= raw_value <= MAX_JUDGE_CONCURRENCY
    ):
        raise SweQaError(
            f"{JUDGE_CONCURRENCY_ENV} must be an integer between 1 and "
            f"{MAX_JUDGE_CONCURRENCY}"
        )
    return raw_value


def _judge_prompt(*, question: str, reference: str, candidate: str) -> str:
    return f"""You are a strict evaluator. Score the candidate only against the supplied question and reference answer.

Score each dimension as an integer from 1 through 20:
- correctness: factual agreement with the reference; penalize errors.
- completeness: coverage of the reference's important points; penalize omissions.
- relevance: focus on the question; penalize tangents.
- clarity: precision and ease of understanding.
- coherence: logical organization and consistency of the explanation.

Scores 16-20 are reserved for excellent answers. When uncertain, choose the lower score. Treat the reference as judge-only evidence, not text to reproduce.

Question:
{question}

Reference answer:
{reference}

Candidate answer:
{candidate}

Return only one strict JSON object with exactly these five integer fields and no markdown:
{{"correctness": 1, "completeness": 1, "relevance": 1, "clarity": 1, "coherence": 1}}
"""


def _response_mapping(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        value = response.model_dump()
        if isinstance(value, dict):
            return value
    try:
        value = dict(response)
    except (TypeError, ValueError) as error:
        raise SweQaError("judge returned an unsupported response object") from error
    return value


def _response_content(response: Any) -> str:
    root = _response_mapping(response)
    choices = root.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise SweQaError("judge response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise SweQaError("judge response has no text content")
    return message["content"]


def _parse_scores(content: str) -> dict[str, int]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise SweQaError("judge response is not strict JSON") from error
    if not isinstance(value, dict) or set(value) != set(SCORE_KEYS):
        raise SweQaError("judge response does not have the five rubric fields")
    scores: dict[str, int] = {}
    for key in SCORE_KEYS:
        score = value.get(key)
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or not 1 <= score <= 20
        ):
            raise SweQaError(f"judge response has invalid {key} score")
        scores[key] = score
    return scores


def _response_usage(response: Any) -> dict[str, int | float | None]:
    root = _response_mapping(response)
    usage = root.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    hidden = root.get("_hidden_params")
    if not isinstance(hidden, dict):
        hidden = getattr(response, "_hidden_params", {})
    if not isinstance(hidden, dict):
        hidden = {}

    def token(name: str) -> int | None:
        value = usage.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    cost = hidden.get("response_cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        cost = None
    return {
        "input_tokens": token("prompt_tokens"),
        "output_tokens": token("completion_tokens"),
        "cost_usd": cost,
    }


def judge_candidate(
    *,
    completion_fn: Completion,
    api_key: str,
    api_base: str,
    question: str,
    reference: str,
    candidate: str,
    attempts: int,
    model: str = "glm-5.2",
) -> dict[str, Any]:
    generation = judge_generation_metadata(model)
    prompt = _judge_prompt(question=question, reference=reference, candidate=candidate)
    last_failure = "unknown"
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = completion_fn(
                model=f"openai/{model}",
                api_key=api_key,
                api_base=api_base,
                temperature=judge_temperature(model),
                seed=BENCHMARK_SEED,
                reasoning_effort=generation["reasoning_effort"],
                # LiteLLM's OpenAI model registry may not know this provider.
                # Explicitly forward it rather than silently dropping it.
                allowed_openai_params=["reasoning_effort"],
                max_tokens=generation["max_tokens"],
                messages=[{"role": "user", "content": prompt}],
                extra_body={"enable_thinking": generation["enable_thinking"]},
            )
        except Exception as error:  # noqa: BLE001 - providers share no stable error base.
            last_failure = f"transport error ({type(error).__name__})"
        else:
            try:
                scores = _parse_scores(_response_content(response))
            except SweQaError as error:
                last_failure = str(error)
            else:
                return {
                    "label": judge_label(model),
                    "model": model,
                    **generation,
                    "scores": scores,
                    "total": sum(scores.values()),
                    "latency_seconds": time.monotonic() - started,
                    "usage": _response_usage(response),
                }
        if attempt < attempts:
            time.sleep(min(float(attempt), 2.0))
    raise SweQaError(f"judge failed after {attempts} attempts: {last_failure}")


def judge_rag_candidate(
    *,
    completion_fn: Completion,
    api_key: str,
    api_base: str,
    question: str,
    required_steps: list[dict[str, Any]],
    accepted_source_ranges: list[dict[str, Any]],
    candidate: str,
    evidence_items: list[dict[str, Any]],
    attempts: int,
    model: str = "glm-5.2",
) -> dict[str, Any]:
    """Grade a RAG answer against gold steps and the exact delivered source pack."""
    generation = judge_generation_metadata(model)
    prompt = f"""You are an independent source-grounded answer grader. The reference steps and evidence pack are judge-only inputs. Do not reward claims that are absent from the supplied source evidence.

Question:
{question}

Required behavior steps:
{json.dumps(required_steps, ensure_ascii=False, indent=2)}

Accepted source spans for those steps:
{json.dumps(accepted_source_ranges, ensure_ascii=False, indent=2)}

Delivered evidence items, each with verified path, line range, and exact source:
{json.dumps(evidence_items, ensure_ascii=False, indent=2)}

Candidate answer:
{candidate}

Score every field as an integer 0, 1, or 2. For unsupported_claims, 2 means there are no material unsupported claims. For honest_gaps, 2 means missing or unverified evidence is clearly acknowledged, including correct abstention. Citation support requires cited source lines in the delivered evidence and those lines must support the cited claim. Source/deployment discipline requires distinguishing source-visible behavior from live deployment behavior.

Return only this exact JSON object with no markdown or extra keys:
{{"correctness":0,"required_step_coverage":0,"citation_support":0,"source_deployment_discipline":0,"honest_gaps":0,"unsupported_claims":0}}
"""
    last_failure = "unknown"
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = completion_fn(
                model=f"openai/{model}",
                api_key=api_key,
                api_base=api_base,
                temperature=judge_temperature(model),
                seed=BENCHMARK_SEED,
                reasoning_effort=generation["reasoning_effort"],
                allowed_openai_params=["reasoning_effort"],
                max_tokens=generation["max_tokens"],
                messages=[{"role": "user", "content": prompt}],
                extra_body={"enable_thinking": generation["enable_thinking"]},
            )
        except Exception as error:  # noqa: BLE001 - providers share no stable error base.
            last_failure = f"transport error ({type(error).__name__})"
        else:
            try:
                raw = json.loads(_response_content(response))
                if not isinstance(raw, dict) or set(raw) != set(RAG_SCORE_KEYS):
                    raise SweQaError("RAG grader response has invalid fields")
                scores = {}
                for key in RAG_SCORE_KEYS:
                    value = raw[key]
                    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
                        raise SweQaError(f"RAG grader returned an invalid {key} score")
                    scores[key] = value
            except (json.JSONDecodeError, SweQaError) as error:
                last_failure = str(error)
            else:
                return {
                    "label": rag_judge_label(model),
                    "model": model,
                    **generation,
                    "scores": scores,
                    "total": sum(scores.values()),
                    "latency_seconds": time.monotonic() - started,
                    "usage": _response_usage(response),
                }
        if attempt < attempts:
            time.sleep(min(float(attempt), 2.0))
    raise SweQaError(f"RAG judge failed after {attempts} attempts: {last_failure}")


def judge_rag_candidates(
    *,
    completion_fn: Completion,
    api_key: str,
    api_base: str,
    question: str,
    required_steps: list[dict[str, Any]],
    accepted_source_ranges: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    attempts: int,
    model: str,
) -> dict[str, Any]:
    """Score blinded answers for one task in a shared model call."""
    if not candidates:
        raise SweQaError("RAG batch judge requires at least one candidate")
    candidate_ids = [row.get("candidate_id") for row in candidates]
    if (
        any(not isinstance(value, str) or not value for value in candidate_ids)
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise SweQaError("RAG batch candidate IDs must be non-empty and unique")
    if any(
        not isinstance(row.get("answer"), str)
        or not isinstance(row.get("evidence_items"), list)
        for row in candidates
    ):
        raise SweQaError("RAG batch candidates must include answers and evidence arrays")

    generation = judge_generation_metadata(model)
    response_shape = {
        "answers": [
            {
                "candidate_id": candidate_id,
                "scores": {key: 0 for key in RAG_SCORE_KEYS},
            }
            for candidate_id in candidate_ids
        ]
    }
    prompt = f"""You are an independent source-grounded answer grader. Each candidate answer was produced under a different evidence condition, but condition names are withheld. Score each answer independently; do not rank answers or infer which retrieval method supplied them. The reference steps and evidence packs are judge-only. Do not reward claims absent from the candidate's exact evidence.

Question:
{question}

Required behavior steps:
{json.dumps(required_steps, ensure_ascii=False, indent=2)}

Accepted source spans:
{json.dumps(accepted_source_ranges, ensure_ascii=False, indent=2)}

Blinded candidates, each with its own exact delivered evidence:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Score every field as an integer 0, 1, or 2. For unsupported_claims, 2 means there are no material unsupported claims. For honest_gaps, 2 means missing or unverified evidence is clearly acknowledged, including correct abstention. Citation support requires cited source lines in the delivered evidence and those lines must support the claim. Source/deployment discipline requires distinguishing source-visible behavior from live deployment behavior.

Return only this JSON object, with each candidate ID exactly once and no extra keys:
{json.dumps(response_shape, ensure_ascii=False)}
"""
    last_failure = "unknown"
    calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    input_complete = True
    output_complete = True
    costs: list[float | None] = []
    started = time.monotonic()
    for attempt in range(1, attempts + 1):
        try:
            response = completion_fn(
                model=f"openai/{model}",
                api_key=api_key,
                api_base=api_base,
                temperature=judge_temperature(model),
                seed=BENCHMARK_SEED,
                reasoning_effort=generation["reasoning_effort"],
                allowed_openai_params=["reasoning_effort"],
                max_tokens=generation["max_tokens"],
                messages=[{"role": "user", "content": prompt}],
                extra_body={"enable_thinking": generation["enable_thinking"]},
            )
            calls += 1
            response_usage = _response_usage(response)
            if response_usage["input_tokens"] is None:
                input_complete = False
            else:
                total_input_tokens += int(response_usage["input_tokens"])
            if response_usage["output_tokens"] is None:
                output_complete = False
            else:
                total_output_tokens += int(response_usage["output_tokens"])
            costs.append(response_usage["cost_usd"])
        except Exception as error:  # noqa: BLE001 - providers share no stable error base.
            last_failure = f"transport error ({type(error).__name__})"
        else:
            try:
                raw = json.loads(_response_content(response))
                rows = raw.get("answers") if isinstance(raw, dict) else None
                if (
                    not isinstance(raw, dict)
                    or set(raw) != {"answers"}
                    or not isinstance(rows, list)
                    or len(rows) != len(candidate_ids)
                ):
                    raise SweQaError("RAG batch grader response has invalid fields")
                scores_by_id: dict[str, dict[str, int]] = {}
                for row in rows:
                    if (
                        not isinstance(row, dict)
                        or set(row) != {"candidate_id", "scores"}
                        or row["candidate_id"] not in candidate_ids
                        or row["candidate_id"] in scores_by_id
                        or not isinstance(row["scores"], dict)
                        or set(row["scores"]) != set(RAG_SCORE_KEYS)
                    ):
                        raise SweQaError("RAG batch grader returned an invalid candidate")
                    scores = {}
                    for key in RAG_SCORE_KEYS:
                        value = row["scores"][key]
                        if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
                            raise SweQaError(f"RAG batch grader returned an invalid {key} score")
                        scores[key] = value
                    scores_by_id[row["candidate_id"]] = scores
                if set(scores_by_id) != set(candidate_ids):
                    raise SweQaError("RAG batch grader omitted a candidate")
            except (json.JSONDecodeError, SweQaError) as error:
                last_failure = str(error)
            else:
                return {
                    "label": rag_judge_label(model),
                    "model": model,
                    **generation,
                    "scores_by_id": scores_by_id,
                    "totals_by_id": {
                        candidate_id: sum(scores.values())
                        for candidate_id, scores in scores_by_id.items()
                    },
                    "latency_seconds": time.monotonic() - started,
                    "usage": {
                        "input_tokens": total_input_tokens if input_complete else None,
                        "output_tokens": total_output_tokens if output_complete else None,
                        "cost_usd": (
                            None
                            if any(cost is None for cost in costs)
                            else sum(float(cost) for cost in costs if cost is not None)
                        ),
                        "calls": calls,
                    },
                }
        if attempt < attempts:
            time.sleep(min(float(attempt), 2.0))
    raise SweQaError(f"RAG batch judge failed after {attempts} attempts: {last_failure}")


def judge_task_trials(
    *,
    pair: dict[str, Any],
    reference: dict[str, Any],
    completion_fn: Completion,
    api_key: str,
    api_base: str,
    attempts: int,
    concurrency: int,
    model: str = "glm-5.2",
    profile_names: tuple[str, ...] = PROFILE_NAMES,
) -> dict[str, list[dict[str, Any]]]:
    if not profile_names or len(set(profile_names)) != len(profile_names):
        raise SweQaError("profile names must be non-empty and unique")
    work_items = [
        (profile_name, trial)
        for profile_name in profile_names
        for trial in pair["profiles"][profile_name]["trials"]
    ]
    futures: list[Future[dict[str, Any]]] = []
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="swe-qa-judge",
    ) as executor:
        for _, trial in work_items:
            futures.append(
                executor.submit(
                    judge_candidate,
                    completion_fn=completion_fn,
                    api_key=api_key,
                    api_base=api_base,
                    question=str(reference["question"]),
                    reference=str(reference["reference_answer"]),
                    candidate=str(trial["answer"]),
                    attempts=attempts,
                    model=model,
                )
            )
        try:
            judged = [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    results = {profile_name: [] for profile_name in profile_names}
    for (profile_name, trial), judge_result in zip(work_items, judged, strict=True):
        results[profile_name].append(
            {
                "trial_index": trial["trial_index"],
                "trial_name": trial.get("trial_name"),
                "judge": judge_result,
                "metrics": {
                    "input_tokens": trial["input_tokens"],
                    "output_tokens": trial["output_tokens"],
                    "tool_calls": trial["tool_calls"],
                    "agent_wall_seconds": trial["agent_wall_seconds"],
                    "cost_usd": trial["cost_usd"],
                    "usage_scope": usage_scope(trial),
                    **{
                        key: trial[key]
                        for key in (
                            *SESSION_USAGE_METRICS,
                            "session_usage",
                            "usage_collection_wall_seconds",
                        )
                        if key in trial
                    },
                },
            }
        )
    return results


def default_completion() -> Completion:
    try:
        import litellm
    except ImportError as error:
        raise SweQaError("LiteLLM is required to run the SWE-QA judge") from error
    litellm.suppress_debug_info = True
    return litellm.completion
