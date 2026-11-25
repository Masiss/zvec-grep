"""Prepare matched RAG evidence datasets and run them through Harbor."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from zg_bench.runner import (
    BenchmarkSuite,
    build_harbor_command,
    execute,
    execution_environment,
    prepare_setup_cache,
    uses_setup_cache,
)
from zg_bench.doctor import collect_checks, print_report
from zg_bench.settings import ZVEC_GREP_PACKAGE
from zg_bench.swe_qa.collect import collect_pair
from zg_bench.swe_qa.rag_evidence import validate_navigation_usage, validate_source_pack

from . import SweQaError

PROFILE_NAMES = ("lexical-rg", "r4-hybrid", "sufficient-pack")
RAG_CANDIDATE_PROFILE = "candidate"
_ALLOWED_PROFILE_SETS = (
    PROFILE_NAMES,
    (*PROFILE_NAMES, RAG_CANDIDATE_PROFILE),
)
_GOLD_FIELDS = {
    "accepted_source_ranges",
    "gold",
    "reference_answer",
    "reference_answers",
    "required_steps",
}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SweQaError(f"could not read {label}: {path}") from error
    if not isinstance(value, dict):
        raise SweQaError(f"{label} must be a JSON object: {path}")
    return value


def _contains_gold_fields(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_GOLD_FIELDS.intersection(value)) or any(
            _contains_gold_fields(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_gold_fields(child) for child in value)
    return False


def _fence(content: str) -> str:
    runs = [len(match.group(0)) for match in re.finditer(r"`+", content)]
    return "`" * max(3, max(runs, default=0) + 1)


def _instruction(question: str, answer_instructions: str, evidence: list[dict[str, Any]]) -> str:
    sections = [
        "# Repository behavior question",
        question.strip(),
        "# Answer instructions",
        answer_instructions.strip(),
        "Use only the evidence included below. Do not run tools or search other files. "
        "If the evidence does not support an answer, say so and abstain. Do not mention "
        "which retrieval method supplied the excerpts.",
        "# Available source evidence",
    ]
    if not evidence:
        sections.append("No source excerpts were retrieved for this question.")
    for item in evidence:
        fence = _fence(item["content"])
        sections.extend(
            [
                f"## {item['path']}:{item['start_line']}-{item['end_line']}",
                f"{fence}csharp",
                item["content"].rstrip("\n"),
                fence,
            ]
        )
    return "\n\n".join(sections) + "\n"


def _task_toml(prompt: dict[str, Any]) -> str:
    task_id = json.dumps(prompt["task_id"])
    repository = json.dumps(prompt["repository"])
    commit = json.dumps(prompt["repository_commit"])
    question_hash = json.dumps(prompt["question_sha256"])
    return f'''version = "1.0"

[metadata]
benchmark = "rag-r4-answer-quality"
benchmark_revision = "rag-r4-v1"
task_id = {task_id}
repository = {repository}
repository_commit = {commit}
question_hash = {question_hash}

[verifier]
timeout_sec = 60.0

[agent]
timeout_sec = 1800.0

[environment]
build_timeout_sec = 300.0
'''


def prepare_rag_datasets(
    *,
    prompts_path: Path,
    evidence_packs_root: Path,
    source_lock_path: Path,
    corpus_root: Path,
    output_root: Path,
    split: str = "development",
    profile_names: tuple[str, ...] = PROFILE_NAMES,
) -> dict[str, Any]:
    """Create three matched, gold-blind local Harbor datasets from frozen packs."""
    if profile_names not in _ALLOWED_PROFILE_SETS:
        raise SweQaError("RAG datasets require the three frozen profiles, optionally plus candidate")
    prompts = _load_object(prompts_path, "RAG prompts")
    source_lock = _load_object(source_lock_path, "source lock")
    if _contains_gold_fields(prompts):
        raise SweQaError("answer prompt asset contains judge-only gold fields")
    answer_instructions = prompts.get("answer_instructions")
    rows = prompts.get("tasks")
    if not isinstance(answer_instructions, str) or not answer_instructions.strip():
        raise SweQaError("RAG prompts have no common answer instructions")
    if not isinstance(rows, list):
        raise SweQaError("RAG prompts.tasks must be an array")
    tasks = [
        row for row in rows
        if isinstance(row, dict) and row.get("split") == split
    ]
    if not tasks:
        raise SweQaError(f"no RAG tasks found for split {split!r}")
    if output_root.exists() and any(output_root.iterdir()):
        raise SweQaError(f"RAG dataset output must be absent or empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    inputs: dict[str, dict[str, str]] = {}
    dataset_file_hashes: dict[str, dict[str, dict[str, str]]] = {}
    common_instruction_hashes: dict[str, str] = {}
    for profile in profile_names:
        profile_root = output_root / "datasets" / profile
        profile_root.mkdir(parents=True, exist_ok=True)
        inputs[profile] = {}
        dataset_file_hashes[profile] = {}
        for prompt in tasks:
            task_id = prompt.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise SweQaError("RAG prompt has invalid task_id")
            sidecar_path = evidence_packs_root / task_id / f"{profile}.json"
            sidecar = _load_object(sidecar_path, f"{profile} evidence pack")
            if sidecar.get("schema_version") != 1:
                raise SweQaError(f"{profile} evidence pack schema_version must be 1")
            evidence = sidecar.get("evidence_items")
            validate_navigation_usage(sidecar.get("navigation_usage"))
            validate_source_pack(evidence, source_root=corpus_root, source_lock=source_lock)
            task_dir = profile_root / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            instruction = _instruction(
                prompt["question"], answer_instructions, evidence
            )
            common_instruction = instruction.split(
                "# Available source evidence", maxsplit=1
            )[0]
            common_hash = hashlib.sha256(common_instruction.encode("utf-8")).hexdigest()
            expected_common = common_instruction_hashes.setdefault(task_id, common_hash)
            if common_hash != expected_common:
                raise SweQaError(
                    f"common task prompt differs across evidence profiles for {task_id}"
                )
            (task_dir / "instruction.md").write_text(instruction, encoding="utf-8")
            (task_dir / "task.toml").write_text(
                _task_toml(prompt), encoding="utf-8"
            )
            (task_dir / "environment").mkdir()
            (task_dir / "environment" / "Dockerfile").write_text(
                "FROM ubuntu:24.04\nWORKDIR /workspace\n", encoding="utf-8"
            )
            (task_dir / "tests").mkdir()
            test_script = task_dir / "tests" / "test.sh"
            test_script.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "mkdir -p /logs/verifier\n"
                "printf '1.0\\n' > /logs/verifier/reward.txt\n",
                encoding="utf-8",
            )
            test_script.chmod(0o755)
            inputs[profile][task_id] = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
            dataset_file_hashes[profile][task_id] = {
                path.relative_to(task_dir).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(task_dir.rglob("*"))
                if path.is_file()
            }

    manifest = {
        "schema_version": 1,
        "split": split,
        "task_count": len(tasks),
        "task_ids": [row["task_id"] for row in tasks],
        "common_instruction_sha256": common_instruction_hashes,
        "prompt_sha256": hashlib.sha256(prompts_path.read_bytes()).hexdigest(),
        "source_lock_sha256": hashlib.sha256(source_lock_path.read_bytes()).hexdigest(),
        "evidence_sidecars_sha256": inputs,
        "dataset_files_sha256": dataset_file_hashes,
        "answer_asset_isolation": "prompts-only; no gold or references copied",
    }
    (output_root / "dataset-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_rag_harbor(
    *,
    datasets_root: Path,
    evidence_packs_root: Path,
    corpus_root: Path,
    source_lock_path: Path,
    jobs_dir: Path,
    pairs_dir: Path,
    run_id: str,
    agent: str,
    model: str,
    trials: int = 1,
    harbor_executable: str = "harbor",
    profile_names: tuple[str, ...] = PROFILE_NAMES,
) -> dict[str, Any]:
    """Run identical baseline agent settings for each evidence-only dataset."""
    if profile_names not in _ALLOWED_PROFILE_SETS:
        raise SweQaError("RAG run requires the three frozen profiles, optionally plus candidate")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise SweQaError("run_id may contain only letters, digits, dots, underscores, and hyphens")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise SweQaError("trials must be a positive integer")
    task_ids = sorted(
        path.name
        for path in (datasets_root / profile_names[0]).iterdir()
        if path.is_dir()
    )
    if not task_ids:
        raise SweQaError("prepared RAG datasets contain no tasks")
    if any(
        sorted(path.name for path in (datasets_root / profile).iterdir() if path.is_dir()) != task_ids
        for profile in profile_names[1:]
    ):
        raise SweQaError("RAG profile datasets do not contain the same task IDs")
    dataset_manifest_path = datasets_root.parent / "dataset-manifest.json"
    dataset_manifest = _load_object(dataset_manifest_path, "RAG dataset manifest")
    if dataset_manifest.get("schema_version") != 1:
        raise SweQaError("RAG dataset manifest schema_version must be 1")
    if set(dataset_manifest.get("task_ids", [])) != set(task_ids):
        raise SweQaError("RAG dataset manifest task IDs do not match prepared datasets")
    expected_dataset_files = set()
    expected_file_hashes = dataset_manifest.get("dataset_files_sha256")
    if not isinstance(expected_file_hashes, dict):
        raise SweQaError("RAG dataset manifest has no dataset file hashes")
    for condition in profile_names:
        profile_hashes = expected_file_hashes.get(condition)
        if not isinstance(profile_hashes, dict):
            raise SweQaError(f"RAG dataset manifest has no hashes for {condition}")
        for task_id in task_ids:
            task_hashes = profile_hashes.get(task_id)
            if not isinstance(task_hashes, dict):
                raise SweQaError(f"RAG dataset manifest has no file hashes for {condition}/{task_id}")
            task_dir = datasets_root / condition / task_id
            actual_relative = {
                path.relative_to(task_dir).as_posix()
                for path in task_dir.rglob("*")
                if path.is_file()
            }
            if actual_relative != set(task_hashes):
                raise SweQaError(f"RAG dataset files differ from manifest for {condition}/{task_id}")
            for relative, expected_hash in task_hashes.items():
                file_path = task_dir / relative
                if hashlib.sha256(file_path.read_bytes()).hexdigest() != expected_hash:
                    raise SweQaError(f"RAG dataset file hash mismatch: {condition}/{task_id}/{relative}")
                expected_dataset_files.add(f"{condition}/{task_id}/{relative}")
    actual_dataset_files = {
        path.relative_to(datasets_root).as_posix()
        for path in datasets_root.rglob("*")
        if path.is_file()
    }
    if actual_dataset_files != expected_dataset_files:
        raise SweQaError("RAG dataset tree contains files absent from its manifest")
    planned_jobs = [jobs_dir / f"{run_id}-{condition}" for condition in profile_names]
    collisions = [path for path in planned_jobs if path.exists()]
    if collisions:
        raise SweQaError("RAG job output already exists: " + ", ".join(map(str, collisions)))
    pair_collisions = [
        pairs_dir / task_id / "pair.json"
        for task_id in task_ids
        if (pairs_dir / task_id / "pair.json").exists()
    ]
    if pair_collisions:
        raise SweQaError("RAG pair output already exists: " + ", ".join(map(str, pair_collisions)))
    source_lock = _load_object(source_lock_path, "source lock")
    sidecar_hashes = dataset_manifest.get("evidence_sidecars_sha256")
    if not isinstance(sidecar_hashes, dict):
        raise SweQaError("RAG dataset manifest has no evidence sidecar hashes")
    for task_id in task_ids:
        for condition in profile_names:
            sidecar_path = evidence_packs_root / task_id / f"{condition}.json"
            expected_hash = sidecar_hashes.get(condition, {}).get(task_id)
            if not isinstance(expected_hash, str) or hashlib.sha256(
                sidecar_path.read_bytes()
            ).hexdigest() != expected_hash:
                raise SweQaError(
                    f"evidence sidecar differs from prepared dataset: {condition}/{task_id}"
                )
            sidecar = _load_object(sidecar_path, f"{condition} evidence pack")
            if sidecar.get("schema_version") != 1:
                raise SweQaError(f"{condition} evidence pack schema_version must be 1")
            validate_navigation_usage(sidecar.get("navigation_usage"))
            validate_source_pack(
                sidecar.get("evidence_items"),
                source_root=corpus_root,
                source_lock=source_lock,
            )
    print("RAG Harbor preflight:", flush=True)
    if print_report(
        collect_checks(
            agent=agent,
            model=model,
            profiles=("baseline",),
            harbor_executable=harbor_executable,
        )
    ) != 0:
        raise SweQaError("Harbor preflight failed; no RAG jobs were started")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    pairs_dir.mkdir(parents=True, exist_ok=True)
    prepared_cache = (
        prepare_setup_cache(agent, "baseline") if uses_setup_cache(agent) else None
    )
    launched = []
    for condition in profile_names:
        job_name = f"{run_id}-{condition}"
        if (jobs_dir / job_name).exists():
            raise SweQaError(f"RAG job already exists: {jobs_dir / job_name}")
        suite = BenchmarkSuite(
            name=f"rag-r4-{condition}",
            dataset=None,
            tier="full",
            tasks=tuple(task_ids),
            path=(datasets_root / condition).resolve(),
        )
        command = build_harbor_command(
            suite,
            profile="baseline",
            agent=agent,
            model=model,
            jobs_dir=jobs_dir,
            job_name=job_name,
            n_attempts=trials,
            max_retries=0,
            zvec_grep_package=(
                prepared_cache.zvec_grep_package
                if prepared_cache is not None
                and prepared_cache.zvec_grep_package is not None
                else ZVEC_GREP_PACKAGE
            ),
            zvec_grep_package_sha256=(
                prepared_cache.zvec_grep_package_sha256
                if prepared_cache is not None
                else None
            ),
            harbor_executable=harbor_executable,
        )
        runtime_environment = execution_environment(agent=agent, model=model)
        if agent == "codex":
            codex_auth = Path.home() / ".codex" / "auth.json"
            if not codex_auth.is_file():
                raise SweQaError("Codex auth file is missing; log in with `codex login` first")
            runtime_environment["CODEX_AUTH_JSON_PATH"] = str(codex_auth)
        return_code = execute(
            command,
            jobs_dir=jobs_dir,
            environment=runtime_environment,
        )
        launched.append({"condition": condition, "job_name": job_name, "return_code": return_code})
        if return_code != 0:
            raise SweQaError(f"Harbor failed for {condition} with exit code {return_code}")

    collected = []
    for task_id in task_ids:
        pair = collect_pair(
            runs_dir=jobs_dir,
            task=task_id,
            output=pairs_dir / task_id / "pair.json",
            expected_trials=trials,
            profile_names=profile_names,
            evidence_packs_root=evidence_packs_root,
        )
        collected.append({"task_id": task_id, "pair_sha256": hashlib.sha256(
            (pairs_dir / task_id / "pair.json").read_bytes()
        ).hexdigest(), "valid": pair["valid"]})
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "trials_per_profile": trials,
        "profiles": list(profile_names),
        "profile_agent_mode": "same baseline agent configuration for all evidence conditions",
        "jobs": launched,
        "pairs": collected,
    }
    (pairs_dir / "run-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="build three gold-blind Harbor datasets")
    prepare.add_argument("--prompts", type=Path, required=True)
    prepare.add_argument("--evidence-packs", type=Path, required=True)
    prepare.add_argument("--source-lock", type=Path, required=True)
    prepare.add_argument("--corpus", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--split", choices=("development", "validation"), default="development")
    prepare.add_argument("--profiles", nargs="+", default=list(PROFILE_NAMES))

    run = commands.add_parser("run", help="run the three Harbor evidence conditions")
    run.add_argument("--datasets", type=Path, required=True)
    run.add_argument("--evidence-packs", type=Path, required=True)
    run.add_argument("--corpus", type=Path, required=True)
    run.add_argument("--source-lock", type=Path, required=True)
    run.add_argument("--jobs", type=Path, required=True)
    run.add_argument("--pairs", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--agent", choices=("opencode", "codex", "claude-code"), default="opencode")
    run.add_argument("--model", required=True)
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--harbor", default="harbor")
    run.add_argument("--profiles", nargs="+", default=list(PROFILE_NAMES))

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_rag_datasets(
                prompts_path=args.prompts,
                evidence_packs_root=args.evidence_packs,
                source_lock_path=args.source_lock,
                corpus_root=args.corpus,
                output_root=args.output,
                split=args.split,
                profile_names=tuple(args.profiles),
            )
        else:
            result = run_rag_harbor(
                datasets_root=args.datasets,
                evidence_packs_root=args.evidence_packs,
                corpus_root=args.corpus,
                source_lock_path=args.source_lock,
                jobs_dir=args.jobs,
                pairs_dir=args.pairs,
                run_id=args.run_id,
                agent=args.agent,
                model=args.model,
                trials=args.trials,
                harbor_executable=args.harbor,
                profile_names=tuple(args.profiles),
            )
    except (FileNotFoundError, RuntimeError, SweQaError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
