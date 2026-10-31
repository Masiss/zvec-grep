#!/usr/bin/env python3
"""Crash-loop recovery test on one collection.

Three successive crash/recovery cycles:
  1. kill during content-CF flush;
  2. kill after the vector indexer flush;
  3. kill during optimize before the FTS seal.

Each cycle opens the same collection, inserts 16 docs, crashes, then a fresh
verifier opens/closes/reopens and checks the exact cumulative count.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CYCLES = (
    ("cycle-1-flush-content", "flush", "rocksdb_flush_cf_after", "content",
     0, 16, 16, "fts"),
    ("cycle-2-flush-vector-after", "flush", "vector_indexer_after_flush", "",
     16, 16, 32, "vector"),
    ("cycle-3-optimize-fts-seal", "optimize", "optimize_dump_before_fts_seal",
     "", 32, 16, 48, "fts"),
)


def wait_marker(proc: subprocess.Popen, marker: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file() and marker.stat().st_size > 0:
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"cannot prepare output dir: {exc}") from exc
    collection = output_dir / "collection"
    worker = str(Path(__file__).resolve().parent /
                 "run_native_query_lock_split_crash_worker.py")

    results = []
    for name, phase, event, cf, base, count, expected, verify_mode in CYCLES:
        cycle_dir = output_dir / name
        try:
            cycle_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit(f"cannot prepare cycle dir: {exc}") from exc
        marker = cycle_dir / "marker"
        if marker.exists():
            marker.unlink()
        child_log = cycle_dir / "child.log"
        verify_log = cycle_dir / "verify.log"
        child_cmd = [
            sys.executable, "-u", worker, "cycle-child",
            "--library-dir", args.library_dir,
            "--path", str(collection),
            "--phase", phase,
            "--event", event,
            "--cf", cf,
            "--marker", str(marker),
            "--base-id", str(base),
            "--count", str(count),
            "--expected-after", str(expected),
        ]
        with child_log.open("wb") as log:
            proc = subprocess.Popen(
                child_cmd, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            reached = wait_marker(proc, marker, args.timeout)
            if not reached:
                if proc.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=30)
                results.append({
                    "cycle": name, "status": "FAIL_MARKER_NOT_REACHED",
                    "child_returncode": proc.returncode,
                    "child_log": str(child_log),
                })
                break
            with contextlib.suppress(ProcessLookupError):
                os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=30)

        verify_mode_arg = ("verify-loop-vector" if verify_mode == "vector"
                           else "verify-loop")
        verify_cmd = [
            sys.executable, "-u", worker, verify_mode_arg,
            "--library-dir", args.library_dir,
            "--path", str(collection),
            "--expected", str(expected),
        ]
        with verify_log.open("wb") as log:
            verify = subprocess.run(
                verify_cmd, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        verify_tail = verify_log.read_text(
            encoding="utf-8", errors="replace")[-2000:]
        results.append({
            "cycle": name,
            "phase": phase,
            "event": event,
            "cf": cf,
            "base_id": base,
            "inserted": count,
            "expected": expected,
            "verify_mode": verify_mode,
            "status": "PASS" if verify.returncode == 0 else "FAIL_VERIFY",
            "child_returncode": proc.returncode,
            "marker": marker.read_text(encoding="utf-8").strip(),
            "verify_returncode": verify.returncode,
            "child_log": str(child_log),
            "verify_log": str(verify_log),
            "verify_tail": verify_tail,
        })
        print(f"{name}: {results[-1]['status']}", flush=True)
        if results[-1]["status"] != "PASS":
            break

    summary = {
        "kind": "native_query_lock_split_crash_loop",
        "collection": str(collection),
        "status": "PASS" if results and all(
            r["status"] == "PASS" for r in results) else "FAIL",
        "cycles": results,
    }
    (output_dir / "crash-loop-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
