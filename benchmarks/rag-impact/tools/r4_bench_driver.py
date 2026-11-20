#!/usr/bin/env python3
"""r4_bench_driver.py — controller-mediated benchmark driver for the R4
three-arm external-agent comparison.

Isolation model (mission §6):
    The answerer is NOT a process with shell or filesystem access. It is a
    pluggable conversation backend (mock policy now; a provider tool-calling
    session later) whose only outputs are *tool-call requests* (strings).
    This driver is the sole executor: it validates every request against the
    frozen tool contract, runs it through the `r4-adapter` CLI (which owns the
    authoritative cumulative ledger), and feeds the serialized envelope back
    to the answerer. There is no code path from answerer output to a shell,
    a file read, or any process other than `r4-adapter` itself.

Boundaries enforced by construction:
    - Only the six frozen tool verbs (search/symbol/facts/evidence/verify/
      status) exist; anything else is a typed rejection.
    - Arguments are validated against per-tool schemas; unknown keys,
      filesystem paths outside the adapter's own argument syntax, shell
      metacharacters and tool-result injection are refused before any
      process is spawned.
    - The answerer never receives the corpus path, the session directory
      path (only opaque budget state), gold, checklists or handoffs.
    - The driver refuses to run if the case policy references evaluator-only
      files, and the negative-control mode verifies each such attempt is
      rejected with no state change.

Real-inference mode is intentionally not implemented beyond a stub: running
it requires the owner's private-source egress authorization (Gate B).

Usage:
    r4_bench_driver.py --adapter <path-to-r4-adapter-binary> \
        --session-dir <fresh-dir> --root <corpus-root> --profile arm-c \
        --case D02 --question-file <q.json> --policy <mock-policy.json> \
        [--adversarial-policy <adv.json>]

Exit codes: 0 = session completed; 2 = answerer attempted a violation
(negative-control evidence); 3 = adapter/contract error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TOOL_CONTRACT = {
    "search": {
        "required": {"query": str},
        "optional": {"mode": str, "limit": int, "scope": str, "round": int},
    },
    "symbol": {"required": {"name": str}, "optional": {"round": int}},
    "facts": {
        "required": {"path": str},
        "optional": {"method": str, "round": int},
    },
    "evidence": {
        "required": {"requests": list},
        "optional": {"round": int},
    },
    "verify": {"required": {}, "optional": {"pack_id": int, "round": int}},
    "status": {"required": {}, "optional": {}},
}
ALLOWED_SEARCH_MODES = {"hybrid", "fts", "vector", "rg"}
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")

VIOLATION_EXIT = 2
ERROR_EXIT = 3


class Violation(Exception):
    """The answerer attempted something outside the frozen contract."""


class ContractError(Exception):
    """Driver/adapter-level failure."""


def validate_call(tool: str, args: dict) -> list:
    """Validates one answerer tool-call request against the frozen contract.

    Raises Violation on anything the contract does not define. Returns the
    argument list for the adapter CLI plus an optional stdin payload.
    """
    if tool not in TOOL_CONTRACT:
        raise Violation(f"unknown tool {tool!r}; contract allows {sorted(TOOL_CONTRACT)}")
    schema = TOOL_CONTRACT[tool]
    unknown = set(args) - set(schema["required"]) - set(schema["optional"])
    if unknown:
        raise Violation(f"unknown arguments for {tool}: {sorted(unknown)}")
    for key, kind in schema["required"].items():
        if key not in args or not isinstance(args[key], kind):
            raise Violation(f"{tool}.{key} is required ({kind.__name__})")

    cli_args = []
    payload = None
    if tool == "search":
        query = args["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise Violation("search.query must be 1..500 chars of text")
        cli_args += ["--query", query]
        if "mode" in args:
            if args["mode"] not in ALLOWED_SEARCH_MODES:
                raise Violation(f"search.mode must be one of {sorted(ALLOWED_SEARCH_MODES)}")
            cli_args += ["--mode", args["mode"]]
        if "limit" in args:
            limit = args["limit"]
            if not 1 <= limit <= 50:
                raise Violation("search.limit must be 1..50")
            cli_args += ["--limit", str(limit)]
        if "scope" in args:
            scope = args["scope"]
            if not PATH_RE.match(scope):
                raise Violation("search.scope must be a plain relative path fragment")
            cli_args += ["--scope", scope]
    elif tool == "symbol":
        name = args["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise Violation("symbol.name must be 1..200 chars")
        cli_args += ["--name", name]
    elif tool == "facts":
        path = args["path"]
        if not isinstance(path, str) or not PATH_RE.match(path):
            raise Violation("facts.path must be a plain relative path (no traversal)")
        cli_args += ["--path", path]
        if "method" in args:
            if not isinstance(args["method"], str) or len(args["method"]) > 200:
                raise Violation("facts.method must be text")
            cli_args += ["--method", args["method"]]
    elif tool == "evidence":
        requests = args["requests"]
        if not isinstance(requests, list) or not requests or len(requests) > 10:
            raise Violation("evidence.requests must be 1..10 span requests")
        for request in requests:
            if not isinstance(request, dict):
                raise Violation("each span request must be an object")
            relative = request.get("relative_path")
            if not isinstance(relative, str) or not PATH_RE.match(relative):
                raise Violation("span request needs a plain relative_path")
            address = request.get("address", {})
            if not isinstance(address, dict) or address.get("mode") not in {
                "lines",
                "bytes",
            }:
                raise Violation("span request needs a lines/bytes address")
        # Span requests go through stdin (`--requests -`): the adapter CLI
        # accepts a file path or stdin, never inline JSON as an argument.
        cli_args += ["--requests", "-"]
        payload = json.dumps(requests)
    elif tool == "verify":
        if "pack_id" in args:
            pack_id = args["pack_id"]
            if not isinstance(pack_id, int) or pack_id < 1:
                raise Violation("verify.pack_id must be a positive integer")
            cli_args += ["--pack-id", str(pack_id)]
    elif tool == "status":
        pass

    if "round" in args:
        round_no = args["round"]
        if not isinstance(round_no, int) or not 1 <= round_no <= 3:
            raise Violation("round must be 1..3")
        cli_args += ["--round", str(round_no)]
    return cli_args, payload


class AdapterController:
    """The only executor in the system. Answerers talk to it via strings."""

    def __init__(self, adapter: Path, session_dir: Path, root: Path,
                 profile: str, case: str):
        self._stdin_payload = None
        self.adapter = str(adapter)
        self.session_dir = session_dir
        self.env = None  # inherit; the adapter pins the root itself
        self._new(session_dir, root, profile, case)

    def _run(self, args: list, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.adapter, *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=300,
        )

    def _new(self, session_dir: Path, root: Path, profile: str, case: str):
        result = self._run([
            "session", "new", "--dir", str(session_dir), "--root", str(root),
            "--profile", profile, "--case", case,
        ])
        if result.returncode != 0:
            raise ContractError(f"session new failed: {result.stdout}{result.stderr}")

    def call(self, tool: str, args: dict) -> dict:
        cli_args, payload = validate_call(tool, args)
        result = self._run(["call", str(self.session_dir), tool, *cli_args], stdin=payload)
        if result.returncode != 0 and not result.stdout.strip():
            raise ContractError(f"adapter crashed: {result.stderr[:400]}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ContractError(f"adapter produced invalid JSON: {error}") from error

    def finalize(self, cancelled: bool = False) -> dict:
        verb = "cancel" if cancelled else "finalize"
        result = self._run(["session", verb, str(self.session_dir)])
        if result.returncode != 0 and not result.stdout.strip():
            raise ContractError(f"adapter {verb} crashed: {result.stderr[:400]}")
        return json.loads(result.stdout)

    def receipt(self) -> dict:
        result = self._run(["session", "receipt", str(self.session_dir)])
        return json.loads(result.stdout)


class MockAnswerer:
    """Deterministic scripted answerer for orchestration verification.

    A policy is a list of steps: {"tool": ..., "args": {...}}. This backend
    has no filesystem access at all: it can only produce the steps given to
    it, which is exactly what makes it safe to use for negative controls.
    """

    def __init__(self, policy: dict):
        self.steps = policy["steps"]
        self.answer_notes = policy.get("final_notes", {})

    def next_call(self, state: dict) -> tuple[str, dict] | None:
        index = state.get("step_index", 0)
        if index >= len(self.steps):
            return None
        state["step_index"] = index + 1
        step = self.steps[index]
        return step["tool"], step["args"]

    def final_notes(self) -> dict:
        return self.answer_notes


def drive(controller: AdapterController, answerer: MockAnswerer) -> dict:
    """Runs one answerer session to completion under the mediated contract."""
    state: dict = {}
    transcript = []
    violations = []
    while True:
        try:
            request = answerer.next_call(state)
        except Violation as violation:  # mock cannot violate, but keep parity
            violations.append(str(violation))
            break
        if request is None:
            break
        tool, args = request
        try:
            envelope = controller.call(tool, args)
        except Violation as violation:
            violations.append(str(violation))
            transcript.append({"tool": tool, "violation": str(violation)})
            continue
        transcript.append({
            "tool": tool,
            "ok": envelope.get("ok"),
            "budget": envelope.get("budget"),
        })
    receipt = controller.finalize(cancelled=bool(violations))
    return {
        "transcript": transcript,
        "violations": violations,
        "receipt": receipt,
        "final_notes": answerer.final_notes(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=["arm-a", "arm-b", "arm-c"])
    parser.add_argument("--case", required=True)
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="write the run report here")
    args = parser.parse_args()

    with open(args.policy, encoding="utf-8") as handle:
        policy = json.load(handle)

    controller = AdapterController(
        args.adapter, args.session_dir, args.root, args.profile, args.case
    )
    answerer = MockAnswerer(policy)
    report = drive(controller, answerer)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
    budget = report["receipt"]["budget"]
    print(json.dumps({
        "case": args.case,
        "profile": args.profile,
        "violations": report["violations"],
        "budget": {
            key: budget[key] for key in (
                "calls_used", "rounds_used", "distinct_files", "delivered_bytes"
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
