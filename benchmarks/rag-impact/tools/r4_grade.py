#!/usr/bin/env python3
"""r4_grade.py — independent C1–C6 / C3 grading pipeline.

Runs in its own process and consumes only frozen artefacts:

  * the answer artifact (structured claims + citations), hash-checked and bound
    to the exact pack it was produced from;
  * the pack, hash-checked with its sidecar;
  * evaluator-only materials: rubric text, C3 steps and the frozen gold spans
    (never sent to the answerer).

Three layers are kept strictly apart, because conflating them is how a wrong
answer earns a PASS:

  structural  — deterministic checks a machine can make: does each claim carry
                a citation that resolves to a verified item of *this* pack with
                a matching source hash; are the required steps present/cited;
                were gaps declared by the answerer.
  semantic    — does the cited source actually support the claim. This needs
                an independent evaluator. Without one the case is
                NOT_MEASURED, never PASS.
  formal      — the graded verdict. It requires both layers: every structural
                criterion measured, thresholds met, and a semantic verdict of
                PASS with a rationale from an identified evaluator.

A structural 12/12 with no semantic evaluation is reported as
`structural_total: 12, verdict: NOT_MEASURED`, not PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------- constants

PASS_TOTAL = 12
PARTIAL_TOTAL = 8
MIN_MEAN_C245 = 1.8
MIN_MEAN_TOTAL = 10.0

SEMANTIC_VERDICTS = ("PASS", "FAIL")

RUNTIME_ASSERTIONS = [
    (r"\b(is|are) (currently )?running\b", "runtime"),
    (r"\bdeployed\b", "deployment"),
    (r"\bcommitted to (the )?database\b", "runtime"),
    (r"\bproduction (database|server|environment)\b", "deployment"),
    (r"\bđang chạy\b", "runtime"),
    (r"\bđã (commit|triển khai)\b", "runtime"),
    (r"\btriển khai (thực tế|lên)\b", "deployment"),
    (r"\bcơ sở dữ liệu (thật|production|thực tế)\b", "deployment"),
]
HEDGES = [
    "not observed", "not established", "unknown", "cannot be determined",
    "does not establish", "only the source", "source-visible", "not proof",
    "không quan sát", "chưa xác định", "không chứng minh", "chưa rõ",
    "không kết luận", "chỉ trong source",
]

REQUIRED_ANSWER_FIELDS = ("answer", "claims", "gaps", "abstain")


class GradingError(Exception):
    """Hard failure: the case cannot be graded at all."""


# --------------------------------------------------------------- utilities

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) <= min(a[1], b[1])


# ------------------------------------------------------------ frozen inputs

def require_sidecar(path: Path) -> str:
    """The freeze hash is mandatory: an answer or pack without its sidecar
    cannot be proven frozen, so it is refused rather than trusted."""
    sidecar = path.with_suffix(".sha256")
    if not sidecar.is_file():
        raise GradingError(f"missing sidecar hash for {path.name}")
    expected = sidecar.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256_bytes(path.read_bytes())
    if expected != actual:
        raise GradingError(
            f"hash mismatch for {path.name}: sidecar {expected} != actual {actual}"
        )
    return actual


def load_answer(answers_dir: Path, case: str, profile: str) -> tuple[dict, dict]:
    path = answers_dir / f"answer-{case}-{profile}.json"
    if not path.is_file():
        raise GradingError(f"frozen answer artifact is missing: {path}")
    digest = require_sidecar(path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    for field in ("case", "profile", "final"):
        if field not in artifact:
            raise GradingError(f"answer artifact missing field {field!r}")
    if artifact["case"] != case or artifact["profile"] != profile:
        raise GradingError(
            f"answer artifact is for {artifact['case']}/{artifact['profile']}, "
            f"not {case}/{profile}"
        )
    status = artifact.get("status", "OK")
    if status != "OK":
        raise GradingError(
            f"answer artifact status is {status}: {artifact.get('error', 'no detail')}"
        )
    final = artifact.get("final") or {}
    missing = [name for name in REQUIRED_ANSWER_FIELDS if name not in final]
    if missing:
        raise GradingError(f"final answer is missing required field(s): {missing}")
    claims = artifact.get("claims")
    if not isinstance(claims, list):
        raise GradingError("answer artifact has no structured claims list")
    return artifact, {"path": str(path), "sha256": digest,
                      "bytes": path.stat().st_size, "sidecar_verified": True}


def load_pack(answers_dir: Path, case: str, profile: str,
              packs_dir: Path | None) -> tuple[dict, dict]:
    """Loads the exact pack the answer names, never 'the last file in a glob'."""
    explicit = answers_dir / f"pack-{case}-{profile}.json"
    if explicit.is_file():
        path = explicit
    elif packs_dir is not None:
        candidates = sorted(packs_dir.glob("pack-*.json"))
        if len(candidates) != 1:
            raise GradingError(
                f"--packs must hold exactly one pack for {case}/{profile}; "
                f"found {len(candidates)}"
            )
        path = candidates[0]
    else:
        raise GradingError(f"no frozen pack found for {case}/{profile}")
    digest = require_sidecar(path)
    pack = json.loads(path.read_text(encoding="utf-8"))
    kind = "workflow" if "selected" in pack else "evidence"
    return pack, {"path": str(path), "sha256": digest, "kind": kind,
                  "sidecar_verified": True}


def bind_answer_to_pack(artifact: dict, pack_record: dict) -> dict:
    """The answer must name the pack it was produced from, and that identity
    must match the pack being scored. Otherwise a different pack could be
    swapped in and the answer graded against it."""
    claimed = artifact.get("pack")
    if not isinstance(claimed, dict) or not claimed.get("sha256"):
        raise GradingError(
            "answer artifact does not declare the pack it was produced from"
        )
    if claimed["sha256"] != pack_record["sha256"]:
        raise GradingError(
            "answer was produced from a different pack: "
            f"{claimed['sha256']} != {pack_record['sha256']}"
        )
    if claimed.get("kind") and claimed["kind"] != pack_record["kind"]:
        raise GradingError(
            f"answer names a {claimed['kind']} pack but a {pack_record['kind']} pack was frozen"
        )
    return {"bound": True, "claimed_sha256": claimed["sha256"]}


def pack_spans(pack: dict) -> list[dict]:
    """Normalizes packs into spans with an explicit verification state.

    A missing verification field is NOT verified. The only accepted
    representations are named here; anything else is a contract error rather
    than a guess.
    """
    spans = []
    if "selected" in pack:
        for item in pack.get("selected") or []:
            state = item.get("verified")
            spans.append({
                "path": item.get("path"),
                "range": (int(item.get("start_line", 0)), int(item.get("end_line", 0))),
                "sha256": item.get("source_sha256"),
                "verified": state is True,
                "verification_state": "explicit_true" if state is True
                else "explicit_false" if state is False else "missing",
            })
    else:
        for item in pack.get("items") or []:
            rng = item.get("range")
            if isinstance(rng, dict):
                start = rng.get("start_line")
                end = rng.get("end_line")
                if rng.get("end_byte_column") == 0 and start is not None and end is not None:
                    end = max(start, end - 1)
            else:
                start, end = (rng or [None, None])[:2]
            verification = item.get("verification")
            spans.append({
                "path": item.get("relative_path") or item.get("path"),
                "range": (int(start or 0), int(end or 0)),
                "sha256": item.get("sha256") or item.get("source_sha256"),
                "verified": verification == "verified",
                "verification_state": str(verification) if verification else "missing",
            })
    return [s for s in spans if s["path"] and s["range"][1] >= s["range"][0]]


def load_materials(materials: Path) -> dict:
    if not materials.is_file():
        raise GradingError(f"evaluator-only materials are missing: {materials}")
    data = load_json(materials)
    for key in ("rubric", "c3_steps"):
        if key not in data:
            raise GradingError(f"materials are missing {key!r}")
    return data


# ------------------------------------------------------------- C3 anchoring

IDENTIFIER_RE = re.compile(r"`([^`]+)`")

NOISE_TOKENS = {
    "cite", "cites", "stated", "state", "state that", "do not", "no", "not",
    "the", "and", "or", "if", "when", "only", "must", "pack", "method",
    "scope", "source", "required", "step",
}


def step_tokens(step: str) -> list[str]:
    tokens = []
    for raw in IDENTIFIER_RE.findall(step):
        raw = raw.strip()
        if not raw or raw.lower() in NOISE_TOKENS:
            continue
        tokens.append(raw)
    return tokens


def locate_step(step: str, gold_spans: list[tuple[str, tuple[int, int]]],
                source_lines: dict[str, list[str]]) -> list[dict]:
    anchors = []
    tokens = step_tokens(step)
    if not tokens:
        return anchors
    for path, span in gold_spans:
        lines = source_lines.get(path)
        if lines is None:
            continue
        start, end = span
        for index in range(max(start, 1), min(end, len(lines)) + 1):
            line = lines[index - 1]
            for token in tokens:
                probe = token.strip().split(".")[-1].strip("()<>[] ")
                if probe and probe in line:
                    anchors.append({"token": token, "path": path,
                                    "range": (index, index),
                                    "line": line.strip()[:160]})
                    break
    seen = set()
    unique = []
    for anchor in anchors:
        if anchor["token"] in seen:
            continue
        seen.add(anchor["token"])
        unique.append(anchor)
    return unique


# ------------------------------------------------------------------ checks

def analyse_citations(claims: list[dict], spans: list[dict]) -> dict:
    """Structural citation validity only. Resolving a citation to a verified
    span says nothing about whether the span supports the claim."""
    valid, invalid = [], []
    for claim_index, claim in enumerate(claims):
        text = str(claim.get("text", "")).strip()
        citations = claim.get("citations") or []
        # Annotate each citation with its validity so later checks (required-step
        # coverage) cannot credit a citation this pass rejected.
        claim["_validated_citations"] = [None] * len(citations)
        if not citations:
            invalid.append({"claim": claim_index, "reason": "no citation",
                            "text": text[:90]})
            continue
        for citation in citations:
            path = citation.get("path")
            start = citation.get("start_line")
            end = citation.get("end_line")
            sha = citation.get("sha256")
            if not path or start is None or end is None:
                invalid.append({"claim": claim_index, "reason": "incomplete citation"})
                continue
            if not sha:
                invalid.append({"claim": claim_index,
                                "reason": "citation carries no source hash",
                                "path": path})
                continue
            match = None
            for span in spans:
                if span["path"] != path:
                    continue
                if overlaps(span["range"], (int(start), int(end))):
                    match = span
                    break
            if match is None:
                invalid.append({"claim": claim_index,
                                "reason": "citation is outside the pack",
                                "path": path, "range": [start, end]})
                continue
            if match["sha256"] != sha:
                invalid.append({"claim": claim_index,
                                "reason": "citation hash does not match the pack item",
                                "path": path})
                continue
            if not match["verified"]:
                invalid.append({"claim": claim_index,
                                "reason": f"pack span not verified ({match['verification_state']})",
                                "path": path})
                continue
            valid.append({"claim": claim_index, "path": path,
                          "range": [int(start), int(end)]})
            claim["_validated_citations"][citations.index(citation)] = True
    return {"valid": valid, "invalid": invalid, "claims": len(claims),
            "claims_with_valid_citation": len({e["claim"] for e in valid})}


def _validated_for_claim(claim: dict) -> list:
    """Per-citation validity flags attached during analysis (see analyse_citations)."""
    return claim.get("_validated_citations") or [None] * len(claim.get("citations") or [])


def analyse_steps(steps: list[dict], spans: list[dict], claims: list[dict]) -> dict:
    results = []
    for step in steps:
        anchors = step.get("anchors") or []
        if not anchors:
            results.append({"step": step["id"], "status": "UNADDRESSABLE",
                            "in_pack": None, "cited": None})
            continue
        in_pack = any(
            overlaps(span["range"], anchor["range"]) and span["path"] == anchor["path"]
            for anchor in anchors for span in spans
        )
        # A step counts as cited only when a *valid* citation covers it: an
        # invalid or unverified citation cannot evidence completeness.
        cited = False
        for claim in claims:
            for raw, validated in zip(claim.get("citations") or [],
                                      _validated_for_claim(claim)):
                if validated is None:
                    continue
                c_range = (int(raw["start_line"]), int(raw["end_line"]))
                if any(raw["path"] == anchor["path"] and overlaps(c_range, anchor["range"])
                       for anchor in anchors):
                    cited = True
        results.append({"step": step["id"], "status": "ADDRESSABLE",
                        "in_pack": in_pack, "cited": cited})
    return {"steps": results}


def analyse_gaps(claims: list[dict], answer_final: dict, pack: dict) -> dict:
    """Separates three different facts that used to be conflated:

    * the engine reported a gap (an obligation to be honest);
    * the answerer declared a gap in its structured final answer;
    * the answerer abstained.

    A pack gap is never evidence that the answerer told the user anything.
    """
    engine_gaps = []
    for gap in pack.get("gaps") or []:
        engine_gaps.append(gap if isinstance(gap, str) else gap.get("code", "gap"))
    declared = [g if isinstance(g, str) else str(g) for g in (answer_final.get("gaps") or [])]
    abstain = answer_final.get("abstain") is True
    text = str(answer_final.get("answer", ""))
    hedges = [term for term in HEDGES if term in text.lower()]
    return {
        "engine_gaps": engine_gaps,
        "answer_declared_gaps": declared,
        "abstain": abstain,
        "text_hedges": hedges,
        "answer_declared_anything": bool(declared) or abstain,
        "obligation": bool(engine_gaps),
    }


def analyse_runtime_claims(text: str) -> dict:
    hits = []
    lowered = text.lower()
    for pattern, kind in RUNTIME_ASSERTIONS:
        for match in re.finditer(pattern, lowered):
            window = lowered[max(0, match.start() - 120):match.end() + 120]
            hedged = any(hedge in window for hedge in HEDGES)
            hits.append({"kind": kind, "match": match.group(0), "hedged": hedged})
    return {"assertions": hits, "unhedged": [h for h in hits if not h["hedged"]]}


# ----------------------------------------------------------------- semantic

def evaluate_semantic(artifact: dict, pack: dict, evaluator: dict | None,
                      binding: dict, registry: dict | None,
                      answer_sha: str, pack_sha: str,
                      question_sha: str, case: str,
                      materials_sha: str) -> dict:
    """Applies an independent semantic evaluation, if one exists.

    Acceptance requires all of:

    * the evaluator is registered in the benchmark's trusted registry (a caller
      cannot mint authority by writing an `evaluator_id`);
    * the evaluation names the exact frozen inputs it judged — answer, pack,
      question and §rubric identity — and those match what is being graded;
    * the result is internally consistent: a PASS cannot carry any unsupported
      claim, and a FAIL must identify at least one.

    Anything else is INVALID, and an INVALID evaluation can never become PASS.
    """
    if evaluator is not None and not isinstance(evaluator, dict):
        return {"status": "INVALID", "verdict": None, "rationale": "",
                "error": "semantic evaluation must be a JSON object",
                "evaluator": None, "claims_reviewed": 0}
    if evaluator is None:
        return {"status": "NOT_EVALUATED", "verdict": None,
                "rationale": "no independent semantic evaluator supplied",
                "evaluator": None, "claims_reviewed": 0}

    def invalid(error: str, **extra) -> dict:
        out = {"status": "INVALID", "verdict": evaluator.get("verdict"),
               "rationale": (evaluator.get("rationale") or "").strip(),
               "error": error,
               "evaluator": evaluator.get("evaluator_id") or "",
               "claims_reviewed": 0}
        out.update(extra)
        return out

    verdict = evaluator.get("verdict")
    rationale = (evaluator.get("rationale") or "").strip() \
        if isinstance(evaluator.get("rationale"), (str, type(None))) else ""
    identity = evaluator.get("evaluator_id") or ""
    claims = artifact.get("claims") or []
    if not isinstance(claims, list) or any(not isinstance(claim, dict) for claim in claims):
        return invalid("answer claims must be a list of objects")

    if verdict not in SEMANTIC_VERDICTS:
        return invalid(f"semantic verdict must be one of {SEMANTIC_VERDICTS}")
    if not rationale:
        return invalid("semantic verdict carries no rationale")
    if not identity:
        return invalid("semantic verdict identifies no evaluator")

    # Authority: only a registered evaluator may grade. An unknown identity is
    # not evidence of trust just because the caller wrote it down.
    if registry is None:
        return invalid("no evaluator registry was supplied; an unregistered evaluator "
                       "cannot produce a formal verdict")
    registered = (registry.get("evaluators") or {}).get(identity)
    if registered is None:
        return invalid(f"evaluator {identity!r} is not in the benchmark registry")
    if not registered.get("trusted"):
        return invalid(f"evaluator {identity!r} is registered but not trusted")

    # Provenance: the evaluation must name the frozen inputs it judged.
    inputs = evaluator.get("inputs")
    if not isinstance(inputs, dict):
        return invalid("semantic evaluation must carry an 'inputs' object")
    expected = {
        "case": case,
        "answer_sha256": answer_sha,
        "pack_sha256": pack_sha,
        "question_sha256": question_sha,
        "rubric_sha256": materials_sha,
    }
    missing = [name for name in expected if not inputs.get(name)]
    if missing:
        return invalid(f"semantic evaluation does not bind these inputs: {missing}")
    mismatched = {
        name: {"expected": want, "given": inputs.get(name)}
        for name, want in expected.items() if inputs.get(name) != want
    }
    if mismatched:
        return invalid(
            "semantic evaluation was produced for different inputs: "
            + json.dumps(mismatched)
        )

    # Consistency: the verdict must agree with its own claim judgements, and
    # every claim must be judged. A missing judgement list is not "all fine" —
    # an evaluator that examined nothing has not evaluated the answer.
    for field_name in ("supported_claims", "unsupported_claims"):
        if field_name not in evaluator:
            return invalid(f"semantic evaluation is missing {field_name!r}; every claim "
                           "must be judged explicitly")
    unsupported = evaluator.get("unsupported_claims")
    supported = evaluator.get("supported_claims")
    if not isinstance(unsupported, list) or not isinstance(supported, list):
        return invalid("supported_claims and unsupported_claims must both be lists")

    def bad_index(index) -> bool:
        # bool is an int subclass in Python: `True` must not pass as index 1.
        return (type(index) is not int) or not 0 <= index < len(claims)

    for label, values in (("unsupported_claims", unsupported),
                          ("supported_claims", supported)):
        for index in values:
            if bad_index(index):
                return invalid(f"{label} index {index!r} is out of range")
        if len(set(values)) != len(values):
            return invalid(f"{label} lists a claim more than once")
    overlap = sorted(set(unsupported) & set(supported))
    if overlap:
        return invalid(f"claims judged both supported and unsupported: {overlap}")
    judged = sorted(set(unsupported) | set(supported))
    if judged != list(range(len(claims))):
        return invalid(
            "every claim must be judged exactly once; judged "
            f"{judged} of {len(claims)}"
        )
    if verdict == "PASS" and unsupported:
        return invalid(
            f"a PASS cannot carry unsupported claims: {sorted(set(unsupported))}"
        )
    if verdict == "FAIL" and not unsupported:
        return invalid("a FAIL must identify at least one unsupported claim")

    return {"status": "EVALUATED", "verdict": verdict, "rationale": rationale,
            "evaluator": identity, "claims_reviewed": len(judged),
            "supported_claims": sorted(set(supported)),
            "unsupported_claims": sorted(set(unsupported)),
            "inputs": expected}


# ----------------------------------------------------------------- scoring

def score_case(case: str, artifact: dict, pack: dict, materials: dict,
               gold_spans: list[tuple[str, tuple[int, int]]],
               source_lines: dict[str, list[str]],
               semantic: dict, formal_eligible: bool,
               ineligibility: str | None) -> dict:
    spans = pack_spans(pack)
    if not spans:
        raise GradingError("pack contains no spans")
    claims = artifact.get("claims") or []
    final = artifact.get("final") or {}
    steps_material = materials["c3_steps"].get(case)
    if not steps_material:
        raise GradingError(f"materials have no C3 steps for case {case}")

    steps = [{"id": index, "text": text,
              "anchors": locate_step(text, gold_spans, source_lines)}
             for index, text in enumerate(steps_material, start=1)]

    citations = analyse_citations(claims, spans)
    step_state = analyse_steps(steps, spans, claims)
    gap_state = analyse_gaps(claims, final, pack)
    runtime = analyse_runtime_claims(str(final.get("answer", "")))

    addressable = [s for s in step_state["steps"] if s["status"] == "ADDRESSABLE"]
    packed = [s for s in addressable if s["in_pack"]]
    omitted = [s for s in packed if not s["cited"]]
    missing_from_pack = [s for s in addressable if not s["in_pack"]]

    criteria = {}

    # C1 — answerable from the pack.
    if not addressable:
        criteria["C1"] = {"score": None, "status": "NEEDS_EVALUATOR",
                          "rationale": "no required step could be anchored to gold spans"}
    else:
        ratio = len(packed) / len(addressable)
        criteria["C1"] = {"score": 2 if ratio == 1 else 1 if ratio > 0 else 0,
                          "status": "MEASURED",
                          "rationale": f"{len(packed)}/{len(addressable)} required steps are present in the pack"}

    # C2 — citation support. Structural validity is measured; support needs an
    # evaluator and is never inferred from it.
    if not claims:
        criteria["C2"] = {"score": 0, "status": "MEASURED",
                          "rationale": "answer contains no claims",
                          "semantic_support": semantic["status"]}
    else:
        measured_invalid = len(citations["invalid"])
        verified_spans = sum(1 for span in spans if span["verified"])
        if verified_spans == 0:
            # No citation can resolve to verified evidence: a provenance
            # failure, independent of how good the reasoning was.
            score = 0
        elif semantic["status"] == "EVALUATED":
            unsupported = set(semantic.get("unsupported_claims") or [])
            if measured_invalid or semantic["verdict"] == "FAIL":
                # A semantic FAIL is graded by how much of the answer is
                # unsupported: everything -> 0, some -> 1.
                score = 0 if len(unsupported) >= len(claims) else 1
            elif citations["claims_with_valid_citation"] < len(claims):
                score = 1
            else:
                score = 2
        else:
            score = None
        criteria["C2"] = {
            "score": score,
            "status": "MEASURED" if semantic["status"] == "EVALUATED"
            else "STRUCTURE_ONLY",
            "rationale": (
                f"{citations['claims_with_valid_citation']}/{citations['claims']} claims "
                f"carry a citation that resolves to a verified item of this pack; "
                f"{measured_invalid} invalid citation(s); semantic support "
                f"{semantic['status'].lower()}"
            ),
            "structural": {
                "claims": citations["claims"],
                "claims_with_valid_citation": citations["claims_with_valid_citation"],
                "verified_spans": verified_spans,
                "invalid": citations["invalid"][:5],
            },
            "semantic_support": semantic["status"],
        }

    # C3 — chain completeness.
    if not addressable:
        criteria["C3"] = {"score": None, "status": "NEEDS_EVALUATOR",
                          "rationale": "no anchorable required steps"}
    else:
        applicable = [s for s in addressable if s["in_pack"]]
        if not applicable:
            criteria["C3"] = {"score": 0, "status": "MEASURED",
                              "rationale": "no required step is supported by the pack"}
        else:
            covered = len([s for s in applicable if s["cited"]])
            criteria["C3"] = {
                "score": 2 if covered == len(applicable) else 1 if covered else 0,
                "status": "MEASURED",
                "rationale": f"{covered}/{len(applicable)} pack-supported required steps are cited",
                "uncited": [s["step"] for s in applicable if not s["cited"]],
            }

    # C4 — source vs deployment discipline (heuristic, downgrade only).
    if runtime["unhedged"]:
        criteria["C4"] = {"score": 0, "status": "HEURISTIC",
                          "rationale": f"{len(runtime['unhedged'])} unhedged runtime/deployment assertion(s)",
                          "assertions": runtime["unhedged"][:5]}
    elif runtime["assertions"]:
        criteria["C4"] = {"score": 1, "status": "HEURISTIC",
                          "rationale": "runtime/deployment assertions present but explicitly hedged"}
    else:
        criteria["C4"] = {"score": 2, "status": "HEURISTIC",
                          "rationale": "no runtime/deployment assertion detected"}

    # C5 — honest gaps. Only the ANSWERER's own declaration counts; an engine
    # gap is the obligation, never the declaration.
    declared = gap_state["answer_declared_gaps"]
    if gap_state["obligation"]:
        if declared:
            score, why = 2, "the engine gap is declared by the answerer"
        elif gap_state["abstain"]:
            score, why = 1, "the answerer abstains without naming the gap"
        else:
            score, why = 0, "the engine gap is not declared in the answer"
    else:
        if gap_state["abstain"]:
            score, why = 0, "the answerer abstains although the pack has no gap"
        elif declared:
            score, why = 1, "the answerer declares a gap the pack does not report"
        elif gap_state["text_hedges"]:
            score, why = 1, "hedging appears in prose but no gap is declared"
        else:
            score, why = 2, "no gap existed and none is claimed"
    criteria["C5"] = {
        "score": score, "status": "MEASURED", "rationale": why,
        "engine_gaps": gap_state["engine_gaps"],
        "answer_declared_gaps": declared,
        "abstain": gap_state["abstain"],
        "text_hedges": gap_state["text_hedges"],
    }

    # C6 — no false negatives.
    omissions = len(omitted)
    criteria["C6"] = {
        "score": 2 if omissions == 0 else 1 if omissions == 1 else 0,
        "status": "MEASURED",
        "rationale": f"{omissions} pack-supported required step(s) omitted from the answer",
        "omitted": [s["step"] for s in omitted],
    }

    measured = [c for c in criteria.values() if c["score"] is not None]
    measured_total = sum(c["score"] for c in measured)
    measured_max = 2 * len(measured)
    structural_complete = len(measured) == len(criteria)
    structural_total = measured_total if structural_complete else None
    if not structural_complete:
        structural_verdict = "NOT_MEASURED"
    elif structural_total >= PASS_TOTAL and not any(
            criteria[k]["score"] == 0 for k in ("C2", "C4", "C5")):
        structural_verdict = "PASS"
    elif structural_total >= PARTIAL_TOTAL:
        structural_verdict = "PARTIAL"
    else:
        structural_verdict = "FAIL"

    unmeasured = [name for name, c in criteria.items()
                  if c["status"] in ("NEEDS_EVALUATOR", "STRUCTURE_ONLY")]

    # Formal verdict: structural thresholds are necessary, never sufficient.
    provenance_blocked = sum(1 for span in spans if span["verified"]) == 0
    if not formal_eligible:
        # A diagnostic run (overridden question, missing identity) is evaluated
        # for development only: it can never be reported as a frozen benchmark
        # result, whatever its structural or semantic outcome.
        formal = "NOT_MEASURED"
    elif semantic["status"] == "INVALID":
        formal = "UNGRADABLE"
    elif provenance_blocked:
        # An arm without a verification capability cannot produce verifiable
        # citations by construction. That is a provenance limitation, not
        # evidence of bad reasoning, so the formal verdict cannot claim either.
        formal = "NOT_MEASURED"
    elif any(c["score"] == 0 for c in criteria.values() if c["score"] is not None):
        # A zero on any criterion is a structural failure regardless of semantics.
        formal = "FAIL" if structural_complete else "NOT_MEASURED"
    elif semantic["status"] != "EVALUATED":
        formal = "NOT_MEASURED"
    elif semantic["verdict"] != "PASS":
        formal = "FAIL"
    elif not structural_complete:
        formal = "NOT_MEASURED"
    elif structural_total >= PASS_TOTAL:
        formal = "PASS"
    elif structural_total >= PARTIAL_TOTAL:
        formal = "PARTIAL"
    else:
        formal = "FAIL"

    return {
        "case": case,
        "profile": artifact.get("profile"),
        "steps": steps,
        "step_state": step_state["steps"],
        "criteria": criteria,
        "structural_total": structural_total,
        "structural_measured_total": measured_total,
        "structural_measured_max": measured_max,
        "structural_complete": structural_complete,
        "structural_missing": [name for name, c in criteria.items() if c["score"] is None],
        "structural_verdict": structural_verdict,
        "verdict": formal,
        "semantic": semantic,
        "formal_eligible": formal_eligible,
        "ineligibility": ineligibility,
        "provenance_blocked": provenance_blocked,
        "failure_attribution": ("provenance:no_verifiable_evidence" if provenance_blocked
                                else "answer" if formal == "FAIL" else None),
        "unmeasured_criteria": unmeasured,
        "gaps": gap_state,
        "artefacts": {
            "claims": len(claims),
            "pack_spans": len(spans),
            "verified_spans": sum(1 for s in spans if s["verified"]),
            "unverified_spans": sum(1 for s in spans if not s["verified"]),
            "citations_valid": len(citations["valid"]),
            "citations_invalid": len(citations["invalid"]),
        },
        "thresholds": {"pass_total": PASS_TOTAL, "partial_total": PARTIAL_TOTAL,
                       "min_mean_c245": MIN_MEAN_C245,
                       "min_mean_total": MIN_MEAN_TOTAL},
    }


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--corpus", type=Path,
                        help="frozen corpus root used to resolve C3 anchor lines")
    parser.add_argument("--packs", type=Path, help="session packs directory")
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--semantic", type=Path,
                        help="independent semantic evaluation JSON for this case")
    parser.add_argument("--registry", type=Path,
                        help="trusted evaluator registry (default: cases/evaluators.json)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        materials = load_materials(args.materials)
        artifact, answer_record = load_answer(args.answers, args.case, args.profile)
        pack, pack_record = load_pack(args.answers, args.case, args.profile, args.packs)
        binding = bind_answer_to_pack(artifact, pack_record)

        try:
            evaluator = load_json(args.semantic) if args.semantic else None
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise GradingError(f"semantic evaluation is not readable JSON: {error}") from error
        if isinstance(evaluator, dict) and evaluator.get("case") not in (None, args.case):
            raise GradingError(
                f"semantic evaluation is for case {evaluator.get('case')}, not {args.case}"
            )
        registry_path = args.registry or (args.labels / "cases" / "evaluators.json")
        if args.semantic and not registry_path.is_file():
            raise GradingError(
                f"semantic evaluation supplied but no evaluator registry exists at "
                f"{registry_path}; an unregistered evaluator cannot grade formally"
            )
        registry = load_json(registry_path) if registry_path.is_file() else None

        manifest_path = args.labels / "scope-manifest.json"
        if not manifest_path.is_file():
            manifest_path = args.labels / "cases" / "scope-manifest.json"
        manifest = load_json(manifest_path)
        anchors = {a["id"]: a["path"] for a in manifest["source_anchors"]}
        gold = []
        for entry in manifest["case_spans"].get(args.case, []):
            if not isinstance(entry, dict) or "anchor" not in entry:
                raise GradingError(
                    f"case {args.case} has an unauthored gold entry: {entry!r}")
            path = anchors[entry["anchor"]]
            for start, end in entry["spans"]:
                gold.append((path, (int(start), int(end))))
        if not gold:
            raise GradingError(f"no gold spans for case {args.case}")

        source_lines = {}
        if args.corpus is not None:
            for path, _span in gold:
                target = args.corpus / path
                if target.is_file() and path not in source_lines:
                    source_lines[path] = target.read_text(
                        encoding="utf-8", errors="replace").split("\n")
        if not source_lines:
            raise GradingError(
                "C3 anchoring needs --corpus so required steps can be located in the "
                "frozen source; refusing to guess anchors"
            )

        # Frozen question identity: the artifact must claim the frozen question,
        # and that claim must match the frozen case text.
        cases = load_json(args.labels / "cases" / "dev.json")["cases"]
        entry = next((item for item in cases if item["id"] == args.case), None)
        if entry is None:
            raise GradingError(f"case {args.case} is not in the frozen dev set")
        frozen_question_sha = sha256_bytes(entry["question"].encode("utf-8"))
        claimed = artifact.get("question") or {}
        formal_eligible = True
        ineligibility = None
        if claimed.get("formal") is not True or claimed.get("source") != "frozen-case":
            formal_eligible = False
            ineligibility = (
                "artifact does not declare the frozen case question as its source "
                f"(source={claimed.get('source')!r}, formal={claimed.get('formal')!r}); "
                "a diagnostic run cannot be reported as a frozen benchmark result"
            )
        elif claimed.get("sha256") != frozen_question_sha:
            formal_eligible = False
            ineligibility = (
                f"artifact question digest {claimed.get('sha256')} does not match the "
                f"frozen {args.case} question digest {frozen_question_sha}"
            )

        semantic = evaluate_semantic(
            artifact, pack, evaluator, binding, registry,
            answer_sha=answer_record["sha256"],
            pack_sha=pack_record["sha256"],
            question_sha=frozen_question_sha,
            case=args.case,
            materials_sha=sha256_bytes(args.materials.read_bytes()),
        )
        result = score_case(args.case, artifact, pack, materials, gold,
                            source_lines, semantic, formal_eligible, ineligibility)
        result["artefacts"]["answer"] = answer_record
        result["artefacts"]["pack"] = pack_record
        result["binding"] = binding
        result["materials"] = {
            "rubric_version": materials.get("rubric_version"),
            "c3_version": materials.get("c3_version"),
            "sha256": sha256_bytes(args.materials.read_bytes()),
        }
    except GradingError as error:
        print(json.dumps({"case": args.case, "profile": args.profile,
                          "status": "UNGRADABLE", "error": str(error)}, indent=1))
        return 3

    text = json.dumps(result, indent=1, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
