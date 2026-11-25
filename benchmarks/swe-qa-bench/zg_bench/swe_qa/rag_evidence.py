"""Source integrity, required-step coverage, and citation audits for RAG trials."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from zg_bench.core.errors import SweQaError

_CITATION = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9]+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?"
)
MAX_EVIDENCE_ITEMS = 10
MAX_EVIDENCE_BYTES = 24_576
MAX_ROUNDS = 3
MAX_CALLS = 6
MAX_OPENED_FILES = 32
MAX_CUMULATIVE_BYTES = 65_536


def validate_navigation_usage(value: Any) -> dict[str, int]:
    """Enforce the frozen R0 navigation caps recorded for an agent trial."""
    fields = {
        "rounds": MAX_ROUNDS,
        "calls": MAX_CALLS,
        "opened_files": MAX_OPENED_FILES,
        "cumulative_source_bytes": MAX_CUMULATIVE_BYTES,
    }
    if not isinstance(value, dict):
        raise SweQaError("RAG trial must record navigation_usage")
    result = {}
    for field, maximum in fields.items():
        count = value.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= maximum:
            raise SweQaError(f"navigation_usage.{field} exceeds the frozen R0 cap")
        result[field] = count
    return result


def validate_trial_navigation_usage(value: Any, tool_calls: Any) -> dict[str, int]:
    """Apply the shared call budget across retrieval and answer-agent tools."""
    usage = validate_navigation_usage(value)
    if isinstance(tool_calls, bool) or not isinstance(tool_calls, int) or tool_calls < 0:
        raise SweQaError("RAG trial tool_calls must be a non-negative integer")
    if usage["calls"] + tool_calls > MAX_CALLS:
        raise SweQaError(f"RAG trial exceeds the {MAX_CALLS}-call budget")
    return usage


def validate_source_pack(
    items: Any,
    *,
    source_root: Path,
    source_lock: dict[str, Any],
) -> dict[str, Any]:
    """Verify bounded pack items against source bytes and the frozen lock."""
    if not isinstance(items, list) or len(items) > MAX_EVIDENCE_ITEMS:
        raise SweQaError("evidence pack must be a list of at most 10 items")
    locked_files = {
        row["path"]: row
        for repository in source_lock.get("repositories", [])
        for row in repository.get("files", [])
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    verified: list[dict[str, Any]] = []
    total_bytes = 0
    previous_rank = 0
    for index, item in enumerate(items):
        label = f"evidence_items[{index}]"
        if not isinstance(item, dict):
            raise SweQaError(f"{label} must be an object")
        rank, start, end = item.get("rank"), item.get("start_line"), item.get("end_line")
        path, content = item.get("path"), item.get("content")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank <= previous_rank:
            raise SweQaError(f"{label}.rank must be strictly increasing")
        if not isinstance(path, str) or path not in locked_files:
            raise SweQaError(f"{label}.path is absent from the source lock")
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise SweQaError(f"{label}.start_line is invalid")
        if not isinstance(end, int) or isinstance(end, bool) or end < start:
            raise SweQaError(f"{label}.end_line is invalid")
        if not isinstance(content, str):
            raise SweQaError(f"{label}.content must be text")
        try:
            source_bytes = (source_root / path).read_bytes()
        except OSError as error:
            raise SweQaError(f"could not read evidence source {path}: {error}") from error
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        if source_hash != locked_files[path].get("sha256") or item.get("source_sha256") != source_hash:
            raise SweQaError(f"source SHA256 mismatch for evidence item {rank}: {path}")
        try:
            source_text = source_bytes.decode("utf-8")
            lines = source_text.splitlines(keepends=True)
        except UnicodeDecodeError as error:
            raise SweQaError(f"evidence source is not UTF-8: {path}") from error
        if end > len(lines):
            raise SweQaError(f"evidence range exceeds source file: {path}")
        content_bytes = content.encode("utf-8")
        offsets = item.get("source_byte_offsets")
        if offsets is not None:
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in offsets)
            ):
                raise SweQaError(f"{label}.source_byte_offsets must contain two integers")
            byte_start, byte_end = offsets
            if byte_start < 0 or byte_end <= byte_start or byte_end > len(source_bytes):
                raise SweQaError(f"{label}.source_byte_offsets are outside the source file")
            if source_bytes[byte_start:byte_end] != content_bytes:
                raise SweQaError(f"evidence content does not match its byte slice: {path}:{start}-{end}")
            actual_start = source_bytes[:byte_start].count(b"\n") + 1
            actual_end = source_bytes[: byte_end - 1].count(b"\n") + 1
            if actual_start != start or actual_end != end:
                raise SweQaError(f"evidence byte offsets disagree with declared lines: {path}:{start}-{end}")
        else:
            expected = "".join(lines[start - 1 : end]).encode("utf-8")
            if content_bytes != expected:
                raise SweQaError(f"evidence content does not match its declared source slice: {path}:{start}-{end}")
        byte_count = len(content_bytes)
        total_bytes += byte_count
        verified.append({"rank": rank, "path": path, "start_line": start, "end_line": end, "source_bytes": byte_count})
        previous_rank = rank
    if total_bytes > MAX_EVIDENCE_BYTES:
        raise SweQaError(f"evidence pack exceeds {MAX_EVIDENCE_BYTES} source bytes")
    return {"valid": True, "item_count": len(verified), "source_bytes": total_bytes, "items": verified}


def score_required_steps(gold_task: dict[str, Any], verified_pack: dict[str, Any]) -> dict[str, Any]:
    """Measure accepted-range reachability as an evidence metric, separately from answers."""
    ranges = {row["range_id"]: row for row in gold_task.get("accepted_source_ranges", [])}
    item_rows = verified_pack["items"]
    if gold_task.get("outcome") == "abstain":
        empty = not item_rows
        return {
            "covered_steps": 0,
            "required_steps": 0,
            "complete": empty,
            "abstention_pack_empty": empty,
            "steps": [],
        }
    step_results = []
    for step in gold_task.get("required_steps", []):
        accepted = [ranges[range_id] for range_id in step.get("accepted_range_ids", [])]
        hits = [
            item
            for item in item_rows
            for target in accepted
            if item["path"] == target["path"]
            and max(item["start_line"], target["start_line"]) <= min(item["end_line"], target["end_line"])
        ]
        step_results.append({"step_id": step["step_id"], "covered": bool(hits), "item_ranks": sorted({item["rank"] for item in hits})})
    covered = sum(step["covered"] for step in step_results)
    total = len(step_results)
    return {
        "covered_steps": covered,
        "required_steps": total,
        "complete": covered == total and total > 0,
        "abstention_pack_empty": None,
        "steps": step_results,
    }


def audit_answer_citations(answer: str, verified_pack: dict[str, Any]) -> dict[str, Any]:
    """Check that explicit path:line citations point into source items delivered to the agent."""
    items = verified_pack["items"]
    citations = []
    for match in _CITATION.finditer(answer):
        path = match.group("path")
        normalized = path.replace("\\", "/").lstrip("/")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        resolved_paths = {
            item["path"]
            for item in items
            if item["path"] == normalized
            or item["path"].endswith("/" + normalized)
            or normalized.endswith("/" + item["path"])
        }
        resolved_path = next(iter(resolved_paths)) if len(resolved_paths) == 1 else None
        supported = resolved_path is not None and any(
            item["path"] == resolved_path
            and item["start_line"] <= start
            and end <= item["end_line"]
            for item in items
        )
        citations.append(
            {
                "path": path,
                "resolved_path": resolved_path,
                "start_line": start,
                "end_line": end,
                "present_in_pack": supported,
            }
        )
    return {
        "citation_count": len(citations),
        "supported_count": sum(row["present_in_pack"] for row in citations),
        "all_citations_in_pack": bool(citations) and all(row["present_in_pack"] for row in citations),
        "citations": citations,
    }
