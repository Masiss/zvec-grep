"""Shared paths and manifest loading for contract tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONTRACTS = Path(__file__).resolve().parents[1]
REPO_ROOT = CONTRACTS.parents[2]
MANIFEST_PATH = CONTRACTS / "golden" / "manifest.json"


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
