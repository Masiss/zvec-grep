"""Receipt adapters for diagnostic-only operation families."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import execution, registry  # ty: ignore[unresolved-import]


class DiagnosticAdapterError(ValueError):
    pass


def summarize(receipt: Any, expected_purpose: str) -> dict[str, Any]:
    normalized = receipt.normalized()
    if normalized.get("purpose") != expected_purpose:
        raise DiagnosticAdapterError(
            f"unexpected purpose: {normalized.get('purpose')!r}"
        )
    return {
        "diagnostic": True,
        "purpose": expected_purpose,
        "status": normalized.get("status"),
        "benchmark_started": normalized.get("benchmark_started") is True,
        "performance_eligible": False,
        "evidence_class": normalized.get("evidence_class"),
        "failure_class": normalized.get("failure_class"),
        "receipt": normalized,
    }


def execute(
    name: str, launch: Callable[[], Any], load_receipt: Callable[[Any], Any]
) -> dict[str, Any]:
    descriptor = registry.get(name)
    if descriptor.purpose is None:
        raise execution.ExecutionRefused(f"{name}: no diagnostic purpose")
    return execution.execute(
        descriptor,
        launch,
        load_receipt,
        lambda receipt: summarize(receipt, descriptor.purpose),
    )
