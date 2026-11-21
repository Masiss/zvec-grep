#!/usr/bin/env python3
"""r4_semantic_evaluator.py — independent semantic-evaluation workflow.

The grader (`r4_grade.py`) deliberately cannot grade meaning: it needs a
semantic evaluation that was produced *outside* the answerer's session and
bound to the exact frozen artefacts. This tool is the smallest workflow that
produces such an evaluation, in two steps that are separated so no model (and
no transport) ever holds the authority to write its own identity or digests:

  packet   Build the evaluator packet from frozen artefacts only: the frozen
           answer, its pack, the frozen question, the public C1–C6 rubric and
           the *text of the spans the answer itself cited* (the same source the
           answerer already received). Gold spans, the per-case C3 checklist
           and any grader verdict are never included. The packet carries the
           binding digests the grader will check.

  accept   Validate a raw evaluator response against a strict schema, derive
           the per-claim support lists, and write the evaluation document. The
           evaluator identity and the input digests are taken from the packet,
           never from the response, so a model cannot name a trusted evaluator
           or grade a different artefact.

The packet/response split is transport-agnostic. A loopback transport (used by
`r4_semantic_e2e.py`) and a real remote model both speak the same two files;
only the transport differs. No network client lives here on purpose: sending
private source to a remote evaluator is an owner decision (see
`cases/evaluator-candidates.json` and the docs).

The tool never registers an evaluator. `cases/evaluators.json` stays empty
until the owner approves an identity; until then every real case is
NOT_MEASURED by construction.

Usage
  r4_semantic_evaluator.py packet --answers DIR --labels DIR --corpus DIR \
      --case D11 --profile arm-c --identity eval-x --out packet.json
  r4_semantic_evaluator.py accept --packet packet.json --response resp.json \
      --out evaluation.json
  r4_semantic_evaluator.py estimate --answers DIR --labels DIR --corpus DIR \
      --candidates cases/evaluator-candidates.json --out cost.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = HERE.parent

sys.path.insert(0, str(HERE))
import r4_grade as grade  # noqa: E402  (reuse artifact loading and freeze checks)

SCHEMA_VERSION = 1
DIMENSIONS = ("claim_correctness", "citation_support", "completeness",
              "uncertainty", "abstention")
PACKET_KIND = "r4-semantic-evaluator-packet"

INSTRUCTIONS = """\
You are an independent evaluator of an AI agent's answer about a source-code
repository. You did not produce the answer and you did not run the agent.

You receive: the question, the agent's answer and claims, the evidence pack the
agent was allowed to see, the exact source text of each span the answer cites
(and nothing else from the repository), and a public rubric.

Judge every claim in `answer.claims`, by index, as `supported` or
`unsupported`:
  * supported   — the cited span text (or pack content, when present) directly
                  supports the claim as stated. Conditions/guards that limit
                  the effect must be stated by the claim.
  * unsupported — the claim overstates the cited text, names a different
                  method/file/behaviour, cites a span that says something else,
                  asserts runtime/deployment facts the source cannot establish,
                  or carries no citation at all.
A claim is not supported merely because its citation hash/range is valid.

Then score five dimensions from 0 to 2 (2 best):
  claim_correctness  are the claims factually correct about the shown source?
  citation_support   do the cited spans actually support what is claimed?
  completeness       does the answer address every part of the question that
                     the delivered evidence can answer?
  uncertainty        are limits/conditions stated instead of implied certainty?
  abstention         are missing, stale or out-of-scope parts declared rather
                     than invented?

Verdict rules: `PASS` only if every claim is supported and claim_correctness
is 2, citation_support is 2, and uncertainty, abstention and completeness are
at least 1. Otherwise `FAIL`, and at least one claim must be `unsupported`.
An answer that asserts no claims is not a valid benchmark answer: the
structural layer fails it, and this workflow refuses to produce a formal
evaluation for it.

Output exactly one JSON object, no prose, with exactly these keys:
{"packet_sha256": "<copy the packet's packet_sha256 verbatim>",
 "verdict": "PASS"|"FAIL",
 "rationale": "<why, non-empty>",
 "claims": [{"index": <int>, "judgement": "supported"|"unsupported",
             "reason": "<non-empty>"}, ...],
 "dimensions": {"claim_correctness": {"score": 0|1|2, "rationale": "..."},
                "citation_support": {...}, "completeness": {...},
                "uncertainty": {...}, "abstention": {...}}}
Every claim index must appear exactly once. Do not add keys.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["packet_sha256", "verdict", "rationale", "claims", "dimensions"],
    "additionalProperties": False,
    "properties": {
        "packet_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "verdict": {"enum": ["PASS", "FAIL"]},
        "rationale": {"type": "string", "minLength": 1},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "judgement", "reason"],
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "judgement": {"enum": ["supported", "unsupported"]},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "dimensions": {
            "type": "object",
            "required": list(DIMENSIONS),
            "additionalProperties": False,
            "properties": {
                name: {
                    "type": "object",
                    "required": ["score", "rationale"],
                    "additionalProperties": False,
                    "properties": {
                        "score": {"type": "integer", "enum": [0, 1, 2]},
                        "rationale": {"type": "string", "minLength": 1},
                    },
                }
                for name in DIMENSIONS
            },
        },
    },
}


class EvaluatorError(Exception):
    """The packet or the response violates the contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(document: dict) -> str:
    return json.dumps(document, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


# --------------------------------------------------------------- packet

def _frozen_question(labels: Path, case: str) -> dict:
    dev = grade.load_json(labels / "cases" / "dev.json")
    for item in dev.get("cases", []):
        if item.get("id") == case:
            text = item.get("question")
            if not isinstance(text, str) or not text.strip():
                raise EvaluatorError(f"frozen case {case} has no question text")
            return {"text": text, "sha256": sha256_bytes(text.encode("utf-8")),
                    "source": "frozen-case", "formal": bool(item.get("formal", True))}
    raise EvaluatorError(f"case {case} is not in the frozen development set")


def _resolve_cited_span(corpus: Path, path: str, start: int, end: int,
                        max_span_bytes: int = 65536) -> dict:
    """Reads exactly one cited span. Path traversal and absolute paths are
    refused: the evaluator may see cited source, never browse the corpus.
    Oversized spans are refused rather than truncated, because a truncated span
    would be judged as if it were the whole text."""
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise EvaluatorError(f"citation path is outside the frozen corpus: {path!r}")
    try:
        target = (corpus / relative).resolve()
        target.relative_to(corpus.resolve())
    except (OSError, ValueError) as error:
        raise EvaluatorError(f"citation path is not readable: {path!r} ({error})") from error
    if not target.is_file():
        return {"path": path, "start_line": start, "end_line": end,
                "resolved": False, "content": None, "source_sha256": None}
    raw = target.read_bytes()
    source_sha = sha256_bytes(raw)
    if len(raw) > 4 * max_span_bytes:
        raise EvaluatorError(f"cited file is too large to evaluate: {path!r}")
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if start < 1 or end < start:
        return {"path": path, "start_line": start, "end_line": end,
                "resolved": False, "content": None, "source_sha256": source_sha}
    content = "\n".join(lines[start - 1:end])
    if len(content.encode("utf-8")) > max_span_bytes:
        raise EvaluatorError(
            f"cited span is larger than {max_span_bytes} bytes: {path!r} "
            f"lines {start}-{end}")
    return {"path": path, "start_line": start, "end_line": end,
            "resolved": True, "content": content, "source_sha256": source_sha}


def build_packet(answers: Path, labels: Path, corpus: Path, case: str,
                 profile: str, identity: str,
                 materials: Path | None = None,
                 packs: Path | None = None) -> dict:
    if not identity or not isinstance(identity, str):
        raise EvaluatorError("an evaluator identity is required")
    materials = materials or labels / "cases" / "c3-steps.json"
    artifact, answer_record = grade.load_answer(answers, case, profile)
    pack, pack_record = grade.load_pack(answers, case, profile, packs)
    binding = grade.bind_answer_to_pack(artifact, pack_record)
    question = _frozen_question(labels, case)
    if not materials.is_file():
        raise EvaluatorError(f"grading materials are missing: {materials}")

    # Delivered evidence is the pack, not the corpus. A citation may be narrower
    # than a pack item but never wider; anything outside the delivered bytes is
    # reported as unresolved and is NOT read from the corpus.
    delivered = grade.pack_spans(pack)
    content_by_span: dict[tuple, str | None] = {}
    items = []
    if "selected" in pack:
        for item in pack.get("selected") or []:
            path = item.get("path")
            start = item.get("start_line")
            end = item.get("end_line")
            if isinstance(start, int) and isinstance(end, int):
                content_by_span[(path, start, end)] = item.get("content")
            items.append({"path": path, "start_line": start, "end_line": end,
                          "sha256": item.get("source_sha256"),
                          "verified": item.get("verified") is True,
                          "content": item.get("content")})
    else:
        for item in pack.get("items") or []:
            rng = item.get("range") or {}
            path = item.get("relative_path") or item.get("path")
            start = rng.get("start_line")
            end = rng.get("end_line")
            normalized_end = end
            if rng.get("end_byte_column") == 0 and isinstance(start, int) and isinstance(end, int):
                normalized_end = max(start, end - 1)
            if isinstance(start, int) and isinstance(normalized_end, int):
                content_by_span[(path, start, normalized_end)] = item.get("content")
            items.append({"path": path, "start_line": start, "end_line": end,
                          "sha256": item.get("sha256") or item.get("source_sha256"),
                          "verified": item.get("verification") == "verified",
                          "content": item.get("content")})

    def delivered_span(citation: dict) -> dict:
        path = citation["path"]
        start = citation["start_line"]
        end = citation["end_line"]
        outer = next((span for span in delivered if span["path"] == path
                      and grade.contains(span["range"], (start, end))), None)
        base = {"path": path, "start_line": start, "end_line": end,
                "citation_sha256": citation.get("sha256"),
                "in_pack": outer is not None,
                "pack_verified": bool(outer and outer["verified"]),
                "pack_sha256": outer["sha256"] if outer else None,
                "resolved": False, "content": None, "source_sha256": None,
                "hash_ok": False}
        if outer is None:
            base["reason"] = "citation is not contained in a delivered pack span"
            return base
        content = content_by_span.get((outer["path"], *outer["range"]))
        if content is not None:
            lines = content.split("\n")
            offset = start - outer["range"][0]
            text = "\n".join(lines[offset:offset + (end - start) + 1])
            if len(text.encode("utf-8")) > 65536:
                raise EvaluatorError(f"cited span is too large to evaluate: {path!r}")
            base.update({"resolved": True, "content": text,
                         "source_sha256": outer["sha256"]})
        else:
            try:
                read = _resolve_cited_span(corpus, path, start, end)
            except (OSError, ValueError) as error:
                raise EvaluatorError(
                    f"cited span could not be read ({path!r}): {error}") from error
            base.update({"resolved": read["resolved"], "content": read["content"],
                         "source_sha256": read["source_sha256"]})
        base["hash_ok"] = (
            base["resolved"]
            and isinstance(citation.get("sha256"), str)
            and citation["sha256"] == outer["sha256"]
            and (base["source_sha256"] is None
                 or base["source_sha256"] == outer["sha256"])
        )
        return base

    claims = artifact.get("claims") or []
    cited: dict[tuple[str, int, int], dict] = {}
    max_total_cited_bytes = 512 * 1024
    claim_view = []
    for index, claim in enumerate(claims):
        if not isinstance(claim.get("text"), str):
            raise EvaluatorError(f"claim {index} has no string text")
        citations = []
        for citation in claim.get("citations") or []:
            if not isinstance(citation, dict):
                raise EvaluatorError(f"claim {index} has a non-object citation")
            path = citation.get("path")
            start = citation.get("start_line")
            end = citation.get("end_line")
            if not isinstance(path, str) or type(start) is not int or type(end) is not int:
                raise EvaluatorError(f"claim {index} has a malformed citation: {citation!r}")
            span = delivered_span({"path": path, "start_line": start,
                                   "end_line": end, "sha256": citation.get("sha256")})
            cited[(path, start, end)] = span
            citations.append({"path": path, "start_line": start, "end_line": end,
                              "sha256": citation.get("sha256")})
        claim_view.append({"index": index, "text": claim.get("text"), "citations": citations})

    total_cited = sum(len((span.get("content") or "").encode("utf-8"))
                      for span in cited.values())
    if total_cited > max_total_cited_bytes:
        raise EvaluatorError(
            f"cited evidence is larger than {max_total_cited_bytes} bytes; refuse "
            "rather than send an unbounded packet")

    gaps = pack.get("gaps")
    if isinstance(gaps, dict):
        gaps = {key: value[:8] if isinstance(value, list) else value
                for key, value in list(gaps.items())[:12]}

    # Public rubric only. The per-case C3 checklist inside the materials file is
    # deliberately not read: `materials_sha256` is a binding digest, not content.
    rubric = grade.load_json(materials).get("rubric") or {}
    packet = {
        "schema_version": SCHEMA_VERSION,
        "kind": PACKET_KIND,
        "evaluator": {"id": identity,
                      "instructions_sha256": sha256_bytes(INSTRUCTIONS.encode("utf-8")),
                      "output_schema": OUTPUT_SCHEMA},
        "case": case,
        "profile": profile,
        "question": question,
        "answer": {"text": (artifact.get("final") or {}).get("answer"),
                   "abstain": (artifact.get("final") or {}).get("abstain") is True,
                   "gaps": artifact.get("gaps") or [],
                   "claims": claim_view,
                   "declared_question": artifact.get("question")},
        "evidence": {"pack_sha256": pack_record["sha256"],
                     "pack_kind": pack_record["kind"],
                     "items": items,
                     "gaps": gaps},
        "cited_spans": list(cited.values()),
        "rubric": {"id": "r4-c1-c6",
                   "criteria": rubric,
                   "contract": (labels / "REPORT-CONTRACT.md").read_text(encoding="utf-8")
                   if (labels / "REPORT-CONTRACT.md").is_file() else None},
        "instructions": INSTRUCTIONS,
        "binding": {"answer": answer_record, "pack": pack_record,
                    "answer_pack_binding": binding},
        "inputs": {
            "case": case,
            "answer_sha256": answer_record["sha256"],
            "pack_sha256": pack_record["sha256"],
            "question_sha256": question["sha256"],
            "rubric_sha256": sha256_path(materials),
        },
    }
    packet["packet_sha256"] = sha256_bytes(canonical_json(packet).encode("utf-8"))
    return packet


def render_prompt(packet: dict) -> str:
    """The exact text a transport sends to an evaluator model.

    The instructions are prepended once and stripped from the embedded copy, so
    a real prompt does not pay for them twice; the standalone packet keeps them
    for a human or a transport that sends the packet alone."""
    payload = {key: value for key, value in packet.items() if key != "instructions"}
    return INSTRUCTIONS + "\nPACKET\n" + json.dumps(payload, indent=1,
                                                    ensure_ascii=False)


def verify_packet(packet: dict) -> None:
    if not isinstance(packet, dict):
        raise EvaluatorError("evaluator packet must be a JSON object")
    if packet.get("kind") != PACKET_KIND:
        raise EvaluatorError(f"not an r4 evaluator packet: kind={packet.get('kind')!r}")
    claimed = packet.get("packet_sha256")
    body = {key: value for key, value in packet.items() if key != "packet_sha256"}
    actual = sha256_bytes(canonical_json(body).encode("utf-8"))
    if claimed != actual:
        raise EvaluatorError("evaluator packet was modified after it was built")
    inputs = packet.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
            "case", "answer_sha256", "pack_sha256", "question_sha256", "rubric_sha256"}:
        raise EvaluatorError("evaluator packet has malformed binding inputs")
    if inputs.get("case") != packet.get("case"):
        raise EvaluatorError("evaluator packet binds a different case than it names")
    question = packet.get("question") or {}
    if inputs.get("question_sha256") != question.get("sha256"):
        raise EvaluatorError("evaluator packet binds a different question than it shows")
    if not isinstance(question.get("text"), str):
        raise EvaluatorError("evaluator packet has no question text")
    identity = (packet.get("evaluator") or {}).get("id")
    if not isinstance(identity, str) or not identity:
        raise EvaluatorError("evaluator packet names no evaluator identity")
    if not isinstance(packet.get("answer"), dict):
        raise EvaluatorError("evaluator packet has no answer")
    evaluator = packet.get("evaluator") or {}
    if evaluator.get("instructions_sha256") != sha256_bytes(INSTRUCTIONS.encode("utf-8")):
        raise EvaluatorError("evaluator packet was built for different instructions")
    if evaluator.get("output_schema") != OUTPUT_SCHEMA:
        raise EvaluatorError("evaluator packet carries a different output schema")


# --------------------------------------------------------------- accept

def _require_object(value, where: str, keys: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise EvaluatorError(f"{where} must be a JSON object")
    if keys is not None:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        if missing:
            raise EvaluatorError(f"{where} is missing {missing}")
        if extra:
            raise EvaluatorError(f"{where} has unexpected key(s) {extra}")
    return value


def _require_text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluatorError(f"{where} must be a non-empty string")
    return value.strip()


def _require_score(value, where: str) -> int:
    if type(value) is not int or value not in (0, 1, 2):
        raise EvaluatorError(f"{where} must be the integer 0, 1 or 2")
    return value


def validate_response(packet: dict, response: dict) -> dict:
    """Strictly validates a raw evaluator response and returns the evaluation
    document the grader consumes. Identity and input digests come from the
    packet; anything in the response that tries to set them is rejected."""
    verify_packet(packet)
    response = _require_object(response, "evaluator response",
                               {"packet_sha256", "verdict", "rationale",
                                "claims", "dimensions"})
    identity = packet["evaluator"]["id"]
    inputs = dict(packet["inputs"])
    claims = packet["answer"].get("claims") or []

    # The response is bound to this exact packet, so a favourable verdict for
    # one answer cannot be replayed onto another at the same digest set.
    if response.get("packet_sha256") != packet["packet_sha256"]:
        raise EvaluatorError("response is not bound to this packet (packet_sha256 mismatch)")
    if not claims:
        raise EvaluatorError(
            "an answer with no claims cannot be semantically evaluated; its "
            "structural verdict governs")

    if response.get("verdict") not in ("PASS", "FAIL"):
        raise EvaluatorError("verdict must be PASS or FAIL")
    rationale = _require_text(response.get("rationale"), "rationale")

    raw_claims = response.get("claims")
    if not isinstance(raw_claims, list):
        raise EvaluatorError("claims must be a list")
    judged: dict[int, dict] = {}
    for position, entry in enumerate(raw_claims):
        where = f"claims[{position}]"
        entry = _require_object(entry, where, {"index", "judgement", "reason"})
        index = entry["index"]
        # bool is an int subclass in Python; reject it explicitly.
        if type(index) is not int or not 0 <= index < len(claims):
            raise EvaluatorError(f"{where}.index {index!r} is out of range")
        if index in judged:
            raise EvaluatorError(f"claim {index} is judged more than once")
        if entry["judgement"] not in ("supported", "unsupported"):
            raise EvaluatorError(f"{where}.judgement must be supported or unsupported")
        judged[index] = {"index": index, "judgement": entry["judgement"],
                         "reason": _require_text(entry["reason"], f"{where}.reason")}
    if sorted(judged) != list(range(len(claims))):
        missing = sorted(set(range(len(claims))) - set(judged))
        raise EvaluatorError(f"every claim must be judged exactly once; missing {missing}")

    dims = _require_object(response.get("dimensions"), "dimensions", set(DIMENSIONS))
    dimensions = {}
    for name in DIMENSIONS:
        dim = _require_object(dims[name], f"dimensions.{name}", {"score", "rationale"})
        dimensions[name] = {
            "score": _require_score(dim["score"], f"dimensions.{name}.score"),
            "rationale": _require_text(dim["rationale"], f"dimensions.{name}.rationale"),
        }

    supported = sorted(i for i, entry in judged.items() if entry["judgement"] == "supported")
    unsupported = sorted(i for i, entry in judged.items() if entry["judgement"] == "unsupported")
    verdict = response["verdict"]

    # Internal consistency: the verdict must agree with the judgements and the
    # dimension scores. This is the evaluator-side half of the guarantee; the
    # grader re-checks the claim lists independently and can only ever return
    # UNGRADABLE for an inconsistent evaluation.
    if verdict == "PASS":
        if unsupported:
            raise EvaluatorError(f"PASS cannot carry unsupported claims: {unsupported}")
        if dimensions["claim_correctness"]["score"] != 2:
            raise EvaluatorError("PASS requires claim_correctness = 2")
        if dimensions["citation_support"]["score"] != 2:
            raise EvaluatorError("PASS requires citation_support = 2")
        for name in ("completeness", "uncertainty", "abstention"):
            if dimensions[name]["score"] < 1:
                raise EvaluatorError(f"PASS requires {name} >= 1")
    else:
        if not unsupported:
            raise EvaluatorError("FAIL must mark at least one claim unsupported")

    return {
        "schema_version": SCHEMA_VERSION,
        "case": packet["case"],
        "evaluator_id": identity,
        "verdict": verdict,
        "rationale": rationale,
        "inputs": inputs,
        "supported_claims": supported,
        "unsupported_claims": unsupported,
        "claim_assessments": [judged[i] for i in sorted(judged)],
        "dimensions": dimensions,
        "packet_sha256": packet["packet_sha256"],
    }


def accept(packet_path: Path, response_path: Path, out: Path,
           identity: str | None = None,
           served_identity: str | None = None) -> dict:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    verify_packet(packet)
    packet_identity = packet["evaluator"]["id"]
    if identity is not None and identity != packet_identity:
        raise EvaluatorError(
            f"packet names evaluator {packet_identity!r}, not {identity!r}")
    response = json.loads(response_path.read_text(encoding="utf-8"))
    document = validate_response(packet, response)
    if served_identity is not None:
        if not isinstance(served_identity, str) or not served_identity.strip():
            raise EvaluatorError("served identity must be a non-empty string")
        document["served_identity"] = served_identity.strip()
        document["served_identity_source"] = "transport"
    document["packet_identity"] = {"path": str(packet_path),
                                   "sha256": packet["packet_sha256"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    return document


# --------------------------------------------------------------- estimate

def estimate(answers: Path, labels: Path, corpus: Path, candidates: Path,
             materials: Path | None = None, output_tokens: int = 700) -> dict:
    """Measures the exact evaluator prompt volume for every frozen answer and
    prices it with the rate cards in the candidates file. Token counts are
    estimates (UTF-8 bytes / 4 + output budget), clearly labelled as such."""
    rate_card = grade.load_json(candidates)
    cases = grade.load_json(labels / "cases" / "dev.json").get("cases", [])
    order, by_case = [], {}
    manifest = grade.load_json(labels / "scope-manifest.json")
    unauthored = {key for key, value in manifest.get("case_spans", {}).items()
                  if not isinstance(value, list) or (value and not isinstance(value[0], dict))}
    for item in cases:
        if item.get("id") not in unauthored:
            order.append(item["id"])
    rows = []
    for case in order:
        dev = next(c for c in cases if c["id"] == case)
        question_sha = sha256_bytes(dev["question"].encode("utf-8"))
        for profile in ("arm-a", "arm-b", "arm-c"):
            answer = answers / f"answer-{case}-{profile}.json"
            if not answer.is_file():
                continue
            try:
                packet = build_packet(answers, labels, corpus, case, profile,
                                      "cost-probe", materials=materials)
            except (EvaluatorError, grade.GradingError) as error:
                rows.append({"case": case, "profile": profile, "error": str(error)})
                continue
            prompt = render_prompt(packet)
            prompt_bytes = len(prompt.encode("utf-8"))
            rows.append({
                "case": case, "profile": profile,
                "question_sha256": question_sha,
                "prompt_bytes": prompt_bytes,
                "prompt_tokens_est": -(-prompt_bytes // 4),
                "output_tokens_assumed": output_tokens,
                "cited_spans": len(packet["cited_spans"]),
                "claims": len(packet["answer"]["claims"]),
            })
    usable = [r for r in rows if "error" not in r]
    prompt_tokens = sum(r["prompt_tokens_est"] for r in usable)
    total_tokens = prompt_tokens + output_tokens * len(usable)
    pricing = []
    for candidate in rate_card.get("candidates", []):
        rates = candidate.get("assumed_rates_usd_per_million_tokens", {})
        input_rate = rates.get("input")
        output_rate = rates.get("output")
        if not isinstance(input_rate, (int, float)) or not isinstance(output_rate, (int, float)):
            pricing.append({"id": candidate.get("id"), "error": "no numeric rate card"})
            continue
        cost = (prompt_tokens / 1_000_000 * input_rate
                + output_tokens * len(usable) / 1_000_000 * output_rate)
        pricing.append({"id": candidate.get("id"),
                        "rates_usd_per_1m": {"input": input_rate, "output": output_rate},
                        "estimated_total_usd": round(cost, 6),
                        "estimated_per_evaluation_usd": round(cost / max(1, len(usable)), 6)})
    cases_covered = {r["case"] for r in usable}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "r4-semantic-evaluator-cost-estimate",
        "note": ("Token counts are estimates: UTF-8 prompt bytes / 4, plus an "
                 "assumed output budget. Rates are the candidate file's "
                 "assumptions, not a provider quote."),
        "answers_dir": str(answers),
        "cases": len(cases_covered),
        "evaluations": len(usable),
        "prompt_tokens_est": prompt_tokens,
        "output_tokens_assumed_total": output_tokens * len(usable),
        "total_tokens_est": total_tokens,
        "pricing": pricing,
        "rows": rows,
    }


# --------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    packet_cmd = sub.add_parser("packet", help="build an evaluator packet")
    packet_cmd.add_argument("--answers", required=True, type=Path)
    packet_cmd.add_argument("--labels", required=True, type=Path)
    packet_cmd.add_argument("--corpus", required=True, type=Path)
    packet_cmd.add_argument("--case", required=True)
    packet_cmd.add_argument("--profile", required=True)
    packet_cmd.add_argument("--identity", required=True)
    packet_cmd.add_argument("--materials", type=Path)
    packet_cmd.add_argument("--packs", type=Path)
    packet_cmd.add_argument("--out", required=True, type=Path)

    accept_cmd = sub.add_parser("accept", help="validate a response and write the evaluation")
    accept_cmd.add_argument("--packet", required=True, type=Path)
    accept_cmd.add_argument("--response", required=True, type=Path)
    accept_cmd.add_argument("--identity")
    accept_cmd.add_argument("--served-identity",
                            help="identity reported by the transport/provider (recorded, not trusted)")
    accept_cmd.add_argument("--out", required=True, type=Path)

    estimate_cmd = sub.add_parser("estimate", help="cost/token estimate over frozen answers")
    estimate_cmd.add_argument("--answers", required=True, type=Path)
    estimate_cmd.add_argument("--labels", required=True, type=Path)
    estimate_cmd.add_argument("--corpus", required=True, type=Path)
    estimate_cmd.add_argument("--candidates", required=True, type=Path)
    estimate_cmd.add_argument("--materials", type=Path)
    estimate_cmd.add_argument("--output-tokens", type=int, default=700)
    estimate_cmd.add_argument("--out", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "packet":
            packet = build_packet(args.answers, args.labels, args.corpus,
                                  args.case, args.profile, args.identity,
                                  materials=args.materials, packs=args.packs)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(packet, indent=1, ensure_ascii=False) + "\n",
                                encoding="utf-8")
            print(json.dumps({"packet": str(args.out),
                              "packet_sha256": packet["packet_sha256"],
                              "prompt_bytes": len(render_prompt(packet).encode("utf-8")),
                              "inputs": packet["inputs"]}, indent=1))
        elif args.command == "accept":
            document = accept(args.packet, args.response, args.out, args.identity,
                              served_identity=args.served_identity)
            print(json.dumps({"evaluation": str(args.out),
                              "verdict": document["verdict"],
                              "evaluator_id": document["evaluator_id"],
                              "supported": document["supported_claims"],
                              "unsupported": document["unsupported_claims"]}, indent=1))
        else:
            report = estimate(args.answers, args.labels, args.corpus,
                              args.candidates, materials=args.materials,
                              output_tokens=args.output_tokens)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
            print(json.dumps({key: report[key] for key in
                              ("cases", "evaluations", "prompt_tokens_est",
                               "total_tokens_est", "pricing")}, indent=1))
    except (EvaluatorError, grade.GradingError, json.JSONDecodeError,
            UnicodeDecodeError) as error:
        print(json.dumps({"status": "REJECTED", "error": str(error)}, indent=1))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
