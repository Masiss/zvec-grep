#!/usr/bin/env python3
"""r4_tool_schema.py — single source of truth for the answerer tool contract.

One declarative spec per tool generates BOTH the JSON schema sent to the model
(OpenAI Responses API function tools, strict mode) and the argument
normalizer/validator used by the controller. There is no second interpretation
of the same contract anywhere else.

Strict mode rules implemented by ``strict_violations``:
  * every object declares ``additionalProperties: false``;
  * every object declares ``properties`` and ``required`` == all property
    names (optional semantics are expressed as nullable types, never by
    omitting a property from ``required``);
  * nested arrays/objects are validated recursively;
  * a nullable union must include ``null`` in ``type`` and, when an ``enum`` is
    present, in the enum as well;
  * only a small, supported keyword set is emitted.
"""

from __future__ import annotations

MAX_QUESTION_CHARS = 2000
MAX_EVIDENCE_REQUESTS = 10
MAX_SEARCH_LIMIT = 50

PATH_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$"
SHA256_PATTERN = r"^[a-f0-9]{64}$"

ALLOWED_KEYWORDS = {
    "type", "description", "enum", "minimum", "maximum", "maxLength",
    "pattern", "items", "properties", "required", "additionalProperties",
}


class SchemaViolation(Exception):
    """The answerer sent arguments outside the tool contract."""


# --------------------------------------------------------------- arg specs

class P:
    """Property spec. `required` is the *logical* requirement; the wire schema
    always lists the property in `required` and marks it nullable instead."""

    def __init__(self, *, type, required=False, enum=None, minimum=None,
                 maximum=None, max_length=None, pattern=None, items=None,
                 properties=None, nonempty=False, description=None):
        self.type = type            # "string" | "integer" | "number" | "boolean" | "object" | "array"
        self.required = required
        self.enum = enum
        self.minimum = minimum
        self.maximum = maximum
        self.max_length = max_length
        self.pattern = pattern
        self.items = items
        self.properties = properties or {}
        self.nonempty = nonempty
        self.description = description

    def nullable(self) -> bool:
        return not self.required

    # -- JSON schema emission
    def to_schema(self) -> dict:
        out: dict = {}
        if self.nullable():
            out["type"] = [self.type, "null"]
        else:
            out["type"] = self.type
        if self.description:
            out["description"] = self.description
        if self.enum is not None:
            values = list(self.enum)
            if self.nullable():
                values.append(None)
            out["enum"] = values
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.max_length is not None:
            out["maxLength"] = self.max_length
        if self.pattern is not None:
            out["pattern"] = self.pattern
        if self.type == "array":
            out["items"] = self.items.to_schema() if self.items else {"type": "string"}
        if self.type == "object":
            out["properties"] = {name: spec.to_schema() for name, spec in self.properties.items()}
            out["required"] = sorted(self.properties)
            out["additionalProperties"] = False
        return out

    # -- value validation
    def validate(self, value, path: str) -> object:
        if value is None:
            if self.required:
                raise SchemaViolation(f"{path} is required and must not be null")
            return None
        expected = self.type
        if expected == "integer":
            if type(value) is not int:   # bool is an int subclass; reject it
                raise SchemaViolation(f"{path} must be an integer")
        elif expected == "number":
            if type(value) not in (int, float):
                raise SchemaViolation(f"{path} must be a number")
        elif expected == "boolean":
            if type(value) is not bool:
                raise SchemaViolation(f"{path} must be a boolean")
        elif expected == "string":
            if not isinstance(value, str):
                raise SchemaViolation(f"{path} must be a string")
        elif expected == "array":
            if not isinstance(value, list):
                raise SchemaViolation(f"{path} must be an array")
            if self.nonempty and not value:
                raise SchemaViolation(f"{path} must not be empty")
        elif expected == "object":
            if not isinstance(value, dict):
                raise SchemaViolation(f"{path} must be an object")
        if isinstance(value, str):
            if self.nonempty and not value.strip():
                raise SchemaViolation(f"{path} must not be empty")
            if self.max_length is not None and len(value) > self.max_length:
                raise SchemaViolation(f"{path} exceeds {self.max_length} characters")
            if self.pattern is not None:
                import re
                if not re.match(self.pattern, value):
                    raise SchemaViolation(f"{path} does not match the required pattern")
        if isinstance(value, (int, float)) and type(value) is not bool:
            if self.minimum is not None and value < self.minimum:
                raise SchemaViolation(f"{path} must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise SchemaViolation(f"{path} must be <= {self.maximum}")
        if self.enum is not None and value not in self.enum:
            raise SchemaViolation(f"{path} must be one of {sorted(map(str, self.enum))}")
        if self.type == "array" and self.items is not None:
            return [self.items.validate(entry, f"{path}[{index}]")
                    for index, entry in enumerate(value)]
        if self.type == "object":
            unknown = set(value) - set(self.properties)
            if unknown:
                raise SchemaViolation(f"{path} has undefined field(s): {sorted(unknown)}")
            out = {}
            for name, spec in self.properties.items():
                if name not in value:
                    if spec.required:
                        raise SchemaViolation(f"{path}.{name} is required")
                    continue
                normalized = spec.validate(value[name], f"{path}.{name}")
                if normalized is not None:
                    out[name] = normalized
            return out
        return value


ADDRESS = P(
    type="object",
    required=True,
    description="Span address: either one-based inclusive lines or half-open byte offsets.",
    properties={
        "mode": P(type="string", required=True, enum=["lines", "bytes"]),
        "start_line": P(type="integer", minimum=1, description="First cited line (lines mode)."),
        "end_line": P(type="integer", minimum=1, description="Last cited line inclusive (lines mode)."),
        "start": P(type="integer", minimum=0, description="Inclusive start byte (bytes mode)."),
        "end": P(type="integer", minimum=0, description="Exclusive end byte (bytes mode)."),
    },
)

EVIDENCE_REQUEST = P(
    type="object",
    required=True,
    properties={
        "project": P(type="string", required=True, nonempty=True,
                     description="Project label from the workspace."),
        "relative_path": P(type="string", required=True, pattern=PATH_PATTERN,
                           description="Workspace-relative path with / separators."),
        "expected_sha256": P(type="string", pattern=SHA256_PATTERN, max_length=64,
                             description="Optional expected file hash; when present the "
                                         "item is served as verified only if it matches."),
        "address": ADDRESS,
    },
)

ARG_SPECS: dict[str, dict[str, P]] = {
    "search": {
        "query": P(type="string", required=True, nonempty=True, max_length=MAX_QUESTION_CHARS),
        "mode": P(type="string", enum=["hybrid", "fts", "vector", "rg"],
                  description="Search route; omit for hybrid."),
        "limit": P(type="integer", minimum=1, maximum=MAX_SEARCH_LIMIT),
        "scope": P(type="string", pattern=PATH_PATTERN,
                   description="Optional workspace-relative subtree."),
    },
    "symbol": {
        "name": P(type="string", required=True, nonempty=True),
    },
    "facts": {
        "path": P(type="string", required=True, pattern=PATH_PATTERN),
        "method": P(type="string", nonempty=True, description="Optional method filter."),
    },
    "evidence": {
        "requests": P(type="array", required=True, nonempty=True,
                      items=EVIDENCE_REQUEST,
                      max_length=None, description=f"1..{MAX_EVIDENCE_REQUESTS} span requests."),
    },
    "verify": {
        "pack_id": P(type="integer", minimum=1, description="Pack to revalidate; omit for the latest."),
    },
    "workflow": {
        "question": P(type="string", required=True, nonempty=True, max_length=MAX_QUESTION_CHARS),
    },
    "status": {},
}

CITATION_SPECS = {
    "path": P(type="string", required=True, pattern=PATH_PATTERN),
    "start_line": P(type="integer", required=True, minimum=1),
    "end_line": P(type="integer", required=True, minimum=1),
    "sha256": P(type="string", required=True, pattern=r"^[0-9a-f]{64}$"),
}

CLAIM_SPECS = {
    "text": P(type="string", required=True, nonempty=True, max_length=MAX_QUESTION_CHARS),
    "citations": P(type="array", required=True, nonempty=True,
                   items=P(type="object", required=True,
                           properties=CITATION_SPECS)),
}

ANSWER_SPECS = {
    "answer": P(type="string", required=True, nonempty=True),
    "claims": P(type="array", required=True,
                items=P(type="object", required=True, properties=CLAIM_SPECS)),
    "gaps": P(type="array", required=True, items=P(type="string", required=True, nonempty=True)),
    "abstain": P(type="boolean", required=True),
}

MAX_CLAIMS = 40

PLAN_SPECS = {
    "queries": P(type="array", required=True, nonempty=True, items=P(type="string", required=True, nonempty=True, max_length=MAX_QUESTION_CHARS)),
    "symbols": P(type="array", required=True, items=P(type="string", required=True, nonempty=True)),
    "scopes": P(type="array", required=True, items=P(type="string", required=True, pattern=PATH_PATTERN)),
    "terms": P(type="array", required=True, items=P(type="string", required=True, nonempty=True)),
}
MAX_PLAN_QUERIES = 8
MAX_PLAN_ITEMS = 16

TOOL_DESCRIPTIONS = {
    "search": "Search the indexed workspace. Returns ranked excerpts with declaration "
              "identity and exact source coordinates.",
    "symbol": "Exact declaration lookup by name.",
    "facts": "Structural facts for one file or an optional method filter.",
    "evidence": "Request exact source spans; each span is hashed and verified.",
    "verify": "Revalidate the latest (or a given) evidence/workflow pack against current source.",
    "workflow": "Engine-managed deterministic investigation for one question.",
    "status": "Remaining session budget and pack state.",
}

ARM_TOOLS = {
    "arm-a": {"search", "status"},
    "arm-b": {"workflow", "verify", "status"},
    "arm-c": {"search", "symbol", "facts", "evidence", "verify", "status"},
}


# ------------------------------------------------------------- plan schema

def plan_parameters() -> dict:
    """Strict schema for the plan-generation tool (fresh blind plan)."""
    return {
        "type": "object",
        "properties": {name: spec.to_schema() for name, spec in PLAN_SPECS.items()},
        "required": sorted(PLAN_SPECS),
        "additionalProperties": False,
    }


def answer_parameters() -> dict:
    return {
        "type": "object",
        "properties": {name: spec.to_schema() for name, spec in ANSWER_SPECS.items()},
        "required": sorted(ANSWER_SPECS),
        "additionalProperties": False,
    }


def answer_tool() -> dict:
    return {
        "type": "function",
        "name": "submit_answer",
        "description": "Submit the final answer: the human-readable text, one claim "
                       "per assertion with the evidence spans that support it, and any "
                       "gaps you could not resolve. Nothing else ends the session.",
        "parameters": answer_parameters(),
        "strict": True,
    }


def normalize_answer(args) -> dict:
    """Validates a submitted final answer. Raises SchemaViolation on anything
    that does not meet the contract, so an unparseable answer is reported as
    INVALID_OUTPUT instead of being graded as a wrong answer."""
    if not isinstance(args, dict):
        raise SchemaViolation("answer must be an object")
    unknown = set(args) - set(ANSWER_SPECS)
    if unknown:
        raise SchemaViolation(f"answer has undefined field(s): {sorted(unknown)}")
    out = {}
    for name, spec in ANSWER_SPECS.items():
        if name not in args or args[name] is None:
            raise SchemaViolation(f"answer.{name} is required")
        out[name] = spec.validate(args[name], f"answer.{name}")
    if not out["abstain"] and not out["claims"]:
        raise SchemaViolation(
            "a non-abstaining answer must carry at least one claim; abstain=true "
            "with empty claims is the only way to submit no claims"
        )
    if out["abstain"] and out["claims"]:
        # An abstention that still asserts claims is contradictory: the caller
        # must choose. Refuse rather than guess which one is meant.
        raise SchemaViolation("abstain=true cannot be combined with claims")
    if len(out["claims"]) > MAX_CLAIMS:
        raise SchemaViolation(f"answer may carry at most {MAX_CLAIMS} claims")
    for index, claim in enumerate(out["claims"]):
        for citation in claim["citations"]:
            _check_relative_path(citation["path"], f"answer.claims[{index}].citations.path")
            if citation["end_line"] < citation["start_line"]:
                raise SchemaViolation(
                    f"answer.claims[{index}] has a citation whose end precedes its start")
    return out


def strict_violations_for_answer() -> list[str]:
    return strict_violations(answer_parameters(), "$answer")


def plan_tool() -> dict:
    return {
        "type": "function",
        "name": "emit_plan",
        "description": "Return the investigation plan: lexical queries, candidate "
                       "symbols and folder scopes derived from the question only.",
        "parameters": plan_parameters(),
        "strict": True,
    }


def normalize_plan(args) -> dict:
    """Validates a plan emitted by the model and returns the AgentPlan shape
    the Rust workflow accepts (queries/terms/symbols/scopes)."""
    if not isinstance(args, dict):
        raise SchemaViolation("plan must be an object")
    unknown = set(args) - set(PLAN_SPECS)
    if unknown:
        raise SchemaViolation(f"plan has undefined field(s): {sorted(unknown)}")
    out = {}
    for name, spec in PLAN_SPECS.items():
        if name not in args or args[name] is None:
            raise SchemaViolation(f"plan.{name} is required")
        out[name] = spec.validate(args[name], f"plan.{name}")
    if not out["queries"]:
        raise SchemaViolation("plan.queries must contain at least one query")
    if len(out["queries"]) > MAX_PLAN_QUERIES:
        raise SchemaViolation(f"plan.queries may contain at most {MAX_PLAN_QUERIES} entries")
    for key in ("symbols", "scopes", "terms"):
        if len(out[key]) > MAX_PLAN_ITEMS:
            raise SchemaViolation(f"plan.{key} may contain at most {MAX_PLAN_ITEMS} entries")
        for value in out[key]:
            if key == "scopes":
                _check_relative_path(value, f"plan.scopes[{value}]")
    return out


def plan_wire_form(compact: dict) -> dict:
    """Strict wire form for a plan tool call (all fields present)."""
    return {name: compact.get(name, []) for name in PLAN_SPECS}


def strict_violations_for_plan() -> list[str]:
    return strict_violations(plan_parameters(), "$plan")


# ------------------------------------------------------------ schema output

def tool_parameters(tool: str) -> dict:
    """Strict-mode JSON schema for one tool's arguments."""
    specs = ARG_SPECS[tool]
    return {
        "type": "object",
        "properties": {name: spec.to_schema() for name, spec in specs.items()},
        "required": sorted(specs),
        "additionalProperties": False,
    }


def tool_payload_schema(profile: str) -> list[dict]:
    """Responses-API function tools for one arm, in stable order.

    The terminal `submit_answer` tool is part of every arm's contract: an arm
    cannot end a session with free prose, because the answer must be
    structurally gradable.
    """
    tools = [{
        "type": "function",
        "name": name,
        "description": TOOL_DESCRIPTIONS[name],
        "parameters": tool_parameters(name),
        "strict": True,
    } for name in sorted(ARM_TOOLS[profile])]
    tools.append(answer_tool())
    return tools


# -------------------------------------------------------- strict validator

def strict_violations(schema: dict, path: str = "$") -> list[str]:
    """Independent strict-mode validator over a JSON schema fragment."""
    problems: list[str] = []
    unsupported = set(schema) - ALLOWED_KEYWORDS
    if unsupported:
        problems.append(f"{path}: unsupported keyword(s) {sorted(unsupported)}")
    types = schema.get("type")
    if types is None:
        problems.append(f"{path}: missing type")
        return problems
    type_list = types if isinstance(types, list) else [types]
    nullable = "null" in type_list
    concrete = [entry for entry in type_list if entry != "null"]
    if len(concrete) != 1:
        problems.append(f"{path}: type must be one concrete type (+ optional null)")
    if nullable and "enum" in schema and None not in schema["enum"]:
        problems.append(f"{path}: nullable type requires null in enum when enum is present")
    kind = concrete[0] if concrete else None
    if kind == "object":
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: object must set additionalProperties: false")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            problems.append(f"{path}: object must declare properties")
            return problems
        required = schema.get("required")
        if not isinstance(required, list) or sorted(required) != sorted(properties):
            problems.append(
                f"{path}: required must list every property (strict mode): "
                f"{sorted(required or [])} != {sorted(properties)}"
            )
        for name, sub in properties.items():
            problems.extend(strict_violations(sub, f"{path}.{name}"))
    if kind == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            problems.append(f"{path}: array must declare items")
        else:
            problems.extend(strict_violations(items, f"{path}[]"))
    if "enum" in schema and not isinstance(schema["enum"], list):
        problems.append(f"{path}: enum must be a list")
    return problems


def strict_violations_for_arm(profile: str) -> list[str]:
    problems: list[str] = []
    for tool in tool_payload_schema(profile):
        problems.extend(
            strict_violations(tool["parameters"], f"${profile}.{tool['name']}")
        )
    return problems


# ------------------------------------------------------- argument handling

def normalize_arguments(tool: str, args) -> dict:
    """Validates wire arguments and returns the compact form (nulls dropped).

    Accepts both the strict wire form (all keys present, optional ones null)
    and the compact form (optional keys absent). Raises SchemaViolation for
    anything the contract does not allow.
    """
    if tool not in ARG_SPECS:
        raise SchemaViolation(f"unknown tool {tool!r}")
    if not isinstance(args, dict):
        raise SchemaViolation(f"{tool} arguments must be an object")
    specs = ARG_SPECS[tool]
    unknown = set(args) - set(specs)
    if unknown:
        raise SchemaViolation(f"{tool} received undefined argument(s): {sorted(unknown)}")
    normalized = {}
    for name, spec in specs.items():
        if name not in args:
            if spec.required:
                raise SchemaViolation(f"{tool}.{name} is required")
            continue
        value = spec.validate(args[name], f"{tool}.{name}")
        if value is not None:
            normalized[name] = value
    return _cross_check(tool, normalized)


def wire_arguments(tool: str, compact: dict) -> dict:
    """Expands compact arguments into the strict wire form a real model must
    emit: every declared property present, optional ones null."""
    def expand(spec: P, value) -> object:
        if value is None:
            return None
        if spec.type == "object":
            out = {}
            for name, sub in spec.properties.items():
                out[name] = expand(sub, value.get(name)) if name in value else None
            return out
        if spec.type == "array":
            return [expand(spec.items, entry) for entry in value] if spec.items else value
        return value

    specs = ARG_SPECS[tool]
    return {name: expand(spec, compact.get(name)) for name, spec in specs.items()}


def _check_relative_path(value: str, field: str) -> None:
    """Rejects traversal, empty and dot segments in workspace-relative paths."""
    if value.startswith("/") or "\\" in value:
        raise SchemaViolation(f"{field} must be workspace-relative with / separators")
    segments = value.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise SchemaViolation(f"{field} must not contain empty, '.' or '..' segments")


def _cross_check(tool: str, args: dict) -> dict:
    if tool == "evidence":
        requests = args.get("requests") or []
        if not requests:
            raise SchemaViolation("evidence requires at least one span request")
        if len(requests) > MAX_EVIDENCE_REQUESTS:
            raise SchemaViolation(
                f"evidence accepts at most {MAX_EVIDENCE_REQUESTS} span requests per call"
            )
        for index, request in enumerate(requests):
            address = request["address"]
            mode = address["mode"]
            if mode == "lines":
                if address.get("start") is not None or address.get("end") is not None:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: bytes fields are not allowed in lines mode"
                    )
                start = address.get("start_line")
                end = address.get("end_line")
                if start is None or end is None:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: lines mode requires start_line and end_line"
                    )
                if end < start:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: end_line must be >= start_line"
                    )
            else:
                if address.get("start_line") is not None or address.get("end_line") is not None:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: line fields are not allowed in bytes mode"
                    )
                start = address.get("start")
                end = address.get("end")
                if start is None or end is None:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: bytes mode requires start and end"
                    )
                if start >= end:
                    raise SchemaViolation(
                        f"evidence.requests[{index}].address: end must be greater than start"
                    )
    if tool == "search" and "scope" in args:
        _check_relative_path(args["scope"], "search.scope")
    if tool == "facts":
        _check_relative_path(args["path"], "facts.path")
    if tool == "evidence":
        for index, request in enumerate(args.get("requests") or []):
            _check_relative_path(request["relative_path"], f"evidence.requests[{index}].relative_path")
    if tool == "search":
        query = args["query"]
        if len(query) > MAX_QUESTION_CHARS:
            raise SchemaViolation("search.query is too long")
    if tool == "workflow":
        if len(args["question"]) > MAX_QUESTION_CHARS:
            raise SchemaViolation("workflow.question is too long")
    return args
