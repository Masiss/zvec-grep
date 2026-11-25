#!/usr/bin/env python3
"""Build the exact .cs-only evaluation corpus from source.lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath


LOCK = Path(__file__).with_name("source.lock.json")
FIXED_MTIME = 1_700_000_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe source path in lock: {value!r}")
    return path


def materialize(lock_path: Path, destination: Path) -> dict[str, object]:
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    if lock.get("schema_version") != 1 or lock.get("status", "").split(";")[0] != "source_scope_frozen":
        raise ValueError("source lock is not a frozen schema v1 lock")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination must be absent or empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    expected_total = 0
    for repo in lock["repositories"]:
        archive = Path(repo["archive"])
        archive_bytes = archive.read_bytes()
        if sha256(archive_bytes) != repo["archive_sha256"]:
            raise ValueError(f"archive digest mismatch for {repo['name']}")

        with tarfile.open(archive, mode="r:gz") as tar:
            members = [member for member in tar.getmembers() if member.isfile()]
            roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
            if len(roots) != 1:
                raise ValueError(f"archive must have one top-level directory: {repo['name']}")
            archive_root = next(iter(roots))
            source_members: dict[str, tarfile.TarInfo] = {}
            for member in members:
                member_path = PurePosixPath(member.name)
                if not member_path.is_relative_to(archive_root):
                    raise ValueError(f"unexpected archive member root: {member.name}")
                relative = member_path.relative_to(archive_root)
                if relative.as_posix().endswith(".cs"):
                    if relative.as_posix() in source_members:
                        raise ValueError(f"duplicate source path in archive: {repo['name']}/{relative}")
                    source_members[relative.as_posix()] = member

            expected = {item["path"].split("/", 1)[1]: item for item in repo["files"]}
            if set(source_members) != set(expected):
                raise ValueError(f"archive .cs inventory differs from source lock for {repo['name']}")

            for relative, item in expected.items():
                source = tar.extractfile(source_members[relative])
                if source is None:
                    raise ValueError(f"unable to read archive member: {repo['name']}/{relative}")
                data = source.read()
                if sha256(data) != item["sha256"] or len(data) != item["bytes"]:
                    raise ValueError(f"source digest or size mismatch: {item['path']}")
                output = destination.joinpath(*safe_relative_path(item["path"]).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                os.chmod(output, 0o644)
                os.utime(output, (FIXED_MTIME, FIXED_MTIME))
                expected_total += 1

    actual_paths = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    expected_paths = {
        item["path"]
        for repo in lock["repositories"]
        for item in repo["files"]
    }
    if actual_paths != expected_paths or len(actual_paths) != expected_total:
        raise ValueError("materialized corpus inventory differs from the frozen source lock")

    return {
        "corpus_id": lock["corpus_id"],
        "destination": str(destination),
        "repositories": len(lock["repositories"]),
        "source_files": expected_total,
        "source_lock_sha256": sha256(lock_bytes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new or empty directory for the .cs-only corpus")
    parser.add_argument("--lock", type=Path, default=LOCK, help="source.lock.json (default: alongside this script)")
    args = parser.parse_args()
    try:
        receipt = materialize(args.lock, args.destination)
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        parser.error(str(error))
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
