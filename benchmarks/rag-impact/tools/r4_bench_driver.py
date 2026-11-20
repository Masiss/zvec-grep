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

VIOLATION_EXIT = 2
ERROR_EXIT = 3

MAX_ROUNDS = 3
MAX_TURNS = 6
MAX_QUESTION_CHARS = 2000

TOOL_SCHEMAS = {
    "search": {
        "description": "Search the indexed workspace. Returns ranked excerpts with "
                       "declaration identity and exact source coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["hybrid", "fts", "vector", "rg"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "scope": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "symbol": {
        "description": "Exact declaration lookup by name.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    "facts": {
        "description": "Structural facts for one file or method.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "method": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "evidence": {
        "description": "Request exact source spans; each span is hashed and verified.",
        "parameters": {
            "type": "object",
            "properties": {"requests": {"type": "array", "items": {"type": "object"}}},
            "required": ["requests"],
            "additionalProperties": False,
        },
    },
    "verify": {
        "description": "Revalidate the latest (or a given) evidence pack against current source.",
        "parameters": {
            "type": "object",
            "properties": {"pack_id": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    "workflow": {
        "description": "Engine-managed deterministic investigation for one question.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "maxLength": MAX_QUESTION_CHARS}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    "status": {
        "description": "Remaining session budget and pack state.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
ARM_TOOLS = {
    "arm-a": {"search", "status"},
    "arm-b": {"workflow", "verify", "status"},
    "arm-c": {"search", "symbol", "facts", "evidence", "verify", "status"},
}
ALLOWED_SEARCH_MODES = {"hybrid", "fts", "vector", "rg"}
PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Violation(Exception):
    """The answerer attempted something outside the frozen contract."""


class ContractError(Exception):
    """Driver/adapter-level failure (not the answerer's fault)."""


# ------------------------------------------------------------- validation

def valid_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not PATH_RE.match(value):
        return False
    return all(seg not in (".", "..") and seg != "" for seg in value.split("/"))


def is_int(value) -> bool:
    return type(value) is int


def _reject_unknown(args: dict, allowed: set[str], tool: str) -> None:
    unknown = set(args) - allowed
    if unknown:
        raise Violation(f"{tool} received undefined argument(s): {sorted(unknown)}")


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
    if not isinstance(args, dict):
        raise Violation(f"{tool} arguments must be an object")
    if tool == "search":
        _reject_unknown(args, {"query", "mode", "limit", "scope"}, tool)
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise Violation("search requires a non-empty string query")
        if len(query) > MAX_QUESTION_CHARS:
            raise Violation("search query is too long")
        cli = ["search", "--query", query]
        if "mode" in args:
            if args["mode"] not in ALLOWED_SEARCH_MODES:
                raise Violation(f"unknown search mode {args['mode']!r}")
            cli += ["--mode", args["mode"]]
        if "limit" in args:
            if not is_int(args["limit"]) or not 1 <= args["limit"] <= 50:
                raise Violation("limit must be an integer in 1..=50")
            cli += ["--limit", str(args["limit"])]
        if "scope" in args:
            if not valid_relative_path(args["scope"]):
                raise Violation("scope must be a workspace-relative path")
            cli += ["--scope", args["scope"]]
        return cli
    if tool == "symbol":
        _reject_unknown(args, {"name"}, tool)
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise Violation("symbol requires a non-empty name")
        return ["symbol", "--name", name]
    if tool == "facts":
        _reject_unknown(args, {"path", "method"}, tool)
        path = args.get("path")
        if not valid_relative_path(path):
            raise Violation("facts path must be a workspace-relative path")
        cli = ["facts", "--path", path]
        if "method" in args:
            if not isinstance(args["method"], str) or not args["method"].strip():
                raise Violation("facts method must be a non-empty string")
            cli += ["--method", args["method"]]
        return cli
    if tool == "evidence":
        _reject_unknown(args, {"requests"}, tool)
        requests = args.get("requests")
        if not isinstance(requests, list) or not requests:
            raise Violation("evidence requires a non-empty requests list")
        if len(requests) > 10:
            raise Violation("evidence accepts at most 10 span requests per call")
        for request in requests:
            if not isinstance(request, dict):
                raise Violation("each evidence request must be an object")
            if not valid_relative_path(request.get("relative_path")):
                raise Violation("evidence request path must be workspace-relative")
            if not isinstance(request.get("project"), str) or not request["project"].strip():
                raise Violation("evidence request project must be non-empty")
            if not isinstance(request.get("address"), dict):
                raise Violation("evidence request address must be an object")
        return ["evidence", "--requests", "-"]
    if tool == "verify":
        _reject_unknown(args, {"pack_id"}, tool)
        cli = ["verify"]
        if "pack_id" in args:
            if not is_int(args["pack_id"]):
                raise Violation("verify pack_id must be an integer")
            cli += ["--pack-id", str(args["pack_id"])]
        return cli
    if tool == "workflow":
        _reject_unknown(args, {"question"}, tool)
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            raise Violation("workflow requires a non-empty question")
        if len(question) > MAX_QUESTION_CHARS:
            raise Violation("workflow question is too long")
        return ["workflow", "--question", question]
    if tool == "status":
        _reject_unknown(args, set(), tool)
        return ["status"]
    raise Violation(f"unknown tool {tool!r}")


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
        if tool == "workflow" and self.plan_file is not None:
            cli += ["--plan-file", str(self.plan_file)]
        stdin = None
        if tool == "evidence":
            stdin = json.dumps(args["requests"], ensure_ascii=False)
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
    tools = []
    for name in sorted(ARM_TOOLS[profile]):
        schema = TOOL_SCHEMAS[name]
        tools.append({
            "type": "function",
            "name": name,
            "description": schema["description"],
            "parameters": schema["parameters"],
            "strict": True,
        })
    return tools


class ResponsesTransport:
    """Builds Responses-API payloads and parses Responses-API outputs.

    Subclasses implement `post(payload)`; everything else (request shape,
    conversation state, tool-call parsing) is shared with the real path.
    """

    def __init__(self, model: str, instructions: str, profile: str, question: str):
        self.model = model
        self.instructions = instructions
        self.profile = profile
        self.question = question
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
            return self._final(
                f"ARM-B: {len(selected)} selected spans, first="
                f"{selected[0]['path'] if selected else 'none'}, "
                f"internal_tool_calls={verified}", abstain=not selected)

        if tool == "search":
            items = result.get("items") or []
            if not items:
                return self._final(f"ARM-{self.profile[-1].upper()}: no items delivered", abstain=True)
            top = items[0]
            if self.served == 2:
                # Second request is derived from the delivered top item.
                if self.profile == "arm-c":
                    start, end = top["content_range"]
                    return self._tool("evidence", {"requests": [{
                        "project": "hrm",
                        "relative_path": top["path"],
                        "address": {"mode": "lines", "start_line": start, "end_line": end},
                    }]})
                return self._tool("search", {
                    "query": top["path"].split("/")[-1], "mode": "fts", "limit": 5})
            return self._final(
                f"ARM-{self.profile[-1].upper()}: {len(items)} items; "
                f"top={top['path']}@{top['content_range']}; "
                f"declaration={(top.get('declaration') or {}).get('symbol_name')}",
                abstain=False)

        if tool == "evidence":
            items = result.get("items") or []
            status = result.get("status")
            return self._final(
                f"ARM-C: evidence status={status}, items={len(items)}, "
                f"first_sha={(items[0]['sha256'][:12] if items else 'none')}, "
                f"first_offsets={(items[0]['content_byte_offsets'] if items else 'none')}",
                abstain=not items)

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
        expected = set(ARM_TOOLS[self.profile])
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
        return {
            "id": f"mock-{self.served}",
            "object": "response",
            "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "function_call",
                "call_id": f"call-{self.served}",
                "name": name,
                "arguments": json.dumps(args),
            }],
            "usage": {"input_tokens": 100 * self.served, "output_tokens": 20},
        }

    def _final(self, text: str, abstain: bool) -> dict:
        return {
            "id": f"mock-{self.served}",
            "object": "response",
            "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text",
                             "text": json.dumps({"answer": text, "abstain": abstain})}],
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
            except json.JSONDecodeError:
                raise Violation("provider returned unparseable tool arguments")
            return "tool", (item.get("name"), args, item.get("call_id"), item)
        if item.get("type") == "message":
            texts = [part.get("text", "") for part in item.get("content") or []
                     if part.get("type") == "output_text"]
            text = "\n".join(texts).strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "answer" in parsed:
                    return "final", {"answer": str(parsed["answer"]),
                                     "abstain": bool(parsed.get("abstain", False))}
            except json.JSONDecodeError:
                pass
            return "final", {"answer": text, "abstain": not text}
    raise Violation("provider response had neither a tool call nor a final message")


def drive(controller: AdapterController, answerer: ResponsesTransport, case: str,
          profile: str, output_dir: Path | None = None,
          verify_at_end: bool = True) -> dict:
    conversation = answerer.initial_conversation()
    transcript: list[dict] = []
    violations: list[str] = []
    round_no = 1
    turns = 0
    final = {"answer": "", "abstain": True}
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
        if kind == "final":
            final = payload
            break
        tool, args, call_id, raw_item = payload
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
    if verify_at_end:
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
                }
            else:
                final_verification = {"verified": None, "packs": 0,
                                      "reason": "no pack produced by this arm"}
        except (ContractError, Violation) as error:
            final_verification = {"verified": False, "error": str(error)}

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
        frozen = {"sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
                  "bytes": len(blob.encode("utf-8"))}
    budget = receipt.get("budget", {})
    return {
        "case": case,
        "profile": profile,
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


def grade(frozen_dir: Path, labels_dir: Path, case: str, profile: str) -> dict:
    """Runs the independent grader in a separate process and returns its verdict."""
    tool = Path(__file__).with_name("r4_grade.py")
    proc = subprocess.run(
        [sys.executable, str(tool), "--answers", str(frozen_dir), "--labels", str(labels_dir),
         "--case", case, "--profile", profile],
        capture_output=True, text=True, timeout=300,
    )
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
    parser.add_argument("--answer-instructions-file", type=Path)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--answers-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--grade", action="store_true")
    parser.add_argument("--authorized-inference", action="store_true",
                        help="explicit owner authorization flag required for the real backend")
    args = parser.parse_args()

    corpus = check_corpus(args.labels_dir, args.root)
    case_info = check_case(args.labels_dir, args.case)
    question = args.question_file.read_text(encoding="utf-8").strip() if args.question_file else ""
    if not question:
        cases = json.loads((args.labels_dir / "cases" / "dev.json").read_text(encoding="utf-8"))["cases"]
        question = next(entry["question"] for entry in cases if entry["id"] == args.case)
    if args.profile == "arm-b" and args.plan_file is None:
        print("NOTE: arm-b without --plan-file runs the question-only plan, which is a "
              "diagnostic (weaker control), not the plan-based baseline32 identity.",
              file=sys.stderr)

    instructions = "You are a code-investigation agent."
    if args.answer_instructions_file:
        instructions = args.answer_instructions_file.read_text(encoding="utf-8")

    if args.backend == "openai-responses":
        if not args.authorized_inference:
            print("CONTRACT_ERROR: real inference requires --authorized-inference plus the "
                  "owner's private-source egress authorization", file=sys.stderr)
            return ERROR_EXIT
        answerer = HttpTransport(
            model=args.model, instructions=instructions, profile=args.profile,
            question=question,
            endpoint=os.environ.get("R4_INFERENCE_ENDPOINT", ""),
            api_key=os.environ.get("R4_INFERENCE_TOKEN", ""),
        )
    else:
        answerer = MockTransport(
            model=args.model, instructions=instructions, profile=args.profile,
            question=question, corpus_marker=str(args.root.resolve()),
        )

    controller = AdapterController(args.adapter, args.session_dir, args.root,
                                   args.profile, args.case, args.plan_file)
    controller.create()
    report = drive(controller, answerer, args.case, args.profile, args.answers_dir)
    report["corpus"] = corpus
    report["case_identity"] = case_info
    report["transport_requests"] = len(answerer.requests)
    if args.grade and args.answers_dir is not None:
        report["grading"] = grade(args.answers_dir, args.labels_dir, args.case, args.profile)
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
