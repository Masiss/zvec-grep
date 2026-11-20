#!/usr/bin/env python3
"""r4_grade.py — independent grader, run in its own process.

Consumes ONLY the frozen answer artifact plus the frozen case labels
(required facts / forbidden claims). It never sees the corpus, the session
directory, tool traces or the answerer's model, so it cannot be steered by
the answerer and cannot read private source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def load_answer(answers: Path, case: str, profile: str) -> tuple[dict, str]:
    path = answers / f"answer-{case}-{profile}.json"
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()

    answer, sha = load_answer(args.answers, args.case, args.profile)
    rubric_path = args.labels / "cases" / "rubric.json"
    rubric = json.loads(rubric_path.read_text(encoding="utf-8")) if rubric_path.exists() else {}
    entry = (rubric.get("cases") or {}).get(args.case, {})

    answer_text = (answer.get("final") or {}).get("answer", "")
    required = entry.get("required_substrings") or []
    forbidden = entry.get("forbidden_substrings") or []
    missing = [needle for needle in required if needle.lower() not in answer_text.lower()]
    present_forbidden = [needle for needle in forbidden if needle.lower() in answer_text.lower()]

    if not required:
        verdict = "UNGRADED_NO_FROZEN_RUBRIC"
    elif missing or present_forbidden:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    print(json.dumps({
        "case": args.case,
        "profile": args.profile,
        "answer_sha256": sha,
        "verdict": verdict,
        "missing_required": missing,
        "present_forbidden": present_forbidden,
        "rubric_present": bool(entry),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
