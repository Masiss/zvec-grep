#!/usr/bin/env python3
"""Prepare a disposable cppjieba header overlay for the M4 cache-lifecycle prototype."""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import tempfile

M4_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = M4_ROOT.parents[1]
DEFAULT_SOURCE = (REPOSITORY_ROOT / ".local/native/zvec-native-profile/thirdparty/cppjieba/"
                  "cppjieba-5.6.7/include/cppjieba")
DEFAULT_OUTPUT = M4_ROOT / ".work/scratch/cppjieba-cache-overlay-safe"
PATCH = M4_ROOT / "patches/cppjieba-dict-cache-lifecycle.patch"


def tree_sha256(root: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"overlay tree contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        digest.update(content_hash)
    return digest.hexdigest()


def prepare(source: pathlib.Path, output: pathlib.Path) -> tuple[pathlib.Path, str, bool]:
    source = source.resolve(strict=True)
    scratch = (M4_ROOT / ".work/scratch").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    try:
        output.relative_to(scratch)
    except ValueError as exc:
        raise ValueError("overlay output must remain under research/m4-native/.work/scratch") from exc
    if not source.is_dir() or not (source / "DictTrie.hpp").is_file():
        raise ValueError(f"pinned cppjieba include directory is incomplete: {source}")

    with tempfile.TemporaryDirectory(prefix="cppjieba-overlay-stage-", dir=scratch) as temporary:
        staged_headers = pathlib.Path(temporary) / "cppjieba"
        shutil.copytree(source, staged_headers)
        result = subprocess.run(
            ["patch", "--batch", "--forward", "-p1", "-i", str(PATCH)],
            cwd=staged_headers, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"could not apply the pinned cppjieba cache patch: {result.stderr.strip()}")
        expected_hash = tree_sha256(staged_headers)
        destination = output / "cppjieba"
        if output.exists():
            if not output.is_dir() or output.is_symlink() or not destination.is_dir():
                raise ValueError(f"refusing to replace an existing non-overlay path: {output}")
            actual_hash = tree_sha256(destination)
            if actual_hash != expected_hash:
                raise ValueError(
                    f"existing overlay differs; preserving it (expected {expected_hash}, found {actual_hash})")
            return output, actual_hash, False
        output.mkdir(parents=False, exist_ok=False)
        try:
            shutil.copytree(staged_headers, destination)
        except BaseException:
            shutil.rmtree(output, ignore_errors=True)
            raise
        actual_hash = tree_sha256(destination)
        if actual_hash != expected_hash:
            raise RuntimeError("prepared overlay hash differs from staged patched headers")
        return output, actual_hash, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE,
                        help="pinned cppjieba include/cppjieba directory")
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT,
                        help="new or already-matching disposable output under .work/scratch")
    args = parser.parse_args()
    try:
        output, digest, created = prepare(args.source, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print({"overlay": str(output), "cppjieba_headers_sha256": digest,
           "created": created, "testing_instrumentation": "compile-only macro"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
