#!/usr/bin/env python3
"""r4_semantic_e2e.py — end-to-end proof of the semantic-evaluation pipeline.

Two halves, both exercised through the real code paths:

A. Three arms on the frozen cases. Real controller, real adapter, real
   Responses-API payloads, frozen question identity, boundary-accounted reads,
   then `packet → loopback judge → accept → grader` for every run. It proves
   the mechanics (binding, accounting, plan chain, accepted evaluations) — the
   mock transports' answers are NOT real answer quality.

B. Crafted fixtures on a real frozen case (D11), built from frozen inputs:
     correct              grounded claim, verified pack      -> formal PASS
     contradicted         valid citation, wrong content      -> semantic FAIL
     runtime-assertion    asserts deployed behaviour         -> C4 zero, no PASS
     missing-evidence     claim without any citation         -> never PASS
     unsupported-citation valid hash, span says otherwise     -> semantic FAIL

The loopback judge is a deterministic stand-in, explicitly labelled synthetic.
It reads only the packet (no gold, no checklist, no corpus browsing) and its
rule is documented in `loopback_response`. It is NOT an independent semantic
evaluator and cannot support a benchmark claim.

Usage:
  r4_semantic_e2e.py --adapter target/debug/r4-adapter --corpus CORPUS \
      --work WORKDIR [--cases D01,D02] [--skip-arms]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = HERE.parent
sys.path.insert(0, str(HERE))

import r4_bench_driver as drv          # noqa: E402
import r4_semantic_evaluator as se     # noqa: E402

EVALUATOR_ID = "loopback-grounded-v1"
PROFILE = "arm-c"
STOP_TOKENS = {
    "then", "than", "that", "this", "with", "switch", "switches", "handles",
    "handle", "assigns", "assign", "calls", "call", "uses", "using", "from",
    "into", "when", "where", "which", "also", "only", "case", "cases",
    "excerpt", "lines", "line", "source", "span", "method", "returns",
    "return", "sets", "setting", "existing", "employee", "model", "fields",
    "field", "after", "before", "there", "their", "each", "every",
}
RUNTIME_MARKERS = ("deployed", "production", "database trigger", "is running",
                   "committed to the database", "triển khai", "đang chạy")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def frozen_question(labels: Path, case: str) -> dict:
    dev = json.loads((labels / "cases" / "dev.json").read_text(encoding="utf-8"))
    text = next(item["question"] for item in dev["cases"] if item["id"] == case)
    return {"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def frozen_case_span(labels: Path, corpus: Path, case: str) -> tuple[str, int, int]:
    manifest = json.loads((labels / "scope-manifest.json").read_text(encoding="utf-8"))
    anchors = {a["id"]: a["path"] for a in manifest["source_anchors"]}
    entry = manifest["case_spans"][case][0]
    path = anchors[entry["anchor"]]
    start, end = entry["spans"][0]
    if not (corpus / path).is_file():
        raise SystemExit(f"frozen corpus has no {path}")
    return path, int(start), int(end)


# ------------------------------------------------------- crafted fixtures

def fixture_pack(directory: Path, corpus: Path, case: str, items: list[dict],
                 gaps: list | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pack = {"schema_version": 1, "status": "ok", "items": [], "gaps": gaps or [],
            "deduplicated": 0}
    for path, start, end in items:
        lines = (corpus / path).read_text(encoding="utf-8").split("\n")
        pack["items"].append({
            "project": "hrm", "relative_path": path,
            "sha256": sha(corpus / path), "verification": "verified",
            "range": {"kind": "text", "start_line": start, "end_line": end + 1,
                      "start_byte_offset": 0, "end_byte_offset": 0,
                      "start_byte_column": 0, "end_byte_column": 0},
            "content": "\n".join(lines[start - 1:end]),
        })
    path = directory / f"pack-{case}-{PROFILE}.json"
    raw = json.dumps(pack, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"pack-{case}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    return path


def fixture_answer(directory: Path, case: str, claims: list[dict], text: str,
                   pack_sha: str, labels: Path, abstain: bool = False,
                   gaps: list[str] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    question = frozen_question(labels, case)
    artifact = {
        "case": case, "profile": PROFILE, "status": "OK", "error": None,
        "question": {"sha256": question["sha256"], "source": "frozen-case",
                     "formal": True},
        "pack": {"sha256": pack_sha, "kind": "evidence"},
        "claims": claims,
        "gaps": gaps or [],
        "transcript_tools": ["search", "evidence", "verify", "submit_answer"],
        "final": {"answer": text, "abstain": abstain, "claims": claims,
                  "gaps": gaps or []},
    }
    path = directory / f"answer-{case}-{PROFILE}.json"
    raw = json.dumps(artifact, ensure_ascii=False, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"answer-{case}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    return path


def citation(path: str, start: int, end: int, corpus: Path) -> dict:
    return {"path": path, "start_line": start, "end_line": end,
            "sha256": sha(corpus / path)}


def significant_tokens(text: str) -> set[str]:
    tokens = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text or "")}
    return {t for t in tokens if t.lower() not in STOP_TOKENS
            and not t.lower().startswith("http")}


def loopback_response(packet: dict) -> dict:
    """Deterministic stand-in judge. NOT an independent evaluator.

    Rule (one rule, deliberately simple and stated): a claim is supported when
    it has at least one citation that (a) resolves inside the frozen corpus,
    (b) matches the frozen source hash, and (c) whose span text contains every
    significant identifier the claim asserts, with no unhedged runtime or
    deployment assertion. A claim that names an identifier absent from its
    cited span is unsupported even when the hash and range are valid — that is
    exactly the failure the semantic layer exists to catch.
    """
    cited = {(s["path"], s["start_line"], s["end_line"]): s
             for s in packet["cited_spans"]}
    assessments = []
    for claim in packet["answer"]["claims"]:
        keys = [(c["path"], c["start_line"], c["end_line"])
                for c in claim.get("citations") or []]
        spans = [cited[key] for key in keys if key in cited]
        text = claim.get("text") or ""
        reason = None
        if not keys:
            reason = "claim carries no citation"
        elif not spans:
            reason = "citation does not resolve to a packet span"
        elif not all(span["hash_ok"] for span in spans):
            reason = "citation hash does not match the frozen source"
        elif not any(span["resolved"] for span in spans):
            reason = "cited span could not be read from the frozen corpus"
        else:
            content = "\n".join(span["content"] or "" for span in spans)
            missing = sorted(token for token in significant_tokens(text)
                             if token not in content)
            if missing:
                reason = f"cited span does not contain {missing}"
            else:
                lowered = text.lower()
                hit = next((marker for marker in RUNTIME_MARKERS
                            if marker in lowered
                            and not any(hedge in lowered for hedge in
                                        ("source", "not observed", "unknown",
                                         "only", "không", "chưa"))), None)
                if hit:
                    reason = f"unhedged runtime/deployment assertion ({hit!r})"
        assessments.append({
            "index": claim["index"],
            "judgement": "unsupported" if reason else "supported",
            "reason": reason or "every asserted identifier is present in the cited span",
        })

    unsupported = [a for a in assessments if a["judgement"] == "unsupported"]
    claims = assessments
    pack_gaps = packet["evidence"].get("gaps") or {}
    answer_gaps = packet["answer"].get("gaps") or []
    abstain = packet["answer"].get("abstain") is True
    if not unsupported:
        verdict = "PASS"
        rationale = "every claim is grounded in the text of a frozen, hash-matching cited span"
    else:
        verdict = "FAIL"
        rationale = (f"{len(unsupported)}/{len(claims)} claim(s) not supported by the "
                     "cited span text")
    claim_correctness = 2 if not unsupported else 0
    citation_support = 2 if not unsupported else (1 if any(
        c.get("citations") for c in packet["answer"]["claims"]) else 0)
    if abstain:
        completeness = 2 if answer_gaps else 1
    else:
        completeness = 2 if any(not a["judgement"] == "unsupported" for a in claims) or not claims else 1
        completeness = max(completeness, 1)
    uncertainty = 2 if all(
        not (any(m in (c.get("text") or "").lower() for m in RUNTIME_MARKERS))
        for c in packet["answer"]["claims"]) else 0
    abstention = 2
    if pack_gaps and not answer_gaps and not abstain:
        abstention = 0
    if abstain and not answer_gaps and pack_gaps:
        abstention = 1
    return {
        "packet_sha256": packet["packet_sha256"],
        "verdict": verdict,
        "rationale": rationale,
        "claims": claims,
        "dimensions": {
            "claim_correctness": {"score": claim_correctness, "rationale": rationale},
            "citation_support": {"score": citation_support,
                                 "rationale": "cited-span containment check"},
            "completeness": {"score": completeness,
                             "rationale": "answer covers the deliverable evidence"},
            "uncertainty": {"score": uncertainty,
                            "rationale": "no unhedged runtime/deployment assertion"},
            "abstention": {"score": abstention,
                           "rationale": "declared gaps match the pack gaps"},
        },
    }


def contrived_pass_evaluation(packet: dict, work: Path, tag: str) -> Path:
    """A counterfactual evaluator that PASSes every claim.

    Used only to answer one question: would the same frozen artifacts reach a
    formal PASS if the semantic layer were taken out of the decision? If yes,
    the grounded FAIL in the real row is what the semantic layer contributed.
    This is not an evaluation of the answer's meaning.
    """
    claims = packet["answer"]["claims"]
    response = {
        "packet_sha256": packet["packet_sha256"],
        "verdict": "PASS",
        "rationale": "contrived counterfactual: every claim declared supported",
        "claims": [{"index": c["index"], "judgement": "supported",
                    "reason": "contrived"} for c in claims],
        "dimensions": {name: {"score": 2, "rationale": "contrived"}
                       for name in se.DIMENSIONS},
    }
    document = se.validate_response(packet, response)
    path = work / "evaluator" / tag / "contrived-pass.json"
    write_json(path, document)
    return path


def evaluate(answers: Path, corpus: Path, case: str, profile: str, work: Path,
             registry: Path, tag: str) -> dict:
    """packet -> loopback judge -> accept -> grader, all on frozen files."""
    packet = se.build_packet(answers, LABELS, corpus, case, profile, EVALUATOR_ID)
    packet_path = work / "evaluator" / tag / "packet.json"
    response_path = work / "evaluator" / tag / "response.json"
    evaluation_path = work / "evaluator" / tag / "evaluation.json"
    write_json(packet_path, packet)
    write_json(response_path, loopback_response(packet))
    document = se.accept(packet_path, response_path, evaluation_path)
    contrived = None
    if packet["answer"]["claims"]:
        contrived = drv.grade(
            answers, LABELS, case, profile, corpus=corpus,
            materials=LABELS / "cases" / "c3-steps.json",
            semantic=contrived_pass_evaluation(packet, work, tag),
            registry=registry)
    return {
        "contrived_pass": contrived,
        "packet_sha256": packet["packet_sha256"],
        "packet_inputs": packet["inputs"],
        "evaluation": {
            "verdict": document["verdict"],
            "evaluator_id": document["evaluator_id"],
            "supported": document["supported_claims"],
            "unsupported": document["unsupported_claims"],
            "dimensions": {k: v["score"] for k, v in document["dimensions"].items()},
        },
        "grading": drv.grade(answers, LABELS, case, profile, corpus=corpus,
                             materials=LABELS / "cases" / "c3-steps.json",
                             semantic=evaluation_path, registry=registry),
        "grading_without_evaluator": drv.grade(
            answers, LABELS, case, profile, corpus=corpus,
            materials=LABELS / "cases" / "c3-steps.json"),
    }


def run_fixtures(corpus: Path, work: Path, registry: Path) -> list[dict]:
    case = "D11"
    path, start, end = frozen_case_span(LABELS, corpus, case)
    gold = citation(path, start, end, corpus)
    decoy_start = max(start, end - 2)
    decoy = citation(path, decoy_start, decoy_start + 1, corpus)
    rows = []

    def run(name: str, claims: list[dict], text: str, items: list[tuple[str, int, int]],
            expect_semantic: str, expect_formal: str, abstain: bool = False,
            gaps: list[str] | None = None, pack_gaps: list | None = None) -> None:
        directory = work / "fixtures" / name
        pack = fixture_pack(directory, corpus, case, items, gaps=pack_gaps)
        fixture_answer(directory, case, claims, text, sha(pack), LABELS,
                       abstain=abstain, gaps=gaps)
        rejected = None
        try:
            result = evaluate(directory, corpus, case, PROFILE, work, registry,
                              f"fixture-{name}")
            observed_semantic = result["evaluation"]["verdict"]
            grading = result["grading"]
            packet_sha = result["packet_sha256"]
            inputs = result["packet_inputs"]
        except se.EvaluatorError as error:
            # A claim-free answer cannot be semantically evaluated; the
            # structural layer governs. This refusal is the intended behaviour.
            rejected = str(error)
            grading = drv.grade(directory, LABELS, case, PROFILE, corpus=corpus,
                                 materials=LABELS / "cases" / "c3-steps.json")
            observed_semantic = "REFUSED"
            packet_sha = None
            inputs = None
        rows.append({
            "fixture": name,
            "expected_semantic": expect_semantic,
            "observed_semantic": observed_semantic,
            "expected_formal": expect_formal,
            "observed_formal": grading.get("verdict"),
            "structural_measured_total": grading.get("structural_measured_total"),
            "structural_complete": grading.get("structural_complete"),
            "criteria": {k: v.get("score") for k, v in
                         (grading.get("criteria") or {}).items()},
            "provenance_blocked": grading.get("provenance_blocked"),
            "refusal": rejected,
            "packet_sha256": packet_sha,
            "inputs": inputs,
            "formal_with_contrived_pass": (
                (result.get("contrived_pass") or {}).get("verdict")
                if rejected is None else None),
        })

    grounded = ("UpdateEmployeeAttr switches on model.Attr, handles Avatar, "
                "then SaveChanges")
    run("correct",
        [{"text": grounded, "citations": [gold]}], grounded,
        [(path, start, end)], "PASS", "PASS")

    # Same citation and structure, but the claim asserts a branch the span does
    # not contain: a valid hash must not be able to carry an unsupported claim.
    unsupported_text = ("UpdateEmployeeAttr switches on model.Attr, handles Avatar "
                        "and Email, then SaveChanges")
    run("contradicted",
        [{"text": unsupported_text, "citations": [gold]}], unsupported_text,
        [(path, start, end)], "FAIL", "FAIL")

    runtime_text = ("UpdateEmployeeAttr is deployed in production and commits to the "
                    "database on every call")
    run("runtime-assertion",
        [{"text": runtime_text, "citations": [gold]}], runtime_text,
        [(path, start, end)], "FAIL", "FAIL")

    run("missing-evidence",
        [{"text": "UpdateEmployeeAttr updates the employee record", "citations": []}],
        "UpdateEmployeeAttr updates the employee record", [(path, start, end)],
        "FAIL", "FAIL")

    # A real, verified span that says something else: the citation is valid,
    # the support is not.
    run("unsupported-citation",
        [{"text": "SaveChanges is called on the DbContext for WorkLog entities",
              "citations": [decoy]}],
        "SaveChanges is called on the DbContext for WorkLog entities",
        [(path, start, end), (path, decoy["start_line"], decoy["end_line"])],
        "FAIL", "FAIL")

    # A citation that reaches past the delivered pack span: valid start line,
    # end outside the pack. The structural gate must refuse it too (overlap is
    # not delivery), so there is no path to a formal PASS.
    wide = citation(path, start, end + 25, corpus)
    run("citation-wider-than-pack",
        [{"text": grounded, "citations": [wide]}], grounded,
        [(path, start, end)], "FAIL", "FAIL")

    # A correct abstention with a declared pack gap: honest, structural C5 full,
    # but a claim-free answer is semantically refused and never a formal PASS.
    run("abstention-declared-gap", [], "Cannot determine from the delivered evidence.",
        [(path, start, end)], "REFUSED", "FAIL", abstain=True,
        gaps=["missing_required_lines"],
        pack_gaps=[{"code": "missing_required_lines", "path": path}])
    return rows


def run_arms(adapter: Path, corpus: Path, work: Path, registry: Path,
             cases: list[str]) -> list[dict]:
    rows = []
    for case in cases:
        identity = drv.resolve_question(LABELS, case, None, False)
        for profile in ("arm-a", "arm-b", "arm-c"):
            plan_path = None
            plan_usage = None
            if profile == "arm-b":
                planner = drv.MockPlanTransport(
                    model="mock-plan", instructions=drv.PLAN_INSTRUCTIONS,
                    question=identity["text"], hints=["Services/HRIS", "Webs/HRM"],
                    corpus_marker=str(corpus.resolve()),
                    question_sha256=identity["sha256"],
                    question_source=identity["source"], question_formal=True)
                prov = drv.generate_plan(planner, work / "plans", case, identity["text"])
                plan_path = Path(prov["path"])
                plan_usage = {"requests": len(planner.requests),
                              "usage": prov["usage"],
                              "sha256_canonical": prov["sha256_canonical"]}
            transport = drv.MockTransport(
                model="mock-model", instructions=drv.ANSWER_INSTRUCTIONS,
                profile=profile, question=identity["text"],
                question_sha256=identity["sha256"],
                question_source=identity["source"], question_formal=True,
                corpus_marker=str(corpus.resolve()))
            controller = drv.AdapterController(
                adapter, work / "sessions" / f"{case}-{profile}", corpus,
                profile, case, plan_path)
            controller.create()
            started = time.time()
            report = drv.drive(controller, transport, case, profile, work / "answers")
            elapsed = time.time() - started
            result = evaluate(work / "answers", corpus, case, profile, work,
                              registry, f"{case}-{profile}")
            receipt = json.loads(
                (work / "sessions" / f"{case}-{profile}" / "receipt.json").read_text())
            delta_sum = sum(e.get("read_ops_delta") or 0 for e in receipt["history"])
            grading = result["grading"]
            rows.append({
                "case": case, "profile": profile, "elapsed_s": round(elapsed, 2),
                "violations": report["violations"],
                "answer_status": (report.get("frozen_answer") or {}).get("status"),
                "pack_bound": (report.get("frozen_answer") or {}).get("pack_bound"),
                "plan_chain_ok": (report.get("plan_binding") or {}).get("chain_ok"),
                "plan_usage": plan_usage,
                "read_deltas_reconcile": delta_sum == (receipt.get("read_ops") or 0),
                "packet_sha256": result["packet_sha256"],
                "packet_inputs": result["packet_inputs"],
                "semantic": result["evaluation"],
                "semantic_status": (grading.get("semantic") or {}).get("status"),
                "formal": grading.get("verdict"),
                "formal_with_contrived_pass": (result.get("contrived_pass") or {}).get("verdict"),
                "structural_measured_total": grading.get("structural_measured_total"),
                "structural_complete": grading.get("structural_complete"),
                "formal_without_evaluator": result["grading_without_evaluator"].get("verdict"),
            })
            print(f"{case} {profile:6} {elapsed:5.2f}s semantic={result['evaluation']['verdict']:4} "
                  f"formal={grading.get('verdict'):12} "
                  f"struct={grading.get('structural_measured_total')} "
                  f"viol={len(report['violations'])}")
    return rows


def summarise(arm_rows: list[dict], fixture_rows: list[dict]) -> dict:
    arm_b = [r for r in arm_rows if r["profile"] == "arm-b"]
    fixture_ok = [
        r for r in fixture_rows
        if r["observed_semantic"] == r["expected_semantic"]
        and r["observed_formal"] == r["expected_formal"]
    ]
    return {
        "arms": {
            "runs": len(arm_rows),
            "violations": sum(len(r["violations"]) for r in arm_rows),
            "answers_ok": sum(1 for r in arm_rows if r["answer_status"] == "OK"),
            "packs_bound": sum(1 for r in arm_rows if r["pack_bound"]),
            "read_deltas_reconcile": sum(1 for r in arm_rows if r["read_deltas_reconcile"]),
            "arm_b_runs": len(arm_b),
            "arm_b_plan_chain_ok": sum(1 for r in arm_b if r["plan_chain_ok"]),
            "packets_built": sum(1 for r in arm_rows
                                 if isinstance(r["packet_sha256"], str)
                                 and len(r["packet_sha256"]) == 64),
            "evaluations_accepted": sum(1 for r in arm_rows
                                        if r["semantic_status"] == "EVALUATED"),
            "semantic_status": dict(Counter(r["semantic_status"] for r in arm_rows)),
            "formal_with_loopback": dict(Counter(r["formal"] for r in arm_rows)),
            "formal_without_evaluator": dict(
                Counter(r["formal_without_evaluator"] for r in arm_rows)),
            "any_formal_pass_without_evaluator": any(
                r["formal_without_evaluator"] == "PASS" for r in arm_rows),
            # How many runs a permissive (contrived-PASS) evaluator would have
            # lifted, i.e. what the grounded semantic layer is actually holding.
            "contrived_pass_flips": sum(
                1 for r in arm_rows
                if r["formal_with_contrived_pass"] not in (None, r["formal"])),
            "evaluator": EVALUATOR_ID,
            "evaluator_kind": "synthetic loopback judge (not an independent evaluator)",
        },
        "fixtures": {
            "runs": len(fixture_rows),
            "as_expected": len(fixture_ok),
            "all_as_expected": len(fixture_ok) == len(fixture_rows),
            "semantic_decisive": sum(
                1 for r in fixture_rows
                if r["formal_with_contrived_pass"] is not None
                and r["formal_with_contrived_pass"] != r["observed_formal"]),
            "rows": [{"fixture": r["fixture"],
                      "semantic": r["observed_semantic"],
                      "formal": r["observed_formal"],
                      "formal_with_contrived_pass": r["formal_with_contrived_pass"],
                      "ok": r in fixture_ok} for r in fixture_rows],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", type=Path,
                        default=Path("/home/masis/.cache/zvec-grep/"
                                     "r4-evidence-20261119/target/debug/r4-adapter"))
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--cases", help="comma-separated case ids (default: all authored)")
    parser.add_argument("--skip-arms", action="store_true")
    args = parser.parse_args(argv)

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    registry = work / "registries" / "loopback-fixture.json"
    write_json(registry, {
        "schema_version": 1,
        "note": ("test-local registry for a synthetic loopback judge; NOT a production "
                 "evaluator and never read by cases/evaluators.json"),
        "evaluators": {EVALUATOR_ID: {"trusted": True, "kind": "synthetic-loopback"}},
    })

    if args.cases:
        cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    else:
        manifest = json.loads((LABELS / "scope-manifest.json").read_text(encoding="utf-8"))
        unauthored = {key for key, value in manifest["case_spans"].items()
                      if not isinstance(value, list)
                      or (value and not isinstance(value[0], dict))}
        dev = json.loads((LABELS / "cases" / "dev.json").read_text(encoding="utf-8"))
        cases = [item["id"] for item in dev["cases"] if item["id"] not in unauthored]

    fixture_rows = run_fixtures(args.corpus, work, registry)
    arm_rows = [] if args.skip_arms else run_arms(
        args.adapter, args.corpus, work, registry, cases)

    summary = summarise(arm_rows, fixture_rows)
    summary["authored_cases"] = cases
    print("\n" + json.dumps(summary, indent=1))
    write_json(work / "receipts" / "arms.json", {"rows": arm_rows})
    write_json(work / "receipts" / "fixtures.json", {"rows": fixture_rows})
    write_json(work / "receipts" / "summary.json", summary)
    ok = summary["fixtures"]["all_as_expected"]
    if arm_rows:
        ok = ok and summary["arms"]["violations"] == 0
        ok = ok and not summary["arms"]["any_formal_pass_without_evaluator"]
        ok = ok and summary["arms"]["arm_b_plan_chain_ok"] == summary["arms"]["arm_b_runs"]
        ok = ok and summary["arms"]["evaluations_accepted"] == summary["arms"]["runs"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
