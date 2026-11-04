"""Receipt adapter for the lock-contention microbench family."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import execution, registry  # ty: ignore[unresolved-import]

EXPECTED_PURPOSE = "m4-lock-contention-microbench"


class LockContentionAdapterError(ValueError):
    pass


def summarize(receipt: Any) -> dict[str, Any]:
    normalized = receipt.normalized()
    if normalized.get("purpose") != EXPECTED_PURPOSE:
        raise LockContentionAdapterError(
            f"unexpected purpose: {normalized.get('purpose')!r}"
        )
    started = normalized.get("benchmark_started") is True
    eligible = started and normalized.get("performance_eligible") is True
    return {
        "operation": "lock_contention_microbench",
        "purpose": EXPECTED_PURPOSE,
        "status": normalized.get("status"),
        "measurement_status": normalized.get("status"),
        "benchmark_started": started,
        "performance_eligible": eligible,
        "evidence_class": normalized.get("evidence_class"),
        "failure_class": normalized.get("failure_class"),
        "receipt": normalized,
    }


def execute(launch: Callable[[], Any], load_receipt: Callable[[Any], Any]) -> dict[str, Any]:
    return execution.execute(
        registry.get("lock_contention_microbench"),
        launch,
        load_receipt,
        summarize,
    )
