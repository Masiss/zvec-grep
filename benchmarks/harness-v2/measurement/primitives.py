"""Measurement identity, clock, and receipt helpers shared by M4 adapters.

This module is standalone: it imports only the standard library so it can be
loaded by historical scripts that add ``benchmarks/harness-v2`` to ``sys.path``.
No thresholds or containment policy live here.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time
from datetime import UTC, datetime
from typing import Any


def strict_json_text(text: str | bytes) -> Any:
    """Malformed control/evidence data must raise, never become a default/PASS."""
    try:
        return json.loads(text)
    except (ValueError, UnicodeError):
        raise


def strict_json_file(path: pathlib.Path) -> Any:
    try:
        return strict_json_text(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def boottime_ns() -> int:
    """Return suspend-inclusive monotonic time for watchdog gap checks."""
    clock_id = getattr(time, "CLOCK_BOOTTIME", None)
    if clock_id is None:
        raise RuntimeError("CLOCK_BOOTTIME is required for contention monitoring")
    return time.clock_gettime_ns(clock_id)


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    """Hash the entire file with bounded buffering, preserving the exact digest gate."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def directory_sha256(path: pathlib.Path) -> str:
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"expected directory for tree digest: {root}")
    digest = hashlib.sha256()
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"symlink not allowed in pinned input tree: {item}")
        if item.is_file():
            relative = item.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
