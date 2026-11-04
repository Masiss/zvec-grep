#!/usr/bin/env python3
"""Compatibility wrapper for the V2 measurement artifact helper."""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib

import m4_containment_runner as containment

REPO = pathlib.Path(__file__).resolve().parents[3]
_V2_PATH = REPO / "benchmarks/harness-v2/measurement/artifact.py"


def _load_v2():
    spec = importlib.util.spec_from_file_location("harness_v2_measurement_artifact", _V2_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_V2_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ARTIFACT = _load_v2()
TARGET = containment.M4_RUST_LIFECYCLE_TARGET / "debug/deps"
debug_sections = _ARTIFACT.debug_sections
load_segments = _ARTIFACT.load_segments


def create(source: pathlib.Path, output: pathlib.Path) -> dict:
    return _ARTIFACT.create(source, output, TARGET)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(create(args.source, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
