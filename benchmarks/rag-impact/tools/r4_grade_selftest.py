#!/usr/bin/env python3
"""r4_grade_selftest.py — fixture validation for the independent grader.

Each fixture is a frozen artefact pair (answer + pack) plus evaluator-only
materials. The grader is invoked in a SEPARATE process, exactly as in a real
run, and the expected verdict is asserted. This proves the pipeline
distinguishes supported / incomplete / unsupported / stale / false-negative /
abstention answers instead of defaulting to PASS.

Usage: r4_grade_selftest.py <corpus-root> <work-dir>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import hashlib
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


def span_block(corpus: Path, start: int, end: int) -> str:
    lines = (corpus / PATH).read_text(encoding="utf-8").split("\n")
    return "\n".join(lines[start - 1:end])


def file_sha(corpus: Path) -> str:
    return hashlib.sha256((corpus / PATH).read_bytes()).hexdigest()


def write_pack(directory: Path, corpus: Path, span: tuple[int, int]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    start, end = span
    pack = {
        "schema_version": 1,
        "status": "ok",
        "items": [{
            "project": "hrm",
            "relative_path": PATH,
            "sha256": file_sha(corpus),
            "verification": "verified",
            "range": {"kind": "text", "start_line": start, "end_line": end + 1,
                      "start_byte_offset": 0, "end_byte_offset": 0,
                      "start_byte_column": 0, "end_byte_column": 0},
            "content": span_block(corpus, start, end),
        }],
        "gaps": [],
        "deduplicated": 0,
    }
    path = directory / f"pack-{CASE}-{PROFILE}.json"
    raw = json.dumps(pack, indent=1).encode()
    path.write_bytes(raw)
    (directory / f"pack-{CASE}-{PROFILE}.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    return path


def write_answer(directory: Path, claims: list[dict], answer_text: str,
                 abstain: bool = False, gaps: list[str] | None = None,
                 sha: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    artifact = {
        "case": CASE, "profile": PROFILE,
        "final": {"answer": answer_text, "abstain": abstain},
        "claims": claims,
        "gaps": gaps or [],
        "transcript_tools": ["search", "evidence"],
    }
    path = directory / f"answer-{CASE}-{PROFILE}.json"
    raw = json.dumps(artifact, ensure_ascii=False, indent=1).encode()
    path.write_bytes(raw)
    digest = sha or hashlib.sha256(raw).hexdigest()
    (directory / f"answer-{CASE}-{PROFILE}.sha256").write_text(
        digest + "  " + path.name + "\n")
    return path


def citation(start: int, end: int, sha: str | None = None) -> dict:
    entry = {"path": PATH, "start_line": start, "end_line": end}
    if sha is not None:
        entry["sha256"] = sha
    return entry


def run_grader(answers: Path, corpus: Path, materials: Path) -> tuple[int, dict]:
    proc = subprocess.run([
        sys.executable, str(GRADER),
        "--answers", str(answers), "--labels", str(LABELS),
        "--corpus", str(corpus), "--materials", str(materials),
        "--case", CASE, "--profile", PROFILE,
    ], capture_output=True, text=True, timeout=300)
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(proc.stdout[:400], proc.stderr[:400])
        raise


def main() -> int:
    corpus = Path(sys.argv[1])
    work = Path(sys.argv[2])
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    sha = file_sha(corpus)
    full_span = [{"text": "UpdateEmployeeAttr switches on model.Attr and saves",
                  "citations": [citation(GOLD[0], GOLD[1], sha)]}]
    answer_text = "UpdateEmployeeAttr switches on model.Attr, handles Avatar, then SaveChanges."

    # 1. Positive: the pack covers the whole gold span and the answer cites it.
    case_dir = work / "positive"
    write_pack(case_dir, corpus, GOLD)
    write_answer(case_dir, full_span, answer_text + " Not observed: deployment.")
    code, result = run_grader(case_dir, corpus, MATERIALS)
    check("positive: graded", code == 0, str(result)[:200])
    check("positive: verdict PASS", result.get("verdict") == "PASS",
          json.dumps({k: v["score"] for k, v in (result.get("criteria") or {}).items()}))
    check("positive: total 12", result.get("total") == 12, str(result.get("total")))

    # 2. Incomplete: the answer cites only part of the required span.
    case_dir = work / "incomplete"
    write_pack(case_dir, corpus, GOLD)
    partial = [{"text": "UpdateEmployeeAttr switches on model.Attr",
                "citations": [citation(GOLD[0], GOLD[0] + 1, sha)]}]
    write_answer(case_dir, partial, answer_text + " Not observed: deployment.")
    code, result = run_grader(case_dir, corpus, MATERIALS)
    criteria = result.get("criteria") or {}
    check("incomplete: C6 detects omitted packed steps",
          (criteria.get("C6") or {}).get("score", 2) < 2, json.dumps(criteria.get("C6")))
    check("incomplete: verdict is not PASS", result.get("verdict") != "PASS",
          str(result.get("verdict")))

    # 3. Unsupported: a claim with no citation at all.
    case_dir = work / "unsupported"
    write_pack(case_dir, corpus, GOLD)
    unsupported = [
        {"text": "UpdateEmployeeAttr switches on model.Attr and saves",
         "citations": [citation(GOLD[0], GOLD[1], sha)]},
        {"text": "It also writes Keyword", "citations": []},
    ]
    write_answer(case_dir, unsupported, answer_text)
    code, result = run_grader(case_dir, corpus, MATERIALS)
    criteria = result.get("criteria") or {}
    check("unsupported: C2 penalised", (criteria.get("C2") or {}).get("score", 2) < 2,
          json.dumps(criteria.get("C2")))
    check("unsupported: verdict is not PASS", result.get("verdict") != "PASS",
          str(result.get("verdict")))

    # 4. Out-of-pack citation is rejected even with a valid-looking range.
    case_dir = work / "outside-pack"
    write_pack(case_dir, corpus, (GOLD[0], GOLD[0] + 2))
    outside = [{"text": "a claim", "citations": [citation(GOLD[1] - 1, GOLD[1], sha)]}]
    write_answer(case_dir, outside, "claim")
    code, result = run_grader(case_dir, corpus, MATERIALS)
    criteria = result.get("criteria") or {}
    check("outside-pack: C2 zero", (criteria.get("C2") or {}).get("score") == 0,
          json.dumps(criteria.get("C2")))

    # 5. Stale: answer sidecar hash does not match the frozen artefact.
    case_dir = work / "stale"
    write_pack(case_dir, corpus, GOLD)
    write_answer(case_dir, full_span, answer_text, sha="0" * 64)
    code, result = run_grader(case_dir, corpus, MATERIALS)
    check("stale: UNGRADABLE", result.get("status") == "UNGRADABLE", json.dumps(result)[:200])
    check("stale: non-zero exit", code == 3, str(code))

    # 6. Abstention: the pack lacks required steps and the answer says so.
    case_dir = work / "abstention"
    write_pack(case_dir, corpus, (GOLD[0], GOLD[0] + 1))
    write_answer(case_dir, [], "Cannot determine from the selected evidence.",
                 abstain=True, gaps=["missing required lines in the pack"])
    code, result = run_grader(case_dir, corpus, MATERIALS)
    criteria = result.get("criteria") or {}
    check("abstention: C5 honours the declaration",
          (criteria.get("C5") or {}).get("score") == 2, json.dumps(criteria.get("C5")))
    check("abstention: not PASS (evidence incomplete)",
          result.get("verdict") != "PASS", str(result.get("verdict")))

    # 7. Missing rubric: materials without the rubric fail clearly.
    case_dir = work / "missing-rubric"
    write_pack(case_dir, corpus, GOLD)
    write_answer(case_dir, full_span, answer_text)
    broken = work / "materials-no-rubric.json"
    data = json.loads(MATERIALS.read_text(encoding="utf-8"))
    data.pop("rubric", None)
    broken.write_text(json.dumps(data), encoding="utf-8")
    code, result = run_grader(case_dir, corpus, broken)
    check("missing rubric: UNGRADABLE", result.get("status") == "UNGRADABLE",
          json.dumps(result)[:200])

    # 8. Missing pack: nothing to grade against.
    case_dir = work / "missing-pack"
    case_dir.mkdir(parents=True, exist_ok=True)
    write_answer(case_dir, full_span, answer_text)
    code, result = run_grader(case_dir, corpus, MATERIALS)
    check("missing pack: UNGRADABLE", result.get("status") == "UNGRADABLE",
          json.dumps(result)[:200])

    # 9. Tampered pack: sidecar hash mismatch.
    case_dir = work / "tampered-pack"
    pack_path = write_pack(case_dir, corpus, GOLD)
    write_answer(case_dir, full_span, answer_text)
    pack_path.write_bytes(pack_path.read_bytes().replace(b"UpdateEmployeeAttr", b"UpdateEmployeeAttrX"))
    code, result = run_grader(case_dir, corpus, MATERIALS)
    check("tampered pack: UNGRADABLE", result.get("status") == "UNGRADABLE",
          json.dumps(result)[:200])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL GRADER FIXTURES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
