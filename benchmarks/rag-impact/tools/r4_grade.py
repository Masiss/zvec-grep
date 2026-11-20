#!/usr/bin/env python3
"""r4_grade.py — independent C1–C6 / C3 grading pipeline.

Runs in its own process. It consumes only:

  * the frozen answer artifact (claims + citations), hash-checked;
  * the frozen evidence/workflow pack, hash-checked;
  * evaluator-only materials: the frozen rubric/checklist text and the frozen
    gold spans (never sent to the answerer).

It produces a structured per-criterion result. Objective dimensions are
computed deterministically from the artifacts:

  C1 answerability      : does the pack contain the required steps at all?
  C2 citation support   : is every claim backed by a citation that resolves to
                          a *verified* pack span? (semantic support is flagged
                          for an evaluator, never assumed)
  C3 chain completeness : is every required step cited by the answer?
  C4 source/deployment  : are runtime/deployment assertions hedged? (heuristic,
                          downgrade-only)
  C5 honest gaps        : are pack gaps declared instead of papered over?
  C6 false negatives    : required steps present in the pack but omitted from
                          the answer.

Anything that genuinely needs semantic judgement is reported as
`NEEDS_EVALUATOR`; with no evaluator the case is `NOT_MEASURED`, never PASS.
A missing rubric, missing pack, or hash mismatch is a hard `UNGRADABLE` error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------- constants

SCORE_MAX = 2
PASS_TOTAL = 12
PARTIAL_TOTAL = 8
MIN_MEAN_C245 = 1.8
MIN_MEAN_TOTAL = 10.0

# Conservative runtime/deployment assertion detector (C4). Given the corpus is
# Vietnamese code with occasional English prose, both languages are covered.
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

ARTIFACT_SCHEMA = {
    "case": str,
    "profile": str,
    "final": dict,
}


class GradingError(Exception):
    """Hard failure: the case cannot be graded at all."""


# --------------------------------------------------------------- utilities

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) <= min(a[1], b[1])


def contains(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


# ------------------------------------------------------------ frozen inputs

def load_answer(answers_dir: Path, case: str, profile: str) -> tuple[dict, dict]:
    path = answers_dir / f"answer-{case}-{profile}.json"
    if not path.is_file():
        raise GradingError(f"frozen answer artifact is missing: {path}")
    raw = path.read_bytes()
    artifact = json.loads(raw)
    for field, kind in ARTIFACT_SCHEMA.items():
        if field not in artifact:
            raise GradingError(f"answer artifact missing field {field!r}")
        if not isinstance(artifact[field], kind):
            raise GradingError(f"answer artifact field {field!r} has the wrong type")
    record = {"path": str(path), "sha256": sha256_bytes(raw), "bytes": len(raw)}
    sidecar = path.with_suffix(".sha256")
    if sidecar.is_file():
        expected = sidecar.read_text(encoding="utf-8").strip().split()[0]
        if expected != record["sha256"]:
            raise GradingError(
                f"answer artifact hash mismatch: sidecar {expected} != actual {record['sha256']}"
            )
        record["sidecar_verified"] = True
    else:
        record["sidecar_verified"] = False
    return artifact, record


def load_pack(answers_dir: Path, case: str, profile: str, packs_dir: Path | None) -> tuple[dict, dict]:
    """Loads the frozen pack the answer was produced from."""
    candidates = []
    if packs_dir is not None:
        candidates.extend(sorted(packs_dir.glob("pack-*.json")))
    candidates.extend(sorted(answers_dir.glob(f"pack-{case}-{profile}*.json")))
    if not candidates:
        raise GradingError(
            f"no frozen pack found for {case}/{profile} (looked in {packs_dir or answers_dir})"
        )
    path = candidates[-1]
    raw = path.read_bytes()
    pack = json.loads(raw)
    record = {
        "path": str(path),
        "sha256": sha256_bytes(raw),
        "kind": "workflow" if "selected" in pack else "evidence",
    }
    sidecar = path.with_suffix(".sha256")
    if sidecar.is_file():
        expected = sidecar.read_text(encoding="utf-8").strip().split()[0]
        if expected != record["sha256"]:
            raise GradingError(
                f"pack hash mismatch: sidecar {expected} != actual {record['sha256']}"
            )
        record["sidecar_verified"] = True
    else:
        record["sidecar_verified"] = False
    return pack, record


def pack_spans(pack: dict) -> list[dict]:
    """Normalizes evidence and workflow packs into (path, range, sha) spans."""
    spans = []
    if "selected" in pack:
        for item in pack.get("selected") or []:
            spans.append({
                "path": item.get("path"),
                "range": (int(item.get("start_line", 0)), int(item.get("end_line", 0))),
                "sha256": item.get("source_sha256"),
                "verified": bool(item.get("verified", True)),
            })
    else:
        for item in pack.get("items") or []:
            rng = item.get("range")
            if isinstance(rng, dict):        # ContentRange::Text
                start = rng.get("start_line")
                end = rng.get("end_line")
                if rng.get("end_byte_column") == 0 and start is not None and end is not None:
                    end = max(start, end - 1)
            else:
                start, end = (rng or [None, None])[:2]
            spans.append({
                "path": item.get("relative_path") or item.get("path"),
                "range": (int(start or 0), int(end or 0)),
                "sha256": item.get("sha256") or item.get("source_sha256"),
                "verified": item.get("verification") in ("verified", "observed", "Verified", "Observed"),
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

# Tokens that carry no locating value for a required step.
NOISE_TOKENS = {
    "cite", "cites", "stated", "state", "state that", "do not", "no", "not",
    "the", "and", "or", "if", "when", "only", "must", "pack", "method",
    "scope", "source", "required", "step",
}


def step_tokens(step: str) -> list[str]:
    """Identifier-shaped tokens a required step talks about."""
    tokens = []
    for raw in IDENTIFIER_RE.findall(step):
        raw = raw.strip()
        if not raw or raw.lower() in NOISE_TOKENS:
            continue
        tokens.append(raw)
    return tokens


def locate_step(step: str, gold_spans: list[tuple[str, tuple[int, int]]],
                source_lines: dict[str, list[str]]) -> list[dict]:
    """Finds the lines of a required step's identifiers inside the case's gold
    spans. Anchoring to gold spans keeps this honest: a step is only
    machine-checkable where the frozen gold says the fact lives."""
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
                probe = token.strip()
                # Compare on the last identifier segment for member access.
                probe = probe.split(".")[-1].strip("()<>[] ")
                if not probe:
                    continue
                if probe in line:
                    anchors.append({
                        "token": token,
                        "path": path,
                        "range": (index, index),
                        "line": line.strip()[:160],
                    })
                    break
    # Collapse to one anchor per token to keep the checks readable.
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
    valid, invalid = [], []
    for claim_index, claim in enumerate(claims):
        text = str(claim.get("text", "")).strip()
        citations = claim.get("citations") or []
        if not citations:
            invalid.append({"claim": claim_index, "reason": "no citation", "text": text[:90]})
            continue
        for citation in citations:
            path = citation.get("path")
            start = citation.get("start_line")
            end = citation.get("end_line")
            sha = citation.get("sha256")
            if not path or start is None or end is None:
                invalid.append({"claim": claim_index, "reason": "incomplete citation"})
                continue
            match = None
            for span in spans:
                if span["path"] != path:
                    continue
                if overlaps(span["range"], (int(start), int(end))):
                    match = span
                    break
            if match is None:
                invalid.append({"claim": claim_index, "reason": "citation is outside the pack",
                                "path": path, "range": [start, end]})
                continue
            if sha and match["sha256"] and sha != match["sha256"]:
                invalid.append({"claim": claim_index, "reason": "citation hash mismatch",
                                "path": path})
                continue
            if not match["verified"]:
                invalid.append({"claim": claim_index, "reason": "pack span not verified",
                                "path": path})
                continue
            valid.append({"claim": claim_index, "path": path,
                          "range": [int(start), int(end)]})
    return {"valid": valid, "invalid": invalid,
            "claims": len(claims), "claims_with_valid_citation":
                len({entry["claim"] for entry in valid})}


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
        cited = False
        for claim in claims:
            for citation in claim.get("citations") or []:
                if not citation.get("path") or citation.get("start_line") is None:
                    continue
                c_range = (int(citation["start_line"]), int(citation["end_line"]))
                if any(citation["path"] == anchor["path"] and overlaps(c_range, anchor["range"])
                       for anchor in anchors):
                    cited = True
        results.append({"step": step["id"], "status": "ADDRESSABLE",
                        "in_pack": in_pack, "cited": cited})
    return {"steps": results}


def analyse_gaps(claims: list[dict], answer: dict, gaps: list[dict]) -> dict:
    declared = [g if isinstance(g, str) else g.get("code", "gap")
                for g in (answer.get("gaps") or [])]
    if gaps:
        declared.extend(g if isinstance(g, str) else g.get("code", "gap") for g in gaps)
    text = str(answer.get("answer", ""))
    abstain = bool(answer.get("abstain", False))
    hedge_terms = [term for term in HEDGES if term in text.lower()]
    return {"declared": declared, "abstain": abstain, "hedges": hedge_terms,
            "has_declaration": bool(declared) or abstain or bool(hedge_terms)}


def analyse_runtime_claims(text: str) -> dict:
    hits = []
    lowered = text.lower()
    for pattern, kind in RUNTIME_ASSERTIONS:
        for match in re.finditer(pattern, lowered):
            window = lowered[max(0, match.start() - 120):match.end() + 120]
            hedged = any(hedge in window for hedge in HEDGES)
            hits.append({"kind": kind, "match": match.group(0), "hedged": hedged})
    return {"assertions": hits, "unhedged": [h for h in hits if not h["hedged"]]}


# ----------------------------------------------------------------- scoring

def score_case(case: str, answer: dict, pack: dict, materials: dict,
               gold_spans: list[tuple[str, tuple[int, int]]],
               source_lines: dict[str, list[str]]) -> dict:
    spans = pack_spans(pack)
    claims = answer.get("claims") or []
    steps_material = materials["c3_steps"].get(case)
    if not steps_material:
        raise GradingError(f"materials have no C3 steps for case {case}")

    steps = []
    for index, step_text in enumerate(steps_material, start=1):
        steps.append({"id": index, "text": step_text,
                      "anchors": locate_step(step_text, gold_spans, source_lines)})

    citations = analyse_citations(claims, spans)
    step_state = analyse_steps(steps, spans, claims)
    gap_state = analyse_gaps(claims, answer, pack.get("gaps") or [])
    runtime = analyse_runtime_claims(str(answer.get("answer", "")))

    addressable = [s for s in step_state["steps"] if s["status"] == "ADDRESSABLE"]
    packed = [s for s in addressable if s["in_pack"]]
    cited = [s for s in addressable if s["cited"]]
    omitted = [s for s in packed if not s["cited"]]
    missing_from_pack = [s for s in addressable if not s["in_pack"]]

    criteria = {}

    # C1 — answerable from the pack.
    if not addressable:
        criteria["C1"] = {"score": None, "status": "NEEDS_EVALUATOR",
                          "rationale": "no required step could be anchored to gold spans"}
    else:
        ratio = len(packed) / len(addressable)
        criteria["C1"] = {
            "score": 2 if ratio == 1 else 1 if ratio > 0 else 0,
            "status": "MEASURED",
            "rationale": f"{len(packed)}/{len(addressable)} required steps are present in the pack",
        }

    # C2 — citation support (structure measured, semantics flagged).
    if not claims:
        criteria["C2"] = {"score": 0, "status": "MEASURED",
                          "rationale": "answer contains no claims"}
    else:
        ratio = citations["claims_with_valid_citation"] / citations["claims"]
        criteria["C2"] = {
            "score": 2 if not citations["invalid"] else 1 if ratio > 0 else 0,
            "status": "MEASURED_STRUCTURE_ONLY",
            "rationale": (
                f"{citations['claims_with_valid_citation']}/{citations['claims']} claims carry a "
                f"citation that resolves to a verified pack span; "
                f"{len(citations['invalid'])} invalid citation(s)"
            ),
            "semantic_support": "NEEDS_EVALUATOR",
            "invalid": citations["invalid"][:5],
        }

    # C3 — chain completeness (cited required steps).
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

    # C5 — honest gaps.
    if missing_from_pack:
        criteria["C5"] = {
            "score": 2 if gap_state["has_declaration"] else 0,
            "status": "MEASURED",
            "rationale": (
                f"{len(missing_from_pack)} required step(s) are absent from the pack; "
                + ("the answer declares the gap/abstains" if gap_state["has_declaration"]
                   else "the answer presents no gap declaration")
            ),
            "missing_steps": [s["step"] for s in missing_from_pack],
        }
    elif gap_state["has_declaration"]:
        criteria["C5"] = {"score": 2, "status": "MEASURED",
                          "rationale": "no pack gap, and the answer still declares its limits"}
    else:
        criteria["C5"] = {"score": 2, "status": "MEASURED",
                          "rationale": "no pack gap required a declaration"}

    # C6 — no false negatives (packed steps omitted from the answer).
    omissions = len(omitted)
    criteria["C6"] = {
        "score": 2 if omissions == 0 else 1 if omissions == 1 else 0,
        "status": "MEASURED",
        "rationale": f"{omissions} pack-supported required step(s) omitted from the answer",
        "omitted": [s["step"] for s in omitted],
    }

    measured = [c for c in criteria.values() if c["score"] is not None]
    total = sum(c["score"] for c in measured) if len(measured) == len(criteria) else None
    needs_evaluator = [name for name, c in criteria.items()
                       if c["status"] in ("NEEDS_EVALUATOR", "MEASURED_STRUCTURE_ONLY")]
    if len(measured) != len(criteria):
        verdict = "NOT_MEASURED"
    elif total >= PASS_TOTAL and not any(
            criteria[k]["score"] == 0 for k in ("C2", "C4", "C5")):
        verdict = "PASS"
    elif total >= PARTIAL_TOTAL:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"

    c245 = [criteria[k]["score"] for k in ("C2", "C4", "C5")
            if criteria[k]["score"] is not None]
    return {
        "case": case,
        "profile": answer.get("profile"),
        "steps": steps,
        "step_state": step_state["steps"],
        "criteria": criteria,
        "total": total,
        "verdict": verdict,
        "needs_evaluator": needs_evaluator,
        "means": {
            "C2_C4_C5": round(sum(c245) / len(c245), 2) if c245 else None,
            "total": round(total / 6, 2) if total is not None else None,
        },
        "thresholds": {"pass_total": PASS_TOTAL, "partial_total": PARTIAL_TOTAL,
                       "min_mean_c245": MIN_MEAN_C245, "min_mean_total": MIN_MEAN_TOTAL},
        "artefacts": {"claims": len(claims), "pack_spans": len(spans),
                      "citations_valid": len(citations["valid"]),
                      "citations_invalid": len(citations["invalid"])},
    }


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path,
                        help="directory with cases/dev.json and scope-manifest.json")
    parser.add_argument("--corpus", type=Path,
                        help="frozen corpus root used to resolve C3 anchor lines")
    parser.add_argument("--packs", type=Path, help="session packs directory")
    parser.add_argument("--materials", required=True, type=Path,
                        help="evaluator-only rubric + C3 steps JSON")
    parser.add_argument("--case", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        materials = load_materials(args.materials)
        answer, answer_record = load_answer(args.answers, args.case, args.profile)
        pack, pack_record = load_pack(args.answers, args.case, args.profile, args.packs)

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

        result = score_case(args.case, answer, pack, materials, gold, source_lines)
        result["artefacts"]["answer"] = answer_record
        result["artefacts"]["pack"] = pack_record
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
