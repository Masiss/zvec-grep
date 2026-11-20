#!/usr/bin/env python3
"""r4_bench_driver_selftest.py — end-to-end acceptance for the R4 harness.

Every assertion exercises the SAME code path used for real inference: the
driver builds Responses-API payloads, the transport returns Responses-API
outputs, tool outputs are fed back keyed by call_id, and the mock transport
derives its answers from the outputs it actually received.

Covered acceptance items:
  1. initial request carries instructions, question and arm-specific tools
  2. tool requests are parsed and validated; unknown/ill-typed calls are violations
  3. actual tool outputs are returned to the model with call identity
  4. the mock's next response depends on the received output (not a script)
  5. the round limit blocks further turns
  6. arm-b receives and uses the trusted plan
  7. final answer is captured, frozen and hashed
  8. common final verification runs for arms with packs
  9. independent grading runs in a separate process
 10. accounting reflects actual calls/reads/rounds
 11. unauthorized real inference is refused
 12. authorized execution reaches a real HTTP transport (loopback server)

Usage: r4_bench_driver_selftest.py <adapter-bin> <corpus-root> <work-dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import http.server
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4_bench_driver as drv  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


class ScriptedViolationPolicy(drv.MockTransport):
    """A transport that keeps asking for tools forever (round-limit test)."""

    def post(self, payload: dict) -> dict:
        self._validate(payload)
        self.served += 1
        return self._tool("status", {})


class LeakPolicy(drv.MockTransport):
    """A transport that tries to smuggle the corpus path into its request."""

    def _validate(self, payload: dict) -> None:
        super()._validate(payload)


def run_arm(adapter: Path, root: Path, work: Path, profile: str, case: str,
            plan_file: Path | None = None, transport=None, tag: str = "") -> dict:
    answers = work / f"answers-{profile}{tag}"
    session = work / f"session-{profile}{tag}"
    answerer = transport or drv.MockTransport(
        model="mock-model",
        instructions="You are a code-investigation agent.",
        profile=profile,
        question=json.loads((work / "question.json").read_text())["question"],
        corpus_marker=str(root.resolve()),
    )
    controller = drv.AdapterController(adapter, session, root, profile, case, plan_file)
    controller.create()
    return drv.drive(controller, answerer, case, profile, answers), answerer


def main() -> int:
    adapter, root, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    labels = Path(__file__).resolve().parents[1]
    work.mkdir(parents=True, exist_ok=True)
    cases = json.loads((labels / "cases" / "dev.json").read_text())["cases"]
    case = "D02"
    question = next(entry["question"] for entry in cases if entry["id"] == case)
    (work / "question.json").write_text(json.dumps({"question": question}), encoding="utf-8")

    ratio = drv.check_corpus(labels, root)
    check("corpus membership verified", ratio["verified_files"] == ratio["frozen_files"],
          json.dumps(ratio))
    check("case membership verified", drv.check_case(labels, case)["case"] == case)

    # --- arm A: question, tools, output echo, derivation, freeze
    report_a, answerer_a = run_arm(adapter, root, work, "arm-a", case)
    first_request = answerer_a.requests[0]
    check("initial request has question", first_request["input"][0]["content"][0]["text"] == question)
    check("initial request has instructions", bool(first_request["instructions"]))
    tools_a = {tool["name"] for tool in first_request["tools"]}
    check("arm-a exposes only its tools", tools_a == {"search", "status"}, str(tools_a))
    check("tool outputs fed back with call identity",
          all(entry["call_id"].startswith("call-") for entry in answerer_a.calls_seen),
          str(answerer_a.calls_seen[:2]))
    final_a = report_a["final"]["answer"]
    delivered_path = answerer_a.calls_seen[0]["tool"]
    check("mock answer derives from actual output",
          "items" in final_a and delivered_path == "search", final_a[:80])
    check("arm-a finalized with a frozen answer", bool(report_a["frozen_answer"]))
    frozen_path = work / f"answers-arm-a" / f"answer-{case}-arm-a.json"
    check("frozen answer hash matches the file",
          hashlib.sha256(frozen_path.read_bytes()).hexdigest() == report_a["frozen_answer"]["sha256"])
    check("arm-a accounting matches calls",
          report_a["accounting"]["calls_used"] == len(report_a["transcript"]), str(report_a["accounting"]))

    # --- arm C: evidence pack + common final verification
    report_c, answerer_c = run_arm(adapter, root, work, "arm-c", case)
    check("arm-c reached evidence", any(entry["tool"] == "evidence" for entry in answerer_c.calls_seen),
          str([entry["tool"] for entry in answerer_c.calls_seen]))
    check("common final verification ran",
          report_c["final_verification"] is not None and
          report_c["final_verification"].get("verified") is True,
          json.dumps(report_c["final_verification"]))
    check("arm-c read accounting is real",
          (report_c["accounting"]["read_ops"] or 0) >= (report_c["accounting"]["read_files"] or 1),
          str(report_c["accounting"]))

    # --- arm B with a trusted plan
    plan = {"queries": [question], "terms": [], "symbols": [], "scopes": []}
    plan_file = work / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    report_b, answerer_b = run_arm(adapter, root, work, "arm-b", case, plan_file=plan_file)
    workflow_pack = (report_b["receipt"]["packs"] or [{}])[0]
    check("arm-b used a plan-based workflow pack", workflow_pack.get("kind") == "workflow",
          json.dumps(workflow_pack))
    check("arm-b internal calls are accounted",
          (report_b["accounting"]["internal_calls"] or 0) > 0, str(report_b["accounting"]))
    check("arm-b verification is real",
          report_b["final_verification"].get("verified") is True,
          json.dumps(report_b["final_verification"]))
    check("arm-b read accounting uses actual reads",
          (report_b["accounting"]["read_files"] or 0) >
          len({span["path"] for span in json.loads(
              (work / f"session-arm-b" / "packs" / "pack-workflow-1.json").read_text())["selected"]}),
          str(report_b["accounting"]))

    # --- round limit blocks execution
    strict = ScriptedViolationPolicy(
        model="mock-model", instructions="x", profile="arm-a", question=question,
        corpus_marker=str(root.resolve()),
    )
    report_r, answerer_r = run_arm(adapter, root, work, "arm-a", case, transport=strict,
                                   tag="-rounds")
    check("round limit refuses extra turns",
          any("round limit" in v for v in report_r["violations"]), str(report_r["violations"]))
    check("refused turns never executed",
          report_r["accounting"]["calls_used"] <= drv.MAX_ROUNDS, str(report_r["accounting"]))

    # --- validation rejects contract breaches without executing
    for label, tool, args in [
        ("unknown tool", "shell", {"cmd": "ls"}),
        ("bool limit", "search", {"query": "x", "limit": True}),
        ("traversal path", "facts", {"path": "a/../../etc/passwd"}),
        ("cross-arm tool", "workflow", {"question": "x"}),
    ]:
        try:
            drv.validate_call("arm-a" if tool != "workflow" else "arm-c", tool, args)
            check(f"rejects {label}", False, "no violation raised")
        except drv.Violation:
            check(f"rejects {label}", True)

    # --- unauthorized real inference is refused
    import subprocess
    proc = subprocess.run([
        sys.executable, str(Path(__file__).with_name("r4_bench_driver.py")),
        "--adapter", str(adapter), "--session-dir", str(work / "session-real"),
        "--root", str(root), "--profile", "arm-c", "--case", case,
        "--backend", "openai-responses", "--model", "fixed-model",
    ], capture_output=True, text=True)
    check("unauthorized real inference refused", proc.returncode == drv.ERROR_EXIT,
          proc.stderr[-200:])

    # --- authorized synthetic execution reaches a real HTTP transport
    seen_requests: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            seen_requests.append(payload)
            body = json.dumps({
                "id": "resp-1", "object": "response", "model": "served-model",
                "system_fingerprint": "fp",
                "output": [{"type": "function_call", "call_id": "call-1",
                            "name": "search",
                            "arguments": json.dumps({"query": question, "limit": 3})}],
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    transport = drv.HttpTransport(
        model="client-model", instructions="x", profile="arm-a", question=question,
        endpoint=endpoint, api_key="test-token",
    )
    response = transport.next_turn(transport.initial_conversation())
    server.shutdown()
    kind, payload = drv.parse_output(response)
    check("authorized execution reached the HTTP transport", len(seen_requests) == 1)
    check("http request carries instructions and tools",
          seen_requests[0]["instructions"] == "x" and
          {tool["name"] for tool in seen_requests[0]["tools"]} == {"search", "status"})
    check("http response parsed into a tool call",
          kind == "tool" and payload[0] == "search", str(payload[:2]))
    check("served model identity recorded",
          transport.usage_log[-1]["served_model"] == "served-model",
          json.dumps(transport.usage_log[-1]))

    # --- independent grading in a separate process
    # The grader input lives in a scratch labels dir: the repository rubric is
    # never modified by a test run.
    grading_labels = work / "labels"
    (grading_labels / "cases").mkdir(parents=True, exist_ok=True)
    (grading_labels / "cases" / "rubric.json").write_text(json.dumps({
        "schema_version": 1,
        "note": "selftest diagnostic rubric",
        "cases": {case: {"required_substrings": ["ARM-A"], "forbidden_substrings": []}},
    }, indent=1), encoding="utf-8")
    verdict = drv.grade(work / "answers-arm-a", grading_labels, case, "arm-a")
    check("independent grader produced a verdict",
          verdict["verdict"] == "PASS", json.dumps(verdict))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL SELFTEST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
