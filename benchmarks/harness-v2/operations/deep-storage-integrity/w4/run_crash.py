#!/usr/bin/env python3
"""W4 crash/recovery matrix: SIGKILL during staged incremental indexing."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time


def write_sources(root: pathlib.Path, count: int = 80) -> None:
    root.mkdir(parents=True, exist_ok=True)
    body = "pub fn item_{i}(value: u32) -> u32 {{\n    // needle-{i}\n    value.wrapping_add({i})\n}}\n"
    for i in range(count):
        (root / f"f_{i:03d}.rs").write_text(body.format(i=i))


def env_for(home: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env["ZVEC_GREP_HOME"] = str(home)
    env["ZVEC_GREP_MODE"] = "direct"
    return env


def run(binary: pathlib.Path, cwd: pathlib.Path, home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(binary), *args],
        cwd=cwd,
        env=env_for(home),
        capture_output=True,
        text=True,
        check=False,
    )


def generation(workspace: pathlib.Path) -> str | None:
    manifest = workspace / ".zvec-grep" / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text()).get("storageGeneration")
    except json.JSONDecodeError:
        return None


def verify(binary: pathlib.Path, cwd: pathlib.Path, home: pathlib.Path) -> tuple[int, dict]:
    proc = run(binary, cwd, home, "--verify", "--json")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"parse_error": proc.stdout}
    return proc.returncode, payload


def fts(binary: pathlib.Path, cwd: pathlib.Path, home: pathlib.Path, query: str) -> int:
    return run(binary, cwd, home, "--fts", query, "--limit", "3").returncode


def build_case(
    binary: pathlib.Path, work: pathlib.Path, home: pathlib.Path, label: str
) -> pathlib.Path:
    if work.exists():
        shutil.rmtree(work)
    # Unique workspace name + directory avoids the shared registry rejecting a
    # name already used by an unrelated fixture.
    workspace = work / f"ws_{label.replace('.', '_')}"
    write_sources(workspace)
    proc = run(
        binary,
        workspace,
        home,
        "--index",
        "--name",
        f"w4crash_{label.replace('.', '_')}",
        "--embedding",
        "local/potion-code-16m-v2",
        "--model-cache",
        str(pathlib.Path.home() / ".zvec-grep" / "models"),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"initial index failed: {proc.stderr[-2000:]}")
    code, report = verify(binary, workspace, home)
    if code != 0:
        raise RuntimeError(f"initial verify failed: {code} {report}")
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--work", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--cases", default="0.05,0.20,0.60,1.20,after-publish")
    args = parser.parse_args()
    binary = args.binary.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    shared_home = args.work / "home"

    results: list[dict] = []
    for label in args.cases.split(","):
        case_dir = args.work / f"case-{label.replace('.', '_')}"
        workspace = build_case(binary, case_dir, shared_home, label)
        home = shared_home
        before = generation(workspace)
        # One-file change forces a staged incremental generation.
        with (workspace / "f_007.rs").open("a") as handle:
            handle.write("\n// crash edit\n")
        proc = subprocess.Popen(
            [str(binary), "--index"],
            cwd=workspace,
            env=env_for(home),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        killed = False
        published = False
        if label == "after-publish":
            deadline = time.time() + 30
            while time.time() < deadline:
                if generation(workspace) != before:
                    published = True
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGKILL)
                        killed = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
        else:
            time.sleep(float(label))
            if proc.poll() is None:
                proc.send_signal(signal.SIGKILL)
                killed = True
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        after = generation(workspace)
        code_after_kill, report_after_kill = verify(binary, workspace, home)
        fts_after_kill = fts(binary, workspace, home, "needle-7")
        recovery = run(binary, workspace, home, "--index")
        code_after_recovery, report_after_recovery = verify(binary, workspace, home)
        fts_after_recovery = fts(binary, workspace, home, "needle-7")
        results.append(
            {
                "case": label,
                "killed": killed,
                "published": published,
                "generation_before": before,
                "generation_after_kill": after,
                "manifest_switched": before != after,
                "verify_after_kill_code": code_after_kill,
                "verify_after_kill_status": (report_after_kill.get("report") or {}).get("status"),
                "fts_after_kill_code": fts_after_kill,
                "recovery_code": recovery.returncode,
                "verify_after_recovery_code": code_after_recovery,
                "verify_after_recovery_status": (report_after_recovery.get("report") or {}).get("status"),
                "fts_after_recovery_code": fts_after_recovery,
                "ok": (
                    code_after_kill == 0
                    and fts_after_kill == 0
                    and recovery.returncode == 0
                    and code_after_recovery == 0
                    and fts_after_recovery == 0
                    and (killed or label == "after-publish")
                ),
            }
        )
        print(results[-1])

    payload = {
        "binary": str(binary),
        "cases": results,
        "passed": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"{payload['passed']} passed, {payload['failed']} failed")
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
