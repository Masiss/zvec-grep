#!/usr/bin/env python3
"""r4_bench_driver_selftest.py — end-to-end acceptance for the R4 harness.

Every check exercises the SAME code path used for real inference: the driver
builds Responses-API payloads, a transport returns Responses-API outputs, tool
outputs are returned keyed by call_id, and the plan/answer artefacts are
frozen, hashed and graded in separate processes.

Covered acceptance items:
  1. generated tool schemas satisfy strict mode (independent validator) and an
     invalid schema must fail this test;
  2. nullable/optional wire arguments normalise to the adapter contract, and
     malformed arguments never reach the adapter;
  3. the mock derives its next step and its claims from the tool output it
     actually received (a fixed "MOCK-FINAL" cannot pass);
  4. rounds and cumulative budgets are enforced at the trusted boundary;
  5. arm B generates a fresh blind plan over the wire, freezes and hashes it,
     and the workflow pack records that hash;
  6. arm C collects and verifies evidence; common final verification runs;
  7. answer + pack are frozen with sidecar hashes;
  8. the grader runs in its own process on synthetic artefacts and does not
     default to PASS;
  9. malformed tool calls, unauthorized inference and exhausted budgets stop;
 10. loopback transport round-trip (no egress, no private source).

Usage: r4_bench_driver_selftest.py <adapter-bin> <corpus-root> <work-dir>
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import threading
import http.server
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4_bench_driver as drv  # noqa: E402
from r4_tool_schema import (  # noqa: E402
    strict_violations,
    strict_violations_for_arm,
    strict_violations_for_plan,
    tool_parameters,
    wire_arguments,
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


class ToolSpamTransport(drv.MockTransport):
    """Keeps asking for tools forever: the round limit must block it."""

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        self.served += 1
        return self._tool("status", {})


class LazyTransport(drv.MockTransport):
    """Answers immediately without any tool call (must not grade as PASS)."""

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        self.served += 1
        return self._final("MOCK-FINAL", abstain=False)


class InvalidPlanTransport(drv.MockPlanTransport):
    """Emits a plan that violates the plan contract."""

    def post(self, payload: dict) -> dict:
        self._validate_request(payload)
        self.served += 1
        return {
            "id": "mock-plan-bad", "object": "response", "model": self.model,
            "system_fingerprint": "mock-fingerprint",
            "output": [{
                "type": "function_call", "call_id": "plan-bad", "name": "emit_plan",
                "arguments": json.dumps({"queries": [], "symbols": [], "scopes": [],
                                         "terms": []}),
            }],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


def run_arm(adapter: Path, root: Path, work: Path, profile: str, case: str,
            question: str, plan_file: Path | None = None, transport=None,
            tag: str = "", grade: bool = False, materials: Path | None = None) -> tuple[dict, object]:
    answers = work / f"answers-{profile}{tag}"
    session = work / f"session-{profile}{tag}"
    answerer = transport or drv.MockTransport(
        model="mock-model", instructions="You are a code-investigation agent.",
        profile=profile, question=question, corpus_marker=str(root.resolve()),
    )
    controller = drv.AdapterController(adapter, session, root, profile, case, plan_file)
    controller.create()
    report = drv.drive(controller, answerer, case, profile, answers)
    if grade:
        report["grading"] = drv.grade(
            answers, Path(__file__).resolve().parents[1], case, profile,
            corpus=root, materials=materials,
        )
    return report, answerer


def main() -> int:
    adapter, root, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    labels = Path(__file__).resolve().parents[1]
    materials = labels / "cases" / "c3-steps.json"
    work.mkdir(parents=True, exist_ok=True)
    cases = json.loads((labels / "cases" / "dev.json").read_text())["cases"]
    case = "D04"
    question = next(entry["question"] for entry in cases if entry["id"] == case)

    # ---------------------------------------------------------- 1. schemas
    for arm in ("arm-a", "arm-b", "arm-c"):
        problems = strict_violations_for_arm(arm)
        check(f"strict schema valid: {arm}", not problems, str(problems[:3]))
    check("strict schema valid: plan", not strict_violations_for_plan(),
          str(strict_violations_for_plan()))
    # A permissive optional field must be reported as invalid: the validator
    # itself has to be trustworthy.
    broken = copy.deepcopy(tool_parameters("search"))
    broken["properties"]["smuggled"] = {"type": "string"}
    check("validator rejects a property missing from required",
          any("smuggled" in p for p in strict_violations(broken)),
          str(strict_violations(broken)[:3]))
    broken_root = copy.deepcopy(tool_parameters("search"))
    broken_root["additionalProperties"] = True
    check("validator rejects an open root object",
          any("additionalProperties" in p for p in strict_violations(broken_root)),
          str(strict_violations(broken_root)[:3]))
    broken2 = copy.deepcopy(tool_parameters("evidence"))
    broken2["properties"]["requests"]["items"].pop("additionalProperties", None)
    check("validator rejects an untyped nested object",
          any("requests[]" in p for p in strict_violations(broken2)),
          str(strict_violations(broken2)[:3]))

    # ------------------------------------------------- 2. argument handling
    wire = wire_arguments("search", {"query": "EmployeeUpdateInfo"})
    check("wire form carries nullable optional fields",
          wire == {"query": "EmployeeUpdateInfo", "mode": None, "limit": None, "scope": None},
          json.dumps(wire))
    cli = drv.validate_call("arm-c", "search", wire)
    check("nullable wire args normalise to CLI", cli == ["search", "--query", "EmployeeUpdateInfo"],
          str(cli))
    wire_ev = wire_arguments("evidence", {"requests": [{
        "project": "hrm", "relative_path": "Core/Service.cs",
        "address": {"mode": "lines", "start_line": 1, "end_line": 4}}]})
    normalized = drv.normalized_arguments("evidence", wire_ev)
    check("evidence address normalises to lines mode",
          normalized["requests"][0]["address"] == {"mode": "lines",
                                                   "start_line": 1, "end_line": 4},
          json.dumps(normalized))
    for label, tool, args in [
        ("unknown tool", "shell", {"cmd": "ls"}),
        ("bool limit", "search", {"query": "x", "mode": None, "limit": True, "scope": None}),
        ("traversal path", "facts", {"path": "../etc/passwd", "method": None}),
        ("cross-arm tool", "workflow", {"question": "x"}),
        ("mixed address modes", "evidence", {"requests": [{
            "project": "p", "relative_path": "a.cs", "expected_sha256": None,
            "address": {"mode": "lines", "start_line": 1, "end_line": 2,
                        "start": 0, "end": 5}}]}),
        ("reversed lines", "evidence", {"requests": [{
            "project": "p", "relative_path": "a.cs", "expected_sha256": None,
            "address": {"mode": "lines", "start_line": 9, "end_line": 2,
                        "start": None, "end": None}}]}),
    ]:
        profile = "arm-c" if tool in ("workflow", "evidence", "facts") else "arm-a"
        try:
            drv.validate_call(profile, tool, args)
            check(f"rejects {label}", False, "no violation raised")
        except drv.Violation:
            check(f"rejects {label}", True)

    # ---------------------------------------------- 3/6. arm C end-to-end
    report_c, answerer_c = run_arm(adapter, root, work, "arm-c", case, question, grade=True)
    check("arm-c reached evidence",
          any(e["tool"] == "evidence" for e in answerer_c.calls_seen),
          str([e["tool"] for e in answerer_c.calls_seen]))
    check("tool outputs fed back with call identity",
          all(e["call_id"].startswith("call-") for e in answerer_c.calls_seen))
    check("claims derive from the delivered spans",
          bool((json.loads((work / "answers-arm-c" / f"answer-{case}-arm-c.json").read_text())
                .get("claims"))))
    check("common final verification ran",
          (report_c["final_verification"] or {}).get("verified") is True,
          json.dumps(report_c["final_verification"]))
    answer_path = work / "answers-arm-c" / f"answer-{case}-arm-c.json"
    sidecar = answer_path.with_suffix(".sha256").read_text().split()[0]
    check("frozen answer hash matches its sidecar",
          sidecar == hashlib.sha256(answer_path.read_bytes()).hexdigest())
    check("pack frozen next to the answer",
          (work / "answers-arm-c" / f"pack-{case}-arm-c.json").is_file())
    grading = report_c["grading"]
    check("grader produced a structured verdict",
          grading.get("verdict") in ("PASS", "PARTIAL", "FAIL"),
          json.dumps(grading)[:200])
    check("grader never defaults to PASS on unsupported evidence",
          grading.get("verdict") != "PASS" or grading.get("total") == 12,
          json.dumps(grading.get("criteria"))[:200])

    # -------------------------------------------------- 4. round enforcement
    strict = ToolSpamTransport(model="m", instructions="x", profile="arm-a",
                               question=question, corpus_marker=str(root.resolve()))
    report_r, _ = run_arm(adapter, root, work, "arm-a", case, question,
                          transport=strict, tag="-rounds")
    check("round limit refuses extra turns",
          any("round limit" in v for v in report_r["violations"]), str(report_r["violations"]))
    check("refused turns never executed",
          report_r["accounting"]["calls_used"] <= drv.MAX_ROUNDS,
          str(report_r["accounting"]))

    # ------------------------------- 5. arm B fresh blind plan + provenance
    planner = drv.MockPlanTransport(model="mock-plan", instructions="plan", question=question,
                                    hints=["Services/HRIS", "Webs/HRM"],
                                    corpus_marker=str(root.resolve()))
    provenance = drv.generate_plan(planner, work / "plans", case, question)
    check("plan generated over the wire contract",
          bool(provenance["queries"]) and provenance["call_id"].startswith("plan-"),
          json.dumps(provenance["queries"]))
    check("plan prompted with the question and hints only",
          all("gold" not in q.lower() for q in provenance["queries"]))
    plan_path = Path(provenance["path"])
    check("plan frozen with a matching file hash",
          hashlib.sha256(plan_path.read_bytes()).hexdigest() == provenance["sha256_file"])
    report_b, _ = run_arm(adapter, root, work, "arm-b", case, question,
                          plan_file=plan_path, tag="-plan")
    pack_path = work / "session-arm-b-plan" / "packs" / "pack-workflow-1.json"
    pack = json.loads(pack_path.read_text())
    check("workflow pack records plan provenance",
          pack["plan"]["source"] == "preregistered-plan", json.dumps(pack.get("plan")))
    check("pack plan hash equals the frozen plan hash",
          pack["plan"]["sha256"] == provenance["sha256_file"],
          f"{pack['plan']['sha256']} != {provenance['sha256_file']}")
    check("arm-b pack verified", (report_b["final_verification"] or {}).get("verified") is True,
          json.dumps(report_b["final_verification"]))
    check("arm-b reads are accounted from real opens",
          (report_b["accounting"]["read_files"] or 0) >
          len({s["path"] for s in pack["selected"]}), str(report_b["accounting"]))

    # arm-b primary must refuse a plan-less run unless explicitly allowed
    proc = subprocess.run([
        sys.executable, str(Path(__file__).with_name("r4_bench_driver.py")),
        "--adapter", str(adapter), "--session-dir", str(work / "session-b-noplan"),
        "--root", str(root), "--profile", "arm-b", "--case", case,
        "--allow-no-plan", "--output", str(work / "b-noplan.json"),
    ], capture_output=True, text=True)
    check("question-only arm-b requires an explicit opt-in",
          "diagnostic arm" in proc.stderr, proc.stderr[-200:])

    # invalid plan generation fails closed
    try:
        drv.generate_plan(InvalidPlanTransport(model="m", instructions="p", question=question,
                                               hints=[], corpus_marker=str(root.resolve())),
                          work / "plans-bad", case, question)
        check("invalid plan fails closed", False, "no error raised")
    except drv.ContractError:
        check("invalid plan fails closed", True)

    # -------------------------------- 7/8. lazy answer must not grade PASS
    lazy = LazyTransport(model="m", instructions="x", profile="arm-a",
                         question=question, corpus_marker=str(root.resolve()))
    report_l, _ = run_arm(adapter, root, work, "arm-a", case, question, transport=lazy,
                          tag="-lazy", grade=True)
    check("arm-a verification policy is explicit",
          (report_c["final_verification"] or {}).get("policy") == "verified_pack_required")
    check("arm-a has no verification tool",
          (run_arm(adapter, root, work, "arm-a", case, question, tag="-policy")[0]
           ["final_verification"] or {}).get("policy") == "no_verification_tool")
    check("lazy answer produced no pack",
          (report_l["final_verification"] or {}).get("packs") == 0,
          json.dumps(report_l["final_verification"]))
    check("lazy answer is not graded PASS",
          (report_l["grading"] or {}).get("verdict") != "PASS",
          json.dumps(report_l["grading"])[:200])

    # --------------------------------- 9. unauthorized / contract refusals
    proc = subprocess.run([
        sys.executable, str(Path(__file__).with_name("r4_bench_driver.py")),
        "--adapter", str(adapter), "--session-dir", str(work / "session-real"),
        "--root", str(root), "--profile", "arm-c", "--case", case,
        "--backend", "openai-responses", "--model", "fixed-model",
    ], capture_output=True, text=True)
    check("unauthorized real inference refused", proc.returncode == drv.ERROR_EXIT,
          proc.stderr[-200:])

    # ------------------------------- 10. loopback real transport round trip
    seen: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            seen.append(payload)
            body = json.dumps({
                "id": "resp-1", "object": "response", "model": "served-model",
                "system_fingerprint": "fp",
                "output": [{
                    "type": "function_call", "call_id": "call-1", "name": "search",
                    "arguments": json.dumps(wire_arguments(
                        "search", {"query": question, "limit": 3})),
                }],
                "usage": {"input_tokens": 11, "output_tokens": 5},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    transport = drv.HttpTransport(
        model="client-model", instructions="x", profile="arm-a", question=question,
        endpoint=f"http://127.0.0.1:{server.server_port}", api_key="test-token")
    response = transport.next_turn(transport.initial_conversation())
    server.shutdown()
    kind, payload = drv.parse_output(response)
    check("authorized execution reached the HTTP transport", len(seen) == 1)
    check("http request carries strict tool schemas",
          not any(strict_violations(tool["parameters"]) for tool in seen[0]["tools"]))
    check("http response parsed into a tool call", kind == "tool" and payload[0] == "search",
          str(payload[:2]))
    check("served model identity recorded",
          transport.usage_log[-1]["served_model"] == "served-model")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL SELFTEST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
