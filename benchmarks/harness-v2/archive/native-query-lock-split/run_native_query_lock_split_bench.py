#!/usr/bin/env python3
"""Run one native lock-split diagnostic arm through its test-only C ABI."""
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
    parser.add_argument('--collection-path', required=True, type=pathlib.Path)
    parser.add_argument('--documents', required=True, type=int)
    parser.add_argument('--cycles', required=True, type=int)
    parser.add_argument('--readers', required=True, type=int)
    parser.add_argument('--batch', required=True, type=int)
    parser.add_argument('--tokens', required=True, type=int)
    args = parser.parse_args()

    library_dir = require_work_path(args.library_dir, 'library directory')
    library = library_dir / 'libzvec_c_api.so'
    if not library.is_file() or library.is_symlink():
        raise FileNotFoundError(f'exact native benchmark library is missing: {library}')
    collection_path = args.collection_path.resolve()
    if not collection_path.is_relative_to(WORK_ROOT):
        raise ValueError(f'collection path must remain beneath {WORK_ROOT}')

    native = ctypes.CDLL(str(library))
    run_bench = native.zvec_m4_lock_split_run_bench_cycles
    run_bench.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_int]
    run_bench.restype = ctypes.c_int
    result = run_bench(str(collection_path).encode(), args.documents,
                       args.cycles, args.readers, args.batch, args.tokens)
    if result != 0:
        raise RuntimeError(f'native lock-split benchmark returned failure code {result}')
    print(f'bench_library={library}')
    print(f'bench_library_sha256={hashlib.sha256(library.read_bytes()).hexdigest()}')
    print('bench_status=PASS')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'lock-split benchmark failed: {error}', file=sys.stderr)
        raise SystemExit(1) from None
