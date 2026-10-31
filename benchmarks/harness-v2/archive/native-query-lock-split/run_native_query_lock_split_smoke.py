#!/usr/bin/env python3
"""Run the isolated native lock-split integration smoke through its test-only C ABI."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import pathlib
import sys

def _repo_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file() and (parent / '.git').exists():
            return parent
    raise RuntimeError('repository root not found')

ROOT = _repo_root()
WORK_ROOT = (ROOT / 'research/m4-native/.work').resolve()


def require_work_path(path: pathlib.Path, label: str) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(WORK_ROOT):
        raise ValueError(f'{label} must resolve beneath {WORK_ROOT}')
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--library-dir', required=True, type=pathlib.Path)
    parser.add_argument('--tmp-dir', required=True, type=pathlib.Path)
    args = parser.parse_args()

    library_dir = require_work_path(args.library_dir, 'library directory')
    tmp_dir = require_work_path(args.tmp_dir, 'temporary directory')
    library = library_dir / 'libzvec_c_api.so'
    if not library.is_file() or library.is_symlink():
        raise FileNotFoundError(f'exact native test library is missing: {library}')

    collection_path = tmp_dir / 'collection'
    if not collection_path.resolve().is_relative_to(WORK_ROOT):
        raise ValueError(f'collection path must remain beneath {WORK_ROOT}')

    native = ctypes.CDLL(str(library))
    run_smoke = native.zvec_m4_lock_split_run_smoke
    run_smoke.argtypes = [ctypes.c_char_p]
    run_smoke.restype = ctypes.c_int
    result = run_smoke(str(collection_path).encode())
    if result != 0:
        raise RuntimeError(f'native lock-split smoke returned failure code {result}')
    run_failure = native.zvec_m4_lock_split_run_failure_smoke
    run_failure.argtypes = [ctypes.c_char_p]
    run_failure.restype = ctypes.c_int
    failure_result = run_failure(str(collection_path).encode())
    if failure_result != 0:
        raise RuntimeError(
            f'native lock-split failure smoke returned failure code {failure_result}')

    print('native_lock_split_smoke=PASS')
    print('native_lock_split_failure_smoke=PASS')
    print(f'collection_path={collection_path}')
    print(f'library={library}')
    print(f'library_sha256={hashlib.sha256(library.read_bytes()).hexdigest()}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'lock-split smoke failed: {error}', file=sys.stderr)
        raise SystemExit(1)
