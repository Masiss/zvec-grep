"""Normalize frozen lexical, R4, and sufficient evidence packs for RAG trials."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from pathlib import Path
from typing import Any

from . import SweQaError
from .rag_evidence import (
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_ITEMS,
    score_required_steps,
    validate_source_pack,
)

PROFILE_NAMES = ("lexical-rg", "r4-hybrid", "sufficient-pack")
ZERO_NAVIGATION = {
    "rounds": 0,
    "calls": 0,
    "opened_files": 0,
    "cumulative_source_bytes": 0,
}


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SweQaError(f"could not read {label}: {path}") from error
    if not isinstance(value, dict):
        raise SweQaError(f"{label} must be a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_lexical_items(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows = value.get("items")
    if not isinstance(rows, list):
        raise SweQaError("lexical delivery must contain an items array")
    if any(not isinstance(row, dict) for row in rows):
        raise SweQaError("lexical delivery contains a non-object evidence item")
    return [
        {
            "rank": row.get("rank"),
            "path": row.get("path"),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
            "source_sha256": row.get("source_sha256"),
            "content": row.get("content"),
        }
        for row in rows
    ]


def _normalize_r4_items(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows = value.get("items")
    if not isinstance(rows, list):
        raise SweQaError("R4 delivery must contain an items array")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise SweQaError("R4 delivery contains a non-object evidence item")
        content_range = row.get("content_range")
        if (
            not isinstance(content_range, list)
            or len(content_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in content_range)
        ):
            raise SweQaError("R4 evidence item has no valid content_range")
        normalized.append(
            {
                "rank": row.get("rank"),
                "path": row.get("path"),
                "start_line": content_range[0],
                "end_line": content_range[1],
                "source_sha256": row.get("sha256"),
                "content": row.get("content"),
                "source_byte_offsets": row.get("raw_file_byte_offsets"),
                "content_columns": row.get("content_columns"),
            }
        )
    return normalized


def _normalize_lexical_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SweQaError("lexical candidate file must contain an array of objects")
    return [
        {
            "rank": row.get("candidate_rank", row.get("rank", index)),
            "path": row.get("path"),
            "start_line": row.get("start_line"),
            "end_line": row.get("end_line"),
            "source_sha256": row.get("source_sha256"),
            "content": row.get("content"),
        }
        for index, row in enumerate(value, start=1)
    ]


def _select_rank_stratified_candidates(
    lexical_items: list[dict[str, Any]],
    r4_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interleave the two frozen rank lists under the shared pack caps."""
    sources = {
        "r4-hybrid": sorted(r4_items, key=lambda item: int(item["rank"])),
        "lexical-rg": sorted(lexical_items, key=lambda item: int(item["rank"])),
    }
    indexes = {name: 0 for name in sources}
    selected: list[dict[str, Any]] = []
    used_bytes = 0
    while len(selected) < MAX_EVIDENCE_ITEMS and any(
        indexes[name] < len(rows) for name, rows in sources.items()
    ):
        made_progress = False
        for name in ("r4-hybrid", "lexical-rg"):
            rows = sources[name]
            while indexes[name] < len(rows):
                candidate = rows[indexes[name]]
                indexes[name] += 1
                path = candidate.get("path")
                start, end = candidate.get("start_line"), candidate.get("end_line")
                content = candidate.get("content")
                if (
                    not isinstance(path, str)
                    or not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or not isinstance(content, str)
                ):
                    raise SweQaError(f"{name} candidate has invalid source fields")
                if any(
                    prior["path"] == path
                    and max(prior["start_line"], start) <= min(prior["end_line"], end)
                    for prior in selected
                ):
                    continue
                if used_bytes + len(content.encode("utf-8")) > MAX_EVIDENCE_BYTES:
                    continue
                selected.append({**candidate, "rank": len(selected) + 1})
                used_bytes += len(content.encode("utf-8"))
                made_progress = True
                break
        if not made_progress:
            break
    return selected


def _sufficient_items(
    task: dict[str, Any], *, corpus_root: Path
) -> list[dict[str, Any]]:
    rows = task.get("accepted_source_ranges")
    if not isinstance(rows, list):
        raise SweQaError("gold task must contain accepted_source_ranges")
    items = []
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise SweQaError("accepted source range must be an object")
        path = row.get("path")
        start, end = row.get("start_line"), row.get("end_line")
        if not isinstance(path, str) or not isinstance(start, int) or not isinstance(end, int):
            raise SweQaError("accepted source range has invalid path or line numbers")
        try:
            file_bytes = (corpus_root / path).read_bytes()
            lines = file_bytes.decode("utf-8").splitlines(keepends=True)
        except (OSError, UnicodeDecodeError) as error:
            raise SweQaError(f"could not read accepted source range: {path}") from error
        if start < 1 or end < start or end > len(lines):
            raise SweQaError(f"accepted source range is outside the file: {path}:{start}-{end}")
        items.append(
            {
                "rank": rank,
                "path": path,
                "start_line": start,
                "end_line": end,
                "source_sha256": hashlib.sha256(file_bytes).hexdigest(),
                "content": "".join(lines[start - 1 : end]),
            }
        )
    return items


def _run_metrics(manifest: dict[str, Any], task_id: str, profile: str) -> tuple[float, int]:
    rows = manifest.get("runs")
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise SweQaError(f"{profile} manifest must contain a runs array")
    if profile == "lexical-rg":
        row = next((item for item in rows if item.get("task_id") == task_id), None)
        if row is None:
            raise SweQaError(f"lexical manifest has no task {task_id}")
        latency = row.get("ranking_end_to_end_seconds")
        rss = row.get("runner_peak_rss_kb")
    else:
        row = next(
            (
                item
                for item in rows
                if item.get("task_id") == task_id and item.get("mode") == "hybrid"
            ),
            None,
        )
        if row is None:
            raise SweQaError(f"R4 manifest has no hybrid run for task {task_id}")
        latency = row.get("elapsed_seconds")
        rss = row.get("peak_rss_kb")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or latency < 0
        or isinstance(rss, bool)
        or not isinstance(rss, int)
        or rss <= 0
    ):
        raise SweQaError(f"{profile} manifest has invalid latency or peak RSS for {task_id}")
    return float(latency), rss


def prepare_rag_packs(
    *,
    prompts_path: Path,
    gold_path: Path,
    source_lock_path: Path,
    corpus_root: Path,
    lexical_root: Path,
    r4_root: Path,
    output_root: Path,
    split: str = "development",
    include_candidate: bool = False,
) -> dict[str, Any]:
    """Write RAG sidecars for the selected frozen prompt split."""
    prompts = _read_object(prompts_path, "RAG prompts")
    gold = _read_object(gold_path, "RAG gold")
    source_lock = _read_object(source_lock_path, "source lock")
    lexical_manifest = _read_object(lexical_root / "run-manifest.json", "lexical manifest")
    r4_manifest = _read_object(r4_root / "run-manifest.json", "R4 manifest")
    prompt_rows = prompts.get("tasks")
    gold_rows = gold.get("tasks")
    if not isinstance(prompt_rows, list) or not isinstance(gold_rows, list):
        raise SweQaError("prompts and gold must contain task arrays")
    gold_by_id = {
        row.get("task_id"): row for row in gold_rows if isinstance(row, dict)
    }
    selected = [
        row for row in prompt_rows
        if isinstance(row, dict) and row.get("split") == split
    ]
    if not selected:
        raise SweQaError(f"no prompt tasks found for split {split!r}")
    selected_prompts = dict(prompts)
    selected_prompts["tasks"] = selected
    selected_prompt_bytes = (
        json.dumps(selected_prompts, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    selected_prompt_sha256 = hashlib.sha256(selected_prompt_bytes).hexdigest()
    for label, manifest in (
        ("lexical", lexical_manifest),
        ("R4", r4_manifest),
    ):
        if manifest.get("task_input_sha256") != selected_prompt_sha256:
            raise SweQaError(f"{label} run task input does not match the selected prompt split")
    if r4_manifest.get("source_lock_sha256") != _sha256_file(source_lock_path):
        raise SweQaError("R4 run used a different source lock")
    if output_root.exists() and any(output_root.iterdir()):
        raise SweQaError(f"RAG pack output directory must be empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    run_rows = []
    profile_names = (*PROFILE_NAMES, "candidate") if include_candidate else PROFILE_NAMES
    evidence_rows: dict[str, list[dict[str, Any]]] = {profile: [] for profile in profile_names}
    for prompt in selected:
        task_id = prompt.get("task_id")
        if not isinstance(task_id, str) or task_id not in gold_by_id:
            raise SweQaError("prompt task has no matching gold task")
        gold_task = gold_by_id[task_id]
        task_output = output_root / task_id
        task_output.mkdir(parents=True, exist_ok=True)

        source_paths = {
            "lexical-rg": lexical_root / task_id / "rg-delivered.json",
            "r4-hybrid": r4_root / task_id / "hybrid-delivered.json",
        }
        for profile, source_path in source_paths.items():
            delivery = _read_object(source_path, f"{profile} delivery")
            items = (
                _normalize_lexical_items(delivery)
                if profile == "lexical-rg"
                else _normalize_r4_items(delivery)
            )
            latency, peak_rss = _run_metrics(
                lexical_manifest if profile == "lexical-rg" else r4_manifest,
                task_id,
                profile,
            )
            verified = _write_sidecar(
                task_output / f"{profile}.json",
                items,
                navigation_usage={**ZERO_NAVIGATION, "rounds": 1, "calls": 1},
                retrieval_latency=latency,
                peak_rss_kib=peak_rss,
                source_root=corpus_root,
                source_lock=source_lock,
            )
            coverage = score_required_steps(gold_task, verified)
            evidence_rows[profile].append(
                {
                    "task_id": task_id,
                    "covered_steps": coverage["covered_steps"],
                    "required_steps": coverage["required_steps"],
                    "complete": coverage["complete"],
                    "abstention_pack_empty": coverage["abstention_pack_empty"],
                    "retrieval_latency_seconds": latency,
                    "peak_rss_kib": peak_rss,
                }
            )
        if include_candidate:
            lexical_candidates = _normalize_lexical_candidates(
                json.loads((lexical_root / task_id / "rg-ranked-candidates.json").read_text(encoding="utf-8"))
            )
            r4_raw = _read_object(r4_root / task_id / "hybrid.json", "R4 hybrid result")
            r4_result = r4_raw.get("result")
            if not isinstance(r4_result, dict):
                raise SweQaError(f"R4 hybrid result has no result object for {task_id}")
            r4_candidates = _normalize_r4_items(r4_result)
            candidate_items = _select_rank_stratified_candidates(lexical_candidates, r4_candidates)
            lexical_latency, _ = _run_metrics(lexical_manifest, task_id, "lexical-rg")
            r4_latency, r4_peak_rss = _run_metrics(r4_manifest, task_id, "r4-hybrid")
            candidate_latency = lexical_latency + r4_latency
            verified = _write_sidecar(
                task_output / "candidate.json",
                candidate_items,
                navigation_usage={"rounds": 1, "calls": 2},
                retrieval_latency=candidate_latency,
                peak_rss_kib=r4_peak_rss,
                source_root=corpus_root,
                source_lock=source_lock,
            )
            coverage = score_required_steps(gold_task, verified)
            evidence_rows["candidate"].append(
                {
                    "task_id": task_id,
                    "covered_steps": coverage["covered_steps"],
                    "required_steps": coverage["required_steps"],
                    "complete": coverage["complete"],
                    "abstention_pack_empty": coverage["abstention_pack_empty"],
                    "retrieval_latency_seconds": candidate_latency,
                    "peak_rss_kib": r4_peak_rss,
                }
            )
        started = time.perf_counter()
        enough = _sufficient_items(gold_task, corpus_root=corpus_root)
        sufficient_latency = time.perf_counter() - started
        verified = _write_sidecar(
            task_output / "sufficient-pack.json",
            enough,
            navigation_usage=dict(ZERO_NAVIGATION),
            retrieval_latency=sufficient_latency,
            peak_rss_kib=max(1, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)),
            source_root=corpus_root,
            source_lock=source_lock,
        )
        coverage = score_required_steps(gold_task, verified)
        evidence_rows["sufficient-pack"].append(
            {
                "task_id": task_id,
                "covered_steps": coverage["covered_steps"],
                "required_steps": coverage["required_steps"],
                "complete": coverage["complete"],
                "abstention_pack_empty": coverage["abstention_pack_empty"],
                "retrieval_latency_seconds": sufficient_latency,
                "peak_rss_kib": max(
                    1, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                ),
            }
        )
        run_rows.append({"task_id": task_id, "profiles": list(profile_names)})

    manifest = {
        "schema_version": 1,
        "split": split,
        "task_count": len(selected),
        "candidate_strategy": (
            {
                "name": "rank-stratified-r4-lexical-interleave",
                "version": 1,
                "selection_order": ["r4-hybrid", "lexical-rg"],
                "overlap_policy": "skip same-path intersecting line ranges",
                "max_items": MAX_EVIDENCE_ITEMS,
                "max_evidence_bytes": MAX_EVIDENCE_BYTES,
            }
            if include_candidate
            else None
        ),
        "input_sha256": {
            "prompts": _sha256_file(prompts_path),
            "selected_prompts": selected_prompt_sha256,
            "gold": _sha256_file(gold_path),
            "source_lock": _sha256_file(source_lock_path),
            "lexical_run": _sha256_file(lexical_root / "run-manifest.json"),
            "r4_run": _sha256_file(r4_root / "run-manifest.json"),
            **(
                {
                    "candidate_sources": {
                        row["task_id"]: {
                            "lexical_ranked_candidates": _sha256_file(
                                lexical_root / row["task_id"] / "rg-ranked-candidates.json"
                            ),
                            "r4_hybrid_result": _sha256_file(
                                r4_root / row["task_id"] / "hybrid.json"
                            ),
                        }
                        for row in run_rows
                    }
                }
                if include_candidate
                else {}
            ),
        },
        "runs": run_rows,
        "evidence_sufficiency": {
            profile: {
                "task_count": len(rows),
                "mean_required_step_coverage": sum(
                    row["covered_steps"] / row["required_steps"]
                    for row in rows
                    if row["required_steps"]
                ) / max(1, sum(bool(row["required_steps"]) for row in rows)),
                "complete_evidence_task_count": sum(row["complete"] for row in rows),
                "mean_retrieval_latency_seconds": sum(
                    row["retrieval_latency_seconds"] for row in rows
                ) / len(rows),
                "mean_peak_rss_kib": sum(row["peak_rss_kib"] for row in rows) / len(rows),
                "per_task": rows,
            }
            for profile, rows in evidence_rows.items()
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _write_sidecar(
    path: Path,
    items: list[dict[str, Any]],
    *,
    navigation_usage: dict[str, int],
    retrieval_latency: float,
    peak_rss_kib: int,
    source_root: Path,
    source_lock: dict[str, Any],
) -> dict[str, Any]:
    verified = validate_source_pack(items, source_root=source_root, source_lock=source_lock)
    navigation = {
        "rounds": navigation_usage["rounds"],
        "calls": navigation_usage["calls"],
        "opened_files": len({item["path"] for item in items}),
        "cumulative_source_bytes": verified["source_bytes"],
    }
    sidecar = {
        "schema_version": 1,
        "evidence_items": items,
        "navigation_usage": navigation,
        "retrieval_latency_seconds": retrieval_latency,
        "peak_rss_kib": peak_rss_kib,
    }
    path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--lexical-root", type=Path, required=True)
    parser.add_argument("--r4-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "validation"), default="development")
    parser.add_argument("--include-candidate", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_rag_packs(
            prompts_path=args.prompts,
            gold_path=args.gold,
            source_lock_path=args.source_lock,
            corpus_root=args.corpus,
            lexical_root=args.lexical_root,
            r4_root=args.r4_root,
            output_root=args.output,
            split=args.split,
            include_candidate=args.include_candidate,
        )
    except SweQaError as error:
        parser.error(str(error))
    print(json.dumps({"split": manifest["split"], "task_count": manifest["task_count"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
