"""Historical-only policy boundary for write-amplification receipts."""
from __future__ import annotations

from typing import Any

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
