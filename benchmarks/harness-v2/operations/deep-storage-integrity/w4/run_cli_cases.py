#!/usr/bin/env python3
"""W4 CLI fault matrix for `zg --verify` against a scratch `zg` + native .so.

Every case is a disposable workspace under a temp root. The runner never writes
to the installed binary/library or a live index.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile


def run_verify(binary: pathlib.Path, root: pathlib.Path, *args: str) -> tuple[int, dict, str, str]:
    proc = subprocess.run(
        [str(binary), "--verify", str(root), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"parse_error": proc.stdout}
    return proc.returncode, payload, proc.stdout, proc.stderr


def copy_workspace(base: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    target = root / "workspace"
    shutil.copytree(base / ".zvec-grep", target / ".zvec-grep")
    return target


def generation(workspace: pathlib.Path) -> pathlib.Path:
    generations = workspace / ".zvec-grep" / "generations"
    entries = [path for path in generations.iterdir() if path.is_dir()]
    if len(entries) != 1:
        raise RuntimeError(f"expected one generation, found {entries}")
    return entries[0]


def flip(path: pathlib.Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    if not 0 <= offset < len(data):
        raise ValueError(f"offset {offset} outside {path}")
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
        segment_id_offset, _, data_index, data_size, _ = entry
        name_end = data.index(b"\0", table + segment_id_offset)
        name = data[table + segment_id_offset : name_end].decode()
        result[name] = (data_index, data_size, 0, content_offset)
    return result


def tree_hash(root: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def mutate_proxima(workspace: pathlib.Path, kind: str) -> None:
    proxima = next(generation(workspace).rglob("*.proxima"))
    segments = proxima_segments(proxima)
    name = "HnswT3S0" if "HnswT3S0" in segments else next(
        key for key in segments if key.startswith("Hnsw")
    )
    data_index, data_size, _, content_offset = segments[name]
    if kind == "header":
        flip(proxima, 0)
    elif kind == "content":
        flip(proxima, content_offset + data_index + data_size // 2)
    elif kind == "meta":
        data = proxima.read_bytes()
        footer_offset = struct.unpack_from("<IHHIIHHIIQQQQ", data, 0)[7]
        footer = struct.unpack_from("<IIIIIIQQQQ7QQQ", data, footer_offset)
        flip(proxima, footer_offset - footer[4])
    elif kind == "truncate":
        truncate(proxima, 4096)
    else:
        raise ValueError(kind)


def rocksdb_dirs(workspace: pathlib.Path) -> list[pathlib.Path]:
    return [
        path
        for path in generation(workspace).rglob("*")
        if path.is_dir() and (path / "CURRENT").exists()
    ]


def mutate_rocksdb(workspace: pathlib.Path, kind: str) -> None:
    directory = max(
        rocksdb_dirs(workspace),
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
        wal = next(directory.glob("*.log"), None) or (directory / "999999.log")
        wal.write_bytes(b"\x00" * 64)
    else:
        raise ValueError(kind)


def mutate_ipc(workspace: pathlib.Path, kind: str) -> None:
    ipc = max(generation(workspace).rglob("*.ipc"), key=lambda path: path.stat().st_size)
    if kind == "footer":
        flip(ipc, max(0, ipc.stat().st_size - 8))
    elif kind == "truncate":
        truncate(ipc, 1024)
    else:
        raise ValueError(kind)


def mutate_sidecar(workspace: pathlib.Path, kind: str) -> None:
    sidecar = max(
        generation(workspace).rglob("reconciliation/*.bin"),
        key=lambda path: path.stat().st_size,
    )
    if kind == "magic":
        flip(sidecar, 0)
    elif kind == "truncate":
        truncate(sidecar, 1)
    elif kind == "count":
        data = bytearray(sidecar.read_bytes())
        data[8:12] = (0xFFFFFFFF).to_bytes(4, "little")
        sidecar.write_bytes(data)
    elif kind == "trailing":
        sidecar.write_bytes(sidecar.read_bytes() + b"\x00")
    else:
        raise ValueError(kind)


def missing_collection(workspace: pathlib.Path) -> None:
    target = generation(workspace) / "storage" / "entities"
    if not target.is_dir():
        raise RuntimeError(f"missing collection fixture not found: {target}")
    try:
        shutil.rmtree(target)
    except OSError as error:
        raise RuntimeError(f"cannot remove {target}: {error}") from error


def normalize_manifest_generation(workspace: pathlib.Path, generation_name: str) -> None:
    manifest = workspace / ".zvec-grep" / "manifest.json"
    try:
        value = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read {manifest}: {error}") from error
    value["storageGeneration"] = generation_name
    try:
        manifest.write_text(json.dumps(value))
    except OSError as error:
        raise RuntimeError(f"cannot write {manifest}: {error}") from error


def assert_case(name: str, code: int, payload: dict, *, expect_code: int, expect_outcome: str,
                checks: list[tuple[str, bool]]) -> dict:
    actual_outcome = payload.get("outcome")
    all_checks = list(checks)
    all_checks.append(("return_code", code == expect_code))
    all_checks.append(("outcome", actual_outcome == expect_outcome))
    return {
        "case": name,
        "expect_code": expect_code,
        "expect_outcome": expect_outcome,
        "code": code,
        "outcome": actual_outcome,
        "status": (payload.get("report") or {}).get("status"),
        "native_abi": payload.get("native_abi"),
        "native_available": payload.get("native_available"),
        "message": payload.get("message"),
        "limitations": (payload.get("report") or {}).get("limitations"),
        "missing_coverage": (payload.get("report") or {}).get("missing_coverage"),
        "checks": dict(all_checks),
        "ok": all(value for _, value in all_checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--old-binary", type=pathlib.Path, required=True)
    parser.add_argument("--clean-workspace", type=pathlib.Path, required=True)
    parser.add_argument("--legacy-workspace", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    temp_root = pathlib.Path(tempfile.mkdtemp(prefix="zg-w4-cases-", dir="/tmp"))
    results: list[dict] = []
    try:
        def case(name: str, base: pathlib.Path, expect_code: int, expect_outcome: str,
                 mutate=None, extra_args: tuple[str, ...] = (), read_only: bool = False,
                 lock_writer: bool = False, binary: pathlib.Path | None = None) -> None:
            root = temp_root / name
            root.mkdir(parents=True)
            workspace = copy_workspace(base, root)
            if mutate is not None:
                mutate(workspace)
            before = tree_hash(workspace) if read_only else None
            lock_handle = None
            try:
                if lock_writer:
                    lock_handle = (generation(workspace) / "storage.lock").open("rb")
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                code, payload, _, _ = run_verify(
                    binary or args.binary, workspace, *extra_args
                )
            finally:
                if lock_handle is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                    lock_handle.close()
            after = tree_hash(workspace) if read_only else None
            checks: list[tuple[str, bool]] = []
            report = payload.get("report") or {}
            if expect_outcome == "pass":
                checks.extend([
                    ("status", report.get("status") == "PASS_COMPLETE"),
                    ("native_abi", payload.get("native_abi") == 1),
                    ("native_available", payload.get("native_available") is True),
                    (
                        "coverage",
                        {entry.get("name") for entry in report.get("coverage", [])}
                        == {"proxima", "rocksdb", "ipc", "sidecars", "wal"},
                    ),
                    (
                        "limitations_explicit",
                        "ipc.content_checksum" in report.get("limitations", [])
                        and "sidecars.content_checksum" in report.get("limitations", []),
                    ),
                    ("generation_identity", bool(payload.get("expected_generation"))),
                ])
            if read_only:
                checks.append(("read_only", before == after))
            results.append(
                assert_case(
                    name, code, payload, expect_code=expect_code,
                    expect_outcome=expect_outcome, checks=checks,
                )
            )

        case("clean", args.clean_workspace, 0, "pass", read_only=True)
        case("clean_require_complete", args.clean_workspace, 0, "pass", extra_args=("--require-complete",))
        case("proxima_header_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_proxima(w, "header"))
        case("proxima_content_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_proxima(w, "content"))
        case("proxima_meta_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_proxima(w, "meta"))
        case("proxima_truncate", args.clean_workspace, 1, "corrupt", lambda w: mutate_proxima(w, "truncate"))
        case("rocksdb_sst_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_rocksdb(w, "sst"))
        case("rocksdb_manifest_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_rocksdb(w, "manifest"))
        case("rocksdb_current_corrupt", args.clean_workspace, 1, "corrupt", lambda w: mutate_rocksdb(w, "current"))
        case("ipc_footer_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_ipc(w, "footer"))
        case("ipc_truncate", args.clean_workspace, 1, "corrupt", lambda w: mutate_ipc(w, "truncate"))
        case("sidecar_magic_flip", args.clean_workspace, 1, "corrupt", lambda w: mutate_sidecar(w, "magic"))
        case("sidecar_truncate", args.clean_workspace, 1, "corrupt", lambda w: mutate_sidecar(w, "truncate"))
        case("sidecar_count_overflow", args.clean_workspace, 1, "corrupt", lambda w: mutate_sidecar(w, "count"))
        case("sidecar_trailing", args.clean_workspace, 1, "corrupt", lambda w: mutate_sidecar(w, "trailing"))
        case("missing_collection", args.clean_workspace, 1, "corrupt", missing_collection)
        case(
            "missing_selected_generation",
            args.clean_workspace,
            1,
            "corrupt",
            lambda w: shutil.rmtree(generation(w)),
        )
        case(
            "identity_manifest_mismatch",
            args.clean_workspace,
            1,
            "corrupt",
            lambda w: normalize_manifest_generation(w, "11111111-1111-4111-8111-111111111111"),
        )
        case("active_writer_refused", args.clean_workspace, 2, "incomplete", lock_writer=True)
        case("wal_non_empty", args.clean_workspace, 2, "incomplete", lambda w: mutate_rocksdb(w, "wal"))
        case("legacy_zero_crc", args.legacy_workspace, 2, "incomplete", read_only=True)
        # The new binary requires the W4 native library. Pairing it with the
        # pre-W4 library fails at dynamic symbol resolution, before --verify.
        old_code, old_payload, _, old_stderr = run_verify(args.old_binary, args.clean_workspace)
        results.append(
            {
                "case": "old_library_pairing_incompatible",
                "expect_code": 127,
                "code": old_code,
                "outcome": old_payload.get("outcome"),
                "checks": {
                    "return_code": old_code == 127,
                    "undefined_symbol": "undefined symbol" in old_stderr,
                },
                "ok": old_code == 127 and "undefined symbol" in old_stderr,
            }
        )

        # Empty workspace with no ancestor .zvec-grep.
        empty = temp_root / "empty"
        empty.mkdir(parents=True)
        code, payload, _, _ = run_verify(args.binary, empty)
        results.append(
            assert_case(
                "uninitialized", code, payload, expect_code=2, expect_outcome="incomplete",
                checks=[("message", bool(payload.get("message")))],
            )
        )

        # Help surface.
        help_proc = subprocess.run(
            [str(args.binary), "--help", "verify"], capture_output=True, text=True, check=False
        )
        results.append(
            {
                "case": "help_verify",
                "expect_code": 0,
                "code": help_proc.returncode,
                "outcome": None,
                "checks": {
                    "return_code": help_proc.returncode == 0,
                    "exit_codes_documented": "Exit codes" in help_proc.stdout,
                },
                "ok": help_proc.returncode == 0 and "Exit codes" in help_proc.stdout,
            }
        )

        # Existing status surface must keep its --verify meaning.
        status_proc = subprocess.run(
            [str(args.binary), "--status", "--verify", str(args.clean_workspace)],
            capture_output=True, text=True, check=False,
        )
        results.append(
            {
                "case": "status_verify_compatibility",
                "expect_code": 0,
                "code": status_proc.returncode,
                "outcome": None,
                "checks": {
                    "return_code": status_proc.returncode == 0,
                    "status_output": "Workspace" in status_proc.stdout or "Index" in status_proc.stdout,
                },
                "ok": status_proc.returncode == 0
                and ("Workspace" in status_proc.stdout or "Index" in status_proc.stdout),
            }
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    payload = {
        "binary": str(args.binary),
        "old_binary": str(args.old_binary),
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
            "outcome",
            result.get("outcome"),
            "OK" if result["ok"] else "FAIL",
        )
    print(f"{payload['passed']} passed, {payload['failed']} failed")
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
