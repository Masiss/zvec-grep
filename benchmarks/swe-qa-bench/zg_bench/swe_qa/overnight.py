"""Durable request journal and budgets for long RAG evaluation runs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import SweQaError

ANSWER_CALL_LIMIT = 72
GRADER_CALL_LIMIT = 36
REVIEWER_CALL_LIMIT = 4
WALL_CLOCK_LIMIT_SECONDS = 8 * 60 * 60


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def overnight_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one checkpointed RAG overnight stage.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        help="defaults to research/rag-r4-answer-quality-20261123/overnight-<run-id>",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--role", choices=("answer", "grader", "reviewer"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.run_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in args.run_id):
        parser.error("run-id may contain only letters, digits, dots, underscores, and hyphens")
    root = args.output_root or Path("research/rag-r4-answer-quality-20261123") / f"overnight-{args.run_id}"
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if command and not (
        len(command) >= 4
        and Path(command[0]).name.startswith("python")
        and command[1:3] == ["-m", "zg_bench.swe_qa"]
    ):
        parser.error("stage command must use python -m zg_bench.swe_qa <command>")
    workspace = Path.cwd()
    required = [
        workspace / "research/public-source-evaluation-20261123/source.lock.json",
        workspace / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/rag-r4-v1/gold.json",
        workspace / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/rag-r4-v1/references.json",
        Path("/tmp/zvec-public-source-20261123"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        parser.error("overnight preflight missing required input(s): " + ", ".join(missing))
    python_ok = (3, 12) <= sys.version_info[:2] < (3, 14)
    codex_path = shutil.which("codex")
    codex_auth = Path.home() / ".codex" / "auth.json"
    prompts_path = workspace / "benchmarks/swe-qa-bench/zg_bench/swe_qa/data/rag-r4-v1/prompts.json"
    manifest_path = workspace / "research/rag-r4-answer-quality-20261123/development-answer-pairs-host-codex-v1/run-manifest.json"
    baseline_tasks = 0
    if manifest_path.is_file():
        baseline_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        baseline_tasks = len(baseline_manifest.get("pairs", []))
    output_parent = root if root.exists() else root.parent
    while not output_parent.exists() and output_parent != output_parent.parent:
        output_parent = output_parent.parent
    writable = os.access(output_parent, os.W_OK)
    preflight_failures = []
    if not python_ok:
        preflight_failures.append("requires Python 3.12 or 3.13")
    if codex_path is None or not codex_auth.is_file():
        preflight_failures.append("Codex CLI or OAuth login is unavailable")
    if not prompts_path.is_file() or baseline_tasks != 12:
        preflight_failures.append("frozen prompts or complete 12-task baseline are unavailable")
    if not writable:
        preflight_failures.append("output location is not writable")
    if not args.dry_run and not command:
        parser.error("provide a stage command after --, or use --dry-run")
    journal = root / "call-journal.json"
    remaining_budget: dict[str, int | float] = {
        "answer": ANSWER_CALL_LIMIT,
        "grader": GRADER_CALL_LIMIT,
        "reviewer": REVIEWER_CALL_LIMIT,
        "seconds": WALL_CLOCK_LIMIT_SECONDS,
    }
    if journal.is_file():
        recorded = json.loads(journal.read_text(encoding="utf-8"))
        latest = {row["request_id"]: row for row in recorded.get("calls", [])}
        used = {"answer": 0, "grader": 0, "reviewer": 0}
        for row in latest.values():
            used[row["role"]] += 1
        run_state_path = root / "run.json"
        deadline = (
            json.loads(run_state_path.read_text(encoding="utf-8"))["deadline_epoch"]
            if run_state_path.is_file()
            else recorded.get("deadline_epoch", time.time() + WALL_CLOCK_LIMIT_SECONDS)
        )
        remaining_budget = {
            "answer": ANSWER_CALL_LIMIT - used["answer"],
            "grader": GRADER_CALL_LIMIT - used["grader"],
            "reviewer": REVIEWER_CALL_LIMIT - used["reviewer"],
            "seconds": max(0.0, deadline - time.time()),
        }
    if args.resume and not journal.is_file():
        parser.error(f"cannot resume without call journal: {journal}")
    if not args.resume and (root.exists() and any(root.iterdir())):
        parser.error(f"run output exists, pass --resume or choose another run-id: {root}")
    if args.dry_run:
        print(json.dumps({
            "run_id": args.run_id,
            "output_root": str(root),
            "dry_run": True,
            "answer_calls": ANSWER_CALL_LIMIT,
            "grader_calls": GRADER_CALL_LIMIT,
            "reviewer_calls": REVIEWER_CALL_LIMIT,
            "wall_clock_seconds": WALL_CLOCK_LIMIT_SECONDS,
            "remaining_budget": remaining_budget,
            "journal_exists": journal.is_file(),
            "role_override": args.role,
            "python_ok": python_ok,
            "codex_cli": codex_path,
            "codex_authenticated": codex_auth.is_file(),
            "baseline_task_count": baseline_tasks,
            "writable_output_parent": writable,
            "preflight_failures": preflight_failures,
            "command": command,
        }, ensure_ascii=False))
        return 0

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".coordinator.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            parser.error(f"overnight coordinator is already running: {root}")
        if not journal.exists():
            _atomic_write(root / "run.json", {
                "schema_version": 1,
                "run_id": args.run_id,
                "started_at_epoch": time.time(),
                "deadline_epoch": time.time() + WALL_CLOCK_LIMIT_SECONDS,
            })
            with OvernightLedger(root):
                pass
        env = os.environ.copy()
        env["ZG_RAG_OVERNIGHT_JOURNAL"] = str(root)
        env["ZG_RAG_OVERNIGHT_RUN_ID"] = args.run_id
        if args.role:
            env["ZG_RAG_OVERNIGHT_ROLE"] = args.role
        run_state_path = root / "run.json"
        run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
        remaining_seconds = max(0, run_state["deadline_epoch"] - time.time())
        if remaining_seconds <= 0:
            parser.error("overnight wall-clock budget is exhausted")
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                check=False,
                timeout=remaining_seconds,
            )
        except subprocess.TimeoutExpired:
            return 124
        return completed.returncode


def review_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a checkpointed GPT-6.1-Sol overnight review.")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol", choices=("gpt-6.1-sol",))
    args = parser.parse_args(argv)
    try:
        prompt = args.prompt.read_text(encoding="utf-8")
        from zg_bench.engines.codex import codex_completion

        response = codex_completion(args.model)(messages=[{"role": "user", "content": prompt}])
        content = response["choices"][0]["message"]["content"]
        _atomic_write(args.output, {
            "schema_version": 1,
            "model": args.model,
            "prompt_sha256": _canonical_hash(prompt),
            "response_sha256": _canonical_hash(content),
            "response": content,
            "usage": response.get("usage", {}),
            "wall_seconds": response.get("_codex_meta", {}).get("wall_seconds"),
        })
    except (OSError, KeyError, TypeError, SweQaError) as error:
        parser.error(f"review failed: {error}")
    print(json.dumps({"output": str(args.output), "model": args.model}, ensure_ascii=False))
    return 0


class OvernightLedger:
    """Cache completed model responses and refuse retries after indeterminate calls."""

    def __init__(self, root: Path, *, resume: bool = False) -> None:
        self.root = root
        self.path = root / "call-journal.json"
        self.lock_path = root / ".run.lock"
        root.mkdir(parents=True, exist_ok=True)
        self._lock = self.lock_path.open("a+")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock.close()
            raise SweQaError(f"overnight run is already locked: {root}") from error
        if self.path.exists():
            if not resume:
                self.close()
                raise SweQaError(f"overnight journal exists, pass --resume: {self.path}")
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            if resume:
                self.close()
                raise SweQaError(f"cannot resume missing overnight journal: {self.path}")
            now = time.time()
            self.state = {
                "schema_version": 1,
                "started_at_epoch": now,
                "deadline_epoch": now + WALL_CLOCK_LIMIT_SECONDS,
                "calls": [],
            }
            self._save()
        self._refresh_indeterminate()

    def _save(self) -> None:
        _atomic_write(self.path, self.state)

    def _refresh_indeterminate(self) -> None:
        latest: dict[str, dict[str, Any]] = {}
        for row in self.state["calls"]:
            latest[row["request_id"]] = row
        for row in latest.values():
            if row["status"] == "started":
                row["status"] = "indeterminate"
                row["finished_at_epoch"] = time.time()
        self._save()

    def usage(self) -> dict[str, int]:
        counts = {"answer": 0, "grader": 0, "reviewer": 0}
        latest: dict[str, dict[str, Any]] = {}
        for row in self.state["calls"]:
            latest[row["request_id"]] = row
        for row in latest.values():
            counts[row["role"]] += 1
        return counts

    def limits_remaining(self) -> dict[str, int | float]:
        used = self.usage()
        return {
            "answer": ANSWER_CALL_LIMIT - used["answer"],
            "grader": GRADER_CALL_LIMIT - used["grader"],
            "reviewer": REVIEWER_CALL_LIMIT - used["reviewer"],
            "seconds": max(0.0, self.state["deadline_epoch"] - time.time()),
        }

    def call(
        self,
        *,
        role: str,
        model: str,
        messages: list[dict[str, Any]],
        invoke: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if role not in {"answer", "grader", "reviewer"}:
            raise SweQaError(f"unsupported overnight call role: {role}")
        request_id = _canonical_hash({"role": role, "model": model, "messages": messages})
        rows = [row for row in self.state["calls"] if row["request_id"] == request_id]
        if rows and rows[-1]["status"] == "completed":
            return rows[-1]["response"]
        if rows and rows[-1]["status"] == "indeterminate":
            raise SweQaError(f"refusing to retry indeterminate model call: {request_id}")
        remaining = self.limits_remaining()
        limit_key = role
        if remaining[limit_key] <= 0 or remaining["seconds"] <= 0:
            raise SweQaError(f"overnight {role} call or wall-clock budget is exhausted")
        row = {
            "request_id": request_id,
            "role": role,
            "model": model,
            "status": "started",
            "started_at_epoch": time.time(),
            "input_sha256": _canonical_hash(messages),
        }
        self.state["calls"].append(row)
        self._save()
        try:
            response = invoke()
        except BaseException as error:
            row["status"] = "indeterminate"
            row["finished_at_epoch"] = time.time()
            row["error_type"] = type(error).__name__
            self._save()
            raise
        row["status"] = "completed"
        row["finished_at_epoch"] = time.time()
        row["usage"] = response.get("usage", {})
        row["response_sha256"] = _canonical_hash(response)
        row["response"] = response
        self._save()
        return response

    def close(self) -> None:
        lock = getattr(self, "_lock", None)
        if lock is not None and not lock.closed:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def __enter__(self) -> "OvernightLedger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
