"""Resolved policy/configuration for the Rust lifecycle operation.

The research containment runner is injected by the CLI shim so this package
stays importable without the research tools directory on ``sys.path``.
"""
from __future__ import annotations

import pathlib
from types import ModuleType
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[5]
CONTRACT: dict[str, Any] = {}
WORKLOAD: dict[str, Any] = {}
RUST_TARGET_DIR: pathlib.Path | None = None
CODE_EXTENSIONS = {".rs", ".cc", ".cpp", ".h", ".hpp", ".c", ".py", ".cs"}
TEST_NAME = ""
SAMPLE_INTERVAL_MS = 500
containment: ModuleType | Any = None
measurement_artifact: ModuleType | Any = None


def configure(containment_module: Any, measurement_artifact_module: Any) -> None:
    global CONTRACT, WORKLOAD, RUST_TARGET_DIR, TEST_NAME, containment, measurement_artifact
    containment = containment_module
    measurement_artifact = measurement_artifact_module
    CONTRACT = containment.BENCHMARK_POLICY["rust_pipeline_lifecycle_pair"]
    WORKLOAD = CONTRACT["workload"]
    RUST_TARGET_DIR = containment.M4_RUST_LIFECYCLE_TARGET
    TEST_NAME = WORKLOAD["test_name"]
