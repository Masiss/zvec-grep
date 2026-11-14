#!/usr/bin/env python3
"""Negative tests for the E6 verifier: contract and provenance gates."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def verify(dataset: Path, result: dict) -> int:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(HERE / "verify_e6.py"),
                "--dataset",
                str(dataset),
                "--results",
                str(path),
            ],
            capture_output=True,
            text=True,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    base = json.loads(args.result.read_text(encoding="utf-8"))
    assert verify(args.dataset, base) == 0

    hidden = json.loads(json.dumps(base))
    for row in hidden["results"]:
        if row["items"]:
            row["items"][0]["matched_by"] = "vector"
            break
    assert verify(args.dataset, hidden) == 2

    missing = json.loads(json.dumps(base))
    del missing["run"]["measurement_contract"]
    assert verify(args.dataset, missing) == 2

    failed = json.loads(json.dumps(base))
    failed["run"]["measurement_contract"]["status"] = "FAIL"
    assert verify(args.dataset, failed) == 2
    print("OK: verifier accepts a clean run and rejects hidden provenance/contract failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
