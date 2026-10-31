"""Pure pair-continuation rules for the Rust lifecycle executor."""
from __future__ import annotations

from typing import Any


def arm_result_allows_pair_continuation(result: dict[str, Any]) -> bool:
    """Continue after clean workload completion with accounting caveats, never after a safety failure."""
    if result.get("status") not in {
        "PASS",
        "COMPLETED_WITH_WORKSPACE_UNCERTAINTY",
        "COMPLETED_WITH_WORKSPACE_WARNINGS",
    }:
        return False
    cleanup = result.get("unit_cleanup") or result.get("controller_cleanup") or {}
    summary = result.get("watchdog_summary") or {}
    postflight = result.get("postflight_decision") or {}
    return (
        result.get("workload_returncode") == 0
        and result.get("watchdog_abort") is None
        and result.get("safety_outcome")
        in {
            "SAFE_COMPLETE",
            "NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED",
        }
        and summary.get("hard_safety_telemetry_complete") is True
        and cleanup.get("verified") is True
        and cleanup.get("unit_collected") is True
        and postflight.get("continuation_allowed") is True
    )
