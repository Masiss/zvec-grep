"""Shared execution gate for P4 operation adapters.

The gate is intentionally small: adapters own launch and receipt loading, while
this module enforces the descriptor disposition before any launch happens.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import registry as _registry  # ty: ignore[unresolved-import]


class ExecutionRefused(RuntimeError):
    pass


def execute(
    descriptor: _registry.OperationDescriptor,
    launch: Callable[[], Any],
    load_receipt: Callable[[Any], Any],
    normalize: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    if not descriptor.execution_allowed:
        raise ExecutionRefused(
            f"{descriptor.name} is {descriptor.status}: {descriptor.notes}"
        )
    result = normalize(load_receipt(launch()))
    if result.get("purpose") != descriptor.purpose:
        raise ExecutionRefused(
            f"{descriptor.name}: receipt purpose {result.get('purpose')!r} "
            f"does not match {descriptor.purpose!r}"
        )
    return result
