#!/usr/bin/env python3
"""Worker for the lock-split crash/kill -9 recovery test.

The child mode arms one crash point and blocks. The parent runner kills it.
The verify mode runs in a fresh process and proves recovery.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys


def _library(library_dir: str) -> ctypes.CDLL:
    path = os.path.join(library_dir, "libzvec_c_api.so")
    if not os.path.isfile(path):
        raise SystemExit(f"library not found: {path}")
    return ctypes.CDLL(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["child", "ddl-child", "cycle-child", "verify", "verify-vector", "verify-loop", "verify-loop-vector"])
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--phase", choices=["flush", "optimize", "ddl"])
    parser.add_argument("--event")
    parser.add_argument("--cf", default="")
    parser.add_argument("--marker")
    parser.add_argument("--expected", type=int)
    parser.add_argument("--base-id", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--expected-after", type=int, default=0)
    args = parser.parse_args(argv)

    library = _library(args.library_dir)

    if args.mode == "cycle-child":
        if not args.phase or not args.event or not args.marker:
            raise SystemExit("cycle-child requires --phase --event --marker")
        fn = library.zvec_m4_lock_split_run_crash_cycle
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_int, ctypes.c_int, ctypes.c_int]
        fn.restype = ctypes.c_int
        rc = fn(
            args.path.encode(),
            args.phase.encode(),
            args.event.encode(),
            (args.cf or "").encode(),
            args.marker.encode(),
            args.base_id,
            args.count,
            args.expected_after,
        )
        print(f"cycle_child_return={rc}", flush=True)
        return 0 if rc == 0 else 1

    if args.mode in ("verify-loop", "verify-loop-vector"):
        if args.expected is None:
            raise SystemExit("verify-loop requires --expected")
        fn = library.zvec_m4_lock_split_verify_crash_loop
        fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        fn.restype = ctypes.c_int
        rc = fn(args.path.encode(), args.expected,
                1 if args.mode == "verify-loop-vector" else 0)
        print("crash_loop_verify=PASS" if rc == 0
              else f"crash_loop_verify=FAIL rc={rc}", flush=True)
        return 0 if rc == 0 else 1

    if args.mode == "ddl-child":
        if not args.event or not args.marker:
            raise SystemExit("ddl-child requires --event --marker")
        fn = library.zvec_m4_lock_split_run_ddl_crash_child
        fn.argtypes = [ctypes.c_char_p] * 3
        fn.restype = ctypes.c_int
        rc = fn(
            args.path.encode(),
            args.event.encode(),
            args.marker.encode(),
        )
        print(f"ddl_child_return={rc}", flush=True)
        return 0 if rc == 0 else 1

    if args.mode == "child":
        if not args.phase or not args.event or not args.marker:
            raise SystemExit("child requires --phase --event --marker")
        fn = library.zvec_m4_lock_split_run_crash_child
        fn.argtypes = [ctypes.c_char_p] * 5
        fn.restype = ctypes.c_int
        rc = fn(
            args.path.encode(),
            args.phase.encode(),
            args.event.encode(),
            (args.cf or "").encode(),
            args.marker.encode(),
        )
        print(f"crash_child_return={rc}", flush=True)
        return 0 if rc == 0 else 1

    if args.expected is None:
        raise SystemExit("verify requires --expected")
    if args.mode == "verify-vector":
        fn = library.zvec_m4_lock_split_verify_vector_after_crash
    else:
        fn = library.zvec_m4_lock_split_verify_after_crash
    fn.argtypes = [ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    rc = fn(args.path.encode(), args.expected)
    if rc == 0:
        print("crash_verify=PASS", flush=True)
        return 0
    print(f"crash_verify=FAIL rc={rc}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
