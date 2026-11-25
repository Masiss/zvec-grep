"""Codex CLI completion adapter for isolated RAG grading."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from zg_bench.core.errors import SweQaError
from zg_bench.swe_qa.overnight import OvernightLedger

Completion = Callable[..., Any]


def codex_completion(model: str) -> Completion:
    """Return a LiteLLM-shaped completion function backed by Codex OAuth."""
    executable = shutil.which("codex")
    if executable is None:
        raise SweQaError("Codex CLI is required for the selected RAG judge backend")

    def invoke(**kwargs: Any) -> dict[str, Any]:
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            raise SweQaError("Codex judge request must include messages")
        prompt_parts = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise SweQaError("Codex judge request contains an invalid message")
            role = message.get("role", "user")
            prompt_parts.append(f"{str(role).upper()}:\n{message['content']}")
        prompt = "\n\n".join(prompt_parts)

        try:
            with tempfile.TemporaryDirectory(prefix="rag-r4-codex-judge-") as temp:
                working_root = Path(temp)
                output_path = working_root / "final-message.txt"
                command = [
                    executable,
                    "exec",
                    "--json",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--model",
                    model,
                    "--sandbox",
                    "read-only",
                    "--cd",
                    str(working_root),
                    "--output-last-message",
                    str(output_path),
                    "-",
                ]
                started = time.perf_counter()
                result = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=360,
                    cwd=working_root,
                )
                wall_seconds = time.perf_counter() - started
                if result.returncode != 0:
                    raise SweQaError(
                        f"Codex judge exited with status {result.returncode}"
                    )
                content = output_path.read_text(encoding="utf-8").strip()
                usage, tool_calls = _parse_events(result.stdout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SweQaError(
                f"Codex judge invocation failed: {type(error).__name__}"
            ) from error
        if not content:
            raise SweQaError("Codex judge returned an empty final message")
        return {
            "choices": [{"message": {"content": content}}],
            "usage": usage,
            "_hidden_params": {"response_cost": None},
            "_codex_meta": {
                "tool_calls": tool_calls,
                "wall_seconds": wall_seconds,
            },
        }

    def complete(**kwargs: Any) -> dict[str, Any]:
        journal_root = os.environ.get("ZG_RAG_OVERNIGHT_JOURNAL")
        if not journal_root:
            return invoke(**kwargs)
        messages = kwargs.get("messages")
        if not isinstance(messages, list):
            raise SweQaError("overnight model call has invalid messages")
        role = os.environ.get("ZG_RAG_OVERNIGHT_ROLE")
        if role is None:
            role = "answer" if model == "gpt-6-luna" else "grader"
        journal_path = Path(journal_root) / "call-journal.json"
        with OvernightLedger(Path(journal_root), resume=journal_path.exists()) as ledger:
            return ledger.call(
                role=role,
                model=model,
                messages=messages,
                invoke=lambda: invoke(**kwargs),
            )

    return complete


def _parse_events(stdout: str) -> tuple[dict[str, int | None], int]:
    final_usage: dict[str, Any] | None = None
    tool_call_ids: set[str] = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            value = event.get("usage")
            if isinstance(value, dict):
                final_usage = value
        if isinstance(event, dict) and event.get("type") == "item.started":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") in {
                "command_execution",
                "mcp_tool_call",
                "tool_call",
                "web_search_call",
                "file_change",
            }:
                item_id = item.get("id")
                tool_call_ids.add(
                    str(item_id) if item_id is not None else f"anonymous-{len(tool_call_ids)}"
                )

    def number(name: str) -> int | None:
        if final_usage is None:
            return None
        value = final_usage.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return (
        {
            "prompt_tokens": number("input_tokens"),
            "completion_tokens": number("output_tokens"),
        },
        len(tool_call_ids),
    )
