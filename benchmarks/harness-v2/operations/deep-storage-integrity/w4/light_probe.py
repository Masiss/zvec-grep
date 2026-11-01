#!/usr/bin/env python3
"""Call the native light seal verifier once (structure + layout only)."""
import argparse
import ctypes
import json
import pathlib
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("library", type=pathlib.Path)
    parser.add_argument("collection", type=pathlib.Path)
    args = parser.parse_args()
    library = ctypes.CDLL(str(args.library.resolve()))
    verify = library.zvec_collection_verify_integrity
    verify.restype = ctypes.c_int
    verify.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    report_json = library.zvec_integrity_report_json
    report_json.restype = ctypes.c_char_p
    report_json.argtypes = [ctypes.c_void_p]
    report_free = library.zvec_integrity_report_free
    report_free.restype = None
    report_free.argtypes = [ctypes.c_void_p]
    report = ctypes.c_void_p()
    code = verify(str(args.collection).encode(), 0x2 | 0x10 | 0x20, ctypes.byref(report))
    payload: dict[str, object] = {}
    if report:
        try:
            raw = report_json(report)
            decoded = json.loads(raw) if raw else {}
            payload = decoded if isinstance(decoded, dict) else {}
        except (ValueError, json.JSONDecodeError) as error:
            payload = {"parse_error": str(error)}
        report_free(report)
    print(json.dumps({"code": code, "status": payload.get("status"), "complete": payload.get("complete")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
