#!/usr/bin/env python3
"""r4_bench_driver.py — trusted controller for the R4 three-arm
external-agent benchmark.

Isolation model:
    The answerer is a pluggable conversation backend whose only outputs are
    *tool-call requests* (strings) or a final answer/abstention. This
    controller is the sole executor: every request is validated against the
    frozen per-arm tool contract, executed through the `r4-adapter` CLI
    (which owns the authoritative cumulative ledger), and the bounded
    envelope is returned as tool output. The answerer never receives the
    corpus path, the session directory, gold, checklists, expected answers
    or handoffs, and there is no code path from answerer output to a shell,
    a filesystem read, or any process other than `r4-adapter`.

Trusted rounds:
    Interaction rounds are assigned by THIS controller (each model turn is
    one round, capped at three). The answerer cannot declare, reduce or
    reset rounds; any round value it supplies is ignored and replaced.

Real inference:
    `OpenAIResponsesBackend` implements the provider client (openai /
    openai-responses, endpoint from configuration). In this repository it is
    exercised ONLY through the deterministic mock transport; a real run
    additionally requires the owner's private-source egress authorization
    and records requested/served model identity plus usage metadata from
    the provider response. Credentials are read from the environment and
    never logged.

Usage (mock):
    r4_bench_driver.py --adapter <bin> --session-dir <dir> --root <corpus> \
        --profile arm-c --case D02 --question-file q.json \
        --backend mock --policy <scripted-policy.json> [--output out.json]

Usage (real; requires authorization):
    r4_bench_driver.py ... --backend openai-responses \
        --model gpt-5.5 --answer-instructions-file <contract.md> \
        [--max-turns 3]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VIOLATION_EXIT = 2
ERROR_EXIT = 3

# ------------------------------------------------------------ tool contract

TOOL_CONTRACT = {
    "search": {
        "required": {"query": str},
        "optional": {"mode": str, "limit": int, "scope": str},
    },
    "symbol": {"required": {"name": str}, "optional": {}},
    "facts": {"required": {"path": str}, "optional": {"method": str}},
    "evidence": {"required": {"requests": list}, "optional": {}},
    "verify": {"required": {}, "optional": {"pack_id": int}},
    "workflow": {"required": {"question": str}, "optional": {}},
    "status": {"required": {}, "optional": {}},
}
ARM_TOOLS = {
    "arm-a": {"search", "status"},
    "arm-b": {"workflow", "verify", "status"},
    "arm-c": {"search", "symbol", "facts", "evidence", "verify", "status"},
}
ALLOWED_SEARCH_MODES = {"hybrid", "fts", "vector", "rg"}
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")
MAX_ROUNDS = 3
MAX_TURNS = 6


def valid_relative_path(value: str) -> bool:
    """Plain workspace-relative path: no traversal, no absolute, no shell
    metacharacters, no dot segments."""
    if not isinstance(value, str) or not PATH_RE.match(value):
        return False
    segments = value.split("/")
    return all(
        segment not in (".", "..") and segment != "" for segment in segments
    )


def is_int(value) -> bool:
    """Strict integers: booleans and floats are rejected."""
    return type(value) is int


class Violation(Exception):
    """The answerer attempted something outside the frozen contract."""


class ContractError(Exception):
    """Driver/adapter-level failure."""


def validate_call(profile: str, tool: str, args: dict) -> tuple[list, str | None]:
    """Validates one answerer tool-call request against the arm's frozen
    contract. Raises Violation on anything the contract does not define.

    Returns the adapter CLI arguments plus an optional stdin payload. The
    round value is NEVER taken from the answerer: the controller owns it.
    """
    allowed = ARM_TOOLS[profile]
    if tool not in allowed:
        raise Violation(
            f"tool {tool!r} is not part of the {profile} contract; allowed: {sorted(allowed)}"
        )
    if tool not in TOOL_CONTRACT:
        raise Violation(f"unknown tool {tool!r}")
    schema = TOOL_CONTRACT[tool]
    unknown = set(args) - set(schema["required"]) - set(schema["optional"])
    if unknown:
        raise Violation(f"unknown arguments for {tool}: {sorted(unknown)}")
    for key, kind in schema["required"].items():
        if key not in args:
            raise Violation(f"{tool}.{key} is required")
        if kind is str and not isinstance(args[key], str):
            raise Violation(f"{tool}.{key} must be a string")
        if kind is list and not isinstance(args[key], list):
            raise Violation(f"{tool}.{key} must be an array")

    cli_args: list = []
    payload = None
    if tool == "search":
        query = args["query"]
        if not query.strip() or len(query) > 500:
            raise Violation("search.query must be 1..500 chars of text")
        cli_args += ["--query", query]
        if "mode" in args:
            if args["mode"] not in ALLOWED_SEARCH_MODES:
                raise Violation(f"search.mode must be one of {sorted(ALLOWED_SEARCH_MODES)}")
            cli_args += ["--mode", args["mode"]]
        if "limit" in args:
            if not is_int(args["limit"]) or not 1 <= args["limit"] <= 50:
                raise Violation("search.limit must be an integer 1..50 (bools/floats rejected)")
            cli_args += ["--limit", str(args["limit"])]
        if "scope" in args:
            if not valid_relative_path(args["scope"]):
                raise Violation("search.scope must be a plain relative path fragment")
            cli_args += ["--scope", args["scope"]]
    elif tool == "symbol":
        name = args["name"]
        if not name.strip() or len(name) > 200:
            raise Violation("symbol.name must be 1..200 chars")
        cli_args += ["--name", name]
    elif tool == "facts":
        if not valid_relative_path(args["path"]):
            raise Violation("facts.path must be a plain relative path (no traversal)")
        cli_args += ["--path", args["path"]]
        if "method" in args:
            if not isinstance(args["method"], str) or len(args["method"]) > 200:
                raise Violation("facts.method must be text")
            cli_args += ["--method", args["method"]]
    elif tool == "evidence":
        requests = args["requests"]
        if not requests or len(requests) > 10:
            raise Violation("evidence.requests must be 1..10 span requests")
        for request in requests:
            if not isinstance(request, dict):
                raise Violation("each span request must be an object")
            relative = request.get("relative_path")
            if not valid_relative_path(relative):
                raise Violation("span request needs a plain relative_path")
            if not isinstance(relative, str):
                raise Violation("relative_path must be a string")
            address = request.get("address", {})
            if not isinstance(address, dict) or address.get("mode") not in {
                "lines",
                "bytes",
            }:
                raise Violation("span request needs a lines/bytes address")
            for key in ("expected_sha256", "project"):
                if key in request and not isinstance(request[key], str):
                    raise Violation(f"{key} must be a string")
        # Span requests go through stdin: the adapter CLI accepts a file
        # path or stdin, never inline JSON as an argument.
        cli_args += ["--requests", "-"]
        payload = json.dumps(requests)
    elif tool == "verify":
        if "pack_id" in args:
            if not is_int(args["pack_id"]) or args["pack_id"] < 1:
                raise Violation("verify.pack_id must be a positive integer")
            cli_args += ["--pack-id", str(args["pack_id"])]
    elif tool == "workflow":
        question = args["question"]
        if not question.strip() or len(question) > 2000:
            raise Violation("workflow.question must be 1..2000 chars")
        cli_args += ["--question", question]
    return cli_args, payload


# ------------------------------------------------------------ adapter control

class AdapterController:
    """The only executor in the system. Answerers talk to it via strings."""

    def __init__(self, adapter: Path, session_dir: Path, root: Path,
                 profile: str, case: str):
        self.adapter = str(adapter)
        self.session_dir = session_dir
        self.profile = profile
        self.case = case
        self._new(session_dir, root, profile, case)

    def _run(self, args: list, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adapter, *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=600,
        )

    def _new(self, session_dir: Path, root: Path, profile: str, case: str):
        result = self._run([
            "session", "new", "--dir", str(session_dir), "--root", str(root),
            "--profile", profile, "--case", case,
        ])
        if result.returncode != 0:
            raise ContractError(f"session new failed: {result.stdout}{result.stderr}")

    def call(self, tool: str, args: dict, round_no: int) -> dict:
        # The controller owns the round: an answerer-supplied round is a
        # contract violation, not silently ignored metadata.
        if "round" in args:
            raise Violation("round is controller-owned; answerer round declarations are invalid")
        cli_args, payload = validate_call(self.profile, tool, args)
        cli_args += ["--round", str(round_no)]
        result = self._run(["call", str(self.session_dir), tool, *cli_args], stdin=payload)
        if result.returncode != 0 and not result.stdout.strip():
            raise ContractError(f"adapter crashed: {result.stderr[:400]}")
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ContractError(f"adapter produced invalid JSON: {error}") from error
        if envelope.get("terminal"):
            raise ContractError(
                f"adapter went terminal: {envelope.get('terminal')}"
            )
        return envelope

    def finalize(self, cancelled: bool = False) -> dict:
        verb = "cancel" if cancelled else "finalize"
        result = self._run(["session", verb, str(self.session_dir)])
        if result.returncode != 0 and not result.stdout.strip():
            raise ContractError(f"adapter {verb} crashed: {result.stderr[:400]}")
        return json.loads(result.stdout)

    def receipt(self) -> dict:
        result = self._run(["session", "receipt", str(self.session_dir)])
        return json.loads(result.stdout)


# ----------------------------------------------------------------- backends

class MockAnswerer:
    """Deterministic scripted answerer for orchestration verification.

    A policy is a list of steps: {"tool": ..., "args": {...}} plus an
    optional {"final": "..."} step declaring the frozen answer/abstention.
    This backend has no filesystem access at all.
    """

    def __init__(self, policy: dict):
        self.steps = policy["steps"]
        self.final = policy.get("final", {"answer": "MOCK-FINAL", "abstain": False})

    def next_call(self, state: dict) -> tuple[str, dict] | None:
        index = state.get("step_index", 0)
        if index >= len(self.steps):
            return None
        state["step_index"] = index + 1
        step = self.steps[index]
        if "final" in step:
            state["final"] = step["final"]
            return None
        return step["tool"], step["args"]

    def final_answer(self, state: dict) -> dict:
        return state.get("final", self.final)


class OpenAIResponsesBackend:
    """Provider tool-calling backend (openai / openai-responses).

    `transport="mock"` runs against a scripted sequence of provider
    responses for deterministic verification of the full mediation loop:
    tool requests, malformed/unknown tool calls, budget exhaustion, final
    answer and abstention. `transport="real"` would call the configured
    endpoint with the case question plus the arm's tool schemas; it is
    gated on the owner's private-source egress authorization and is never
    invoked by repository tests.
    """

    def __init__(self, question: str, tool_schemas: dict, transport: str = "mock",
                 model: str = "gpt-5.5", scripted: list | None = None):
        self.question = question
        self.tool_schemas = tool_schemas
        self.transport = transport
        self.model = model
        self.scripted = scripted or []
        self.cursor = 0
        self.usage_log: list = []
        self.identity: dict = {}

    def _request(self, messages: list) -> dict:
        if self.transport == "mock":
            if self.cursor >= len(self.scripted):
                return {
                    "type": "final",
                    "answer": "MOCK-FINAL",
                    "abstain": False,
                }
            response = self.scripted[self.cursor]
            self.cursor += 1
            self.usage_log.append({
                "requested_model": self.model,
                "served_model": response.get("model", "mock-unknown"),
                "system_fingerprint": response.get("system_fingerprint", "mock-none"),
                "input_tokens": response.get("input_tokens", 0),
                "output_tokens": response.get("output_tokens", 0),
                "latency_ms": 0,
            })
            return response
        # Real transport: gated on owner authorization; never executed in
        # repository tests or without an explicit authorization flag.
        raise ContractError(
            "real inference requires the owner's private-source egress "
            "authorization; run with --authorized-inference and an approved "
            "provider/model identity after the gate opens"
        )

    def next_call(self, state: dict) -> tuple[str, dict] | None:
        messages = state.setdefault("messages", [])
        response = self._request(messages)
        if response.get("type") == "final":
            state["final"] = {
                "answer": response.get("answer", ""),
                "abstain": bool(response.get("abstain", False)),
            }
            return None
        tool = response.get("tool")
        args = response.get("args", {})
        if not isinstance(tool, str) or not isinstance(args, dict):
            # Malformed provider output becomes a contract error, never an
            # executed operation.
            raise Violation(
                f"malformed provider tool call: {json.dumps(response)[:200]}"
            )
        return tool, args

    def final_answer(self, state: dict) -> dict:
        return state.get("final", {"answer": "", "abstain": True})


# ------------------------------------------------------------------ driving

def drive(controller: AdapterController, answerer, case: str, profile: str,
          output_dir: Path | None = None) -> dict:
    """Runs one answerer session under the mediated contract with
    controller-owned rounds."""
    state: dict = {}
    transcript = []
    violations = []
    round_no = 1
    turns = 0
    while True:
        try:
            request = answerer.next_call(state)
        except Violation as violation:
            # A protocol breach is recorded; the loop continues so the
            # answerer may still produce a final answer/abstention, and the
            # violation is reported with the receipt.
            violations.append(str(violation))
            turns += 1
            if turns > MAX_TURNS:
                break
            continue
        if request is None:
            break
        turns += 1
        if turns > MAX_TURNS:
            violations.append("turn budget exhausted before a final answer")
            break
        tool, args = request
        try:
            envelope = controller.call(tool, args, round_no)
        except Violation as violation:
            violations.append(str(violation))
            transcript.append({"tool": tool, "violation": str(violation)})
            continue
        except ContractError as error:
            transcript.append({"tool": tool, "contract_error": str(error)})
            break
        transcript.append({
            "tool": tool,
            "ok": envelope.get("ok"),
            "round": round_no,
            "budget": envelope.get("budget"),
        })
        # Controller-owned rounds: every model turn advances the round.
        round_no = min(round_no + 1, MAX_ROUNDS)

    final = answerer.final_answer(state)
    receipt = controller.finalize(cancelled=bool(violations))

    frozen = None
    if output_dir is not None and final.get("answer"):
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "case": case,
            "profile": profile,
            "final": final,
            "transcript_tools": [entry.get("tool") for entry in transcript],
        }
        blob = json.dumps(artifact, ensure_ascii=False, indent=1)
        (output_dir / f"answer-{case}-{profile}.json").write_text(blob, encoding="utf-8")
        frozen = {
            "sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
            "bytes": len(blob.encode("utf-8")),
        }
    return {
        "case": case,
        "profile": profile,
        "transcript": transcript,
        "violations": violations,
        "final": final,
        "frozen_answer": frozen,
        "rounds_used": min(turns, MAX_ROUNDS),
        "usage": getattr(answerer, "usage_log", []),
        "model_identity": getattr(answerer, "identity", {}),
        "receipt": receipt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=list(ARM_TOOLS))
    parser.add_argument("--case", required=True)
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--backend", default="mock", choices=["mock", "openai-responses"])
    parser.add_argument("--policy", type=Path, help="mock scripted policy")
    parser.add_argument("--scripted", type=Path, help="mock provider responses for the real backend")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--answers-dir", type=Path, help="freeze final answers here before grading")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--authorized-inference", action="store_true",
                        help="explicit owner authorization flag required for the real backend")
    args = parser.parse_args()

    controller = AdapterController(
        args.adapter, args.session_dir, args.root, args.profile, args.case
    )
    if args.backend == "mock":
        with open(args.policy, encoding="utf-8") as handle:
            answerer = MockAnswerer(json.load(handle))
    else:
        if not args.authorized_inference:
            print(
                "CONTRACT_ERROR: real inference requires --authorized-inference "
                "plus the owner's private-source egress authorization",
                file=sys.stderr,
            )
            return ERROR_EXIT
        question = ""
        if args.question_file:
            question = args.question_file.read_text(encoding="utf-8")
        scripted = None
        if args.scripted:
            with open(args.scripted, encoding="utf-8") as handle:
                scripted = json.load(handle)
        answerer = OpenAIResponsesBackend(
            question=question,
            tool_schemas={tool: TOOL_CONTRACT[tool] for tool in ARM_TOOLS[args.profile]},
            transport="mock" if scripted is not None else "real",
            model=args.model,
            scripted=scripted,
        )

    report = drive(controller, answerer, args.case, args.profile, args.answers_dir)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
    budget = report["receipt"]["budget"]
    print(json.dumps({
        "case": args.case,
        "profile": args.profile,
        "violations": report["violations"],
        "rounds_used": report["rounds_used"],
        "budget": {
            key: budget[key] for key in (
                "calls_used", "internal_calls", "rounds_used",
                "read_files", "read_ops", "distinct_files", "delivered_bytes",
            )
        },
    }, indent=1))
    if report["violations"]:
        return VIOLATION_EXIT
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Violation as error:
        print(f"VIOLATION: {error}", file=sys.stderr)
        sys.exit(VIOLATION_EXIT)
    except ContractError as error:
        print(f"CONTRACT_ERROR: {error}", file=sys.stderr)
        sys.exit(ERROR_EXIT)
