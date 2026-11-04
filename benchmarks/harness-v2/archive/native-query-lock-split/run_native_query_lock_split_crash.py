#!/usr/bin/env python3
"""Run the lock-split crash/kill -9 recovery test.

For each crash point:
  1. a child process creates a collection, inserts 64 docs, arms the event and
     blocks the maintenance thread;
  2. the parent waits for the marker, sends SIGKILL;
  3. a fresh verifier process opens the collection, checks the exact document
     count, inserts one more doc, closes and reopens.

This is a research-only Phase 3 extension. It never touches production.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

CASES = (
    ("flush-content-cf-after", "flush", "rocksdb_flush_cf_after", "content",
     "fts", "standard"),
    ("flush-vector-before", "flush", "vector_indexer_before_flush", "",
     "vector", "standard"),
    ("flush-vector-after", "flush", "vector_indexer_after_flush", "",
     "vector", "standard"),
    ("optimize-before-inverted-seal", "optimize",
     "optimize_dump_before_inverted_seal", "", "fts", "standard"),
    ("optimize-before-fts-seal", "optimize", "optimize_dump_before_fts_seal",
     "", "fts", "standard"),
    ("optimize-before-side-cf-drop", "optimize",
     "optimize_fts_side_cf_reset_before_drop", "", "fts", "standard"),
    ("ddl-drop-before-commit", "ddl", "ddl_drop_schema_prepared", "", "fts",
     "ddl"),
    ("ddl-drop-after-commit", "ddl", "ddl_drop_after_commit", "", "fts",
     "ddl"),
)


def _wait_for_marker(proc: subprocess.Popen, marker: Path,
                     timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file() and marker.stat().st_size > 0:
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def run_case(case: tuple[str, str, str, str, str, str], *,
             library_dir: str, output_dir: Path, worker: str,
             timeout: float) -> dict:
    name, phase, event, cf, verify_mode, child_mode = case
    if name not in {item[0] for item in CASES}:
        raise ValueError(f'unknown case: {name}')
    case_dir = output_dir / name
    try:
        if case_dir.exists():
            shutil.rmtree(case_dir)
        case_dir.mkdir(parents=True)
    except OSError as exc:
        raise RuntimeError(f"cannot prepare crash case {name}: {exc}") from exc
    collection_path = case_dir / "collection"
    marker = case_dir / "marker"
    child_log = case_dir / "child.log"
    verify_log = case_dir / "verify.log"

    child_cmd = [
        sys.executable, "-u", worker,
        "ddl-child" if child_mode == "ddl" else "child",
        "--library-dir", library_dir,
        "--path", str(collection_path),
        "--phase", phase,
        "--event", event,
        "--cf", cf,
        "--marker", str(marker),
    ]
    with child_log.open("wb") as log:
        proc = subprocess.Popen(
            child_cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        reached = _wait_for_marker(proc, marker, timeout)
        if not reached:
            if proc.poll() is None:
                try:
                    os.kill(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=30)
            return {
                "case": name,
                "phase": phase,
                "event": event,
                "cf": cf,
                "status": "FAIL_MARKER_NOT_REACHED",
                "verify_mode": verify_mode,
                "child_mode": child_mode,
                "child_returncode": proc.returncode,
                "child_log": str(child_log),
            }
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=30)

    verify_cmd = [
        sys.executable, "-u", worker,
        "verify-vector" if verify_mode == "vector" else "verify",
        "--library-dir", library_dir,
        "--path", str(collection_path),
        "--expected", "64",
    ]
    with verify_log.open("wb") as log:
        verify = subprocess.run(
            verify_cmd, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    verify_text = verify_log.read_text(encoding="utf-8", errors="replace")
    return {
        "case": name,
        "phase": phase,
        "event": event,
        "cf": cf,
        "status": "PASS" if verify.returncode == 0 else "FAIL_VERIFY",
        "verify_mode": verify_mode,
        "child_mode": child_mode,
        "child_returncode": proc.returncode,
        "marker": marker.read_text(encoding="utf-8").strip(),
        "verify_returncode": verify.returncode,
        "verify_log": str(verify_log),
        "verify_tail": verify_text[-2000:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    worker = str(Path(__file__).resolve().parent /
                 "run_native_query_lock_split_crash_worker.py")

    results = []
    for case in CASES:
        result = run_case(
            case,
            library_dir=args.library_dir,
            output_dir=output_dir,
            worker=worker,
            timeout=args.timeout,
        )
        results.append(result)
        print(f"{result['case']}: {result['status']}", flush=True)

    summary = {
        "kind": "native_query_lock_split_crash_recovery",
        "status": "PASS" if all(r["status"] == "PASS" for r in results)
                  else "FAIL",
        "cases": results,
    }
    (output_dir / "crash-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
