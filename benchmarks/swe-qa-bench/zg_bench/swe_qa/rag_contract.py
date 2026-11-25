"""Validation for frozen R4 answer-quality task and grader assets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from . import SweQaError

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLES = {"development", "validation"}
_TASK_TYPES = {"direct_lookup", "multi_step", "vocabulary_divergent", "abstention"}
_GOLD_FIELDS = {
    "accepted_source_ranges",
    "gold",
    "reference_answer",
    "reference_answers",
    "required_steps",
}


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SweQaError(f"{label} must be an array of objects")
    return value


def _text(row: dict[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SweQaError(f"{label}.{key} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise SweQaError(f"{label} must be a lowercase SHA-256")
    return value


def _contains_gold_fields(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_GOLD_FIELDS.intersection(value)) or any(
            _contains_gold_fields(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_gold_fields(child) for child in value)
    return False


def validate_rag_contract(
    prompts: dict[str, Any],
    gold: dict[str, Any],
    references: dict[str, Any] | None = None,
    source_lock: dict[str, Any] | None = None,
    corpus_root: Path | None = None,
) -> dict[str, Any]:
    """Validate split balance, source provenance, gold spans, and asset isolation."""
    if prompts.get("schema_version") != 1 or gold.get("schema_version") != 1:
        raise SweQaError("RAG contract schema_version must be 1")
    if prompts.get("gold_visibility") != "grader-only":
        raise SweQaError("RAG prompt manifest gold_visibility must be grader-only")
    _text(prompts, "answer_instructions", "prompts")
    prompt_rows = _records(prompts.get("tasks"), "prompts.tasks")
    gold_rows = _records(gold.get("tasks"), "gold.tasks")
    if len(prompt_rows) != 24:
        raise SweQaError(f"RAG prompt manifest must contain 24 tasks, found {len(prompt_rows)}")
    if len(gold_rows) != len(prompt_rows):
        raise SweQaError("RAG gold and prompt task counts differ")

    prompt_by_id: dict[str, dict[str, Any]] = {}
    locked_repositories: dict[str, dict[str, Any]] = {}
    task_type_counts = {task_type: 0 for task_type in _TASK_TYPES}
    abstention_by_split = {role: 0 for role in _ROLES}
    question_hashes: set[str] = set()
    if source_lock is not None:
        repositories = source_lock.get("repositories")
        if not isinstance(repositories, list):
            raise SweQaError("source lock repositories must be an array")
        for repository in repositories:
            if not isinstance(repository, dict) or not isinstance(repository.get("name"), str):
                raise SweQaError("source lock has an invalid repository record")
            name = repository["name"]
            if name in locked_repositories:
                raise SweQaError(f"source lock has duplicate repository {name}")
            files = repository.get("files")
            if not isinstance(files, list):
                raise SweQaError(f"source lock has no file list for {name}")
            locked_repositories[name] = {
                "revision": repository.get("upstream_revision"),
                "files": {row.get("path"): row.get("sha256") for row in files if isinstance(row, dict)},
            }
    split_counts = {role: 0 for role in _ROLES}
    for index, row in enumerate(prompt_rows):
        label = f"prompts.tasks[{index}]"
        task_id = _text(row, "task_id", label)
        question = _text(row, "question", label)
        repository = _text(row, "repository", label)
        if task_id in prompt_by_id:
            raise SweQaError(f"duplicate RAG task_id {task_id}")
        role = row.get("split")
        if role not in _ROLES:
            raise SweQaError(f"{label}.split must be development or validation")
        task_type = row.get("task_type")
        if task_type not in _TASK_TYPES:
            raise SweQaError(f"{label}.task_type is invalid")
        task_type_counts[task_type] += 1
        if task_type == "abstention":
            abstention_by_split[role] += 1
        if hashlib.sha256(question.encode("utf-8")).hexdigest() != row.get("question_sha256"):
            raise SweQaError(f"question SHA256 mismatch for {task_id}")
        if row["question_sha256"] in question_hashes:
            raise SweQaError(f"duplicate RAG question text for {task_id}")
        question_hashes.add(row["question_sha256"])
        commit = _text(row, "repository_commit", label)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SweQaError(f"repository_commit must be a full lowercase SHA for {task_id}")
        if source_lock is not None:
            locked = locked_repositories.get(repository)
            if locked is None or locked["revision"] != commit:
                raise SweQaError(f"source lock revision mismatch for {repository}")
        if source_lock is not None:
            locked = locked_repositories.get(repository)
            if locked is None or locked["revision"] != commit:
                raise SweQaError(f"source lock revision mismatch for {repository}")
        prompt_by_id[task_id] = row
        split_counts[role] += 1

    if split_counts != {"development": 12, "validation": 12}:
        raise SweQaError("RAG contract requires 12 development and 12 validation tasks")
    if task_type_counts != {
        "direct_lookup": 8,
        "multi_step": 8,
        "vocabulary_divergent": 4,
        "abstention": 4,
    }:
        raise SweQaError("RAG task types must contain 8 direct, 8 multi-step, 4 vocabulary-divergent, and 4 abstention tasks")
    if abstention_by_split != {"development": 2, "validation": 2}:
        raise SweQaError("RAG contract requires two abstention tasks in each split")

    gold_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(gold_rows):
        label = f"gold.tasks[{index}]"
        task_id = _text(row, "task_id", label)
        if task_id in gold_by_id or task_id not in prompt_by_id:
            raise SweQaError(f"duplicate or unknown gold task_id {task_id}")
        if row.get("split") != prompt_by_id[task_id]["split"]:
            raise SweQaError(f"split mismatch for {task_id}")
        outcome = row.get("outcome")
        if outcome not in {"answerable", "abstain"}:
            raise SweQaError(f"gold outcome is invalid for {task_id}")
        if (outcome == "abstain") != (prompt_by_id[task_id]["task_type"] == "abstention"):
            raise SweQaError(f"gold outcome does not match task type for {task_id}")
        steps = _records(row.get("required_steps"), f"{label}.required_steps")
        if not steps:
            raise SweQaError(f"{label}.required_steps must not be empty")
        for step_index, step in enumerate(steps):
            _text(step, "step_id", f"{label}.required_steps[{step_index}]")
            _text(step, "description", f"{label}.required_steps[{step_index}]")
            range_ids = step.get("accepted_range_ids")
            if not isinstance(range_ids, list) or any(not isinstance(item, str) for item in range_ids):
                raise SweQaError(f"{label}.required_steps[{step_index}].accepted_range_ids must be an array of IDs")
        ranges = _records(row.get("accepted_source_ranges"), f"{label}.accepted_source_ranges")
        range_ids_seen: set[str] = set()
        if outcome == "answerable" and not ranges:
            raise SweQaError(f"{label}.accepted_source_ranges must not be empty")
        if outcome == "abstain" and ranges:
            raise SweQaError(f"abstention task must not claim accepted source ranges: {task_id}")
        for range_index, source_range in enumerate(ranges):
            range_label = f"{label}.accepted_source_ranges[{range_index}]"
            range_id = _text(source_range, "range_id", range_label)
            if range_id in range_ids_seen:
                raise SweQaError(f"duplicate accepted range ID for {task_id}")
            range_ids_seen.add(range_id)
            repository = _text(source_range, "repository", range_label)
            if repository != prompt_by_id[task_id]["repository"]:
                raise SweQaError(f"source range repository mismatch for {task_id}")
            path = _text(source_range, "path", range_label)
            normalized = PurePosixPath(path)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise SweQaError(f"unsafe source path for {task_id}")
            start, end = source_range.get("start_line"), source_range.get("end_line")
            if (not isinstance(start, int) or isinstance(start, bool) or start < 1
                    or not isinstance(end, int) or isinstance(end, bool) or end < start):
                raise SweQaError(f"invalid source line range for {task_id}")
            source_hash = _sha256(source_range.get("source_sha256"), f"{range_label}.source_sha256")
            if source_lock is not None:
                locked = locked_repositories[repository]["files"].get(path)
                if locked != source_hash:
                    raise SweQaError(f"source range hash does not match source lock for {task_id}: {path}")
            if (source_lock is None) != (corpus_root is None):
                raise SweQaError("source_lock and corpus_root must be supplied together")
            if source_lock is not None and corpus_root is not None:
                try:
                    contents = (corpus_root / path).read_bytes()
                except OSError as error:
                    raise SweQaError(f"could not read accepted source for {task_id}: {path}") from error
                if hashlib.sha256(contents).hexdigest() != source_hash:
                    raise SweQaError(f"materialized source SHA256 mismatch for {task_id}: {path}")
                try:
                    line_count = len(contents.decode("utf-8").splitlines())
                except UnicodeDecodeError as error:
                    raise SweQaError(f"accepted source is not UTF-8 for {task_id}: {path}") from error
                if end > line_count:
                    raise SweQaError(f"source range exceeds file for {task_id}: {path}")
        for step_index, step in enumerate(steps):
            step_range_ids = set(step["accepted_range_ids"])
            if not step_range_ids.issubset(range_ids_seen):
                raise SweQaError(f"required step references an unknown source range for {task_id}")
            if outcome == "answerable" and not step_range_ids:
                raise SweQaError(f"answerable step has no accepted source range for {task_id}")
            if outcome == "abstain" and step_range_ids:
                raise SweQaError(f"abstention step must not cite accepted source ranges for {task_id}")
        gold_by_id[task_id] = row

    if set(prompt_by_id) != set(gold_by_id):
        raise SweQaError("RAG gold task IDs do not exactly match prompt task IDs")
    if references is not None:
        if references.get("schema_version") != 1 or references.get("visibility") != "judge-only":
            raise SweQaError("RAG references must be schema v1 and judge-only")
        reference_rows = _records(references.get("references"), "references.references")
        refs_by_id = {}
        for index, row in enumerate(reference_rows):
            label = f"references.references[{index}]"
            task_id = _text(row, "task_id", label)
            if task_id in refs_by_id or task_id not in prompt_by_id:
                raise SweQaError(f"duplicate or unknown RAG reference task_id {task_id}")
            if row.get("question") != prompt_by_id[task_id]["question"]:
                raise SweQaError(f"RAG reference question mismatch for {task_id}")
            reference_answer = _text(row, "reference_answer", label)
            gold_answer = " ".join(
                step["description"] for step in gold_by_id[task_id]["required_steps"]
            )
            if reference_answer != gold_answer:
                raise SweQaError(f"RAG reference answer does not match gold steps for {task_id}")
            refs_by_id[task_id] = row
        if set(refs_by_id) != set(prompt_by_id):
            raise SweQaError("RAG reference task IDs do not exactly match prompt task IDs")
    if _contains_gold_fields(prompts):
        raise SweQaError("judge-only gold fields must not appear in the prompt manifest")
    return {
        "valid": True,
        "task_count": len(prompt_rows),
        "split_counts": split_counts,
        "gold_is_separate": True,
    }
