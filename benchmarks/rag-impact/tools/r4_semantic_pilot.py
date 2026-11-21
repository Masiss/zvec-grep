#!/usr/bin/env python3
"""r4_semantic_pilot.py — controlled development pilot of the evaluator workflow.

Five synthetic scenarios over one small frozen world, with answers known
independently of the evaluator:

  complete      every claim true, every asked part stated      -> formal PASS
  incomplete    every claim true, one asked part missing       -> FAIL (omission)
  wrong         a claim the cited source contradicts           -> FAIL (unsupported)
  wrong-citation the citation does not contain the claim       -> FAIL (unsupported)
  stale         packed item has no content and the source moved -> FAIL (stale)

The pilot judge is a deterministic, scripted stand-in (`pilot_judge`). It reads
only the packet: the question's backticked required elements, the delivered
items, and the cited span text. It is NOT a production evaluator and its verdicts
are not answer-quality measurements; what it proves is that the workflow can
represent and grade "correct but incomplete" (SE-01) and "stale evidence"
(SE-02) distinctly from "correct" and "wrong".

The real grader is used unchanged, over a synthetic frozen world written to
`--work/world`. The pilot also grades each answer with a contrived always-PASS
evaluator, so structural-only acceptance can be compared with the grounded
verdict.

Usage: r4_semantic_pilot.py --work DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r4_bench_driver as drv       # noqa: E402
import r4_semantic_evaluator as se  # noqa: E402

EVALUATOR_ID = "pilot-scripted-v1"
CASE = "D90"
PROFILE = "arm-c"
PATH = "src/Widget.cs"
SOURCE = "\n".join([
    "class Widget {",
    "    public void Rename(string name) {",
    "        this.Name = name;",
    "    }",
    "    public void Save() {",
    "        Context.SaveChanges();",
    "    }",
    "}",
])
QUESTION = ("Report the method that renames the widget, the property it assigns, "
            "and the final call: `Rename`, `Name`, `SaveChanges`.")
REQUIRED = ["Rename", "Name", "SaveChanges"]  # independently fixed by the world

# Generic words that carry no evidence on their own; identifiers (Rename, Name,
# SaveChanges, Save, Context) are checked against the cited span text.
STOPWORDS = {
    "this", "that", "the", "and", "or", "is", "are", "with", "from", "into",
    "assigns", "assign", "assigned", "calls", "call", "called", "ends", "end",
    "final", "value", "incoming", "property", "method", "widget", "class",
    "public", "void", "string", "then", "only", "also", "every", "all",
}
RUNTIME_MARKERS = ("deployed", "production", "database trigger", "committed")
HEDGES = ("unknown", "not observed", "not established", "cannot be determined")


class PilotError(Exception):
    pass


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def significant(text: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text or ""))
    return {token for token in tokens if token.lower() not in STOPWORDS}


def pilot_judge(packet: dict) -> dict:
    """Scripted pilot judge. Documented rule, applied identically to all cases:
    a claim is supported when every significant token it asserts occurs in the
    text of a resolved, hash-matching cited span; a required element is missing
    when it does not occur in the answer text and in a supported claim."""
    cited = {(span["path"], span["start_line"], span["end_line"]): span
             for span in packet["cited_spans"]}
    claims = packet["answer"]["claims"]
    judged = []
    for claim in claims:
        keys = [(c["path"], c["start_line"], c["end_line"])
                for c in claim.get("citations") or []]
        spans = [cited[key] for key in keys if key in cited]
        reason = None
        if not keys:
            reason = "no citation"
        elif len(spans) != len(keys):
            reason = "citation is not in the packet"
        elif any(not span["in_pack"] for span in spans):
            reason = "citation is outside the delivered pack"
        elif any(span.get("stale_source") for span in spans):
            reason = "cited evidence is stale since freeze"
        elif any(not span["resolved"] or not span["hash_ok"] for span in spans):
            reason = "citation does not resolve to matching frozen text"
        else:
            content = "\n".join(span["content"] or "" for span in spans)
            missing = sorted(token for token in significant(claim.get("text", ""))
                             if token not in content)
            if missing:
                reason = f"cited text does not contain {missing}"
            else:
                lowered = (claim.get("text") or "").lower()
                hit = next((marker for marker in RUNTIME_MARKERS
                            if marker in lowered
                            and not any(hedge in lowered for hedge in HEDGES)), None)
                if hit:
                    reason = f"unhedged runtime/deployment assertion ({hit!r})"
        judged.append({"index": claim["index"],
                       "judgement": "unsupported" if reason else "supported",
                       "reason": reason or "all asserted identifiers occur in the cited span"})

    unsupported = [entry for entry in judged if entry["judgement"] == "unsupported"]
    supported_text = " ".join(claim.get("text", "") for claim, entry
                              in zip(claims, judged) if entry["judgement"] == "supported")
    answer_text = packet["answer"].get("text") or ""
    coverage_text = f"{answer_text}\n{supported_text}"
    omissions = [required for required in REQUIRED
                 if not re.search(rf"\b{re.escape(required)}\b", coverage_text)]
    completeness = 0 if omissions else 2
    claim_correctness = 0 if unsupported else 2
    citation_support = 2 if not unsupported else (
        1 if any(claim.get("citations") for claim in claims) else 0)
    pack_gaps = packet["evidence"].get("gaps") or {}
    abstention = 2 if not pack_gaps else 1
    verdict = "PASS" if not unsupported and not omissions else "FAIL"
    rationale = ("all claims are grounded and every asked element is stated"
                 if verdict == "PASS" else
                 f"{len(unsupported)} unsupported claim(s), {len(omissions)} omission(s)")
    return {
        "packet_sha256": packet["packet_sha256"],
        "verdict": verdict,
        "rationale": rationale,
        "claims": judged,
        "omissions": omissions,
        "dimensions": {
            "claim_correctness": {"score": claim_correctness, "rationale": rationale},
            "citation_support": {"score": citation_support,
                                 "rationale": "asserted identifiers vs cited text"},
            "completeness": {"score": completeness,
                             "rationale": "coverage of the question's required elements"},
            "uncertainty": {"score": 2, "rationale": "no unhedged runtime assertion"},
            "abstention": {"score": abstention,
                           "rationale": "pack gaps declared or absent"},
        },
    }


def contrived_pass(packet: dict, work: Path, tag: str) -> Path:
    claims = packet["answer"]["claims"]
    response = {
        "packet_sha256": packet["packet_sha256"],
        "verdict": "PASS",
        "rationale": "contrived counterfactual: every claim supported, nothing missing",
        "claims": [{"index": claim["index"], "judgement": "supported",
                    "reason": "contrived"} for claim in claims],
        "omissions": [],
        "dimensions": {name: {"score": 2, "rationale": "contrived"}
                       for name in se.DIMENSIONS},
    }
    document = se.validate_response(packet, response)
    path = work / "pilot" / tag / "contrived-pass.json"
    write_json(path, document)
    return path


def build_world(root: Path) -> dict:
    corpus = root / "corpus"
    labels = root / "labels"
    (corpus / "src").mkdir(parents=True, exist_ok=True)
    (corpus / PATH).write_text(SOURCE, encoding="utf-8")
    write_json(labels / "cases" / "dev.json",
               {"cases": [{"id": CASE, "question": QUESTION, "formal": True}]})
    write_json(labels / "cases" / "c3-steps.json", {
        "rubric_version": "pilot",
        "c3_version": "pilot",
        "provenance": "synthetic pilot world",
        "rubric": {
            "C1": {"name": "answerable from the pack"},
            "C2": {"name": "citation support"},
            "C3": {"name": "chain completeness"},
            "C4": {"name": "source vs deployment"},
            "C5": {"name": "honest gaps"},
            "C6": {"name": "no false negatives"},
        },
        "thresholds": {"pass_total": 12, "partial_total": 8},
        # Deliberately measures Rename and SaveChanges only: `Name` is asked in
        # the question but has no C3 row, mirroring the D07 step-3 gap.
        "c3_steps": {CASE: [
            "Cite `Rename` and the assignment it performs.",
            "Cite `SaveChanges` at the end.",
        ]},
    })
    write_json(labels / "scope-manifest.json", {
        "source_anchors": [{"id": "W1", "path": PATH}],
        "case_spans": {CASE: [{"anchor": "W1", "spans": [[1, 8]]}]},
    })
    (labels / "REPORT-CONTRACT.md").write_text("pilot contract", encoding="utf-8")
    return {"corpus": corpus, "labels": labels,
            "question_sha": sha_bytes(QUESTION.encode("utf-8"))}


def write_pack(directory: Path, corpus: Path, with_content: bool = True,
               stale: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    frozen = SOURCE
    if stale:
        (corpus / PATH).write_text(SOURCE + "\n// changed after freeze\n",
                                   encoding="utf-8")
    item = {
        "project": "pilot", "relative_path": PATH,
        "sha256": sha_bytes(frozen.encode("utf-8")), "verification": "verified",
        "range": {"kind": "text", "start_line": 1, "end_line": 8,
                  "start_byte_offset": 0, "end_byte_offset": 0,
                  "start_byte_column": 0, "end_byte_column": 0},
    }
    if with_content:
        item["content"] = frozen
    pack = {"schema_version": 1, "status": "ok", "items": [item], "gaps": [],
            "deduplicated": 0}
    path = directory / f"pack-{CASE}-{PROFILE}.json"
    raw = json.dumps(pack, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + f"  pack-{CASE}-{PROFILE}.json\n")
    return path


def write_answer(directory: Path, claims: list[dict], text: str,
                 pack_sha: str, question_sha: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    artifact = {
        "case": CASE, "profile": PROFILE, "status": "OK", "error": None,
        "question": {"sha256": question_sha, "source": "frozen-case",
                     "formal": True},
        "pack": {"sha256": pack_sha, "kind": "evidence"},
        "claims": claims, "gaps": [],
        "transcript_tools": ["search", "evidence", "verify", "submit_answer"],
        "final": {"answer": text, "abstain": False, "claims": claims, "gaps": []},
    }
    path = directory / f"answer-{CASE}-{PROFILE}.json"
    raw = json.dumps(artifact, ensure_ascii=False, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + f"  answer-{CASE}-{PROFILE}.json\n")
    return path


def citation(start: int, end: int) -> dict:
    return {"path": PATH, "start_line": start, "end_line": end,
            "sha256": sha_bytes(SOURCE.encode("utf-8"))}


def run_scenario(name: str, work: Path, world: dict, registry: Path,
                 claims: list[dict], text: str, expected: dict,
                 with_content: bool = True, stale: bool = False) -> dict:
    directory = work / "answers" / name
    pack = write_pack(directory, world["corpus"], with_content=with_content,
                      stale=stale)
    write_answer(directory, claims, text, sha_path(pack), world["question_sha"])
    packet = se.build_packet(directory, world["labels"], world["corpus"],
                             CASE, PROFILE, EVALUATOR_ID)
    write_json(work / "pilot" / name / "packet.json", packet)
    rejected = None
    try:
        response = pilot_judge(packet)
        write_json(work / "pilot" / name / "response.json", response)
        document = se.accept(work / "pilot" / name / "packet.json",
                             work / "pilot" / name / "response.json",
                             work / "pilot" / name / "evaluation.json",
                             corpus=world["corpus"])
        grading = drv.grade(directory, world["labels"], CASE, PROFILE,
                            corpus=world["corpus"],
                            materials=world["labels"] / "cases" / "c3-steps.json",
                            semantic=work / "pilot" / name / "evaluation.json",
                            registry=registry)
        observed_semantic = document["verdict"]
    except se.EvaluatorError as error:
        rejected = str(error)
        document = None
        observed_semantic = "REFUSED"
        grading = drv.grade(directory, world["labels"], CASE, PROFILE,
                            corpus=world["corpus"],
                            materials=world["labels"] / "cases" / "c3-steps.json")
    contrived = drv.grade(
        directory, world["labels"], CASE, PROFILE, corpus=world["corpus"],
        materials=world["labels"] / "cases" / "c3-steps.json",
        semantic=contrived_pass(packet, work, name), registry=registry)
    observed = {
        "semantic": observed_semantic,
        "unsupported": document and document["unsupported_claims"],
        "omissions": document and document["omissions"],
        "formal": grading.get("verdict"),
        "structural_measured_total": grading.get("structural_measured_total"),
        "contrived": contrived.get("verdict"),
        "stale_spans": sum(1 for span in packet["cited_spans"]
                           if span.get("stale_source")),
        "spans_with_content": sum(1 for span in packet["cited_spans"]
                                  if span.get("content") is not None),
    }
    # Assert the distinguishing evidence, not only the verdict: verdicts alone
    # cannot tell a correct-but-incomplete answer from a wrong one.
    checks = {}
    for key, want in expected.items():
        got = observed.get(key)
        checks[key] = (got == want)
    return {
        "scenario": name,
        "expected": expected,
        "observed": observed,
        "checks": checks,
        "criteria": {key: value.get("score") for key, value
                     in (grading.get("criteria") or {}).items()},
        "semantic": {
            "verdict": observed["semantic"],
            "unsupported": observed["unsupported"],
            "omissions": observed["omissions"],
            "dimensions": document and {k: v["score"] for k, v
                                        in document["dimensions"].items()},
        },
        "refusal": rejected,
        "ok": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args(argv)
    work = args.work
    shutil.rmtree(work, ignore_errors=True)
    world = build_world(work / "world")
    registry = work / "registries" / "pilot.json"
    write_json(registry, {
        "schema_version": 1,
        "note": "pilot-only registry for the scripted judge; not production",
        "evaluators": {EVALUATOR_ID: {"trusted": True, "kind": "synthetic-pilot"}},
    })

    complete_claims = [
        {"text": "Rename assigns this.Name", "citations": [citation(1, 3)]},
        {"text": "Save ends with SaveChanges()", "citations": [citation(5, 7)]},
    ]
    incomplete_claims = [
        {"text": "Rename assigns the incoming value", "citations": [citation(1, 3)]},
        {"text": "Save ends with SaveChanges()", "citations": [citation(5, 7)]},
    ]
    wrong_claims = [
        {"text": "Rename deletes the widget", "citations": [citation(1, 3)]},
    ]
    wrong_citation_claims = [
        {"text": "SaveChanges is called by Save", "citations": [citation(1, 3)]},
    ]

    rows = [
        run_scenario("complete", work, world, registry, complete_claims,
                     "Rename assigns Name; Save ends with SaveChanges.",
                     {"semantic": "PASS", "unsupported": [], "omissions": [],
                      "formal": "PASS", "structural_measured_total": 12,
                      "contrived": "PASS", "stale_spans": 0,
                      "spans_with_content": 2}),
        run_scenario("incomplete", work, world, registry, incomplete_claims,
                     "Rename assigns the incoming value; Save ends with SaveChanges.",
                     {"semantic": "FAIL", "unsupported": [], "omissions": ["Name"],
                      "formal": "FAIL", "structural_measured_total": 12,
                      "contrived": "PASS", "stale_spans": 0}),
        run_scenario("wrong", work, world, registry, wrong_claims,
                     "Rename deletes the widget.",
                     {"semantic": "FAIL", "unsupported": [0],
                      "omissions": ["Name", "SaveChanges"],
                      "formal": "FAIL", "contrived": "PARTIAL"}),
        run_scenario("wrong-citation", work, world, registry, wrong_citation_claims,
                     "SaveChanges is called by Save.",
                     {"semantic": "FAIL", "unsupported": [0],
                      "omissions": ["Rename", "Name"],
                      "formal": "FAIL", "contrived": "PARTIAL"}),
        run_scenario("stale", work, world, registry, complete_claims,
                     "Rename assigns Name; Save ends with SaveChanges.",
                     {"semantic": "FAIL", "unsupported": [0, 1], "omissions": [],
                      "formal": "FAIL", "contrived": "PASS",
                      "stale_spans": 2, "spans_with_content": 0},
                     with_content=False, stale=True),
    ]

    summary = {
        "scenarios": len(rows),
        "as_expected": sum(1 for row in rows if row["ok"]),
        "all_as_expected": all(row["ok"] for row in rows),
        "semantic_distinguishes": {
            "complete": next(row["observed"]["semantic"] for row in rows
                             if row["scenario"] == "complete"),
            "incomplete": next(row["observed"]["semantic"] for row in rows
                               if row["scenario"] == "incomplete"),
            "wrong": next(row["observed"]["semantic"] for row in rows
                          if row["scenario"] == "wrong"),
            "wrong-citation": next(row["observed"]["semantic"] for row in rows
                                   if row["scenario"] == "wrong-citation"),
            "stale": next(row["observed"]["semantic"] for row in rows
                          if row["scenario"] == "stale"),
        },
        "incomplete_has_omissions_without_unsupported": next(
            row["observed"]["omissions"] == ["Name"]
            and row["observed"]["unsupported"] == [] for row in rows
            if row["scenario"] == "incomplete"),
        "structural_only_would_accept_incomplete": next(
            row["observed"]["contrived"] for row in rows
            if row["scenario"] == "incomplete") == "PASS",
        "evaluator": EVALUATOR_ID,
        "evaluator_kind": "scripted pilot judge (not a production evaluator)",
    }
    write_json(work / "receipts" / "pilot-summary.json",
               {"summary": summary, "rows": rows})
    print(json.dumps({"summary": summary,
                      "rows": [{"scenario": row["scenario"],
                                "observed": row["observed"],
                                "ok": row["ok"]} for row in rows]}, indent=1))
    return 0 if summary["all_as_expected"] else 1


if __name__ == "__main__":
    sys.exit(main())
