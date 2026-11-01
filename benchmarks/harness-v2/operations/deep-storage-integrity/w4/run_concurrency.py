#!/usr/bin/env python3
"""W4 reader/verify concurrency and indexing overlap checks."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys


def env_for(home: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ZVEC_GREP_HOME"] = str(home)
    env["ZVEC_GREP_MODE"] = "direct"
    return env


def run(binary: pathlib.Path, workspace: pathlib.Path, home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(binary), *args],
        cwd=workspace,
        env=env_for(home),
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--workspace", type=pathlib.Path, required=True)
    parser.add_argument("--home", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    workspace = args.workspace.resolve()
    home = args.home.resolve()

    def verify() -> dict:
        proc = run(binary, workspace, home, "--verify", "--json")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {}
        return {"code": proc.returncode, "status": (payload.get("report") or {}).get("status")}

    def fts() -> dict:
        proc = run(binary, workspace, home, "--fts", "changed token", "--limit", "3", "--compact")
        return {"code": proc.returncode}

    phase1: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(verify) for _ in range(8)] + [pool.submit(fts) for _ in range(8)]
        for future in futures:
            phase1.append(future.result())

    target = workspace / "f_000.rs"
    original = target.read_text()
    for index in range(30):
        with (workspace / f"f_{index:03d}.rs").open("a") as handle:
            handle.write(f"\n// concurrency edit {index}\n")
    writer = subprocess.Popen(
        [str(binary), "--index"],
        cwd=workspace,
        env=env_for(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    during: list[dict] = []
    while writer.poll() is None:
        during.append(verify())
        if len(during) > 30:
            break
    writer.wait()
    after_writer = verify()
    after_fts = fts()

    for index in range(30):
        path = workspace / f"f_{index:03d}.rs"
        path.write_text(original if index == 0 else path.read_text().split("\n// concurrency edit", 1)[0] + "\n")
    restore = run(binary, workspace, home, "--index")
    restored = verify()

    payload = {
        "binary": str(binary),
        "workspace": str(workspace),
        "phase1_parallel_verify_and_fts": phase1,
        "phase1_ok": all(item["code"] == 0 for item in phase1),
        "writer_exit": writer.returncode,
        "verify_during_writer": during,
        "verify_during_writer_ok": all(item["code"] in (0, 2) and item["status"] != "CORRUPT" for item in during),
        "verify_after_writer": after_writer,
        "fts_after_writer": after_fts,
        "restore_index_exit": restore.returncode,
        "verify_after_restore": restored,
        "ok": (
            all(item["code"] == 0 for item in phase1)
            and writer.returncode == 0
            and all(item["code"] in (0, 2) and item["status"] != "CORRUPT" for item in during)
            and after_writer["code"] == 0
            and after_fts["code"] == 0
            and restore.returncode == 0
            and restored["code"] == 0
        ),
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
