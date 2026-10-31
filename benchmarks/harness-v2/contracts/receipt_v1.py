"""Compatibility reader for historical M4 receipt/protocol documents (v1).

The reader never rewrites a receipt. It recognizes the three document shapes
used by the historical harness and normalizes only identity/status fields that
are safe to compare across versions. Unknown schema versions and unknown shapes
fail closed instead of being guessed.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA_VERSION = 1


class ReceiptV1Error(ValueError):
    """Base error for an invalid or unsupported receipt/protocol document."""


class UnsupportedReceiptSchema(ReceiptV1Error):
    """The document is not receipt/protocol schema version 1."""


class UnsupportedReceiptKind(ReceiptV1Error):
    """The document shape is not a recognized v1 receipt/protocol."""


class ReceiptFieldTypeError(ReceiptV1Error):
    """A recognized field has the wrong JSON type."""


_MISSING = object()


def _first(document: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = document
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                break
            value = value[part]
        else:
            return value
    return _MISSING


def _optional_str(document: Mapping[str, Any], field: str, *paths: str) -> str | None:
    value = _first(document, *paths)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, str):
        raise ReceiptFieldTypeError(f"{field} must be a string or null")
    return value


def _optional_bool(document: Mapping[str, Any], field: str, *paths: str) -> bool | None:
    value = _first(document, *paths)
    if value is _MISSING or value is None:
        return None
    if not isinstance(value, bool):
        raise ReceiptFieldTypeError(f"{field} must be a boolean or null")
    return value


def _detect_kind(document: Mapping[str, Any]) -> str:
    if "arms" in document and ("parity" in document or "measurement_status" in document):
        return "pair_result"
    if "approved_budget" in document or (
        document.get("policy_id") == "m4-benchmark-operation-policy-v1"
        and any(
            key in document
            for key in (
                "operation_outcome",
                "unit_cleanup",
                "preflight",
                "postflight_decision",
                "watchdog_summary",
            )
        )
    ):
        return "containment_receipt"
    if "contract" in document or (
        "policy_sha256" in document
        and "operation_id" in document
        and "schema_version" in document
    ):
        return "operation_plan"
    raise UnsupportedReceiptKind("unrecognized v1 receipt/protocol shape")


@dataclass(frozen=True)
class ReceiptV1:
    """A parsed historical receipt/protocol with normalized identity fields."""

    path: Path
    raw: Mapping[str, Any]
    kind: str
    schema_version: int
    purpose: str | None
    operation_id: str | None
    status: str | None
    policy_id: str | None
    policy_sha256: str | None
    benchmark_started: bool | None
    performance_eligible: bool | None
    evidence_class: str | None
    failure_class: str | None

    @classmethod
    def load(cls, path: str | Path) -> ReceiptV1:
        receipt_path = Path(path)
        try:
            document = json.loads(receipt_path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ReceiptV1Error(f"cannot read receipt {receipt_path}: {error}") from error
        except json.JSONDecodeError as error:
            raise ReceiptV1Error(f"invalid JSON in receipt {receipt_path}: {error}") from error
        if not isinstance(document, dict):
            raise ReceiptV1Error(f"receipt {receipt_path} must be a JSON object")
        return cls.from_mapping(document, path=receipt_path)

    @classmethod
    def from_mapping(
        cls, document: Mapping[str, Any], *, path: str | Path = Path("<memory>")
    ) -> ReceiptV1:
        if not isinstance(document, Mapping):
            raise ReceiptV1Error("receipt document must be a JSON object")
        version = document.get("schema_version")
        if version != RECEIPT_SCHEMA_VERSION:
            raise UnsupportedReceiptSchema(
                f"unsupported receipt schema {version!r}; expected {RECEIPT_SCHEMA_VERSION}"
            )
        return cls(
            path=Path(path),
            raw=document,
            kind=_detect_kind(document),
            schema_version=RECEIPT_SCHEMA_VERSION,
            purpose=_optional_str(
                document,
                "purpose",
                "purpose",
                "approved_budget.purpose",
                "contract.purpose",
                "policy_context.purpose",
            ),
            operation_id=_optional_str(
                document, "operation_id", "operation_id", "policy_context.operation_id"
            ),
            status=_optional_str(document, "status", "status"),
            policy_id=_optional_str(document, "policy_id", "policy_id"),
            policy_sha256=_optional_str(
                document,
                "policy_sha256",
                "policy_sha256",
                "policy_context.policy_sha256",
            ),
            benchmark_started=_optional_bool(
                document, "benchmark_started", "benchmark_started"
            ),
            performance_eligible=_optional_bool(
                document, "performance_eligible", "performance_eligible"
            ),
            evidence_class=_optional_str(
                document,
                "evidence_class",
                "evidence_class",
                "policy_context.evidence_class",
                "contract.evidence_class",
            ),
            failure_class=_optional_str(document, "failure_class", "failure_class"),
        )

    def normalized(self) -> dict[str, Any]:
        """Return the stable cross-version identity/status projection."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "purpose": self.purpose,
            "operation_id": self.operation_id,
            "status": self.status,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "benchmark_started": self.benchmark_started,
            "performance_eligible": self.performance_eligible,
            "evidence_class": self.evidence_class,
            "failure_class": self.failure_class,
        }


def read_receipt(path: str | Path) -> ReceiptV1:
    """Read a v1 receipt/protocol without modifying it."""
    return ReceiptV1.load(path)
