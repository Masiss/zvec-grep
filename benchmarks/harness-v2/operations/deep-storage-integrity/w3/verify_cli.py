#!/usr/bin/env python3
"""Call the W3 native verifier through its C API.

Usage:
    verify_cli.py <library> <collection-path> [--flags N] [--json-out PATH]

Exit code is the native verifier return code: 0 PASS_COMPLETE, 1 CORRUPT,
2 INCOMPLETE, 3 INVALID_ARG.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import pathlib
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=pathlib.Path)
    parser.add_argument("collection", type=pathlib.Path)
    parser.add_argument("--flags", type=int, default=0)
    parser.add_argument("--json-out", type=pathlib.Path)
    args = parser.parse_args()

    library = ctypes.CDLL(str(args.library.resolve()))
    verify = library.zvec_collection_verify_integrity
    verify.restype = ctypes.c_int
    verify.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    report_complete = library.zvec_integrity_report_complete
    report_complete.restype = ctypes.c_int
    report_complete.argtypes = [ctypes.c_void_p]
    report_json = library.zvec_integrity_report_json
    report_json.restype = ctypes.c_char_p
    report_json.argtypes = [ctypes.c_void_p]
    report_free = library.zvec_integrity_report_free
    report_free.restype = None
    report_free.argtypes = [ctypes.c_void_p]

    path = str(args.collection).encode()
    report = ctypes.c_void_p()
    code = verify(path, args.flags, ctypes.byref(report))
    payload: dict[str, object] = {}
    if report:
        try:
            raw = report_json(report)
            decoded = json.loads(raw) if raw else {}
            if isinstance(decoded, dict):
                payload = {str(key): value for key, value in decoded.items()}
        except (ValueError, json.JSONDecodeError) as error:
            payload = {"report_parse_error": str(error)}
        payload["native_code"] = code
        payload["native_complete"] = bool(report_complete(report))
        report_free(report)
    else:
        payload = {"native_code": code, "native_complete": False}

    text = json.dumps(payload, indent=2)
    if args.json_out is not None:
        args.json_out.write_text(text + "\n")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
