"""Run matched RAG answer trials with the host Codex CLI and OAuth session."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from zg_bench.core.errors import SweQaError
from zg_bench.engines.codex import codex_completion

from .rag_evidence import validate_source_pack, validate_trial_navigation_usage
from .rag_harbor import PROFILE_NAMES, RAG_CANDIDATE_PROFILE

Completion = Callable[..., Any]
_ALLOWED_PROFILE_SETS = (
    PROFILE_NAMES,
    (*PROFILE_NAMES, RAG_CANDIDATE_PROFILE),
)


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


def run_rag_codex(
    *,
    datasets_root: Path,
    evidence_packs_root: Path,
    source_lock_path: Path,
    corpus_root: Path,
    output_root: Path,
    run_id: str,
    model: str = "gpt-6-luna",
    trials: int = 1,
    profile_names: Sequence[str] = PROFILE_NAMES,
    completion_fn: Completion | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Run same-model answer trials against the prepared evidence-only datasets."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise SweQaError("run_id may contain only letters, digits, dots, underscores, and hyphens")
    profile_names = tuple(profile_names)
    all_profiles = set(PROFILE_NAMES) | {"candidate"}
    if not profile_names or len(set(profile_names)) != len(profile_names) or not set(profile_names) <= all_profiles:
        raise SweQaError("RAG run profiles must be a unique non-empty subset of the frozen profiles and candidate")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise SweQaError("trials must be a positive integer")
    if output_root.exists() and any(output_root.iterdir()) and not resume:
        raise SweQaError(f"RAG Codex output must be absent or empty: {output_root}")

    codex_cli = shutil.which("codex")
    codex_auth = Path.home() / ".codex" / "auth.json"
    if completion_fn is None:
        if codex_cli is None:
            raise SweQaError("Codex CLI is not on PATH")
        if not codex_auth.is_file():
            raise SweQaError("Codex auth file is missing; log in with codex login first")
        completion_fn = codex_completion(model)

    dataset_manifest_path = datasets_root.parent / "dataset-manifest.json"
    dataset_manifest = _read_object(dataset_manifest_path, "RAG dataset manifest")
    if dataset_manifest.get("schema_version") != 1:
        raise SweQaError("RAG dataset manifest schema_version must be 1")
    task_ids = dataset_manifest.get("task_ids")
    dataset_hashes = dataset_manifest.get("dataset_files_sha256")
    sidecar_hashes = dataset_manifest.get("evidence_sidecars_sha256")
    if (
        not isinstance(task_ids, list)
        or not task_ids
        or any(not isinstance(task_id, str) for task_id in task_ids)
        or len(set(task_ids)) != len(task_ids)
        or not isinstance(dataset_hashes, dict)
        or not isinstance(sidecar_hashes, dict)
    ):
        raise SweQaError("RAG dataset manifest has invalid task or hash records")
    source_lock = _read_object(source_lock_path, "source lock")
    for profile in profile_names:
        profile_root = datasets_root / profile
        if sorted(path.name for path in profile_root.iterdir() if path.is_dir()) != sorted(task_ids):
            raise SweQaError(f"{profile} dataset task IDs differ from the manifest")
        if not isinstance(dataset_hashes.get(profile), dict) or not isinstance(
            sidecar_hashes.get(profile), dict
        ):
            raise SweQaError(f"RAG dataset manifest has no hashes for {profile}")
        for task_id in task_ids:
            task_dir = profile_root / task_id
            file_hashes = dataset_hashes[profile].get(task_id)
            if not isinstance(file_hashes, dict):
                raise SweQaError(f"RAG dataset manifest lacks {profile}/{task_id} file hashes")
            actual_files = {
                path.relative_to(task_dir).as_posix()
                for path in task_dir.rglob("*")
                if path.is_file()
            }
            if actual_files != set(file_hashes):
                raise SweQaError(f"dataset files differ from manifest for {profile}/{task_id}")
            for relative_path, expected_hash in file_hashes.items():
                if _sha256_file(task_dir / relative_path) != expected_hash:
                    raise SweQaError(f"dataset file hash mismatch: {profile}/{task_id}/{relative_path}")
            pack_path = evidence_packs_root / task_id / f"{profile}.json"
            if _sha256_file(pack_path) != sidecar_hashes[profile].get(task_id):
                raise SweQaError(f"evidence sidecar hash mismatch: {profile}/{task_id}")
            pack = _read_object(pack_path, f"{profile} evidence pack")
            if pack.get("schema_version") != 1:
                raise SweQaError(f"{profile} evidence pack schema_version must be 1")
            validate_source_pack(
                pack.get("evidence_items"),
                source_root=corpus_root,
                source_lock=source_lock,
            )

    output_root.mkdir(parents=True, exist_ok=True)
    pair_hashes = []
    for task_id in task_ids:
        pair_path = output_root / task_id / "pair.json"
        if pair_path.is_file():
            if not resume:
                raise SweQaError(f"pair already exists, pass resume to reuse: {pair_path}")
            existing_pair = _read_object(pair_path, f"existing pair for {task_id}")
            if (
                existing_pair.get("task_id") != task_id
                or existing_pair.get("model") != model
                or set(existing_pair.get("profiles", {})) != set(profile_names)
                or existing_pair.get("expected_trials") != trials
                or existing_pair.get("actual_trials") != trials
                or existing_pair.get("common_instruction_sha256")
                != dataset_manifest["common_instruction_sha256"][task_id]
            ):
                raise SweQaError(f"existing pair is incompatible with this resumed run: {pair_path}")
            for profile in profile_names:
                trials_value = existing_pair["profiles"][profile].get("trials", [])
                if len(trials_value) != trials or any(row.get("model") != model for row in trials_value):
                    raise SweQaError(f"existing pair has incompatible trials: {pair_path}")
            pair_hashes.append({"task_id": task_id, "sha256": _sha256_file(pair_path)})
            continue
        profile_trials: dict[str, list[dict[str, Any]]] = {}
        for profile in profile_names:
            instruction_path = datasets_root / profile / task_id / "instruction.md"
            instruction = instruction_path.read_text(encoding="utf-8")
            pack_path = evidence_packs_root / task_id / f"{profile}.json"
            sidecar = _read_object(pack_path, f"{profile} evidence pack")
            evidence_items = sidecar["evidence_items"]
            navigation = sidecar["navigation_usage"]
            trials_out = []
            for trial_index in range(1, trials + 1):
                started = time.perf_counter()
                response = completion_fn(
                    model=model,
                    messages=[{"role": "user", "content": instruction}],
                )
                wall_seconds = time.perf_counter() - started
                answer = response["choices"][0]["message"]["content"]
                response_usage = response.get("usage", {})
                codex_meta = response.get("_codex_meta", {})
                input_tokens = response_usage.get("prompt_tokens")
                output_tokens = response_usage.get("completion_tokens")
                tool_calls = codex_meta.get("tool_calls", 0)
                validate_trial_navigation_usage(navigation, tool_calls)
                if (
                    not isinstance(answer, str)
                    or not answer.strip()
                    or isinstance(input_tokens, bool)
                    or not isinstance(input_tokens, int)
                    or input_tokens < 1
                    or isinstance(output_tokens, bool)
                    or not isinstance(output_tokens, int)
                    or output_tokens < 1
                ):
                    raise SweQaError(f"Codex returned invalid answer/usage for {task_id}/{profile}")
                trials_out.append(
                    {
                        "trial_index": trial_index,
                        "profile": profile,
                        "job_name": f"{run_id}-{profile}",
                        "trial_name": f"{task_id}-{profile}-{trial_index}",
                        "model": model,
                        "answer": answer,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "tool_calls": tool_calls,
                        "agent_wall_seconds": codex_meta.get("wall_seconds", wall_seconds),
                        "cost_usd": None,
                        "evidence_items": evidence_items,
                        "navigation_usage": navigation,
                        "retrieval_latency_seconds": sidecar["retrieval_latency_seconds"],
                        "peak_rss_kib": sidecar["peak_rss_kib"],
                        "evidence_sidecar_sha256": _sha256_file(pack_path),
                    }
                )
            profile_trials[profile] = trials_out

        pair = {
            "schema_version": 2,
            "task_id": task_id,
            "task_slug": task_id.replace(":", "-"),
            "valid": True,
            "expected_trials": trials,
            "actual_trials": trials,
            "agent": "codex-cli-host",
            "model": model,
            "common_instruction_sha256": dataset_manifest["common_instruction_sha256"][task_id],
            "profiles": {
                profile: {
                    "profile": profile,
                    "job_name": f"{run_id}-{profile}",
                    "trial_count": trials,
                    "trials": profile_trials[profile],
                }
                for profile in profile_names
            },
        }
        pair_path.parent.mkdir(parents=True, exist_ok=True)
        pair_path.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        pair_hashes.append({"task_id": task_id, "sha256": _sha256_file(pair_path)})

    codex_version = "injected completion"
    if codex_cli is not None:
        version_result = subprocess.run(
            [codex_cli, "--version"], capture_output=True, text=True, check=True
        )
        codex_version = version_result.stdout.strip()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "agent": "codex-cli-host",
        "model": model,
        "trials_per_profile": trials,
        "profiles": list(profile_names),
        "dataset_manifest_sha256": _sha256_file(dataset_manifest_path),
        "source_lock_sha256": _sha256_file(source_lock_path),
        "codex_version": codex_version,
        "pairs": pair_hashes,
    }
    manifest_path = output_root / "run-manifest.json"
    if resume and manifest_path.is_file():
        previous_manifest = _read_object(manifest_path, "existing RAG run manifest")
        if (
            previous_manifest.get("run_id") != run_id
            or previous_manifest.get("model") != model
            or previous_manifest.get("dataset_manifest_sha256") != manifest["dataset_manifest_sha256"]
        ):
            raise SweQaError("existing RAG run manifest is incompatible with resume")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, required=True)
    parser.add_argument("--evidence-packs", type=Path, required=True)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--profiles", nargs="+", default=list(PROFILE_NAMES))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_rag_codex(
            datasets_root=args.datasets,
            evidence_packs_root=args.evidence_packs,
            source_lock_path=args.source_lock,
            corpus_root=args.corpus,
            output_root=args.output,
            run_id=args.run_id,
            model=args.model,
            trials=args.trials,
            profile_names=tuple(args.profiles),
            resume=args.resume,
        )
    except (OSError, SweQaError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
