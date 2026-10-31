"""Runtime provenance manifest reader for the Harness V2 closure.

The manifest pins the files a clean checkout needs to import and run the V2
adapter boundary. It is intentionally separate from the golden receipt
manifest: receipts are evidence, runtime modules are executable dependencies.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from typing import Any


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeFile:
    path: str
    sha256: str
    role: str


def sha256_file(path: pathlib.Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load(path: str | pathlib.Path) -> dict[str, Any]:
    try:
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProvenanceError(f"cannot read runtime provenance: {error}") from error
    if document.get("schema_version") != 1:
        raise ProvenanceError("unsupported runtime provenance schema")
    files = document.get("files")
    if not isinstance(files, list):
        raise ProvenanceError("runtime provenance requires a files list")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ProvenanceError("runtime provenance entries must be objects")
        for key in ("path", "sha256", "role"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise ProvenanceError(f"runtime provenance entry missing {key}")
        if item["path"] in seen:
            raise ProvenanceError(f"duplicate runtime provenance path: {item['path']}")
        seen.add(item["path"])
    return document


def files(document: dict[str, Any]) -> tuple[RuntimeFile, ...]:
    return tuple(
        RuntimeFile(item["path"], item["sha256"], item["role"])
        for item in document["files"]
    )


def verify(document: dict[str, Any], repo_root: str | pathlib.Path) -> None:
    root = pathlib.Path(repo_root)
    for item in files(document):
        path = root / item.path
        if not path.is_file():
            raise ProvenanceError(f"missing runtime file: {item.path}")
        actual = sha256_file(path)
        if actual != item.sha256:
            raise ProvenanceError(
                f"runtime provenance mismatch for {item.path}: {actual} != {item.sha256}"
            )
