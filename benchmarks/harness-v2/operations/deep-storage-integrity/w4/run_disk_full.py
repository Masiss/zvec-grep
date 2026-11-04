#!/usr/bin/env python3
"""W4 write-exhaustion proxy: RLIMIT_FSIZE instead of true ENOSPC.

A true disk-full test needs an isolated filesystem/mount privilege that this
host does not grant. RLIMIT_FSIZE is used as a bounded write-failure proxy and
is labelled as such in the receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import resource
import shutil
import subprocess
import sys


def write_sources(root: pathlib.Path, count: int = 80) -> None:
    root.mkdir(parents=True, exist_ok=True)
    body = "pub fn disk_{i}(value: u32) -> u32 {{\n    // disk needle {i}\n    value.wrapping_add({i})\n}}\n"
    for i in range(count):
        (root / f"d_{i:03d}.rs").write_text(body.format(i=i))


def env_for(home: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ZVEC_GREP_HOME"] = str(home)
    env["ZVEC_GREP_MODE"] = "direct"
    return env


def run(binary: pathlib.Path, cwd: pathlib.Path, home: pathlib.Path, *args: str,
        limit: int | None = None) -> subprocess.CompletedProcess:
    def preexec() -> None:
        if limit is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    return subprocess.run(
        [str(binary), *args],
        cwd=cwd,
        env=env_for(home),
        capture_output=True,
        text=True,
        check=False,
        preexec_fn=preexec,
    )


def verify(binary: pathlib.Path, cwd: pathlib.Path, home: pathlib.Path) -> tuple[int, dict]:
    proc = run(binary, cwd, home, "--verify", "--json")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {}
    return proc.returncode, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--work", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--limit", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    binary = args.binary.resolve()
    work = args.work
    if work.exists():
        shutil.rmtree(work)
    workspace = work / "ws_diskfull"
    home = work / "home"
    write_sources(workspace)
    initial = run(
        binary, workspace, home, "--index", "--name", "w4diskfull",
        "--embedding", "local/potion-code-16m-v2",
        "--model-cache", str(pathlib.Path.home() / ".zvec-grep" / "models"),
    )
    if initial.returncode != 0:
        raise RuntimeError(f"initial index failed: {initial.stderr[-2000:]}")
    initial_verify_code, initial_verify = verify(binary, workspace, home)

    with (workspace / "d_007.rs").open("a") as handle:
        handle.write("\n// disk edit\n")
    failing = run(binary, workspace, home, "--index", limit=args.limit)
    after_failure_code, after_failure = verify(binary, workspace, home)
    verify_under_limit = run(
        binary, workspace, home, "--verify", "--json", limit=args.limit
    )
    verify_under_limit_code = verify_under_limit.returncode
    try:
        verify_under_limit_payload = json.loads(verify_under_limit.stdout)
    except json.JSONDecodeError:
        verify_under_limit_payload = {}
    recovery = run(binary, workspace, home, "--index")
    after_recovery_code, after_recovery = verify(binary, workspace, home)

    payload = {
        "method": "RLIMIT_FSIZE write-failure proxy; true ENOSPC OPERATION_BLOCKED (no isolated mount privilege)",
        "limit_bytes": args.limit,
        "initial_index_code": initial.returncode,
        "initial_verify_code": initial_verify_code,
        "initial_status": (initial_verify.get("report") or {}).get("status"),
        "failing_index_code": failing.returncode,
        "failing_index_stderr_tail": failing.stderr[-800:],
        "verify_after_failure_code": after_failure_code,
        "verify_after_failure_status": (after_failure.get("report") or {}).get("status"),
        "verify_under_limit_code": verify_under_limit_code,
        "verify_under_limit_status": (verify_under_limit_payload.get("report") or {}).get("status"),
        "recovery_index_code": recovery.returncode,
        "verify_after_recovery_code": after_recovery_code,
        "verify_after_recovery_status": (after_recovery.get("report") or {}).get("status"),
        "ok": (
            initial.returncode == 0
            and initial_verify_code == 0
            and failing.returncode != 0
            and after_failure_code == 0
            and verify_under_limit_code == 0
            and recovery.returncode == 0
            and after_recovery_code == 0
        ),
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
