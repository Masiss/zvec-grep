#!/usr/bin/env python3
"""W3 native integrity verifier fault matrix.

The runner copies a sealed generation for every case, calls the C API from the
custom scratch library, and records the return code plus report. It never
touches the installed native library or the production binary.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import pathlib
import shutil
import struct
import sys


def load_library(path: pathlib.Path):
    library = ctypes.CDLL(str(path.resolve()))
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
    return library, verify, report_complete, report_json, report_free


def call_verify(
    library, path: pathlib.Path | str | None, flags: int = 0
) -> tuple[int, dict]:
    _, verify, report_complete, report_json, report_free = library
    if path is None:
        encoded = None
    elif isinstance(path, str):
        encoded = path.encode()
    else:
        encoded = str(path).encode()
    report = ctypes.c_void_p()
    code = verify(encoded, flags, ctypes.byref(report))
    payload: dict[str, object] = {}
    if report:
        try:
            raw = report_json(report)
            decoded = json.loads(raw) if raw else {}
            if isinstance(decoded, dict):
                payload = {str(key): value for key, value in decoded.items()}
        except (ValueError, json.JSONDecodeError) as error:
            payload = {"report_parse_error": str(error)}
        payload["native_complete"] = bool(report_complete(report))
        report_free(report)
    else:
        payload = {"native_complete": False}
    payload["native_code"] = code
    return code, payload


def flip(path: pathlib.Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    if not 0 <= offset < len(data):
        raise ValueError(f"offset {offset} outside {path} ({len(data)} bytes)")
    data[offset] ^= 0x01
    path.write_bytes(data)


def truncate(path: pathlib.Path, amount: int) -> None:
    data = path.read_bytes()
    path.write_bytes(data[:-amount])


def proxima_segments(path: pathlib.Path) -> dict[str, tuple[int, int, int, int]]:
    data = path.read_bytes()
    header = struct.unpack_from("<IHHIIHHIIQQQQ", data, 0)
    footer_offset = header[7]
    content_offset = header[8]
    footer = struct.unpack_from("<IIIIIIQQQQ7QQQ", data, footer_offset)
    segment_count = footer[3]
    segments_meta_size = footer[4]
    table = footer_offset - segments_meta_size
    result: dict[str, tuple[int, int, int, int]] = {}
    for index in range(segment_count):
        entry = struct.unpack_from("<IIQQQ", data, table + index * 32)
        segment_id_offset, data_crc, data_index, data_size, _ = entry
        name_end = data.index(b"\0", table + segment_id_offset)
        name = data[table + segment_id_offset : name_end].decode()
        result[name] = (data_index, data_size, data_crc, content_offset)
    return result


def tree_state(root: pathlib.Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state[str(path.relative_to(root))] = digest
    return state


def copy_generation(base: pathlib.Path, root: pathlib.Path, name: str) -> pathlib.Path:
    destination = root / name / base.name
    shutil.copytree(base, destination)
    return destination


def run_case(
    library,
    root: pathlib.Path,
    base: pathlib.Path,
    name: str,
    *,
    expect_code: int,
    expect_status: str | None = None,
    flags: int = 0,
    mutate=None,
    lock: bool = False,
    read_only_proof: bool = False,
    path_override: pathlib.Path | None = None,
) -> dict:
    generation = path_override or copy_generation(base, root, name)
    if mutate is not None:
        mutate(generation)
    target = generation / "storage" if path_override is None else generation
    before = tree_state(generation) if read_only_proof else None
    report: dict
    code: int
    if lock:
        with (generation / "storage.lock").open("rb") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                code, report = call_verify(library, target, flags)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    else:
        code, report = call_verify(library, target, flags)
    after = tree_state(generation) if read_only_proof else None

    checks: list[tuple[str, bool]] = [
        ("return_code", code == expect_code),
    ]
    if expect_status is not None:
        checks.append(("status", report.get("status") == expect_status))
    if read_only_proof:
        checks.append(("read_only", before == after))
    if path_override is None:
        checks.append(
            ("generation_identity", report.get("generation") == base.name)
        )
        checks.append(
            (
                "coverage_named",
                {entry.get("name") for entry in report.get("coverage", [])}
                == {"proxima", "rocksdb", "ipc", "sidecars", "wal"},
            )
        )
    return {
        "case": name,
        "expect_code": expect_code,
        "expect_status": expect_status,
        "code": code,
        "status": report.get("status"),
        "pass": report.get("pass"),
        "complete": report.get("complete"),
        "stable": report.get("stable"),
        "legacy": report.get("legacy"),
        "generation": report.get("generation"),
        "missing_coverage": report.get("missing_coverage"),
        "limitations": report.get("limitations"),
        "errors": report.get("errors"),
        "checks": dict(checks),
        "ok": all(value for _, value in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=pathlib.Path, required=True)
    parser.add_argument("--clean", type=pathlib.Path, required=True)
    parser.add_argument("--legacy", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    library = load_library(args.library)
    root = args.out.parent / "cases"
    if root.exists():
        try:
            shutil.rmtree(root)
        except OSError as error:
            raise RuntimeError(f"cannot clear case root {root}: {error}") from error
    root.mkdir(parents=True)

    clean = args.clean
    legacy = args.legacy
    proxima = next(clean.rglob("*.proxima"))
    segments = proxima_segments(proxima)
    segment_name = "HnswT3S0" if "HnswT3S0" in segments else next(
        name for name in segments if name.startswith("Hnsw")
    )

    def proxima_case(kind: str):
        def mutate(generation: pathlib.Path) -> None:
            target = generation / proxima.relative_to(clean)
            data_index, data_size, _, content_offset = segments[segment_name]
            if kind == "header":
                flip(target, 0)
            elif kind == "content":
                flip(target, content_offset + data_index + data_size // 2)
            elif kind == "meta":
                header = struct.unpack_from("<IHHIIHHIIQQQQ", target.read_bytes(), 0)
                footer_offset = header[7]
                footer = struct.unpack_from("<IIIIIIQQQQ7QQQ", target.read_bytes(), footer_offset)
                table = footer_offset - footer[4]
                flip(target, table)
            elif kind == "truncate":
                truncate(target, 4096)
            else:
                raise ValueError(kind)

        return mutate

    def rocksdb_mutate(kind: str):
        def mutate(generation: pathlib.Path) -> None:
            directories = [
                path
                for path in generation.rglob("*")
                if path.is_dir() and (path / "CURRENT").exists()
            ]
            directory = max(
                directories,
                key=lambda path: sum(
                    item.stat().st_size for item in path.glob("*") if item.is_file()
                ),
            )
            if kind == "sst":
                sst = max(directory.glob("*.sst"), key=lambda path: path.stat().st_size)
                flip(sst, sst.stat().st_size // 2)
            elif kind == "manifest":
                manifest = next(directory.glob("MANIFEST-*"))
                flip(manifest, manifest.stat().st_size // 2)
            elif kind == "current":
                (directory / "CURRENT").write_text("MANIFEST-999999\n")
            elif kind == "wal":
                wal = next(directory.glob("*.log"), None)
                if wal is None:
                    wal = directory / "999999.log"
                wal.write_bytes(b"\x00" * 64)
            else:
                raise ValueError(kind)

        return mutate

    def ipc_mutate(kind: str):
        def mutate(generation: pathlib.Path) -> None:
            ipc = max(generation.rglob("*.ipc"), key=lambda path: path.stat().st_size)
            if kind == "footer":
                # Arrow file format ends with a 4-byte footer length followed
                # by the "ARROW1" magic.
                flip(ipc, max(0, ipc.stat().st_size - 8))
            elif kind == "truncate":
                truncate(ipc, 1024)
            else:
                raise ValueError(kind)

        return mutate

    def sidecar_mutate(kind: str):
        def mutate(generation: pathlib.Path) -> None:
            sidecar = max(
                generation.rglob("reconciliation/*.bin"),
                key=lambda path: path.stat().st_size,
            )
            if kind == "truncate":
                truncate(sidecar, 1)
            elif kind == "magic":
                flip(sidecar, 0)
            elif kind == "count":
                data = bytearray(sidecar.read_bytes())
                data[8:12] = (0xFFFFFFFF).to_bytes(4, "little")
                sidecar.write_bytes(data)
            elif kind == "trailing":
                sidecar.write_bytes(sidecar.read_bytes() + b"\x00")
            else:
                raise ValueError(kind)

        return mutate

    def missing_collection(generation: pathlib.Path) -> None:
        target = generation / "storage" / "entities"
        if not target.is_dir():
            raise RuntimeError(f"missing collection fixture not found: {target}")
        try:
            shutil.rmtree(target)
        except OSError as error:
            raise RuntimeError(f"cannot remove {target}: {error}") from error

    results: list[dict] = []
    results.append(
        run_case(
            library,
            root,
            clean,
            "clean",
            expect_code=0,
            expect_status="PASS_COMPLETE",
            read_only_proof=True,
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "proxima_header_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=proxima_case("header"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "proxima_segment_content_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=proxima_case("content"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "proxima_segment_meta_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=proxima_case("meta"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "proxima_truncate_tail",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=proxima_case("truncate"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "rocksdb_sst_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=rocksdb_mutate("sst"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "rocksdb_manifest_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=rocksdb_mutate("manifest"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "rocksdb_current_corrupt",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=rocksdb_mutate("current"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "ipc_footer_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=ipc_mutate("footer"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "ipc_truncate",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=ipc_mutate("truncate"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "sidecar_magic_flip",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=sidecar_mutate("magic"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "sidecar_truncate",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=sidecar_mutate("truncate"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "sidecar_count_overflow",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=sidecar_mutate("count"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "sidecar_trailing_bytes",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=sidecar_mutate("trailing"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "missing_collection",
            expect_code=1,
            expect_status="CORRUPT",
            mutate=missing_collection,
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "active_writer_refused",
            expect_code=2,
            expect_status="INCOMPLETE",
            lock=True,
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "wal_non_empty_incomplete",
            expect_code=2,
            expect_status="INCOMPLETE",
            mutate=rocksdb_mutate("wal"),
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "rocksdb_only_subset_incomplete",
            expect_code=2,
            expect_status="INCOMPLETE",
            flags=1,
        )
    )
    results.append(
        run_case(
            library,
            root,
            clean,
            "require_complete",
            expect_code=0,
            expect_status="PASS_COMPLETE",
            flags=7 | 8,
        )
    )
    results.append(
        run_case(
            library,
            root,
            legacy,
            "legacy_zero_crc",
            expect_code=2,
            expect_status="INCOMPLETE_LEGACY",
            read_only_proof=True,
        )
    )

    invalid_cases = [
        ("invalid_null", None, 3),
        ("invalid_empty", "", 3),
        ("invalid_missing", root / "does-not-exist", 3),
    ]
    for name, target, expect in invalid_cases:
        code, report = call_verify(library, target)
        results.append(
            {
                "case": name,
                "expect_code": expect,
                "code": code,
                "status": report.get("status"),
                "checks": {"return_code": code == expect},
                "ok": code == expect,
            }
        )

    payload = {
        "library": str(args.library),
        "clean_generation": clean.name,
        "legacy_generation": legacy.name,
        "cases": results,
        "passed": sum(1 for result in results if result["ok"]),
        "failed": sum(1 for result in results if not result["ok"]),
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    for result in results:
        print(
            result["case"],
            "code",
            result["code"],
            "status",
            result.get("status"),
            "OK" if result["ok"] else "FAIL",
        )
    print(f"{payload['passed']} passed, {payload['failed']} failed")
    if not args.keep:
        try:
            shutil.rmtree(root, ignore_errors=True)
        except OSError as error:
            print(f"warning: cannot remove case root {root}: {error}", file=sys.stderr)
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
