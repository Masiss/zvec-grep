#!/usr/bin/env python3
"""Audit every shipped ELF in a Linux x64 GNU zvec-grep payload."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--min-glibc", default="2.39")
    args = parser.parse_args()
    binary = args.bundle / "bin/zg.exe"
    if not binary.exists():
        binary = args.bundle / "bin/zg"
    library = args.bundle / "lib/libzvec_c_api.so"
    elf_files = [binary, *sorted((args.bundle / "lib").glob("*.so*"))]
    runtime = []
    unresolved = []
    for path in elf_files:
        header = run("readelf", "-h", str(path))
        dynamic = run("readelf", "-d", str(path))
        version_info = run("readelf", "--version-info", str(path))
        dependencies = run("ldd", str(path))
        missing = [
            line.strip() for line in dependencies.splitlines()
            if "not found" in line or "undefined symbol" in line
        ]
        if missing:
            unresolved.append({"file": str(path.relative_to(args.bundle)), "lines": missing})
        runtime.append({
            "file": str(path.relative_to(args.bundle)),
            "machineX86_64": "ELF64" in header and "X86-64" in header,
            "versions": sorted(set(re.findall(r"(?:GLIBC|GLIBCXX|CXXABI)_[\d.]+", version_info))),
            "ldd": dependencies.strip().splitlines(),
        })
    exported = {
        line.split()[-1]
        for line in run("nm", "-D", "--defined-only", str(library)).splitlines()
        if line.split()
    }
    required = {
        "zvec_initialize",
        "zvec_collection_create_and_open",
        "zvec_collection_open",
        "zvec_collection_upsert",
        "zvec_collection_query",
        "zvec_collection_optimize",
        "zvec_collection_destroy",
    }
    missing_symbols = sorted(required - exported)
    runpath = re.findall(r"\(RUNPATH\)[^\[]*\[([^\]]+)\]", run("readelf", "-d", str(binary)))
    def version_key(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split("."))

    glibc_versions = sorted(
        {version.removeprefix("GLIBC_") for item in runtime for version in item["versions"]
         if version.startswith("GLIBC_")},
        key=version_key,
    )
    baseline = version_key(args.min_glibc)
    checks = {
        "glibc_within_declared_baseline": all(version_key(value) <= baseline for value in glibc_versions),
        "all_runtime_elves_x86_64": all(item["machineX86_64"] for item in runtime),
        "runpath_origin_lib": any("$ORIGIN/../lib" in value for value in runpath),
        "required_symbols": not missing_symbols,
        "all_dependencies_resolved": not unresolved,
        "no_legacy_jieba_accessors": not any(
            name in exported for name in (
                "zvec_get_default_jieba_dict_dir",
                "zvec_set_default_jieba_dict_dir",
            )
        ),
    }
    report = {
        "schemaVersion": 1,
        "bundle": str(args.bundle.resolve()),
        "checks": checks,
        "runpath": runpath,
        "requiredSymbols": sorted(required),
        "missingSymbols": missing_symbols,
        "unresolvedDependencies": unresolved,
        "requiredGlibc": glibc_versions[-1] if glibc_versions else None,
        "declaredMinGlibc": args.min_glibc,
        "runtime": runtime,
    }
    print(json.dumps(report, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
