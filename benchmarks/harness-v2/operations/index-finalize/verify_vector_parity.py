#!/usr/bin/env python3
"""Targeted vector-parity validation for the V1 fixture.

This does not replace the 45-cell matrix; it fills the runner's missing vector
route guard on a preserved representative workspace.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import re
import socket
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[4]
BIN = ROOT / "rust/target/release/zg"
PATH_PATTERN = re.compile(
    r"(?:^|\s)((?:src/)?[A-Za-z0-9_./-]+\.(?:rs|txt))(?::|$)",
    re.MULTILINE,
)


def run(args: list[str], env: dict[str, str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BIN), *args],
        env=env,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=1800,
    )


def paths(output: str) -> list[str]:
    return sorted({match.group(1) for match in PATH_PATTERN.finditer(output)})


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--state", required=True, type=pathlib.Path)
    parser.add_argument("--receipt", required=True, type=pathlib.Path)
    args = parser.parse_args()

    root = args.root.resolve()
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(state / "home")
    env["USERPROFILE"] = str(state / "home")
    env["ZVEC_GREP_HOME"] = str(state / "runtime")

    def zg(command: list[str]) -> str:
        result = run(command, env, root)
        if result.returncode != 0:
            raise RuntimeError(f"{command}: {result.stderr[-2000:]}")
        return result.stdout

    checks: list[dict[str, object]] = []
    raw: dict[str, str] = {}

    def query(term: str) -> list[str]:
        output = zg(["--vector", term, "--mode", "server", "--no-color"])
        raw[term] = output
        return paths(output)

    zg(["--server", "on", "--listen", f"127.0.0.1:{free_port()}"])
    try:
        zg(["--index", "--mode", "server", "--no-color"])
        found = query("finalize_0000")
        checks.append(
            {
                "check": "initial_vector_present",
                "ok": "src/chunk_00/file_0000.rs" in found,
                "result": found,
            }
        )

        inserted = root / "src/zz/insert_vector_9999.rs"
        inserted.parent.mkdir(parents=True, exist_ok=True)
        body = 'pub const VECTOR_INSERT_9999: &str = "finalize_vector_insert_9999";\n'
        inserted.write_text((body * ((8192 + len(body) - 1) // len(body)))[:8192])
        zg(["--index", "--mode", "server", "--no-color"])
        found = query("finalize_vector_insert_9999")
        checks.append(
            {
                "check": "insert_vector_present",
                "ok": "src/zz/insert_vector_9999.rs" in found,
                "result": found,
            }
        )

        deleted = root / "src/chunk_00/file_0000.rs"
        deleted.unlink()
        zg(["--index", "--mode", "server", "--no-color"])
        found = query("finalize_0000")
        checks.append(
            {
                "check": "delete_vector_absent",
                "ok": "src/chunk_00/file_0000.rs" not in found,
                "result": found,
            }
        )

        before = set(query("finalize_0010"))
        zg(["--server", "off"])
        zg(["--server", "on", "--listen", f"127.0.0.1:{free_port()}"])
        after = set(query("finalize_0010"))
        checks.append(
            {
                "check": "restart_vector_parity",
                "ok": before == after,
                "before": sorted(before),
                "after": sorted(after),
            }
        )
    finally:
        with contextlib.suppress(Exception):
            zg(["--server", "off"])

    receipt = {
        "protocol_id": "index-finalize-qualification-v1",
        "scope": "targeted vector parity on one preserved workspace",
        "root": str(root),
        "checks": checks,
        "raw_outputs": raw,
        "all_pass": all(check["ok"] for check in checks),
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"all_pass": receipt["all_pass"], "checks": checks}, indent=2))
    return 0 if receipt["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
