"""Read-only access to the frozen M4 benchmark policy schema version 1.

This module deliberately contains no thresholds and no operation branching.
Callers ask for a named contract section or a purpose; the JSON file remains
the single source of authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

POLICY_SCHEMA_VERSION = 1


class PolicyV1Error(ValueError):
    """Base error for an invalid or unsupported policy document."""


class UnsupportedPolicySchema(PolicyV1Error):
    """The document is not policy schema version 1."""


class UnknownPolicySection(PolicyV1Error):
    """The requested contract section does not exist."""


class AmbiguousPolicyPurpose(PolicyV1Error):
    """More than one section declares the same purpose."""


def _load_json_object(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PolicyV1Error(f"cannot read policy {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PolicyV1Error(f"invalid JSON in policy {path}: {error}") from error
    if not isinstance(document, dict):
        raise PolicyV1Error(f"policy {path} must be a JSON object")
    return document


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PolicyV1:
    """A parsed, read-only policy v1 document and its identity."""

    path: Path
    raw: Mapping[str, Any]
    sha256: str

    @classmethod
    def load(cls, path: str | Path, expected_sha256: str | None = None) -> PolicyV1:
        policy_path = Path(path)
        digest = sha256_file(policy_path)
        if expected_sha256 is not None and digest != expected_sha256:
            raise PolicyV1Error(
                f"policy SHA-256 mismatch for {policy_path}: "
                f"expected {expected_sha256}, got {digest}"
            )
        document = _load_json_object(policy_path)
        return cls.from_mapping(document, path=policy_path, sha256=digest)

    @classmethod
    def from_mapping(
        cls,
        document: Mapping[str, Any],
        *,
        path: str | Path = Path("<memory>"),
        sha256: str = "0" * 64,
    ) -> PolicyV1:
        policy_path = Path(path)
        version = document.get("schema_version")
        if version != POLICY_SCHEMA_VERSION:
            raise UnsupportedPolicySchema(
                f"unsupported policy schema {version!r} in {policy_path}; "
                f"expected {POLICY_SCHEMA_VERSION}"
            )
        if not isinstance(document.get("policy_id"), str):
            raise PolicyV1Error(f"policy {policy_path} is missing a string policy_id")
        return cls(path=policy_path, raw=document, sha256=sha256)

    @property
    def schema_version(self) -> int:
        return POLICY_SCHEMA_VERSION

    @property
    def policy_id(self) -> str:
        return self.raw["policy_id"]

    def section(self, name: str) -> Mapping[str, Any]:
        """Return one named contract section; unknown names fail closed."""
        value = self.raw.get(name)
        if not isinstance(value, dict):
            raise UnknownPolicySection(f"policy has no contract section {name!r}")
        return value

    def sections_for_purpose(self, purpose: str) -> tuple[str, ...]:
        """Return every section whose declared purpose matches."""
        return tuple(
            name
            for name, value in self.raw.items()
            if isinstance(value, dict) and value.get("purpose") == purpose
        )

    def operation_contract(self, purpose: str) -> Mapping[str, Any]:
        """Return the single section for a purpose, refusing ambiguity."""
        names = self.sections_for_purpose(purpose)
        if not names:
            raise UnknownPolicySection(f"policy has no contract for purpose {purpose!r}")
        if len(names) != 1:
            raise AmbiguousPolicyPurpose(
                f"purpose {purpose!r} is declared by multiple sections: {', '.join(names)}"
            )
        return self.section(names[0])
