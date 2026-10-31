"""Pure preflight/safety predicates for the Rust lifecycle pair."""
from __future__ import annotations

from typing import Any


def is_qgroup_headroom_gate_failure(error: BaseException) -> bool:
    return "insufficient qgroup headroom for the next workspace allocation" in str(error)


def require_pair_workspace_headroom(quota: dict[str, Any], additional_bytes: int) -> None:
    live = quota["live_headroom"]
    required = live["required_free_bytes"] + additional_bytes
    if live["available_bytes"] < required:
        raise RuntimeError(
            "insufficient qgroup headroom for the next workspace allocation: "
            f"available={live['available_bytes']}, additional={additional_bytes}, "
            f"required_free={live['required_free_bytes']}, total_required={required}"
        )
