"""Historical-only policy boundary for write-amplification receipts."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import execution, registry  # ty: ignore[unresolved-import]

EXPECTED_PURPOSE = "immutable-write-amplification-pair"


class OperationUnavailable(RuntimeError):
    pass


def require_runner_provenance() -> None:
    raise OperationUnavailable(
        "write-amplification execution is unavailable: no tracked V2 runner "
        "references immutable-write-amplification-pair; receipts are historical"
    )


def summarize_historical(receipt: Any) -> dict[str, Any]:
    normalized = receipt.normalized()
    if normalized.get("purpose") != EXPECTED_PURPOSE:
        raise ValueError(f"unexpected purpose: {normalized.get('purpose')!r}")
    return {
        "operation": "write_amplification",
        "purpose": EXPECTED_PURPOSE,
        "status": normalized.get("status"),
        "historical_only": True,
        "execution_allowed": False,
        "performance_eligible": False,
        "evidence_class": normalized.get("evidence_class"),
        "failure_class": normalized.get("failure_class"),
        "receipt": normalized,
    }


def execute(launch: Callable[[], Any], load_receipt: Callable[[Any], Any]) -> dict[str, Any]:
    return execution.execute(
        registry.get("write_amplification"),
        launch,
        load_receipt,
        summarize_historical,
    )
