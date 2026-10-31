"""Corpus and artifact identity helpers for the Rust lifecycle pair."""
from __future__ import annotations

import pathlib
from typing import Any


def validate_full_corpus_reference(workload: dict[str, Any]) -> None:
    """Reject an unprovenanced full-corpus reference unless it is explicitly blocked."""
    reference = workload.get("full_corpus_reference")
    if not reference:
        return
    if reference.get("provenance_status") == "blocked_missing_source":
        return
    missing = [
        key
        for key in ("source_repo", "source_commit", "source_selection")
        if not reference.get(key)
    ]
    if missing:
        raise ValueError("full corpus reference lacks provenance: " + ", ".join(missing))


def capture_pair_artifacts(
    binary: pathlib.Path, library_path: pathlib.Path, file_sha256
) -> dict[str, str]:
    binary = binary.resolve(strict=True)
    library_path = library_path.resolve(strict=True)
    library = (library_path / "libzvec_c_api.so").resolve(strict=True)
    if library.parent != library_path or not binary.is_file() or not library.is_file():
        raise ValueError("binary or native library path is not an exact regular artifact")
    return {
        "binary_path": str(binary),
        "binary_sha256": file_sha256(binary),
        "library_path": str(library_path),
        "library_file": str(library),
        "library_sha256": file_sha256(library),
    }


def verify_pair_artifacts(expected: dict[str, str], file_sha256) -> None:
    actual = capture_pair_artifacts(
        pathlib.Path(expected["binary_path"]), pathlib.Path(expected["library_path"]), file_sha256
    )
    if actual != expected:
        raise ValueError(
            "pair binary or native library identity changed; refusing continuation"
        )
