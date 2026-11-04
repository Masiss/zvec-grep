#!/usr/bin/env python3
"""Controlled W2 corruption cases for the native checksum verifier."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import struct
import subprocess


def copy_case(base: pathlib.Path, root: pathlib.Path, name: str) -> pathlib.Path:
    dst = root / name / "storage"
    shutil.copytree(base, dst)
    return dst


def flip(path: pathlib.Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    if not 0 <= offset < len(data):
        raise ValueError(f"offset {offset} outside {path} ({len(data)} bytes)")
    data[offset] ^= 0x01
    path.write_bytes(data)


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


def run(verifier: pathlib.Path, root: pathlib.Path, name: str) -> dict:
    proc = subprocess.run(
        [str(verifier), str(root / name / "storage")],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"parse_error": proc.stdout, "stderr": proc.stderr}
    report["case"] = name
    report["rc"] = proc.returncode
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=pathlib.Path)
    parser.add_argument("--verifier", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--second-proxima", type=pathlib.Path)
    args = parser.parse_args()

    root = args.out.parent / "cases"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    proxima = next(args.base.rglob("*.proxima"))
    segments = proxima_segments(proxima)
    results: list[dict] = []

    case = copy_case(args.base, root, "clean")
    results.append(run(args.verifier, root, "clean"))

    case = copy_case(args.base, root, "proxima_hnsw_flip")
    data_index, data_size, _, content_offset = segments["HnswT3S0"]
    flip(case / proxima.relative_to(args.base), content_offset + data_index + data_size // 2)
    results.append(run(args.verifier, root, "proxima_hnsw_flip"))

    case = copy_case(args.base, root, "proxima_indexmeta_flip")
    data_index, data_size, _, content_offset = segments["IndexMeta"]
    flip(case / proxima.relative_to(args.base), content_offset + data_index + data_size // 2)
    results.append(run(args.verifier, root, "proxima_indexmeta_flip"))

    case = copy_case(args.base, root, "proxima_header_flip")
    flip(case / proxima.relative_to(args.base), 0)
    results.append(run(args.verifier, root, "proxima_header_flip"))

    case = copy_case(args.base, root, "proxima_footer_flip")
    footer_offset = struct.unpack_from("<IHHIIHHIIQQQQ", proxima.read_bytes(), 0)[7]
    flip(case / proxima.relative_to(args.base), footer_offset)
    results.append(run(args.verifier, root, "proxima_footer_flip"))

    case = copy_case(args.base, root, "proxima_truncate_tail")
    target = case / proxima.relative_to(args.base)
    target.write_bytes(target.read_bytes()[:-4096])
    results.append(run(args.verifier, root, "proxima_truncate_tail"))

    case = copy_case(args.base, root, "proxima_truncate_footer")
    target = case / proxima.relative_to(args.base)
    target.write_bytes(target.read_bytes()[:1_000_000])
    results.append(run(args.verifier, root, "proxima_truncate_footer"))

    case = copy_case(args.base, root, "proxima_replace_random")
    target = case / proxima.relative_to(args.base)
    target.write_bytes(os.urandom(4096))
    results.append(run(args.verifier, root, "proxima_replace_random"))

    if args.second_proxima is not None:
        case = copy_case(args.base, root, "proxima_replace_valid")
        target = case / proxima.relative_to(args.base)
        target.write_bytes(args.second_proxima.read_bytes())
        results.append(run(args.verifier, root, "proxima_replace_valid"))

    fragment = (
        "fragments_bf8b58d66b0d4a68e01c0f31c59b7d77f4ca3899124fe7cc116bcc55bda5cc00"
    )
    case = copy_case(args.base, root, "rocksdb_fts_sst_flip")
    sst = max((case / fragment / "0/fts.2.rocksdb").glob("*.sst"), key=lambda p: p.stat().st_size)
    flip(sst, sst.stat().st_size // 2)
    results.append(run(args.verifier, root, "rocksdb_fts_sst_flip"))

    case = copy_case(args.base, root, "rocksdb_idmap_sst_flip")
    sst = max((case / "files/idmap.0").glob("*.sst"), key=lambda p: p.stat().st_size)
    flip(sst, sst.stat().st_size // 2)
    results.append(run(args.verifier, root, "rocksdb_idmap_sst_flip"))

    case = copy_case(args.base, root, "rocksdb_fts_sst_truncate")
    sst = max((case / fragment / "0/fts.2.rocksdb").glob("*.sst"), key=lambda p: p.stat().st_size)
    sst.write_bytes(sst.read_bytes()[:-1024])
    results.append(run(args.verifier, root, "rocksdb_fts_sst_truncate"))

    case = copy_case(args.base, root, "rocksdb_current_corrupt")
    (case / "files/idmap.0/CURRENT").write_text("MANIFEST-999999\n")
    results.append(run(args.verifier, root, "rocksdb_current_corrupt"))

    case = copy_case(args.base, root, "rocksdb_manifest_flip")
    manifest = next((case / "files/idmap.0").glob("MANIFEST-*"))
    flip(manifest, manifest.stat().st_size // 2)
    results.append(run(args.verifier, root, "rocksdb_manifest_flip"))

    case = copy_case(args.base, root, "scalar_ipc_flip")
    ipc = max(case.rglob("*.ipc"), key=lambda p: p.stat().st_size)
    flip(ipc, ipc.stat().st_size // 2)
    results.append(run(args.verifier, root, "scalar_ipc_flip"))

    case = copy_case(args.base, root, "reconciliation_bin_flip")
    bins = list(case.rglob("reconciliation/*.bin"))
    if bins:
        binary = max(bins, key=lambda p: p.stat().st_size)
        flip(binary, binary.stat().st_size // 2)
    results.append(run(args.verifier, root, "reconciliation_bin_flip"))

    args.out.write_text(json.dumps(results, indent=2))
    for report in results:
        print(
            report["case"],
            "rc",
            report["rc"],
            "pass",
            report.get("pass"),
            "errors",
            len(report.get("errors", [])),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
