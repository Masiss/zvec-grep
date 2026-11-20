#!/usr/bin/env python3
"""r4_bench_driver_selftest.py — deterministic isolation, validation and
real-interface mock controls for the trusted benchmark controller.

Controls:
    1. Adversarial answerer policies (shell access, argument injection,
       traversal/scope escape paths, bool/float type confusion, cross-arm
       tools, oversized requests): every attempt must be a typed violation
       with NO admitted call and NO session state change.
    2. Gold-free scripted arm-c policy completes a verified evidence pack
       inside the frozen budget on the benchmark corpus (usability).
    3. The same policy under arm-a only ever reaches the profile gate.
    4. The provider backend runs the full mediation loop through its mock
       transport: tool calls, unknown tool, malformed call, budget
       exhaustion, final answer, abstention — and the answer freeze hash is
       verified before "grading".

No real inference, no private-source egress, no filesystem access for the
answerer side by construction.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "r4_bench_driver.py"

ADVERSARIAL_POLICIES = [
    {"name": "unknown-tool-shell",
     "steps": [{"tool": "shell", "args": {"command": "cat /etc/passwd"}}]},
    {"name": "mode-injection",
     "steps": [{"tool": "search", "args": {"query": "x", "mode": "rg; rm -rf /", "round": 1}}]},
    {"name": "scope-traversal",
     "steps": [{"tool": "search",
                "args": {"query": "x", "mode": "rg", "scope": "a/../../outside"}}]},
    {"name": "facts-dotdot-path",
     "steps": [{"tool": "facts", "args": {"path": "a/../b.cs"}}]},
    {"name": "traversal-path",
     "steps": [{"tool": "facts", "args": {"path": "../../etc/passwd"}}]},
    {"name": "absolute-path",
     "steps": [{"tool": "facts", "args": {"path": "/etc/passwd"}}]},
    {"name": "bool-round",
     "steps": [{"tool": "search", "args": {"query": "x", "round": True}}]},
    {"name": "float-limit",
     "steps": [{"tool": "search", "args": {"query": "x", "limit": 3.0}}]},
    {"name": "unknown-arg",
     "steps": [{"tool": "search", "args": {"query": "x", "shell": "true"}}]},
    {"name": "cross-arm-workflow",
     "steps": [{"tool": "workflow", "args": {"question": "anything"}}]},
    {"name": "too-many-evidence-items",
     "steps": [{"tool": "evidence", "args": {
         "requests": [{"relative_path": f"f{i}.txt",
                       "address": {"mode": "lines", "start_line": 1, "end_line": 1}}
                      for i in range(11)]}}]},
]

GOLD_FREE_ARM_C = {
    "steps": [
        {"tool": "search", "args": {
            "query": "EmployeeEditThongTinCaNhan", "mode": "fts", "limit": 3}},
        {"tool": "symbol", "args": {"name": "EmployeeEditThongTinCaNhan"}},
        {"tool": "evidence", "args": {
            "requests": [
                {"project": "hrm2",
                 "relative_path": "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs",
                 "address": {"mode": "lines", "start_line": 215, "end_line": 285}},
                {"project": "hrm2",
                 "relative_path": "Webs/HRM/Controllers/Employee/EmployeeController.cs",
                 "address": {"mode": "lines", "start_line": 459, "end_line": 520}},
            ]}},
        {"tool": "verify", "args": {}},
    ],
    "final": {"answer": "MOCK-FINAL", "abstain": False},
}

SCRIPTED_PROVIDER = [
    {"type": "tool", "tool": "search", "args": {"query": "EmployeeEditThongTinCaNhan", "mode": "fts", "limit": 3},
     "model": "gpt-5.5-mock", "system_fingerprint": "fp-mock-1"},
    {"type": "tool", "tool": "quantum_search", "args": {"q": "x"}},
    {"type": "tool", "tool": "facts", "args": "not-a-dict"},
    {"type": "final", "answer": "Unsupported tool requests were refused; final answer follows.", "abstain": False},
]


def run_driver(adapter: Path, session_dir: Path, root: Path, profile: str,
               case: str, policy: dict, output: Path, backend: str = "mock",
               extra: list | None = None) -> subprocess.CompletedProcess:
    policy_file = output.with_suffix(".policy.json")
    with open(policy_file, "w", encoding="utf-8") as handle:
        json.dump(policy, handle)
    return subprocess.run(
        [sys.executable, str(DRIVER), "--adapter", str(adapter),
         "--session-dir", str(session_dir), "--root", str(root),
         "--profile", profile, "--case", case,
         "--backend", backend, "--policy", str(policy_file),
         "--output", str(output), *(extra or [])],
        capture_output=True, text=True, timeout=900,
    )


def ledger(session_dir: Path) -> dict:
    return json.loads((session_dir / "ledger.json").read_text())


def main() -> int:
    adapter = Path(sys.argv[1])
    corpus = Path(sys.argv[2])
    out_root = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp())
    out_root.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    fixture = out_root / "fixture"
    if fixture.exists():
        shutil.rmtree(fixture)
    (fixture / "DB").mkdir(parents=True)
    (fixture / "DB" / "proc.sql").write_text(
        "CREATE PROCEDURE sp_x AS BEGIN\n    UPDATE T SET A = 1;\nEND\n"
    )
    for index in range(12):
        (fixture / f"f{index}.txt").write_text(f"token{index}\n")

    # --- 1. adversarial policies ----------------------------------------
    for policy in ADVERSARIAL_POLICIES:
        name = policy["name"]
        session = out_root / f"adv-{name}"
        report = out_root / f"adv-{name}.json"
        result = run_driver(adapter, session, fixture, "arm-c", "ADV", policy, report)
        led = ledger(session)
        if result.returncode != 2 or led["calls_used"] != 0 or led["read_ops"] != 0:
            failures.append(
                f"{name}: expected violation exit 2 with no admitted call, "
                f"got exit={result.returncode} calls={led['calls_used']} "
                f"reads={led['read_ops']}"
            )
        if led["distinct_files"] or led["read_files"]:
            failures.append(f"{name}: delivered/read files despite violation")

    # --- 2. gold-free scripted arm-c on the corpus -----------------------
    session = out_root / "armc-goldfree"
    report = out_root / "armc-goldfree.json"
    answers = out_root / "answers"
    result = run_driver(adapter, session, corpus, "arm-c", "D02",
                        GOLD_FREE_ARM_C, report,
                        extra=["--answers-dir", str(answers)])
    if result.returncode != 0:
        failures.append(f"gold-free arm-c exited {result.returncode}: {result.stderr[:300]}")
    else:
        data = json.loads(report.read_text())
        receipt = data["receipt"]["budget"]
        packs = data["receipt"]["packs"]
        if data["violations"]:
            failures.append(f"gold-free arm-c had violations: {data['violations']}")
        if not packs or not packs[0]["verified"]:
            failures.append("gold-free arm-c did not end with a verified pack")
        if receipt["calls_used"] + receipt["internal_calls"] > 6:
            failures.append("gold-free arm-c exceeded the shared call pool")
        if receipt["delivered_bytes"] > receipt["delivered_bytes_max"]:
            failures.append("gold-free arm-c exceeded the delivered budget")
        if not data.get("frozen_answer"):
            failures.append("gold-free arm-c did not freeze its final answer")
        else:
            blob = (answers / "answer-D02-arm-c.json").read_text(encoding="utf-8")
            digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
            if digest != data["frozen_answer"]["sha256"]:
                failures.append("answer freeze hash mismatch")

    # --- 3. same policy under arm-a hits only the profile gate -----------
    session = out_root / "arma-crossprofile"
    report = out_root / "arma-crossprofile.json"
    run_driver(adapter, session, corpus, "arm-a", "D02",
               GOLD_FREE_ARM_C, report)
    led = ledger(session)
    if led["packs"]:
        failures.append("arm-a unexpectedly produced a pack")

    # --- 4. provider backend mock transport ------------------------------
    session = out_root / "backend-mock"
    report = out_root / "backend-mock.json"
    scripted_file = out_root / "scripted-provider.json"
    scripted_file.write_text(json.dumps(SCRIPTED_PROVIDER))
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--adapter", str(adapter),
         "--session-dir", str(session), "--root", str(corpus),
         "--profile", "arm-c", "--case", "D12",
         "--backend", "openai-responses", "--authorized-inference",
         "--model", "gpt-5.5", "--scripted", str(scripted_file),
         "--answers-dir", str(out_root / "answers-backend"),
         "--output", str(report)],
        capture_output=True, text=True, timeout=900,
    )
    # Unknown and malformed tool calls are protocol breaches: the run must
    # exit VIOLATION while still executing the legal parts before them.
    if result.returncode != 2:
        failures.append(
            f"backend mock: expected violation exit 2, got {result.returncode}: {result.stderr[:200]}"
        )
    if not report.exists():
        failures.append("backend mock: no report written")
    else:
        data = json.loads(report.read_text())
        tools = [entry.get("tool") for entry in data["transcript"]]
        violations = data["violations"]
        if "search" not in tools:
            failures.append("backend mock: legal search must execute before the breaches")
        if len(violations) != 2:
            failures.append(
                f"backend mock: unknown/malformed tool calls must be violations, got {violations}"
            )
        usage = data["usage"]
        if not usage or usage[0]["served_model"] != "gpt-5.5-mock":
            failures.append("backend mock: served model identity not recorded")
        if not data.get("frozen_answer"):
            failures.append("backend mock: final answer not frozen")

    # --- 5. real backend refuses without explicit authorization ----------
    session = out_root / "real-refusal"
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--adapter", str(adapter),
         "--session-dir", str(session), "--root", str(corpus),
         "--profile", "arm-c", "--case", "D12", "--backend", "openai-responses"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 3 or "authorized" not in result.stderr:
        failures.append(
            "real backend must refuse without --authorized-inference "
            f"(exit={result.returncode})"
        )

    print(json.dumps({
        "fixture": str(fixture),
        "failures": failures,
        "arm_c_budget": json.loads(
            (out_root / "armc-goldfree.json").read_text()
        )["receipt"]["budget"] if (out_root / "armc-goldfree.json").exists() else None,
    }, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
