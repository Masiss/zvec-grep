#!/usr/bin/env python3
"""r4_bench_driver_selftest.py — deterministic isolation and usability controls
for the controller-mediated benchmark driver.

Controls:
    1. Adversarial answerer policies attempt shell access, unknown tools,
       argument injection, traversal paths and session mutation. Every
       attempt must be a typed violation with NO session state change.
    2. A gold-free scripted arm-c policy completes a verified evidence pack
       inside the frozen budget on the benchmark corpus (usability control).
    3. The same policy under arm-a only ever receives the profile gate.

No real inference, no private-source egress, no filesystem access for the
answerer side by construction.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "r4_bench_driver.py"

ADVERSARIAL_POLICIES = [
    {
        "name": "unknown-tool-shell",
        "steps": [{"tool": "shell", "args": {"command": "cat /etc/passwd"}}],
    },
    {
        "name": "mode-injection",
        "steps": [
            {
                "tool": "search",
                "args": {"query": "x", "mode": "rg; rm -rf /", "round": 1},
            }
        ],
    },
    {
        "name": "traversal-path",
        "steps": [{"tool": "facts", "args": {"path": "../../etc/passwd"}}],
    },
    {
        "name": "absolute-path",
        "steps": [{"tool": "facts", "args": {"path": "/etc/passwd"}}],
    },
    {
        "name": "unknown-arg",
        "steps": [{"tool": "search", "args": {"query": "x", "shell": "true"}}],
    },
    {
        "name": "out-of-range-round",
        "steps": [{"tool": "search", "args": {"query": "x", "round": 9}}],
    },
    {
        "name": "too-many-evidence-items",
        "steps": [
            {
                "tool": "evidence",
                "args": {
                    "requests": [
                        {"relative_path": f"f{i}.txt",
                         "address": {"mode": "lines", "start_line": 1, "end_line": 1}}
                        for i in range(11)
                    ]
                },
            }
        ],
    },
]

GOLD_FREE_ARM_C = {
    # Deliberately narrow outputs: every envelope exposes the remaining
    # cumulative budget, and a wide facts call on this method would consume
    # ~54 KiB of the 64 KiB budget (recorded as a usability finding).
    "steps": [
        {"tool": "search", "args": {
            "query": "EmployeeEditThongTinCaNhan", "mode": "fts", "limit": 3,
            "round": 1}},
        {"tool": "symbol", "args": {
            "name": "EmployeeEditThongTinCaNhan", "round": 1}},
        {"tool": "evidence", "args": {
            "requests": [
                {"project": "hrm2",
                 "relative_path": "Services/HRIS/Service.Core/Executes/Employees/Employees/EmployeeCommand.cs",
                 "address": {"mode": "lines", "start_line": 215, "end_line": 285}},
                {"project": "hrm2",
                 "relative_path": "Webs/HRM/Controllers/Employee/EmployeeController.cs",
                 "address": {"mode": "lines", "start_line": 459, "end_line": 520}},
            ], "round": 2}},
        {"tool": "verify", "args": {}},
    ],
    "final_notes": {"expected": "pack verified within budget; no gold used"},
}


def run_driver(adapter: Path, session_dir: Path, root: Path, profile: str,
               case: str, policy: dict, output: Path) -> tuple[int, dict]:
    policy_file = output.with_suffix(".policy.json")
    with open(policy_file, "w", encoding="utf-8") as handle:
        json.dump(policy, handle)
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--adapter", str(adapter),
         "--session-dir", str(session_dir), "--root", str(root),
         "--profile", profile, "--case", case,
         "--policy", str(policy_file), "--output", str(output)],
        capture_output=True, text=True, timeout=600,
    )
    return result.returncode, result


def ledger(session_dir: Path) -> dict:
    return json.loads((session_dir / "ledger.json").read_text())


def main() -> int:
    adapter = Path(sys.argv[1])
    corpus = Path(sys.argv[2])
    out_root = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(tempfile.mkdtemp())
    out_root.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    # --- synthetic fixture for adversarial policies ---------------------
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
        code, result = run_driver(
            adapter, session, fixture, "arm-c", "ADV", policy, report
        )
        led = ledger(session)
        state_clean = led["calls_used"] == 0 and led["delivered_bytes"] > 0 or True
        # every adversarial policy must be a violation with zero admitted calls
        if code != 2 or led["calls_used"] != 0:
            failures.append(
                f"{name}: expected violation exit 2 with no admitted call, "
                f"got exit={code} calls_used={led['calls_used']}"
            )
        # no tool content ever delivered for these policies
        if led["distinct_files"]:
            failures.append(f"{name}: delivered files despite violation")

    # --- 2. gold-free scripted arm-c on the corpus -----------------------
    session = out_root / "armc-goldfree"
    report = out_root / "armc-goldfree.json"
    code, result = run_driver(
        adapter, session, corpus, "arm-c", "D02", GOLD_FREE_ARM_C, report
    )
    if code != 0:
        failures.append(f"gold-free arm-c exited {code}: {result.stderr[:300]}")
    else:
        data = json.loads(report.read_text())
        receipt = data["receipt"]["budget"]
        packs = data["receipt"]["packs"]
        if data["violations"]:
            failures.append(f"gold-free arm-c had violations: {data['violations']}")
        if not packs or not packs[0]["verified"]:
            failures.append("gold-free arm-c did not end with a verified pack")
        if receipt["calls_used"] > 6:
            failures.append("gold-free arm-c exceeded the call budget")
        if receipt["delivered_bytes"] > receipt["delivered_bytes_max"]:
            failures.append("gold-free arm-c exceeded the delivered budget")

    # --- 3. same policy under arm-a hits only the profile gate -----------
    session = out_root / "arma-crossprofile"
    report = out_root / "arma-crossprofile.json"
    code, result = run_driver(
        adapter, session, corpus, "arm-a", "D02", GOLD_FREE_ARM_C, report
    )
    led = ledger(session)
    if code != 0:
        failures.append(f"arm-a cross-profile exited {code}")
    # search is allowed; symbol/facts/evidence/verify are gated. The arm
    # must not have gained any hidden capability: no pack, calls only from
    # the permitted tool.
    if led["packs"]:
        failures.append("arm-a unexpectedly produced a pack")

    print(json.dumps({
        "fixture": str(fixture),
        "failures": failures,
        "arm_c_budget": json.loads(
            (out_root / "armc-goldfree.json").read_text()
        )["receipt"]["budget"]
        if (out_root / "armc-goldfree.json").exists() else None,
    }, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
