#!/usr/bin/env python3
"""r4_grade_selftest.py — fixtures that prove the grader cannot be fooled.

Each fixture is a frozen artefact triple (answer + pack + sidecars) plus
evaluator-only materials. The grader runs in a SEPARATE process, exactly as in
a real run, and each expected outcome is asserted.

The control that matters most: an answer that cites a real, verified, in-pack
span while asserting the opposite of what the source says must never be a
formal PASS. Without an independent semantic evaluation the formal verdict is
NOT_MEASURED, and even a structurally perfect score cannot lift it to PASS.

Usage: r4_grade_selftest.py <corpus-root> <work-dir>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = HERE.parent
GRADER = HERE / "r4_grade.py"
MATERIALS = LABELS / "cases" / "c3-steps.json"

CASE = "D11"
PROFILE = "arm-c"
PATH = "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs"
GOLD = (866, 887)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


def file_sha(corpus: Path) -> str:
    return hashlib.sha256((corpus / PATH).read_bytes()).hexdigest()


def write_pack(directory: Path, corpus: Path, span: tuple[int, int],
               verification: str = "verified", gaps: list | None = None,
               mutate: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    start, end = span
    lines = (corpus / PATH).read_text(encoding="utf-8").split("\n")
    pack = {
        "schema_version": 1,
        "status": "ok",
        "items": [{
            "project": "hrm",
            "relative_path": PATH,
            "sha256": file_sha(corpus),
            "verification": verification,
            "range": {"kind": "text", "start_line": start, "end_line": end + 1,
                      "start_byte_offset": 0, "end_byte_offset": 0,
                      "start_byte_column": 0, "end_byte_column": 0},
            "content": "\n".join(lines[start - 1:end]),
        }],
        "gaps": gaps or [],
        "deduplicated": 0,
    }
    path = directory / f"pack-{CASE}-{PROFILE}.json"
    raw = json.dumps(pack, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    if mutate:
        path.write_bytes(path.read_bytes().replace(b"UpdateEmployeeAttr", b"X"))
    return path


def write_answer(directory: Path, claims: list[dict], answer_text: str,
                 abstain: bool = False, gaps: list[str] | None = None,
                 pack_sha: str | None = None, status: str = "OK",
                 sidecar: bool = True, sidecar_sha: str | None = None,
                 question: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pack_path = directory / f"pack-{CASE}-{PROFILE}.json"
    if pack_sha is None and pack_path.is_file():
        pack_sha = hashlib.sha256(pack_path.read_bytes()).hexdigest()
    artifact = {
        "case": CASE, "profile": PROFILE, "status": status,
        "final": {"answer": answer_text, "abstain": abstain,
                  "claims": claims, "gaps": gaps or []},
        "claims": claims,
        "gaps": gaps or [],
        "pack": {"sha256": pack_sha, "kind": "evidence"} if pack_sha else None,
        "question": question or {"sha256": frozen_question_sha(),
                                 "source": "frozen-case", "formal": True},
        "transcript_tools": ["search", "evidence"],
    }
    path = directory / f"answer-{CASE}-{PROFILE}.json"
    raw = json.dumps(artifact, ensure_ascii=False, indent=1).encode()
    path.write_bytes(raw)
    if sidecar:
        digest = sidecar_sha or hashlib.sha256(raw).hexdigest()
        (directory / f"answer-{CASE}-{PROFILE}.sha256").write_text(
            digest + "  " + path.name + "\n")
    return path


def citation(start: int, end: int, sha: str | None) -> dict:
    return {"path": PATH, "start_line": start, "end_line": end, "sha256": sha}


REGISTRY = None  # written per run into the work dir


def run_grader(answers: Path, corpus: Path, materials: Path | None = None,
               semantic: Path | None = None) -> tuple[int, dict]:
    args = [sys.executable, str(GRADER), "--answers", str(answers),
            "--labels", str(LABELS), "--corpus", str(corpus),
            "--materials", str(materials or MATERIALS),
            "--registry", str(REGISTRY),
            "--case", CASE, "--profile", PROFILE]
    if semantic is not None:
        args += ["--semantic", str(semantic)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(proc.stdout[:400], proc.stderr[:400])
        raise


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_question_sha() -> str:
    dev = json.loads((LABELS / "cases" / "dev.json").read_text(encoding="utf-8"))
    question = next(item["question"] for item in dev["cases"] if item["id"] == CASE)
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def semantic_file(directory: Path, verdict: str, rationale: str,
                  answers: Path | None = None,
                  evaluator_id: str = "fixture-evaluator",
                  override_inputs: dict | None = None,
                  **extra) -> Path:
    """Writes an evaluation bound to the frozen inputs of `answers`."""
    directory.mkdir(parents=True, exist_ok=True)
    inputs = {
        "case": CASE,
        "answer_sha256": sha256_path(answers / f"answer-{CASE}-{PROFILE}.json"),
        "pack_sha256": sha256_path(answers / f"pack-{CASE}-{PROFILE}.json"),
        "question_sha256": frozen_question_sha(),
        "rubric_sha256": sha256_path(MATERIALS),
    }
    if override_inputs:
        inputs.update(override_inputs)
    claims = json.loads((answers / f"answer-{CASE}-{PROFILE}.json").read_text())["claims"] \
        if answers is not None else []
    index = list(range(len(claims)))
    payload = {"case": CASE, "verdict": verdict, "rationale": rationale,
               "evaluator_id": evaluator_id, "inputs": inputs,
               "supported_claims": index if verdict == "PASS" else index[1:],
               "unsupported_claims": [] if verdict == "PASS" else index[:1]}
    payload.update(extra)
    path = directory / f"semantic-{evaluator_id}-{verdict}-{abs(hash((verdict, rationale, str(inputs))))}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def main() -> int:
    global REGISTRY
    corpus = Path(sys.argv[1])
    work = Path(sys.argv[2])
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    REGISTRY = work / "evaluators.json"
    REGISTRY.write_text(json.dumps({
        "schema_version": 1,
        "evaluators": {
            "fixture-evaluator": {"trusted": True, "kind": "fixture"},
            "untrusted-evaluator": {"trusted": False, "kind": "fixture"},
        },
    }), encoding="utf-8")
    sha = file_sha(corpus)
    good_claim = [{"text": "UpdateEmployeeAttr switches on model.Attr, handles Avatar, then SaveChanges",
                   "citations": [citation(GOLD[0], GOLD[1], sha)]}]
    good_text = "UpdateEmployeeAttr switches on model.Attr, handles Avatar, then SaveChanges."

    # ---------------------------------------------------------------- 1. NULL
    # The reviewer's reproduction: a wrong answer with a real, verified,
    # in-pack citation. Structurally perfect, semantically false.
    case = work / "wrong-but-cited"
    write_pack(case, corpus, GOLD)
    write_answer(case, [{"text": "This method silently deletes every employee and has no switch.",
                         "citations": [citation(GOLD[0], GOLD[1], sha)]}],
                 "This method silently deletes every employee and has no switch.")
    code, result = run_grader(case, corpus)
    check("wrong-but-cited: structural score may be high",
          (result.get("structural_measured_total") or 0) >= 8,
          json.dumps({k: v.get("score") for k, v in (result.get("criteria") or {}).items()}))
    check("wrong-but-cited: formal verdict is NOT_MEASURED, never PASS",
          result.get("verdict") == "NOT_MEASURED", str(result.get("verdict")))

    # The same answer, with a semantic evaluator that says it is unsupported.
    semantic = semantic_file(work / "sem", "FAIL",
                             "the cited span contains a switch on model.Attr and SaveChanges, "
                             "not a delete of every employee", answers=case,
                             unsupported_claims=[0], supported_claims=[])
    code, result = run_grader(case, corpus, semantic=semantic)
    check("wrong-but-cited + semantic FAIL: formal FAIL", result.get("verdict") == "FAIL",
          str(result.get("verdict")))
    check("wrong-but-cited + semantic FAIL: C2 is 0",
          (result.get("criteria") or {}).get("C2", {}).get("score") == 0,
          json.dumps((result.get("criteria") or {}).get("C2")))

    # ------------------------------------------------- 2. citation does not help
    case = work / "citeless-claim"
    write_pack(case, corpus, GOLD)
    write_answer(case, [{"text": "unsupported claim", "citations": []}], "unsupported claim")
    code, result = run_grader(case, corpus)
    check("claim without citation: C2 structural zero",
          (result.get("criteria") or {}).get("C2", {}).get("structural", {})
          .get("claims_with_valid_citation") == 0, json.dumps(result.get("criteria", {}).get("C2")))
    check("claim without citation: not PASS", result.get("verdict") != "PASS",
          str(result.get("verdict")))

    # --------------------------------------------- 3. citation outside the pack
    case = work / "outside-pack"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 2))
    write_answer(case, [{"text": "claim", "citations": [citation(GOLD[1] - 1, GOLD[1], sha)]}],
                 "claim")
    code, result = run_grader(case, corpus)
    check("citation outside the pack: invalid and no C2 credit",
          (result.get("criteria") or {}).get("C2", {}).get("structural", {})
          .get("invalid"), json.dumps((result.get("criteria") or {}).get("C2")))

    # ------------------------------------------------------- 4. stale citation
    case = work / "stale"
    write_pack(case, corpus, GOLD)
    write_answer(case, [{"text": "claim", "citations": [citation(GOLD[0], GOLD[1], "b" * 64)]}],
                 "claim")
    code, result = run_grader(case, corpus)
    invalid = (result.get("criteria") or {}).get("C2", {}).get("structural", {}).get("invalid") or []
    check("stale citation hash: rejected as invalid", bool(invalid), json.dumps(invalid))

    # ------------------------------------- 5. citation without any source hash
    case = work / "no-hash"
    write_pack(case, corpus, GOLD)
    write_answer(case, [{"text": "claim", "citations": [citation(GOLD[0], GOLD[1], None)]}],
                 "claim")
    code, result = run_grader(case, corpus)
    invalid = (result.get("criteria") or {}).get("C2", {}).get("structural", {}).get("invalid") or []
    check("citation without a source hash: rejected", bool(invalid), json.dumps(invalid))

    # ------------------------------------ 6. unverified pack (arm-a capability)
    case = work / "unverified-pack"
    write_pack(case, corpus, GOLD, verification="unverified")
    write_answer(case, good_claim, good_text)
    code, result = run_grader(case, corpus, semantic=semantic_file(
        work / "sem", "PASS", "the span supports both statements", answers=case,
        supported_claims=[0]))
    check("unverified pack: no C2 credit even with a passing evaluator",
          (result.get("criteria") or {}).get("C2", {}).get("score") == 0,
          json.dumps((result.get("criteria") or {}).get("C2")))
    check("unverified pack: formal verdict is not PASS", result.get("verdict") != "PASS",
          str(result.get("verdict")))

    # ------------------------------------------- 7. missing verification field
    case = work / "missing-verification"
    write_pack(case, corpus, GOLD)
    pack_path = case / f"pack-{CASE}-{PROFILE}.json"
    pack = json.loads(pack_path.read_text())
    pack["items"][0].pop("verification")
    raw = json.dumps(pack, indent=1).encode()
    pack_path.write_bytes(raw)
    (case / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + pack_path.name + "\n")
    write_answer(case, good_claim, good_text)
    code, result = run_grader(case, corpus)
    check("missing verification state is not assumed true",
          result.get("artefacts", {}).get("verified_spans") == 0,
          json.dumps(result.get("artefacts")))

    # -------------------------------------------------- 8. missing sidecar
    case = work / "no-sidecar"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text, sidecar=False)
    code, result = run_grader(case, corpus)
    check("answer without a sidecar is UNGRADABLE",
          result.get("status") == "UNGRADABLE" and code == 3, json.dumps(result)[:160])

    # ------------------------------------------------------ 9. tampered answer
    case = work / "tampered"
    write_pack(case, corpus, GOLD)
    path = write_answer(case, good_claim, good_text)
    path.write_bytes(path.read_bytes().replace(b"UpdateEmployeeAttr", b"SomethingElse"))
    code, result = run_grader(case, corpus)
    check("edited answer after freezing is UNGRADABLE",
          result.get("status") == "UNGRADABLE", json.dumps(result)[:160])

    # ------------------------------------------- 10. answer bound to other pack
    case = work / "wrong-binding"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text, pack_sha="c" * 64)
    code, result = run_grader(case, corpus)
    check("answer bound to a different pack is UNGRADABLE",
          result.get("status") == "UNGRADABLE" and "different pack" in result.get("error", ""),
          json.dumps(result)[:160])

    # -------------------------------------------- 11. swapped pack, same names
    case = work / "swapped-pack"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 3))  # new pack, answer unchanged
    code, result = run_grader(case, corpus)
    check("a pack swapped after freezing breaks the binding",
          result.get("status") == "UNGRADABLE", json.dumps(result)[:160])

    # ------------------------------------------------- 12. INVALID_OUTPUT status
    case = work / "invalid-output"
    write_pack(case, corpus, GOLD)
    write_answer(case, [], "prose only", status="INVALID_OUTPUT")
    code, result = run_grader(case, corpus)
    check("INVALID_OUTPUT artifact is UNGRADABLE, not a quality FAIL",
          result.get("status") == "UNGRADABLE", json.dumps(result)[:160])

    # --------------------------------------- 13. C5: engine gap is not a claim
    case = work / "engine-gap-silent-answer"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 1),
               gaps=[{"code": "missing_required_lines", "path": PATH}])
    write_answer(case, [{"text": "the method is fully explained",
                         "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}],
                 "The method is fully explained and complete.")
    code, result = run_grader(case, corpus)
    c5 = (result.get("criteria") or {}).get("C5", {})
    check("engine gap + silent answer: C5 is not maximum", c5.get("score", 2) < 2, json.dumps(c5))
    check("engine gap is recorded as the obligation, not as a declaration",
          c5.get("engine_gaps") and not c5.get("answer_declared_gaps"), json.dumps(c5))

    # --------------------------- 14. C5: answer declares the engine gap itself
    case = work / "engine-gap-declared"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 1),
               gaps=[{"code": "missing_required_lines", "path": PATH}])
    write_answer(case, [{"text": "the switch handles model.Attr",
                         "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}],
                 "The switch handles model.Attr; the remaining required lines were not in the pack.",
                 gaps=["missing_required_lines"])
    code, result = run_grader(case, corpus)
    c5 = (result.get("criteria") or {}).get("C5", {})
    check("declared engine gap scores full C5", c5.get("score") == 2, json.dumps(c5))

    # ------------------------------- 15. C5: abstention with an engine gap
    case = work / "engine-gap-abstain"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 1),
               gaps=[{"code": "missing_required_lines", "path": PATH}])
    write_answer(case, [], "Cannot determine from the selected evidence.",
                 abstain=True, gaps=["missing_required_lines"])
    code, result = run_grader(case, corpus)
    check("abstention that names the gap scores full C5",
          (result.get("criteria") or {}).get("C5", {}).get("score") == 2, json.dumps(result.get("criteria", {}).get("C5")))

    # ---------------------------- 16. C5: no gap but a fabricated declaration
    case = work / "fake-gap"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text, gaps=["the pack was incomplete"])
    code, result = run_grader(case, corpus)
    check("fabricated gap is not full C5",
          (result.get("criteria") or {}).get("C5", {}).get("score") == 1,
          json.dumps((result.get("criteria") or {}).get("C5")))

    # ------------------------ 17. C5: abstains although the pack is complete
    case = work / "unwarranted-abstain"
    write_pack(case, corpus, GOLD)
    write_answer(case, [], "I cannot answer.", abstain=True)
    code, result = run_grader(case, corpus)
    check("abstaining on a complete pack is C5 zero",
          (result.get("criteria") or {}).get("C5", {}).get("score") == 0,
          json.dumps((result.get("criteria") or {}).get("C5")))

    # ------------------------------------------- 18. semantic verdict invalid
    case = work / "semantic-invalid"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    for label, payload in [
        ("verdict outside the contract", {"verdict": "MAYBE", "rationale": "x"}),
        ("no rationale", {"verdict": "PASS", "rationale": ""}),
        ("no evaluator identity", {"verdict": "PASS", "rationale": "x", "evaluator_id": ""}),
        ("out-of-range claim index", {"verdict": "FAIL", "rationale": "x",
                                      "unsupported_claims": [99]}),
    ]:
        sem = work / "sem" / f"bad-{abs(hash(label))}.json"
        sem.parent.mkdir(parents=True, exist_ok=True)
        sem.write_text(json.dumps({"case": CASE, **payload}), encoding="utf-8")
        code, result = run_grader(case, corpus, semantic=sem)
        check(f"invalid semantic evaluation ({label}) cannot PASS",
              result.get("verdict") == "UNGRADABLE", str(result.get("verdict")))

    # ------------------------------- 19. valid semantic PASS + structure PASS
    case = work / "fully-supported"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    sem = semantic_file(work / "sem", "PASS", "the span contains every asserted step",
                        answers=case, supported_claims=[0])
    code, result = run_grader(case, corpus, semantic=sem)
    check("fully supported + semantic PASS: formal PASS",
          result.get("verdict") == "PASS", json.dumps(
              {k: v.get("score") for k, v in (result.get("criteria") or {}).items()}))
    check("fully supported: structural total is complete",
          result.get("structural_complete") is True and result.get("structural_total") == 12)

    # --------------------------------- 20. semantic PASS but structure fails
    case = work / "semantic-pass-structure-fail"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 1))
    write_answer(case, [{"text": "partial", "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}],
                 "partial answer")
    code, result = run_grader(case, corpus, semantic=semantic_file(
        work / "sem", "PASS", "the single cited line is accurate", answers=case,
        supported_claims=[0]))
    check("semantic PASS cannot rescue a structural failure",
          result.get("verdict") != "PASS", str(result.get("verdict")))

    # ------------------------------------------------ 21. required step missing
    case = work / "missing-step"
    write_pack(case, corpus, GOLD)
    claims = [{"text": "sets FullName and Email",
               "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}]
    write_answer(case, claims, "sets FullName and Email")
    code, result = run_grader(case, corpus)
    check("missing required step lowers C6/C3",
          (result.get("criteria") or {}).get("C6", {}).get("score", 2) < 2,
          json.dumps((result.get("criteria") or {}).get("C6")))

    # ------------------------ 22. semantic consistency: PASS + unsupported claim
    # The reviewer's reproduction: the verdict contradicts its own judgement.
    case = work / "pass-with-unsupported"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    sem = semantic_file(work / "sem", "PASS", "claims are supported", answers=case,
                        unsupported_claims=[0], supported_claims=[])
    code, result = run_grader(case, corpus, semantic=sem)
    check("semantic PASS carrying an unsupported claim is rejected",
          result.get("verdict") == "UNGRADABLE"
          and "unsupported" in (result.get("semantic", {}).get("error") or ""),
          json.dumps(result.get("semantic"))[:200])

    # -------------------- 23. semantic FAIL with no unsupported claim is invalid
    sem = semantic_file(work / "sem", "FAIL", "something feels off", answers=case,
                        supported_claims=[0])
    code, result = run_grader(case, corpus, semantic=sem)
    check("semantic FAIL that identifies no unsupported claim is invalid",
          result.get("verdict") == "UNGRADABLE", json.dumps(result.get("semantic"))[:200])

    # --------------------- 24. judgements must cover every claim exactly once
    case = work / "unjudged-claims"
    write_pack(case, corpus, GOLD)
    two = [good_claim[0], {"text": "second claim",
                           "citations": [citation(GOLD[0], GOLD[1], sha)]}]
    write_answer(case, two, "two claims")
    sem = semantic_file(work / "sem", "PASS", "both supported", answers=case,
                        supported_claims=[0])
    code, result = run_grader(case, corpus, semantic=sem)
    check("a PASS that leaves a claim unjudged is invalid",
          result.get("verdict") == "UNGRADABLE", json.dumps(result.get("semantic"))[:200])

    # --------------------------- 25. evaluator authority: unknown id, untrusted
    case = work / "evaluator-authority"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    for label, eid in [("unknown evaluator id", "totally-unknown"),
                       ("forged evaluator id", "fixture-evaluator "),
                       ("registered but untrusted", "untrusted-evaluator")]:
        sem = semantic_file(work / "sem", "PASS", "supported", answers=case,
                            evaluator_id=eid, supported_claims=[0])
        code, result = run_grader(case, corpus, semantic=sem)
        check(f"{label} cannot produce a formal verdict",
              result.get("verdict") == "UNGRADABLE", json.dumps(result.get("semantic"))[:160])

    # ------------------------- 26. provenance: evaluation bound to wrong inputs
    case = work / "wrong-eval-inputs"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    for field in ("answer_sha256", "pack_sha256", "question_sha256", "rubric_sha256", "case"):
        sem = semantic_file(work / "sem", "PASS", "supported", answers=case,
                            supported_claims=[0], override_inputs={field: "f" * 64})
        code, result = run_grader(case, corpus, semantic=sem)
        check(f"evaluation bound to a wrong {field} is rejected",
              result.get("verdict") == "UNGRADABLE",
              json.dumps(result.get("semantic"))[:160])

    # ---------------- 27. non-formal (diagnostic) answer never gets formal PASS
    case = work / "diagnostic-question"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text,
                 question={"sha256": frozen_question_sha(),
                           "source": "override-diagnostic", "formal": False})
    sem = semantic_file(work / "sem", "PASS", "supported", answers=case,
                        supported_claims=[0],
                        override_inputs={"question_sha256": frozen_question_sha()})
    code, result = run_grader(case, corpus, semantic=sem)
    check("diagnostic question cannot receive a formal PASS",
          result.get("verdict") == "NOT_MEASURED" and result.get("formal_eligible") is False,
          json.dumps({k: result.get(k) for k in ("verdict", "formal_eligible", "ineligibility")}))

    # ------------- 28. tampering formal:false -> true is caught by the digest
    case = work / "question-digest-mismatch"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text,
                 question={"sha256": "e" * 64, "source": "frozen-case", "formal": True})
    sem = semantic_file(work / "sem", "PASS", "supported", answers=case,
                        supported_claims=[0],
                        override_inputs={"question_sha256": "e" * 64})
    code, result = run_grader(case, corpus, semantic=sem)
    check("a claimed frozen question with the wrong digest is not formal",
          result.get("verdict") == "NOT_MEASURED" and result.get("formal_eligible") is False,
          json.dumps({k: result.get(k) for k in ("verdict", "formal_eligible")}))

    # ------------- 29. an evaluation cannot be replayed after the answer changes
    # Produce a valid PASS evaluation, then re-freeze a *different* answer. The
    # old evaluation now names an answer digest that no longer exists, so it
    # cannot carry over: this is what stops a favourable verdict being reused.
    case = work / "replayed-eval"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    sem = semantic_file(work / "sem", "PASS", "supported", answers=case,
                        supported_claims=[0])
    swapped = [{"text": "a different claim entirely",
                "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}]
    write_answer(case, swapped, "a different answer")
    code, result = run_grader(case, corpus, semantic=sem)
    check("a PASS evaluation cannot be replayed onto a different answer",
          result.get("verdict") == "UNGRADABLE"
          and "answer_sha256" in (result.get("semantic", {}).get("error") or ""),
          json.dumps(result.get("semantic"))[:200])

    # ------------------- 30. validation order: identity is checked first
    case = work / "unbound-eval"
    write_pack(case, corpus, GOLD)
    write_answer(case, good_claim, good_text)
    sem = work / "sem" / "unbound.json"
    sem.parent.mkdir(parents=True, exist_ok=True)
    sem.write_text(json.dumps({"case": CASE, "verdict": "PASS",
                               "rationale": "trust me",
                               "evaluator_id": "fixture-evaluator"}))
    code, result = run_grader(case, corpus, semantic=sem)
    check("an evaluation that binds no inputs is rejected",
          result.get("verdict") == "UNGRADABLE"
          and "bind" in (result.get("semantic", {}).get("error") or ""),
          json.dumps(result.get("semantic"))[:160])

    # ------------- 31. index type confusion (bool is an int subclass in Python)
    case = work / "index-types"
    write_pack(case, corpus, GOLD)
    pair = [good_claim[0], {"text": "second",
                            "citations": [citation(GOLD[0], GOLD[1], sha)]}]
    write_answer(case, pair, "two claims")
    for label, extra in [
        ("bool index", {"supported_claims": [True]}),
        ("duplicate index", {"supported_claims": [0, 0], "unsupported_claims": []}),
        ("negative index", {"supported_claims": [-1]}),
        ("duplicate unsupported", {"verdict": "FAIL", "supported_claims": [],
                                   "unsupported_claims": [0, 0]}),
    ]:
        sem = semantic_file(work / "sem", extra.pop("verdict", "PASS"), "x",
                            answers=case, **extra)
        code, result = run_grader(case, corpus, semantic=sem)
        check(f"{label} is rejected", result.get("verdict") == "UNGRADABLE",
              json.dumps(result.get("semantic"))[:160])

    # ------- 32. an evaluation that judges nothing cannot pass (review W1 repro)
    case = work / "unjudged-nothing"
    write_pack(case, corpus, GOLD)
    bad_claim = [{"text": "This method silently deletes every employee and has no switch.",
                  "citations": [citation(GOLD[0], GOLD[1], sha)]}]
    write_answer(case, bad_claim, "This method silently deletes every employee.")
    sem = semantic_file(work / "sem", "PASS", "looks fine to me", answers=case,
                        supported_claims=[], unsupported_claims=[])
    code, result = run_grader(case, corpus, semantic=sem)
    check("an evaluation that judges no claim is rejected",
          result.get("verdict") == "UNGRADABLE"
          and "exactly once" in (result.get("semantic", {}).get("error") or ""),
          json.dumps(result.get("semantic"))[:200])
    # Omitting the judgement fields entirely must behave the same way.
    unbound = work / "sem" / "no-judgements.json"
    payload = json.loads(sem.read_text())
    payload.pop("supported_claims"); payload.pop("unsupported_claims")
    unbound.write_text(json.dumps(payload))
    code, result = run_grader(case, corpus, semantic=unbound)
    check("an evaluation with no judgement fields is rejected",
          result.get("verdict") == "UNGRADABLE"
          and "missing" in (result.get("semantic", {}).get("error") or ""),
          json.dumps(result.get("semantic"))[:200])

    # ---- 33. an invalid citation cannot make a required step look complete
    case = work / "invalid-citation-step"
    write_pack(case, corpus, (GOLD[0], GOLD[0] + 1))
    outside = [{"text": "covers the switch",
                "citations": [citation(GOLD[1] - 1, GOLD[1], sha)]}]
    write_answer(case, outside, "the switch is handled")
    code, result = run_grader(case, corpus)
    check("C3 does not credit an invalid citation",
          (result.get("criteria") or {}).get("C3", {}).get("score") == 0,
          json.dumps((result.get("criteria") or {}).get("C3")))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL GRADER FIXTURES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
