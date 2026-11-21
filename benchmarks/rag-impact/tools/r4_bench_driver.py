#!/usr/bin/env python3
"""r4_bench_driver.py — trusted controller for the R4 three-arm benchmark.

Isolation model
---------------
The answerer is a conversation backend whose only outputs are tool-call
requests or a final answer. This controller is the sole executor: every
request is validated against the frozen per-arm tool contract, executed
through the `r4-adapter` CLI (which owns the authoritative cumulative
ledger), and the bounded envelope is returned to the model as a
`function_call_output` item. The answerer never receives the corpus path,
session directory, gold, checklists, expected answers or handoffs.

Real inference
--------------
`ResponsesTransport` speaks the OpenAI Responses API. `HttpTransport` is the
real client (endpoint + credentials from the environment); it is only
constructible when the owner authorization flag is present. `MockTransport`
implements the same wire contract in-process: it validates the request it
receives (instructions, tools, question, and the previous tool output keyed
by call_id) and *derives* its next response from the actual received tool
output, so a fixed script cannot satisfy it. This is what makes the synthetic
end-to-end test exercise the real conversation path instead of a scripted
backend.

Usage (synthetic end-to-end; no private source leaves the machine):
    r4_bench_driver.py --adapter <bin> --session-dir <dir> --root <corpus> \
        --profile arm-c --case D02 --question-file q.json \
        --backend mock-transport [--plan-file plan.json] [--grade]

Usage (real; requires the owner's egress authorization):
    r4_bench_driver.py ... --backend openai-responses --authorized-inference \
        --model <fixed-id> --answer-instructions-file <contract.md>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r4_tool_schema import (  # noqa: E402
    ARM_TOOLS,
    normalize_answer,
    normalize_plan,
    plan_tool,
    plan_wire_form,
    strict_violations_for_plan,
    MAX_EVIDENCE_REQUESTS,
    MAX_QUESTION_CHARS,
    MAX_SEARCH_LIMIT,
    SchemaViolation,
    normalize_arguments,
    strict_violations_for_arm,
    tool_payload_schema,
    wire_arguments,
)

VERIFYING_PROFILES = ("arm-b", "arm-c")

VIOLATION_EXIT = 2
ERROR_EXIT = 3

ANSWER_INSTRUCTIONS = (
    "You investigate a codebase using the tools you are given, then submit a "
    "final answer by calling `submit_answer` exactly once.\n"
    "Rules for the submission:\n"
    "- `answer`: the human-readable answer.\n"
    "- `claims`: one entry per assertion; every claim needs at least one "
    "citation.\n"
    "- a citation is `{path, start_line, end_line, sha256}` copied from a tool "
    "result you actually received. Never invent a citation, never cite a range "
    "you were not shown, and never cite a path you did not receive.\n"
    "- `gaps`: the required facts you could not establish from the evidence you "
    "received. If a tool reported a gap or dropped content, say so here.\n"
    "- `abstain`: true only when you cannot answer at all; then `claims` must be "
    "empty.\n"
    "Do not describe runtime or deployment state that the source cannot show."
)

PLAN_INSTRUCTIONS = (
    "You produce investigation plans for a code search engine. Use only the "
    "question and the permitted folder hints. Never assume gold evidence, "
    "expected answers or checklists exist."
)

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_ROUNDS = 3
MAX_TURNS = 6
MAX_QUESTION_CHARS = 2000

# Tool schemas, arm membership and argument validation come from
# r4_tool_schema.py: one declarative spec generates the wire schema and the
# controller-side validator, so the two can never disagree.

class Violation(Exception):
    """The answerer attempted something outside the frozen contract."""


class ContractError(Exception):
    """Driver/adapter-level failure (not the answerer's fault)."""


# ------------------------------------------------------------- validation

def validate_call(profile: str, tool: str, args: dict) -> list[str]:
    """Validates one answerer tool-call request against the arm contract and
    returns the adapter CLI arguments. The round is controller-owned and never
    taken from the answerer."""
    if not isinstance(tool, str) or not TOOL_NAME_RE.match(tool):
        raise Violation(f"malformed tool name {tool!r}")
    if tool not in ARM_TOOLS[profile]:
        raise Violation(
            f"tool {tool!r} is not part of the {profile} contract; allowed: {sorted(ARM_TOOLS[profile])}"
        )
    try:
        normalized = normalize_arguments(tool, args)
    except SchemaViolation as error:
        raise Violation(str(error)) from error
    if tool == "search":
        cli = ["search", "--query", normalized["query"]]
        if "mode" in normalized:
            cli += ["--mode", normalized["mode"]]
        if "limit" in normalized:
            cli += ["--limit", str(normalized["limit"])]
        if "scope" in normalized:
            cli += ["--scope", normalized["scope"]]
        return cli
    if tool == "symbol":
        return ["symbol", "--name", normalized["name"]]
    if tool == "facts":
        cli = ["facts", "--path", normalized["path"]]
        if "method" in normalized:
            cli += ["--method", normalized["method"]]
        return cli
    if tool == "evidence":
        return ["evidence", "--requests", "-"]
    if tool == "verify":
        cli = ["verify"]
        if "pack_id" in normalized:
            cli += ["--pack-id", str(normalized["pack_id"])]
        return cli
    if tool == "workflow":
        return ["workflow", "--question", normalized["question"]]
    if tool == "status":
        return ["status"]
    raise Violation(f"unknown tool {tool!r}")


def normalized_arguments(tool: str, args: dict) -> dict:
    """Compact, validated arguments (nulls dropped) for execution."""
    try:
        return normalize_arguments(tool, args)
    except SchemaViolation as error:
        raise Violation(str(error)) from error


# ---------------------------------------------------------------- adapter

class AdapterController:
    """Sole executor. Owns the adapter CLI process and nothing else."""

    def __init__(self, adapter: Path, session_dir: Path, root: Path, profile: str,
                 case: str, plan_file: Path | None = None):
        self.adapter = adapter
        self.session_dir = session_dir
        self.root = root
        self.profile = profile
        self.case = case
        self.plan_file = plan_file

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        # The adapter must never inherit a shell-visible corpus hint beyond --root.
        return subprocess.run([str(self.adapter), *args], capture_output=True, env=env,
                              text=True, input=stdin, timeout=600)

    def create(self) -> None:
        proc = self._run(["session", "new", "--dir", str(self.session_dir),
                          "--root", str(self.root), "--profile", self.profile,
                          "--case", self.case])
        if proc.returncode != 0:
            raise ContractError(f"session new failed: {proc.stdout[:400]}{proc.stderr[:400]}")

    def call(self, tool: str, args: dict, round_no: int) -> dict:
        cli = validate_call(self.profile, tool, args)
        normalized = normalized_arguments(tool, args)
        if tool == "workflow" and self.plan_file is not None:
            cli += ["--plan-file", str(self.plan_file)]
        stdin = None
        if tool == "evidence":
            stdin = json.dumps(normalized["requests"], ensure_ascii=False)
        proc = self._run(["call", str(self.session_dir), *cli], stdin=stdin)
        if proc.returncode != 0 and not proc.stdout.strip():
            raise ContractError(f"adapter call failed: {proc.stderr[:400]}")
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            raise ContractError(f"adapter emitted non-JSON: {error}: {proc.stdout[:200]}")
        if proc.returncode != 0 and "error" in envelope:
            # A rejected session/plan/tool invocation happened *before*
            # admission: this is a controller-level failure, never an
            # answerer outcome, so it must surface loudly.
            raise ContractError(f"adapter rejected the call: {envelope['error']}")
        return envelope

    def finalize(self, cancelled: bool = False) -> dict:
        verb = "cancel" if cancelled else "finalize"
        proc = self._run(["session", verb, str(self.session_dir)])
        if proc.returncode != 0:
            raise ContractError(f"session {verb} failed: {proc.stdout[:300]}{proc.stderr[:300]}")
        receipt = self._run(["session", "receipt", str(self.session_dir)])
        if receipt.returncode != 0:
            raise ContractError(f"session receipt failed: {receipt.stderr[:300]}")
        return json.loads(receipt.stdout)


# ------------------------------------------------------------- transports

def _tool_payload_schema(profile: str) -> list[dict]:
    return tool_payload_schema(profile)


class ResponsesTransport:
    """Builds Responses-API payloads and parses Responses-API outputs.

    Subclasses implement `post(payload)`; everything else (request shape,
    conversation state, tool-call parsing) is shared with the real path.
    """

    def __init__(self, model: str, instructions: str, profile: str, question: str,
                 question_sha256: str | None = None,
                 question_source: str = "frozen-case",
                 question_formal: bool = True):
        self.model = model
        self.instructions = instructions
        self.profile = profile
        self.question = question
        self.question_sha256 = question_sha256 or hashlib.sha256(
            question.encode("utf-8")).hexdigest()
        self.question_source = question_source
        self.question_formal = question_formal
        self.usage_log: list[dict] = []
        self.identity: dict = {}
        self.requests: list[dict] = []

    # --- request construction
    def build_request(self, conversation: list[dict]) -> dict:
        return {
            "model": self.model,
            "instructions": self.instructions,
            "input": conversation,
            "tools": _tool_payload_schema(self.profile),
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

    def initial_conversation(self) -> list[dict]:
        return [{
            "role": "user",
            "content": [{"type": "input_text", "text": self.question}],
        }]

    # --- one turn
    def next_turn(self, conversation: list[dict]) -> dict:
        payload = self.build_request(conversation)
        self.requests.append(payload)
        started = time.perf_counter()
        response = self.post(payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        self.record_usage(response, latency_ms)
        return response

    def record_usage(self, response: dict, latency_ms: float) -> None:
        self.usage_log.append({
            "requested_model": self.model,
            "served_model": response.get("model", "unknown"),
            "system_fingerprint": response.get("system_fingerprint"),
            "input_tokens": (response.get("usage") or {}).get("input_tokens"),
            "output_tokens": (response.get("usage") or {}).get("output_tokens"),
            "latency_ms": round(latency_ms, 2),
        })

    def post(self, payload: dict) -> dict:
        raise NotImplementedError


class PlanTransport(ResponsesTransport):
    """Plan-generation transport: same wire contract, plan tool instead of the
    arm tools. The plan must be derived from the question and permitted folder
    hints only (no gold, no checklist, no prior answers)."""

    def __init__(self, model, instructions, question, hints, corpus_marker="",
                 question_sha256=None, question_source="frozen-case",
                 question_formal=True):
        super().__init__(model=model, instructions=instructions, profile="arm-b",
                         question=question, question_sha256=question_sha256,
                         question_source=question_source, question_formal=question_formal)
        self.hints = list(hints)
        self.corpus_marker = corpus_marker
        self.served = 0

    def build_request(self, conversation: list[dict]) -> dict:
        return {
            "model": self.model,
            "instructions": self.instructions,
            "input": conversation,
            "tools": [plan_tool()],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }

    def initial_conversation(self) -> list[dict]:
        hints = "\n".join(f"- {hint}" for hint in self.hints) or "- (none)"
        text = (
            "Question:\n" + self.question + "\n\n"
            "Permitted folder hints (paths only, no source content):\n" + hints + "\n\n"
            "Return one investigation plan with lexical queries, candidate symbol "
            "names and folder scopes. Use only the question and these hints."
        )
        return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


class MockPlanTransport(PlanTransport):
    """Plan generation over the same wire contract, derived from the question
    text and folder hints (no gold, no checklist)."""

    def post(self, payload: dict) -> dict:
        self._validate_request(payload)
        self.served += 1
        text = payload["input"][0]["content"][0]["text"]
        question = text.split("Question:\n", 1)[1].split("\n\n", 1)[0]
        hints_block = text.split("Permitted folder hints (paths only, no source content):\n", 1)[1]
        hints = [line[2:] for line in hints_block.splitlines()
                 if line.startswith("- ") and line != "- (none)"]
        plan = derive_plan(question, hints)
        return {
            "id": f"mock-plan-{self.served}",
            "object": "response",
            "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "function_call",
                "call_id": f"plan-{self.served}",
                "name": "emit_plan",
                "arguments": json.dumps(plan_wire_form(plan)),
            }],
            "usage": {"input_tokens": 120, "output_tokens": 60},
        }

    def _validate_request(self, payload: dict) -> None:
        validate_plan_request(payload, self)


def validate_plan_request(payload: dict, planner) -> None:
    """The plan request contract, checked by every planner (mock and real)."""
    if payload.get("instructions") != planner.instructions:
        raise ContractError("plan request must carry the planning instructions verbatim")
    if payload.get("tools") != [plan_tool()]:
        raise ContractError(
            "plan request must expose exactly the strict emit_plan tool, got "
            f"{[tool.get('name') for tool in payload.get('tools') or []]}"
        )
    if strict_violations_for_plan():
        raise ContractError("plan tool schema is not strict-compliant")
    if payload.get("tool_choice") != "auto" or payload.get("parallel_tool_calls") is not False:
        raise ContractError("plan request must allow one tool call at a time")
    text = payload["input"][0]["content"][0]["text"]
    if planner.corpus_marker and planner.corpus_marker in text:
        raise Violation("plan request leaked the corpus root path")
    if planner.question not in text:
        raise ContractError("plan request must carry the question verbatim")
    for hint in planner.hints:
        if hint not in text:
            raise ContractError(f"plan request dropped a permitted folder hint: {hint}")
    if not planner.hints and "Permitted folder hints" not in text:
        raise ContractError("plan request must declare the permitted-hint section")
    for forbidden in ("gold", "c3", "checklist", "expected answer", "rubric", "dev.json"):
        if forbidden in text.lower():
            raise Violation(f"plan prompt carries evaluator-only material: {forbidden}")


def derive_plan(question: str, hints: list[str]) -> dict:
    """Deterministic source-free plan derivation used by the mock model.

    It only sees the question text and folder-hint paths, exactly like a
    provider-backed plan generator would.
    """
    import re as _re
    identifiers = _re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", question)
    words = [w for w in _re.findall(r"[\w~/.-]+", question) if len(w) >= 4]
    queries = []
    if identifiers:
        queries.append(" ".join(identifiers[:6]))
    queries.append(question.strip())
    scopes = []
    for hint in hints:
        cleaned = hint.strip().strip("/")
        if cleaned and cleaned not in scopes:
            scopes.append(cleaned)
    return {
        "queries": queries[:4],
        "symbols": identifiers[:8],
        "scopes": scopes[:6],
        "terms": words[:12],
    }


class HttpPlanTransport(PlanTransport):
    """Real provider client for plan generation.

    It shares request construction, prompt, strict `emit_plan` schema and
    response parsing with the mock planner; only the transport differs. The
    request is validated against the same contract before it leaves the
    process, so a wrong tool set or evaluator material can never be sent.
    """

    def __init__(self, *args, endpoint: str, api_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        if not endpoint or not api_key:
            raise ContractError("real plan transport requires an endpoint and a key")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.identity = {"requested_model": self.model, "endpoint": self.endpoint}

    def post(self, payload: dict) -> dict:
        validate_plan_request(payload, self)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/responses", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ContractError(f"provider HTTP {error.code}: {error.read()[:400]!r}")
        except urllib.error.URLError as error:
            raise ContractError(f"provider unreachable: {error.reason}")


class HttpTransport(ResponsesTransport):
    """Real provider client. Constructible only under explicit authorization."""

    def __init__(self, *args, endpoint: str, api_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        if not endpoint or not api_key:
            raise ContractError("real transport requires R4_INFERENCE_ENDPOINT and an API key")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.identity = {"requested_model": self.model, "endpoint": self.endpoint}

    def post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/responses", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ContractError(f"provider HTTP {error.code}: {error.read()[:400]!r}")
        except urllib.error.URLError as error:
            raise ContractError(f"provider unreachable: {error.reason}")


class MockTransport(ResponsesTransport):
    """In-process Responses-API implementation for synthetic end-to-end runs.

    It validates every request exactly like the real endpoint contract and
    derives its answer from the tool output it actually received, so a fixed
    scripted sequence cannot pass the test.
    """

    def __init__(self, *args, corpus_marker: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.corpus_marker = corpus_marker
        self.calls_seen: list[dict] = []
        self.served = 0

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        conversation = payload["input"]
        last = conversation[-1]
        self.served += 1
        if last.get("role") == "user":
            if self.profile == "arm-b":
                return self._tool("workflow", {"question": self.question})
            return self._tool("search", {"query": self.question, "limit": 10})

        # Every later turn must carry a real tool output for the previous call.
        if last.get("type") != "function_call_output":
            raise ContractError("mock transport requires a function_call_output item")
        output = json.loads(last["output"])
        calls = [item for item in conversation if item.get("type") == "function_call"]
        call = calls[-1]
        self.calls_seen.append({"call_id": last["call_id"], "tool": call["name"],
                                "ok": output.get("ok"), "budget": output.get("budget")})
        if not output.get("ok"):
            code = (output.get("result") or {}).get("code")
            return self._final(f"ABSTAIN: tool failure {code}", abstain=True)

        result = output.get("result") or {}
        tool = call["name"]

        if tool == "workflow":
            selected = result.get("selected") or []
            verified = (result.get("stats") or {}).get("tool_calls")
            claims = []
            for span in selected:
                claims.append({
                    "text": f"selected span {span['path']} lines "
                            f"{span['start_line']}-{span['end_line']}",
                    "citations": [{
                        "path": span["path"], "start_line": span["start_line"],
                        "end_line": span["end_line"], "sha256": span.get("source_sha256"),
                    }],
                })
            gaps = [g.get("code") if isinstance(g, dict) else str(g)
                    for g in (result.get("gaps") or [])]
            return self._final(
                f"ARM-B: {len(selected)} selected spans, first="
                f"{selected[0]['path'] if selected else 'none'}, "
                f"internal_tool_calls={verified}",
                abstain=not selected, claims=claims, gaps=gaps)

        if tool == "search":
            items = result.get("items") or []
            if not items:
                return self._final(f"ARM-{self.profile[-1].upper()}: no items delivered", abstain=True)
            top = items[0]
            if self.served == 2:
                # Second request is derived from the delivered top item.
                if self.profile == "arm-c":
                    start, end = top["content_range"]
                    request = {
                        "project": "hrm",
                        "relative_path": top["path"],
                        "address": {"mode": "lines", "start_line": start, "end_line": end},
                    }
                    # Pin the hash the search delivered, so the evidence item is
                    # `verified` rather than merely `observed`: this is the
                    # protocol step that makes a citation verifiable, and doing
                    # it here keeps the synthetic run honest about capabilities.
                    if top.get("sha256"):
                        request["expected_sha256"] = top["sha256"]
                    return self._tool("evidence", {"requests": [request]})
                return self._tool("search", {
                    "query": top["path"].split("/")[-1], "mode": "fts", "limit": 5})
            claims = []
            for item in items:
                start, end = item["content_range"]
                claims.append({
                    "text": f"excerpt {item['path']} lines {start}-{end}",
                    "citations": [{
                        "path": item["path"], "start_line": start, "end_line": end,
                        "sha256": item.get("sha256"),
                    }],
                })
            return self._final(
                f"ARM-{self.profile[-1].upper()}: {len(items)} items; "
                f"top={top['path']}@{top['content_range']}; "
                f"declaration={(top.get('declaration') or {}).get('symbol_name')}",
                abstain=False, claims=claims)

        if tool == "evidence":
            items = result.get("items") or []
            status = result.get("status")
            claims = []
            for item in items:
                start, end = item["range"]
                claims.append({
                    "text": f"source span {item['path']} lines {start}-{end}",
                    "citations": [{
                        "path": item["path"], "start_line": start, "end_line": end,
                        "sha256": item.get("sha256"),
                    }],
                })
            gaps = [g.get("code") if isinstance(g, dict) else str(g)
                    for g in (result.get("gaps") or [])]
            return self._final(
                f"ARM-C: evidence status={status}, items={len(items)}, "
                f"first_sha={(items[0]['sha256'][:12] if items else 'none')}",
                abstain=not items, claims=claims, gaps=gaps)

        if tool == "symbol":
            items = result.get("items") or []
            if not items:
                return self._final("ARM-C: no declarations", abstain=True)
            return self._tool("facts", {"path": items[0]["path"]})

        if tool == "facts":
            facts = result.get("facts") or {}
            return self._final(f"ARM-C: facts keys={sorted(facts)[:4]}", abstain=not facts)

        return self._final("DONE", abstain=False)

    def _validate(self, payload: dict) -> None:
        for key in ("model", "instructions", "input", "tools"):
            if key not in payload:
                raise ContractError(f"mock transport: request missing {key!r}")
        raw = json.dumps(payload, ensure_ascii=False)
        if self.corpus_marker and self.corpus_marker in raw:
            raise Violation("answerer request leaked the corpus root path")
        names = {tool["name"] for tool in payload["tools"]}
        expected = set(ARM_TOOLS[self.profile]) | {"submit_answer"}
        if names != expected:
            raise ContractError(
                f"mock transport: tool schema mismatch, got {sorted(names)} wanted {sorted(expected)}"
            )
        for tool in payload["tools"]:
            if tool.get("type") != "function" or "parameters" not in tool:
                raise ContractError("mock transport: malformed tool schema")
        conversation = payload["input"]
        if not conversation or conversation[0].get("role") != "user":
            raise ContractError("mock transport: conversation must start with the user question")
        question_text = conversation[0]["content"][0]["text"]
        if question_text != self.question:
            raise ContractError("mock transport: question mismatch")
        if self.corpus_marker and self.corpus_marker in question_text:
            raise Violation("answerer question leaked the corpus root path")

    def _tool(self, name: str, args: dict) -> dict:
        # Emit the strict wire form: every declared property present with
        # optional ones null, exactly as a strict-mode model must.
        return {
            "id": f"mock-{self.served}",
            "object": "response",
            "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "function_call",
                "call_id": f"call-{self.served}",
                "name": name,
                "arguments": json.dumps(wire_arguments(name, args)),
            }],
            "usage": {"input_tokens": 100 * self.served, "output_tokens": 20},
        }

    def _final(self, text: str, abstain: bool, claims=None, gaps=None) -> dict:
        """Submits the final answer through the protocol's terminal tool.

        The mock emits exactly the same `submit_answer` function call a real
        model must emit, so the driver's answer validation is exercised for
        every synthetic run instead of being bypassed by prose.
        """
        claims = claims or []
        payload = {"answer": text, "abstain": abstain,
                   "claims": claims, "gaps": gaps or []}
        try:
            normalize_answer(payload)
        except Exception:
            # An abstention cannot carry claims; keep the submission legal and
            # let the driver record what the model actually produced.
            payload["claims"] = []
        return {
            "id": f"mock-{self.served}",
            "object": "response",
            "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "function_call",
                "call_id": f"answer-{self.served}",
                "name": "submit_answer",
                "arguments": json.dumps(payload),
            }],
            "usage": {"input_tokens": 100 * self.served, "output_tokens": 30},
        }


# ------------------------------------------------------------- conversation

def parse_output(response: dict) -> tuple[str, object]:
    """Returns ("tool", (name, args, call_id, raw_item)) or ("final", {...})."""
    outputs = response.get("output") or []
    for item in outputs:
        if item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError as error:
                # Unparseable arguments are an output-contract failure, not a
                # wrong answer: report INVALID_OUTPUT instead of guessing.
                return "invalid", {"error": f"unparseable tool arguments: {error}"}
            return "tool", (item.get("name"), args, item.get("call_id"), item)
        if item.get("type") == "message":
            # Free prose is not an answer under the frozen protocol: the answer
            # must arrive as a validated `submit_answer` call, otherwise the
            # case is INVALID_OUTPUT rather than an ungraded "wrong" answer.
            texts = [part.get("text", "") for part in item.get("content") or []
                     if part.get("type") == "output_text"]
            return "prose", {"text": "\n".join(texts).strip()}
    raise Violation("provider response had neither a tool call nor a final message")


def resolve_question(labels_dir: Path, case: str, question_file: Path | None,
                     allow_override: bool) -> dict:
    """Returns the exact question to send, plus its identity.

    The formal D01–D12 protocol is defined by the frozen case text. A different
    `--question-file` may only be used as an explicit non-formal diagnostic and
    never inherits the frozen case's identity.
    """
    cases = json.loads((labels_dir / "cases" / "dev.json").read_text(encoding="utf-8"))["cases"]
    entry = next((item for item in cases if item["id"] == case), None)
    if entry is None:
        raise ContractError(f"case {case} is not in the frozen dev set")
    frozen = entry["question"]
    frozen_sha = hashlib.sha256(frozen.encode("utf-8")).hexdigest()
    if question_file is None:
        return {"text": frozen, "sha256": frozen_sha, "source": "frozen-case",
                "formal": True, "frozen_sha256": frozen_sha}
    text = question_file.read_text(encoding="utf-8").strip()
    if not text:
        raise ContractError(f"question file {question_file} is empty")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest == frozen_sha:
        return {"text": text, "sha256": digest, "source": "frozen-case",
                "formal": True, "frozen_sha256": frozen_sha}
    if not allow_override:
        raise ContractError(
            f"question in {question_file} does not match the frozen {case} question "
            f"(file {digest[:12]} != frozen {frozen_sha[:12]}); a formal run cannot "
            "substitute the question. Pass --allow-question-override only for a "
            "clearly labelled non-formal diagnostic"
        )
    return {"text": text, "sha256": digest, "source": "override-diagnostic",
            "formal": False, "frozen_sha256": frozen_sha}


def generate_plan(answerer: ResponsesTransport, work_dir: Path, case: str,
                  question: str) -> dict:
    """Runs the plan conversation and freezes the result BEFORE any workflow
    execution. Returns a provenance record including the plan file path."""
    conversation = answerer.initial_conversation()
    response = answerer.next_turn(conversation)
    kind, payload = parse_output(response)
    if kind != "tool" or payload[0] != "emit_plan":
        raise ContractError(f"plan generation did not emit a plan: {payload!r}")
    _, raw_args, call_id, _item = payload
    try:
        plan = normalize_plan(raw_args)
    except Exception as error:  # SchemaViolation and friends
        raise ContractError(f"plan generation produced an invalid plan: {error}") from error
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = work_dir / f"plan-{case}-{digest[:12]}.json"
    plan_path.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    file_digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    return {
        "path": str(plan_path),
        "sha256_canonical": digest,
        "sha256_file": file_digest,
        "queries": plan["queries"],
        "symbols": plan["symbols"],
        "scopes": plan["scopes"],
        "call_id": call_id,
        "usage": answerer.usage_log[-1] if answerer.usage_log else None,
        "model_identity": answerer.identity,
    }


def drive(controller: AdapterController, answerer: ResponsesTransport, case: str,
          profile: str, output_dir: Path | None = None,
          verify_at_end: bool = True) -> dict:
    # Plan identity of the exact file handed to the adapter, computed before the
    # call so it cannot be substituted afterwards.
    plan_file = controller.plan_file
    plan_identity = None
    if plan_file is not None:
        plan_bytes = Path(plan_file).read_bytes()
        plan_identity = {"path": str(plan_file),
                         "sha256": hashlib.sha256(plan_bytes).hexdigest()}
    conversation = answerer.initial_conversation()
    transcript: list[dict] = []
    violations: list[str] = []
    round_no = 1
    turns = 0
    final = {"answer": "", "abstain": True}
    answer_status = "MISSING_FINAL_ANSWER"
    answer_error = "the session ended without a submit_answer call"
    while True:
        if turns >= MAX_TURNS:
            violations.append("turn budget exhausted before a final answer")
            break
        if round_no > MAX_ROUNDS:
            violations.append(f"round limit {MAX_ROUNDS} reached; further turns refused")
            break
        response = answerer.next_turn(conversation)
        turns += 1
        kind, payload = parse_output(response)
        if kind == "invalid":
            answer_status = "INVALID_OUTPUT"
            answer_error = payload["error"]
            transcript.append({"round": round_no, "tool": "invalid",
                               "contract_error": answer_error})
            final = {"answer": "", "claims": [], "gaps": [], "abstain": False}
            break
        if kind == "prose":
            answer_status = "INVALID_OUTPUT"
            answer_error = "the model returned prose instead of a submit_answer call"
            transcript.append({"round": round_no, "tool": "prose",
                               "contract_error": answer_error})
            final = {"answer": payload.get("text", ""), "claims": [], "gaps": [],
                     "abstain": False}
            break
        tool, args, call_id, raw_item = payload
        if tool == "submit_answer":
            conversation.append(raw_item)
            try:
                final = normalize_answer(args)
                answer_status = "OK"
                answer_error = None
            except Exception as error:  # SchemaViolation
                answer_status = "INVALID_OUTPUT"
                answer_error = str(error)
                final = {"answer": "", "claims": [], "gaps": [], "abstain": False}
            transcript.append({"round": round_no, "tool": "submit_answer",
                               "status": answer_status, "error": answer_error})
            break
        # Faithful Responses-API conversation: the assistant's function_call
        # item precedes its function_call_output.
        conversation.append(raw_item)
        try:
            envelope = controller.call(tool, args, round_no)
        except Violation as violation:
            violations.append(str(violation))
            transcript.append({"round": round_no, "tool": tool, "violation": str(violation)})
            conversation.append({"type": "function_call_output",
                                 "call_id": call_id or f"call-{turns}",
                                 "output": json.dumps({"ok": False,
                                                       "result": {"code": "violation"}})})
            round_no += 1
            continue
        except ContractError as error:
            transcript.append({"round": round_no, "tool": tool,
                               "contract_error": str(error)})
            raise
        transcript.append({
            "round": round_no,
            "tool": tool,
            "ok": envelope.get("ok"),
            "budget": envelope.get("budget"),
            "result": envelope.get("result"),
        })
        # Actual tool output goes back to the model verbatim, keyed by call_id.
        conversation.append({
            "type": "function_call_output",
            "call_id": call_id or f"call-{turns}",
            "output": json.dumps(envelope, ensure_ascii=False),
        })
        round_no += 1

    # Common final verification for every arm that produced a pack.
    final_verification = None
    if verify_at_end and profile not in VERIFYING_PROFILES:
        # Arm A exposes no verification tool. The policy is explicit rather
        # than silently "verified": its pack is frozen as unverified and the
        # receipt says so.
        final_verification = {
            "verified": None,
            "packs": 0,
            "policy": "no_verification_tool",
            "note": "arm-a cannot verify packs; delivered excerpts are frozen as unverified",
        }
    elif verify_at_end:
        try:
            status = controller.call("status", {}, round_no if round_no <= MAX_ROUNDS else MAX_ROUNDS)
            packs = (status.get("result") or {}).get("packs") or []
            if packs:
                envelope = controller.call("verify", {}, round_no if round_no <= MAX_ROUNDS else MAX_ROUNDS)
                transcript.append({"round": "final", "tool": "verify",
                                   "ok": envelope.get("ok")})
                final_verification = {
                    "verified": (envelope.get("result") or {}).get("verified"),
                    "packs": len(packs),
                    "policy": "verified_pack_required",
                }
            else:
                final_verification = {"verified": None, "packs": 0,
                                      "policy": "verified_pack_required",
                                      "reason": "no pack produced by this arm"}
        except (ContractError, Violation) as error:
            final_verification = {"verified": False, "error": str(error)}

    receipt = controller.finalize(cancelled=bool(violations))
    frozen = None
    pack_record = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Freeze the pack FIRST: the answer artifact names the exact pack it was
        # produced from, so the grader can prove the binding instead of trusting
        # whatever pack happens to sit next to the answer.
        packs_dir = controller.session_dir / "packs"
        session_packs = sorted(packs_dir.glob("pack-*.json")) if packs_dir.is_dir() else []
        if session_packs:
            if len(session_packs) > 1:
                raise ContractError(
                    f"session produced {len(session_packs)} packs; refusing to guess "
                    "which one the answer used"
                )
            pack_raw = session_packs[0].read_bytes()
            pack_name = f"pack-{case}-{profile}.json"
            (output_dir / pack_name).write_bytes(pack_raw)
            (output_dir / f"pack-{case}-{profile}.sha256").write_text(
                hashlib.sha256(pack_raw).hexdigest() + "  " + pack_name + "\n",
                encoding="utf-8")
            pack_kind = "workflow" if "selected" in json.loads(pack_raw) else "evidence"
            pack_record = {"sha256": hashlib.sha256(pack_raw).hexdigest(),
                           "kind": pack_kind,
                           "path": str(output_dir / pack_name)}
        else:
            derived = derive_pack_from_transcript(transcript)
            if derived is not None:
                # Arm A has no evidence/verify tool, so it cannot produce a
                # verified pack. Its delivered excerpts are frozen as an
                # explicitly unverified pack, so the same grader applies and the
                # missing verification shows up as a C2 penalty.
                pack_name = f"pack-{case}-{profile}.json"
                raw = json.dumps(derived, indent=1, ensure_ascii=False).encode("utf-8")
                (output_dir / pack_name).write_bytes(raw)
                (output_dir / f"pack-{case}-{profile}.sha256").write_text(
                    hashlib.sha256(raw).hexdigest() + "  " + pack_name + "\n",
                    encoding="utf-8")
                pack_record = {"sha256": hashlib.sha256(raw).hexdigest(),
                               "kind": "evidence", "path": str(output_dir / pack_name)}

        artifact = {
            "case": case,
            "profile": profile,
            "status": answer_status,
            "error": answer_error,
            "question": {"sha256": answerer.question_sha256,
                         "source": answerer.question_source,
                         "formal": answerer.question_formal},
            "pack": ({"sha256": pack_record["sha256"], "kind": pack_record["kind"]}
                     if pack_record else None),
            "final": final,
            "claims": final.get("claims") or [],
            "gaps": final.get("gaps") or [],
            "transcript_tools": [entry.get("tool") for entry in transcript],
        }
        blob = json.dumps(artifact, ensure_ascii=False, indent=1)
        answer_path = output_dir / f"answer-{case}-{profile}.json"
        answer_path.write_text(blob, encoding="utf-8")
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        answer_path.with_suffix(".sha256").write_text(
            digest + "  " + answer_path.name + "\n", encoding="utf-8")
        frozen = {"sha256": digest, "bytes": len(blob.encode("utf-8")),
                  "path": str(answer_path), "status": answer_status,
                  "pack_bound": pack_record is not None}
    # Plan chain: the pack records the digest of the plan bytes the engine used;
    # it must equal the file handed to the adapter and the generated provenance.
    plan_binding = None
    if plan_identity is not None:
        pack_plan = None
        pack_executed = None
        packs_dir = Path(controller.session_dir) / "packs"
        for path in sorted(packs_dir.glob("pack-*.json")) if packs_dir.is_dir() else []:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload.get("plan"), dict):
                pack_plan = payload["plan"].get("sha256")
                pack_executed = payload["plan"].get("executed_sha256")
        # The engine hashes the plan it executed; recompute the same canonical
        # form from the frozen file so a plan substituted after freezing is
        # visible instead of being blessed by the file digest alone.
        executed_expected = None
        try:
            frozen_plan = json.loads(Path(plan_identity["path"]).read_text(encoding="utf-8"))
            normalized = {key: frozen_plan.get(key, []) for key in
                          ("queries", "terms", "symbols", "scopes")}
            executed_expected = hashlib.sha256(
                json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")).hexdigest()
        except (OSError, json.JSONDecodeError):
            executed_expected = None
        plan_binding = {
            "frozen_plan_sha256": plan_identity["sha256"],
            "plan_file": plan_identity["path"],
            "pack_plan_sha256": pack_plan,
            "pack_executed_sha256": pack_executed,
            "executed_expected_sha256": executed_expected,
            "chain_ok": (pack_plan is not None and pack_plan == plan_identity["sha256"]
                         and pack_executed is not None
                         and pack_executed == executed_expected),
        }
        if not plan_binding["chain_ok"]:
            violations.append(
                "plan provenance mismatch: the workflow pack does not record the plan "
                "file that was handed to the adapter"
            )
    elif profile == "arm-b":
        # Primary arm B without a plan identity is an invalid control, not a
        # valid no-plan run: say so instead of leaving the field null.
        violations.append(
            "arm-b produced no plan identity: this run is not a valid primary control"
        )

    budget = receipt.get("budget", {})
    return {
        "case": case,
        "profile": profile,
        "plan_identity": plan_identity,
        "plan_binding": plan_binding,
        "transcript": transcript,
        "violations": violations,
        "final": final,
        "frozen_answer": frozen,
        "final_verification": final_verification,
        "rounds_used": min(max(round_no - 1, 0), MAX_ROUNDS),
        "usage": answerer.usage_log,
        "model_identity": answerer.identity,
        "receipt": receipt,
        "accounting": {
            "calls_used": budget.get("calls_used"),
            "internal_calls": budget.get("internal_calls"),
            "read_files": budget.get("read_files"),
            "read_ops": budget.get("read_ops"),
            "distinct_files": budget.get("distinct_files"),
            "delivered_bytes": budget.get("delivered_bytes"),
        },
    }


# ------------------------------------------------------------------- main

def check_corpus(labels_dir: Path, root: Path) -> dict:
    """Frozen corpus membership enforcement.

    The frozen manifest records the original source root, while benchmarks run
    on an isolated copy. Membership is therefore proven by content: every
    manifest entry must exist under the run root with the recorded sha256, and
    the run root must not expose files outside the frozen set.
    """
    manifest_path = labels_dir / "corpus-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["files"]
    missing, mismatched = [], []
    for entry in entries:
        path = root / entry["path"]
        if not path.is_file():
            missing.append(entry["path"])
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            mismatched.append(entry["path"])
    if missing or mismatched:
        raise ContractError(
            f"corpus membership failed: {len(missing)} missing, {len(mismatched)} "
            f"changed (first: {(missing + mismatched)[:3]})"
        )
    frozen = {entry["path"] for entry in entries}
    observed = set()
    for current, _dirs, names in os.walk(root):
        if ".zvec-grep" in current:
            continue
        for name in names:
            observed.add(str((Path(current) / name).relative_to(root)).replace(os.sep, "/"))
    extra = sorted(observed - frozen)
    if extra:
        raise ContractError(
            f"corpus contains {len(extra)} file(s) outside the frozen set: {extra[:3]}"
        )
    return {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "frozen_files": len(entries),
        "verified_files": len(entries),
        "extra_files": 0,
        "source_root_recorded": manifest.get("source_root"),
        "actual_root": str(root.resolve()),
    }


def check_case(labels_dir: Path, case: str) -> dict:
    cases = json.loads((labels_dir / "cases" / "dev.json").read_text(encoding="utf-8"))["cases"]
    by_id = {entry["id"]: entry for entry in cases}
    if case not in by_id:
        raise ContractError(
            f"case {case} is not part of the frozen dev set {sorted(by_id)}"
        )
    entry = by_id[case]
    return {"case": case, "question_sha256":
            hashlib.sha256(entry["question"].encode("utf-8")).hexdigest()}


def derive_pack_from_transcript(transcript: list[dict]) -> dict | None:
    """Builds a pack from delivered search excerpts for arms without a pack.

    The items keep their real path/range/hash and are marked unverified: the
    grader must see the missing verification, not a missing file.
    """
    items = []
    seen = set()
    for entry in transcript:
        for item in (entry.get("result") or {}).get("items") or []:
            path = item.get("path")
            rng = item.get("content_range")
            if not path or not rng:
                continue
            key = (path, tuple(rng))
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "project": item.get("project"),
                "relative_path": path,
                "sha256": item.get("sha256"),
                "verification": "unverified",
                "range": {"kind": "text", "start_line": rng[0], "end_line": rng[1],
                          "start_byte_offset": 0, "end_byte_offset": 0,
                          "start_byte_column": 0, "end_byte_column": 0},
                "content": item.get("content"),
            })
    if not items:
        return None
    return {
        "schema_version": 1,
        "status": "ok",
        "pack_kind": "search-derived-unverified",
        "note": "assembled from delivered search excerpts; this arm has no verification tool",
        "items": items,
        "gaps": [],
        "deduplicated": 0,
    }


def grade(frozen_dir: Path, labels_dir: Path, case: str, profile: str,
          corpus: Path | None = None, materials: Path | None = None,
          packs: Path | None = None, semantic: Path | None = None,
          registry: Path | None = None) -> dict:
    """Runs the independent grader in a separate process and returns its verdict.

    The grader receives the frozen artefacts, the evaluator-only materials and
    the corpus (for C3 anchoring). It never receives the answerer's session.
    """
    tool = Path(__file__).with_name("r4_grade.py")
    args = [sys.executable, str(tool), "--answers", str(frozen_dir),
            "--labels", str(labels_dir), "--case", case, "--profile", profile,
            "--materials", str(materials or labels_dir / "cases" / "c3-steps.json")]
    if corpus is not None:
        args += ["--corpus", str(corpus)]
    if packs is not None:
        args += ["--packs", str(packs)]
    if semantic is not None:
        args += ["--semantic", str(semantic)]
    if registry is not None:
        args += ["--registry", str(registry)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if proc.returncode == 3:
        # UNGRADABLE is a graded outcome, not a driver crash: surface it.
        return json.loads(proc.stdout)
    if proc.returncode != 0:
        raise ContractError(f"grader failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=list(ARM_TOOLS))
    parser.add_argument("--case", required=True)
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--labels-dir", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backend", default="mock-transport",
                        choices=["mock-transport", "openai-responses"])
    parser.add_argument("--plan-file", type=Path,
                        help="trusted blind plan for arm-b (fresh-blind or frozen reproduction)")
    parser.add_argument("--folder-hints", action="append",
                        help="permitted folder hint for plan generation (repeatable)")
    parser.add_argument("--allow-no-plan", action="store_true",
                        help="arm-b diagnostic: run question-only without a plan")
    parser.add_argument("--allow-question-override", action="store_true",
                        help="allow a question that differs from the frozen case text; "
                             "the run is then classified non-formal")
    parser.add_argument("--answer-instructions-file", type=Path)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--answers-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--grade", action="store_true")
    parser.add_argument("--materials", type=Path,
                        default=Path(__file__).resolve().parents[1] / "cases" / "c3-steps.json",
                        help="evaluator-only rubric + C3 steps (never sent to the answerer)")
    parser.add_argument("--semantic", type=Path,
                        help="independent semantic evaluation for this case (bound to the "
                             "frozen answer/pack/question/rubric identities)")
    parser.add_argument("--evaluator-registry", type=Path,
                        help="trusted evaluator registry; an unregistered evaluator cannot "
                             "produce a formal verdict")
    parser.add_argument("--authorized-inference", action="store_true",
                        help="explicit owner authorization flag required for the real backend")
    args = parser.parse_args()

    corpus = check_corpus(args.labels_dir, args.root)
    case_info = check_case(args.labels_dir, args.case)
    identity = resolve_question(args.labels_dir, args.case, args.question_file,
                                args.allow_question_override)
    question = identity["text"]
    plan_provenance = None
    plan_file = args.plan_file
    if args.profile == "arm-b" and plan_file is None:
        if args.allow_no_plan:
            print("WARNING: arm-b question-only plan requested via --allow-no-plan; this is a "
                  "diagnostic arm, not the plan-based control.", file=sys.stderr)
        else:
            # Primary arm-b fails closed without a fresh blind plan.
            if args.backend == "openai-responses":
                if not args.authorized_inference:
                    print("CONTRACT_ERROR: plan generation for arm-b needs the same "
                          "inference authorization as the answerer", file=sys.stderr)
                    return ERROR_EXIT
                hints = [line.strip() for line in (args.folder_hints or []) if line.strip()]
                planner = HttpPlanTransport(
                    model=args.model, instructions=PLAN_INSTRUCTIONS, question=question,
                    hints=hints, corpus_marker=str(args.root.resolve()),
                    question_sha256=identity["sha256"],
                    question_source=identity["source"],
                    question_formal=identity["formal"],
                    endpoint=os.environ.get("R4_INFERENCE_ENDPOINT", ""),
                    api_key=os.environ.get("R4_INFERENCE_TOKEN", ""),
                )
            else:
                hints = [line.strip() for line in (args.folder_hints or []) if line.strip()]
                planner = MockPlanTransport(
                    model=args.model, instructions=PLAN_INSTRUCTIONS, question=question,
                    hints=hints, corpus_marker=str(args.root.resolve()),
                    question_sha256=identity["sha256"],
                    question_source=identity["source"],
                    question_formal=identity["formal"],
                )
            plan_provenance = generate_plan(planner, args.session_dir.parent / "plans",
                                            args.case, question)
            plan_file = Path(plan_provenance["path"])
            plan_provenance["transport_requests"] = len(planner.requests)

    instructions = ANSWER_INSTRUCTIONS
    if args.answer_instructions_file:
        instructions = args.answer_instructions_file.read_text(encoding="utf-8")
    # The answer contract is only enforceable if the model was told about it.
    if "submit_answer" not in instructions:
        print("CONTRACT_ERROR: answer instructions must describe the submit_answer "
              "contract", file=sys.stderr)
        return ERROR_EXIT

    if args.backend == "openai-responses":
        if not args.authorized_inference:
            print("CONTRACT_ERROR: real inference requires --authorized-inference plus the "
                  "owner's private-source egress authorization", file=sys.stderr)
            return ERROR_EXIT
        answerer = HttpTransport(
            model=args.model, instructions=instructions, profile=args.profile,
            question=question, question_sha256=identity["sha256"],
            question_source=identity["source"], question_formal=identity["formal"],
            endpoint=os.environ.get("R4_INFERENCE_ENDPOINT", ""),
            api_key=os.environ.get("R4_INFERENCE_TOKEN", ""),
        )
    else:
        answerer = MockTransport(
            model=args.model, instructions=instructions, profile=args.profile,
            question=question, question_sha256=identity["sha256"],
            question_source=identity["source"], question_formal=identity["formal"],
            corpus_marker=str(args.root.resolve()),
        )

    controller = AdapterController(args.adapter, args.session_dir, args.root,
                                   args.profile, args.case, plan_file)
    controller.create()
    report = drive(controller, answerer, args.case, args.profile, args.answers_dir)
    report["plan_provenance"] = plan_provenance
    # The chain must close: generated plan file digest == what the driver handed
    # to the adapter == what the workflow pack records.
    chain = report.get("plan_binding")
    if plan_provenance is not None:
        generated = plan_provenance["sha256_file"]
        report["plan_chain"] = {
            "generated_sha256": generated,
            "handed_to_adapter_sha256": (report.get("plan_identity") or {}).get("sha256"),
            "pack_sha256": (chain or {}).get("pack_plan_sha256"),
            "pack_executed_sha256": (chain or {}).get("pack_executed_sha256"),
            "executed_expected_sha256": (chain or {}).get("executed_expected_sha256"),
            "ok": bool(
                (report.get("plan_identity") or {}).get("sha256") == generated
                and (chain or {}).get("pack_plan_sha256") == generated
                and (chain or {}).get("chain_ok")
            ),
        }
        if not report["plan_chain"]["ok"]:
            print("CONTRACT_ERROR: plan provenance chain did not close", file=sys.stderr)
            return ERROR_EXIT
    report["corpus"] = corpus
    report["case_identity"] = case_info
    report["question_identity"] = {
        "sha256": identity["sha256"],
        "source": identity["source"],
        "formal": identity["formal"],
        "frozen_sha256": identity["frozen_sha256"],
        "matches_frozen": identity["sha256"] == identity["frozen_sha256"],
    }
    report["transport_requests"] = len(answerer.requests)
    if args.grade and args.answers_dir is not None:
        report["grading"] = grade(
            args.answers_dir, args.labels_dir, args.case, args.profile,
            corpus=args.root, materials=args.materials,
            semantic=args.semantic, registry=args.evaluator_registry,
        )
    if args.output:
        args.output.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({
        "case": args.case,
        "profile": args.profile,
        "violations": report["violations"],
        "rounds_used": report["rounds_used"],
        "transport_requests": report["transport_requests"],
        "final_verification": report["final_verification"],
        "accounting": report["accounting"],
        "frozen_answer": report["frozen_answer"],
        "grading": report.get("grading"),
    }, indent=1))
    return VIOLATION_EXIT if report["violations"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Violation as error:
        print(f"VIOLATION: {error}", file=sys.stderr)
        sys.exit(VIOLATION_EXIT)
    except ContractError as error:
        print(f"CONTRACT_ERROR: {error}", file=sys.stderr)
        sys.exit(ERROR_EXIT)
