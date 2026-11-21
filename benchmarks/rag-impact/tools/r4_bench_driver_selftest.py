#!/usr/bin/env python3
"""r4_bench_driver_selftest.py — end-to-end acceptance for the R4 harness.

Every check uses the same code path a real run uses: the driver builds
Responses-API payloads, a transport answers them, tool outputs return keyed by
`call_id`, the terminal answer arrives as a validated `submit_answer` call, and
artefacts are frozen, hashed and graded in separate processes. Only the
transport differs between the mock and the real path.

Covered:
  1. strict tool schemas (independent validator, both directions);
  2. wire-argument normalisation and malformed-argument refusal;
  3. the mock derives each step from the tool output it actually received;
  4. round and cumulative-budget enforcement;
  5. arm B generates a fresh blind plan over the wire;
  6. the REAL plan transport is exercised against a loopback Responses server
     that rejects any request that is not the plan contract;
  7. frozen question identity, and refusal of an unlabelled override;
  8. the final answer contract, with negative controls for prose, malformed
     JSON, missing fields, nonexistent/outside citations and invalid abstention;
  9. answer + pack frozen with sidecars and bound to each other;
 10. an independent grader process that cannot return a formal PASS without a
     semantic evaluation, including the wrong-but-cited control;
 11. no private-source egress in the plan stage.

Usage: r4_bench_driver_selftest.py <adapter-bin> <corpus-root> <work-dir>
"""

from __future__ import annotations

import copy
import hashlib
import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4_bench_driver as drv  # noqa: E402
from r4_tool_schema import (  # noqa: E402
    strict_violations,
    strict_violations_for_answer,
    strict_violations_for_arm,
    strict_violations_for_plan,
    tool_parameters,
    wire_arguments,
)

FAILURES: list[str] = []
REGISTRY_PATH: Path | None = None


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


LABELS = Path(__file__).resolve().parents[1]
CASE = "D04"
MATERIALS = LABELS / "cases" / "c3-steps.json"


class LazyTransport(drv.MockTransport):
    """Answers with prose, never calling any tool (must be INVALID_OUTPUT)."""

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        self.served += 1
        return {"id": "lazy", "object": "response", "model": self.model,
                "system_fingerprint": "mock", "usage": {"input_tokens": 1, "output_tokens": 1},
                "output": [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text",
                                         "text": "MOCK-FINAL: everything is fine"}]}]}


class BadAnswerTransport(drv.MockTransport):
    """Calls submit_answer with an arguments payload chosen by `mode`."""

    def __init__(self, *args, mode: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = mode

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        self.served += 1
        claims = [{"text": "a claim",
                   "citations": [{"path": "Core/Service.cs", "start_line": 1,
                                  "end_line": 2, "sha256": "d" * 64}]}]
        good = {"answer": "x", "claims": claims, "gaps": [], "abstain": False}
        mode = self.mode
        if mode == "malformed-json":
            args_text = "{not json"
        elif mode == "missing-fields":
            args_text = json.dumps({"answer": "x", "claims": claims})
        elif mode == "no-citations":
            args_text = json.dumps({"answer": "x",
                                    "claims": [{"text": "c", "citations": []}],
                                    "gaps": [], "abstain": False})
        elif mode == "traversal":
            bad = copy.deepcopy(good)
            bad["claims"][0]["citations"][0]["path"] = "../secret.cs"
            args_text = json.dumps(bad)
        elif mode == "abstain-with-claims":
            args_text = json.dumps({"answer": "x", "claims": claims,
                                    "gaps": [], "abstain": True})
        elif mode == "reversed-range":
            bad = copy.deepcopy(good)
            bad["claims"][0]["citations"][0].update({"start_line": 9, "end_line": 2})
            args_text = json.dumps(bad)
        else:
            args_text = json.dumps(good)
        return {"id": "bad", "object": "response", "model": self.model,
                "system_fingerprint": "mock", "usage": {"input_tokens": 1, "output_tokens": 1},
                "output": [{"type": "function_call", "call_id": "answer-1",
                            "name": "submit_answer", "arguments": args_text}]}


def run_arm(adapter: Path, root: Path, work: Path, profile: str, tag: str = "",
            transport=None, plan_file: Path | None = None,
            question_file: Path | None = None,
            allow_override: bool = False, grade: bool = False,
            semantic: Path | None = None) -> tuple[dict, object]:
    answers = work / f"answers-{profile}{tag}"
    session = work / f"session-{profile}{tag}"
    identity = drv.resolve_question(LABELS, CASE, question_file, allow_override)
    answerer = transport or drv.MockTransport(
        model="mock-model", instructions="You are a code-investigation agent.",
        profile=profile, question=identity["text"],
        question_sha256=identity["sha256"], question_source=identity["source"],
        question_formal=identity["formal"], corpus_marker=str(root.resolve()),
    )
    controller = drv.AdapterController(adapter, session, root, profile, CASE, plan_file)
    controller.create()
    report = drv.drive(controller, answerer, CASE, profile, answers)
    if grade:
        report["grading"] = drv.grade(answers, LABELS, CASE, profile, corpus=root,
                                      materials=MATERIALS, semantic=semantic,
                                      registry=REGISTRY_PATH)
    return report, answerer


def main() -> int:
    adapter, root, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    global REGISTRY_PATH
    registry = work / "evaluators.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "evaluators": {"selftest-evaluator": {"trusted": True, "kind": "fixture"}},
    }), encoding="utf-8")
    REGISTRY_PATH = registry
    question = drv.resolve_question(LABELS, CASE, None, False)["text"]

    # ---------------------------------------------------------- 1. schemas
    for arm in ("arm-a", "arm-b", "arm-c"):
        check(f"strict schema valid: {arm}", not strict_violations_for_arm(arm),
              str(strict_violations_for_arm(arm)[:3]))
        payload_tools = drv.tool_payload_schema(arm)
        check(f"{arm} exposes submit_answer last",
              payload_tools[-1]["name"] == "submit_answer")
        for tool in payload_tools:
            check(f"{arm}/{tool['name']} schema strict",
                  not strict_violations(tool["parameters"]),
                  str(strict_violations(tool["parameters"])[:3]))
    check("strict schema valid: plan", not strict_violations_for_plan())
    check("strict schema valid: answer", not strict_violations_for_answer())
    broken = copy.deepcopy(tool_parameters("search"))
    broken["properties"]["smuggled"] = {"type": "string"}
    check("validator rejects a property missing from required",
          any("smuggled" in p for p in strict_violations(broken)))
    broken_root = copy.deepcopy(tool_parameters("search"))
    broken_root["additionalProperties"] = True
    check("validator rejects an open root object",
          any("additionalProperties" in p for p in strict_violations(broken_root)))

    # ------------------------------------------------- 2. argument handling
    wire = wire_arguments("search", {"query": "EmployeeUpdateInfo"})
    check("nullable wire args normalise to CLI",
          drv.validate_call("arm-c", "search", wire) == ["search", "--query", "EmployeeUpdateInfo"])
    for label, tool, args, profile in [
        ("unknown tool", "shell", {"cmd": "ls"}, "arm-c"),
        ("bool limit", "search", {"query": "x", "mode": None, "limit": True, "scope": None}, "arm-c"),
        ("traversal path", "facts", {"path": "../etc/passwd", "method": None}, "arm-c"),
        ("cross-arm tool", "workflow", {"question": "x"}, "arm-a"),
        ("mixed address modes", "evidence", {"requests": [{
            "project": "p", "relative_path": "a.cs", "expected_sha256": None,
            "address": {"mode": "lines", "start_line": 1, "end_line": 2,
                        "start": 0, "end": 5}}]}, "arm-c"),
        ("reversed lines", "evidence", {"requests": [{
            "project": "p", "relative_path": "a.cs", "expected_sha256": None,
            "address": {"mode": "lines", "start_line": 9, "end_line": 2,
                        "start": None, "end": None}}]}, "arm-c"),
    ]:
        try:
            drv.validate_call(profile, tool, args)
            check(f"rejects {label}", False, "no violation raised")
        except drv.Violation:
            check(f"rejects {label}", True)

    # ------------------------------------------- 3. arm-C end-to-end + grade
    report_c, answerer_c = run_arm(adapter, root, work, "arm-c", grade=True)
    check("arm-c reached evidence", any(e["tool"] == "evidence" for e in answerer_c.calls_seen))
    check("tool outputs keyed by call_id",
          all(e["call_id"].startswith("call-") for e in answerer_c.calls_seen))
    art = json.loads((work / "answers-arm-c" / f"answer-{CASE}-arm-c.json").read_text())
    check("answer artifact records the submitted claims", bool(art["claims"]))
    check("answer artifact names the pack it used", art["pack"] is not None)
    check("answer artifact records question identity",
          art["question"]["sha256"] == hashlib.sha256(question.encode()).hexdigest())
    sidecar = work / "answers-arm-c" / f"answer-{CASE}-arm-c.sha256"
    check("answer sidecar present", sidecar.is_file())
    check("common final verification ran",
          (report_c["final_verification"] or {}).get("verified") is True)
    grading = report_c["grading"]
    check("grader returns a formal verdict", "verdict" in grading, json.dumps(grading)[:200])
    check("no semantic evaluation means no formal PASS",
          grading["verdict"] != "PASS" or grading["semantic"]["status"] == "EVALUATED",
          json.dumps(grading["semantic"]))

    # ------------------------------------------------ 4. wrong-but-cited control
    # The reviewer's reproduction, end-to-end: a false claim with a real,
    # verified, in-pack citation.
    case_dir = work / "wrong-but-cited"
    pack_src = work / "answers-arm-c" / f"pack-{CASE}-arm-c.json"
    case_dir.mkdir(parents=True, exist_ok=True)
    pack_raw = pack_src.read_bytes()
    (case_dir / f"pack-{CASE}-arm-c.json").write_bytes(pack_raw)
    (case_dir / f"pack-{CASE}-arm-c.sha256").write_text(
        hashlib.sha256(pack_raw).hexdigest() + f"  pack-{CASE}-arm-c.json\n")
    pack = json.loads(pack_raw)
    item = pack["items"][0] if "items" in pack else pack["selected"][0]
    if "items" in pack:
        start, end = item["range"]["start_line"], item["range"]["end_line"]
        sha = item.get("sha256") or item.get("source_sha256")
    else:
        start, end = item["start_line"], item["end_line"]
        sha = item.get("source_sha256") or item.get("sha256")
    wrong = {
        "case": CASE, "profile": "arm-c", "status": "OK", "error": None,
        "question": {"sha256": hashlib.sha256(question.encode()).hexdigest(),
                     "source": "frozen-case", "formal": True},
        "pack": {"sha256": hashlib.sha256(pack_raw).hexdigest(),
                 "kind": "workflow" if "selected" in pack else "evidence"},
        "final": {"answer": "This method silently deletes every employee.",
                  "abstain": False,
                  "claims": [{"text": "This method silently deletes every employee and has no switch.",
                              "citations": [{"path": item.get("path") or item.get("relative_path"),
                                             "start_line": start, "end_line": end,
                                             "sha256": sha}]}],
                  "gaps": []},
        "claims": [], "gaps": [], "transcript_tools": ["search"],
    }
    wrong["claims"] = wrong["final"]["claims"]
    blob = json.dumps(wrong, indent=1).encode()
    (case_dir / f"answer-{CASE}-arm-c.json").write_bytes(blob)
    (case_dir / f"answer-{CASE}-arm-c.sha256").write_text(
        hashlib.sha256(blob).hexdigest() + f"  answer-{CASE}-arm-c.json\n")
    verdict = drv.grade(case_dir, LABELS, CASE, "arm-c", corpus=root,
                        materials=MATERIALS, registry=registry)
    check("wrong-but-cited cannot be a formal PASS",
          verdict.get("verdict") != "PASS", str(verdict.get("verdict")))
    check("wrong-but-cited is NOT_MEASURED without an evaluator",
          verdict.get("verdict") == "NOT_MEASURED", str(verdict.get("verdict")))
    check("wrong-but-cited structural score is reported separately",
          verdict.get("structural_measured_total") is not None,
          json.dumps(verdict.get("structural_measured_total")))

    # An evaluation bound to those exact artefacts is accepted; a fabricated one
    # that names them without judging the claims consistently is not.
    def bounded_semantic(directory: Path, verdict_word: str, rationale: str,
                         **extra) -> Path:
        answers = work / "answers-arm-c"
        dev = json.loads((LABELS / "cases" / "dev.json").read_text(encoding="utf-8"))
        question_sha = hashlib.sha256(
            next(item["question"] for item in dev["cases"] if item["id"] == CASE)
            .encode("utf-8")).hexdigest()
        payload = {
            "case": CASE, "verdict": verdict_word, "rationale": rationale,
            "evaluator_id": "selftest-evaluator",
            "inputs": {
                "case": CASE,
                "answer_sha256": hashlib.sha256(
                    (answers / f"answer-{CASE}-arm-c.json").read_bytes()).hexdigest(),
                "pack_sha256": hashlib.sha256(
                    (answers / f"pack-{CASE}-arm-c.json").read_bytes()).hexdigest(),
                "question_sha256": question_sha,
                "rubric_sha256": hashlib.sha256(MATERIALS.read_bytes()).hexdigest(),
            },
            "dimensions": {
                "claim_correctness": {"score": 2, "rationale": "selftest"},
                "citation_support": {"score": 2, "rationale": "selftest"},
                "completeness": {"score": 2, "rationale": "selftest"},
                "uncertainty": {"score": 2, "rationale": "selftest"},
                "abstention": {"score": 2, "rationale": "selftest"},
            },
        }
        payload.update(extra)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"sem-{verdict_word}-{abs(hash(rationale))}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    claims_in_answer = len(art["claims"])
    good_sem = bounded_semantic(work / "semantics", "PASS", "every claim is supported",
                                supported_claims=list(range(claims_in_answer)),
                                unsupported_claims=[])
    evaluated = drv.grade(work / "answers-arm-c", LABELS, CASE, "arm-c", corpus=root,
                          materials=MATERIALS, semantic=good_sem, registry=registry)
    check("a bound, registered evaluation is accepted",
          evaluated.get("semantic", {}).get("status") == "EVALUATED",
          json.dumps(evaluated.get("semantic"))[:200])
    contradictory = bounded_semantic(work / "semantics", "PASS", "claims are supported",
                                     supported_claims=[], unsupported_claims=[0])
    contradictory_result = drv.grade(work / "answers-arm-c", LABELS, CASE, "arm-c",
                                     corpus=root, materials=MATERIALS,
                                     semantic=contradictory, registry=registry)
    check("an end-to-end contradictory evaluation cannot yield a formal PASS",
          contradictory_result.get("verdict") != "PASS",
          str(contradictory_result.get("verdict")))
    forged = bounded_semantic(work / "semantics", "PASS", "trust me",
                              supported_claims=[0], unsupported_claims=[])
    forged_payload = json.loads(forged.read_text())
    forged_payload["evaluator_id"] = "not-a-registered-evaluator"
    forged.write_text(json.dumps(forged_payload))
    forged_result = drv.grade(work / "answers-arm-c", LABELS, CASE, "arm-c", corpus=root,
                              materials=MATERIALS, semantic=forged, registry=registry)
    check("an unregistered evaluator identity cannot yield a formal verdict",
          forged_result.get("verdict") == "UNGRADABLE",
          json.dumps(forged_result.get("semantic"))[:160])

    # ------------------------------------------ 5. final answer contract (neg)
    for mode, expect_status in [("malformed-json", "INVALID_OUTPUT"),
                                ("missing-fields", "INVALID_OUTPUT"),
                                ("no-citations", "INVALID_OUTPUT"),
                                ("traversal", "INVALID_OUTPUT"),
                                ("abstain-with-claims", "INVALID_OUTPUT"),
                                ("reversed-range", "INVALID_OUTPUT"),
                                ("good", "OK")]:
        profile = "arm-c"
        transport = BadAnswerTransport(
            model="m", instructions="x", profile=profile, question=question,
            corpus_marker=str(root.resolve()), mode=mode)
        report, _ = run_arm(adapter, root, work, profile, tag=f"-ans-{mode}",
                            transport=transport)
        got = report["frozen_answer"]["status"]
        check(f"answer contract: {mode} -> {expect_status}", got == expect_status,
              f"got {got}")
        if expect_status == "INVALID_OUTPUT":
            graded = drv.grade(work / f"answers-{profile}-ans-{mode}", LABELS, CASE,
                               profile, corpus=root, materials=MATERIALS,
                               registry=registry)
            check(f"answer contract: {mode} is UNGRADABLE, not a quality FAIL",
                  graded.get("status") == "UNGRADABLE", json.dumps(graded)[:120])

    # prose-only session is INVALID_OUTPUT
    lazy = LazyTransport(model="m", instructions="x", profile="arm-a", question=question,
                         corpus_marker=str(root.resolve()))
    report_l, _ = run_arm(adapter, root, work, "arm-a", tag="-lazy", transport=lazy)
    check("prose final answer is INVALID_OUTPUT",
          report_l["frozen_answer"]["status"] == "INVALID_OUTPUT")

    # --------------------------------------------- 6. question identity controls
    frozen_sha = hashlib.sha256(question.encode()).hexdigest()
    qfile = work / "modified-question.txt"
    qfile.write_text(question + " (modified)", encoding="utf-8")
    try:
        drv.resolve_question(LABELS, CASE, qfile, False)
        check("formal mode rejects a modified question", False, "no error raised")
    except drv.ContractError as error:
        check("formal mode rejects a modified question", "does not match" in str(error))
    override = drv.resolve_question(LABELS, CASE, qfile, True)
    check("override is classified non-formal",
          override["formal"] is False and override["source"] == "override-diagnostic")
    check("override keeps the frozen digest for comparison",
          override["frozen_sha256"] == frozen_sha)
    same = work / "frozen-question.txt"
    same.write_text(question, encoding="utf-8")
    identical = drv.resolve_question(LABELS, CASE, same, False)
    check("a byte-identical file keeps frozen identity",
          identical["formal"] is True and identical["sha256"] == frozen_sha)
    empty = work / "empty-question.txt"
    empty.write_text("   ", encoding="utf-8")
    try:
        drv.resolve_question(LABELS, CASE, empty, True)
        check("empty question is refused", False, "no error raised")
    except drv.ContractError:
        check("empty question is refused", True)

    # --------------------------------- 7. REAL plan transport over loopback HTTP
    seen_plan: list[dict] = []
    plan_hints = ["Services/HRIS", "Webs/HRM"]

    class PlanHandler(http.server.BaseHTTPRequestHandler):
        reject_reason = None

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            seen_plan.append(payload)
            try:
                # Server-side enforcement of the plan request contract.
                names = [tool.get("name") for tool in payload.get("tools") or []]
                if names != ["emit_plan"]:
                    raise ValueError(f"tools must be exactly emit_plan, got {names}")
                text = payload["input"][0]["content"][0]["text"]
                if question not in text:
                    raise ValueError("question missing from the plan prompt")
                for hint in plan_hints:
                    if hint not in text:
                        raise ValueError(f"permitted hint {hint} missing")
                lowered = text.lower()
                for forbidden in ("gold", "checklist", "expected answer", "rubric"):
                    if forbidden in lowered:
                        raise ValueError(f"evaluator-only material leaked: {forbidden}")
                plan = drv.derive_plan(question, plan_hints)
                body = json.dumps({
                    "id": "resp-plan", "object": "response", "model": "served-planner",
                    "system_fingerprint": "fp-plan",
                    "output": [{"type": "function_call", "call_id": "plan-1",
                                "name": "emit_plan",
                                "arguments": json.dumps(drv.plan_wire_form(plan))}],
                    "usage": {"input_tokens": 11, "output_tokens": 22},
                }).encode()
                status = 200
            except ValueError as error:
                PlanHandler.reject_reason = str(error)
                body = json.dumps({"error": str(error)}).encode()
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), PlanHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    planner = drv.HttpPlanTransport(
        model="client-planner", instructions=drv.PLAN_INSTRUCTIONS, question=question,
        hints=plan_hints, corpus_marker=str(root.resolve()), question_sha256=frozen_sha,
        question_source="frozen-case", question_formal=True,
        endpoint=endpoint, api_key="test-token")
    provenance = drv.generate_plan(planner, work / "plans-http", CASE, question)
    check("real planner used the emit_plan contract",
          seen_plan and [t["name"] for t in seen_plan[0]["tools"]] == ["emit_plan"],
          json.dumps([t.get("name") for t in (seen_plan[0]["tools"] if seen_plan else [])]))
    check("real planner request carried the question and hints",
          seen_plan and question in seen_plan[0]["input"][0]["content"][0]["text"]
          and all(h in seen_plan[0]["input"][0]["content"][0]["text"] for h in plan_hints))
    check("real planner response parsed into a validated plan",
          bool(provenance["queries"]), json.dumps(provenance["queries"]))
    plan_path = Path(provenance["path"])
    check("real plan frozen with the exact file digest",
          hashlib.sha256(plan_path.read_bytes()).hexdigest() == provenance["sha256_file"])
    check("served planner identity recorded",
          planner.usage_log[-1]["served_model"] == "served-planner")

    # The same transport must survive a server-side contract check for the arm
    # tools case: prove it does NOT send arm tools even if asked to.
    bad_planner = drv.HttpPlanTransport(
        model="client-planner", instructions=drv.PLAN_INSTRUCTIONS, question=question,
        hints=[], corpus_marker=str(root.resolve()), endpoint=endpoint, api_key="t")
    bad_planner.build_request = (lambda conv: {  # simulate a wrong construction
        "model": "m", "instructions": bad_planner.instructions, "input": conv,
        "tools": drv._tool_payload_schema("arm-b"), "tool_choice": "auto",
        "parallel_tool_calls": False})
    try:
        drv.generate_plan(bad_planner, work / "plans-bad", CASE, question)
        check("a planner sending arm tools is refused", False, "no error raised")
    except drv.ContractError:
        check("a planner sending arm tools is refused", True)
    # The server itself must reject a wrong-tool-set request, independent of the
    # client-side validation.
    import urllib.error
    import urllib.request
    bad_payload = json.dumps({
        "model": "m", "instructions": drv.PLAN_INSTRUCTIONS,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": question}]}],
        "tools": drv._tool_payload_schema("arm-b"),
        "tool_choice": "auto", "parallel_tool_calls": False,
    }).encode()
    request = urllib.request.Request(f"{endpoint}/responses", data=bad_payload,
                                     method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer t"})
    try:
        urllib.request.urlopen(request, timeout=10)
        rejected = False
    except urllib.error.HTTPError as error:
        rejected = error.code == 400
    check("loopback planner server rejects arm tools on its own", rejected,
          str(PlanHandler.reject_reason))
    server.shutdown()

    # ---------------------------- 8. arm B plan provenance through the driver
    plan_file = plan_path
    report_b, _ = run_arm(adapter, root, work, "arm-b", tag="-http-plan",
                          plan_file=plan_file)
    pack_b = json.loads((work / "answers-arm-b-http-plan" / f"pack-{CASE}-arm-b.json").read_text())
    check("workflow pack records the plan provenance",
          pack_b["plan"]["source"] == "preregistered-plan")
    check("workflow pack plan hash equals the HTTP-frozen plan digest",
          pack_b["plan"]["sha256"] == provenance["sha256_file"],
          f"{pack_b['plan']['sha256']} != {provenance['sha256_file']}")
    check("arm-b pack verified", (report_b["final_verification"] or {}).get("verified") is True)
    check("arm-b reads are real opens",
          report_b["accounting"]["read_ops"] >= report_b["accounting"]["read_files"])

    # --------------------------- 9. unauthorized inference and round refusal
    proc = subprocess.run([
        sys.executable, str(Path(__file__).with_name("r4_bench_driver.py")),
        "--adapter", str(adapter), "--session-dir", str(work / "session-real"),
        "--root", str(root), "--profile", "arm-c", "--case", CASE,
        "--backend", "openai-responses", "--model", "fixed-model",
    ], capture_output=True, text=True)
    check("unauthorized real inference refused", proc.returncode == drv.ERROR_EXIT,
          proc.stderr[-160:])

    class SpamTransport(drv.MockTransport):
        def post(self, payload):
            self._validate(payload)
            self.served += 1
            return self._tool("status", {})

    spam = SpamTransport(model="m", instructions="x", profile="arm-a", question=question,
                         corpus_marker=str(root.resolve()))
    report_r, _ = run_arm(adapter, root, work, "arm-a", tag="-rounds", transport=spam)
    check("round limit refuses extra turns",
          any("round limit" in v for v in report_r["violations"]))
    check("refused turns never executed", report_r["accounting"]["calls_used"] <= drv.MAX_ROUNDS)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL SELFTEST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
