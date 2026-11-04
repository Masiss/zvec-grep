#!/usr/bin/env python3
"""Run the multi-field/tokenizer lock-split smoke.

Schema: content (standard FTS), title (whitespace FTS), embedding (vector).
The native smoke pauses flush after both FTS CFs have flushed and pauses
optimize before the FTS seal; both fields are queried during maintenance.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args(argv)

    library_path = os.path.join(args.library_dir, "libzvec_c_api.so")
    if not os.path.isfile(library_path):
        raise SystemExit(f"library not found: {library_path}")
    try:
        shutil.rmtree(args.path, ignore_errors=True)
    except OSError as exc:
        raise SystemExit(f"cannot clear test path: {exc}") from exc

    library = ctypes.CDLL(library_path)
    fn = library.zvec_m4_lock_split_run_multifield_smoke
    fn.argtypes = [ctypes.c_char_p]
    fn.restype = ctypes.c_int
    rc = fn(args.path.encode())
    print(f"multifield_return={rc}", flush=True)
    print("multifield_smoke=PASS" if rc == 0 else "multifield_smoke=FAIL",
          flush=True)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
