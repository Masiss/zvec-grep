#!/usr/bin/env python3
"""Compatibility CLI for the policy-bound Rust Gate B lifecycle operation."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid

import create_measurement_artifact as measurement_artifact
import m4_containment_runner as containment

REPO = pathlib.Path(__file__).resolve().parents[3]
HARNESS_V2 = REPO / "benchmarks" / "harness-v2"
if str(HARNESS_V2) not in sys.path:
    sys.path.insert(0, str(HARNESS_V2))

import importlib  # noqa: E402

_config = importlib.import_module("operations.m4.rust_lifecycle.config")
_context = importlib.import_module("operations.m4.rust_lifecycle.context")
_executor = importlib.import_module("operations.m4.rust_lifecycle.executor")
_preflight = importlib.import_module("operations.m4.rust_lifecycle.preflight")
_receipt = importlib.import_module("operations.m4.rust_lifecycle.receipt")
_verify = importlib.import_module("operations.m4.rust_lifecycle.verify")

_config.configure(containment, measurement_artifact)

_CONFIG_EXPORTS = {
    "CONTRACT",
    "WORKLOAD",
    "RUST_TARGET_DIR",
    "CODE_EXTENSIONS",
    "TEST_NAME",
    "SAMPLE_INTERVAL_MS",
}


def __getattr__(name: str):
    if name in _CONFIG_EXPORTS:
        return getattr(_config, name)
    raise AttributeError(name)

validate_full_corpus_reference = _context.validate_full_corpus_reference
git = _context.git
frozen_manifest = _context.frozen_manifest
find_test_binary = _context.find_test_binary
make_context = _context.make_context
build_environment = _context.build_environment
test_command = _context.test_command
is_qgroup_headroom_gate_failure = _preflight.is_qgroup_headroom_gate_failure
require_pair_workspace_headroom = _preflight.require_pair_workspace_headroom
preflight = _preflight.preflight
extract_corpus = _executor.extract_corpus
run_pair = _executor.run_pair
_run_pair_arms = _executor._run_pair_arms
arm_result_allows_pair_continuation = _executor.arm_result_allows_pair_continuation
query_latency_pair_gate = _verify.query_latency_pair_gate


def capture_pair_artifacts(binary: pathlib.Path, library_path: pathlib.Path) -> dict[str, str]:
    return _context.capture_pair_artifacts(binary, library_path, containment.file_sha256)


def verify_pair_artifacts(expected: dict[str, str]) -> None:
    _context.verify_pair_artifacts(expected, containment.file_sha256)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true',
                        help='validate corpus, binary, quota, policy bindings and commands without creating files or launching')
    parser.add_argument('--stage-a-interval-ms', type=int,
                        help='Stage A rate-ladder interval in milliseconds; requires the Stage A operation ID')
    parser.add_argument('--stage-c-interval-ms', type=int,
                        help='Stage C capacity-attribution interval in milliseconds; requires the Stage C operation ID')
    parser.add_argument('--stage-e2-interval-ms', type=int,
                        help='larger-corpus diagnostic interval in milliseconds; requires its operation ID')
    parser.add_argument('--stage-e3-interval-ms', type=int,
                        help='camera 421 diagnostic interval in milliseconds; requires its operation ID')
    parser.add_argument('--stage-e4-interval-ms', type=int,
                        help='camera 300 diagnostic interval in milliseconds; requires its operation ID')
    parser.add_argument('--native-only-diagnostic', action='store_true',
                        help='use only the exactly authorized native arm for FTS stage attribution; never run the M4 arm or a pair comparison')
    parser.add_argument('--operation-id', help='fresh unique operation ID; required for launch')
    parser.add_argument('--binary-path', type=pathlib.Path,
                        help='opt in to a test binary under debug/deps/.m4-measurement; all existing preflight and hash guards still apply')
    parser.add_argument('--expected-binary-sha256',
                        help='require this exact lowercase SHA-256 for the selected test binary, whether explicitly supplied or default-discovered')
    parser.add_argument('--library-path', type=pathlib.Path,
                        help='opt in to one exact native-library directory under debug/build')
    parser.add_argument('--expected-library-sha256',
                        help='require this exact lowercase SHA-256 before the native library is used')
    args = parser.parse_args()
    stage_a_interval_ms = getattr(args, 'stage_a_interval_ms', None)
    stage_c_interval_ms = getattr(args, 'stage_c_interval_ms', None)
    stage_e2_interval_ms = getattr(args, 'stage_e2_interval_ms', None)
    stage_e3_interval_ms = getattr(args, 'stage_e3_interval_ms', None)
    stage_e4_interval_ms = getattr(args, 'stage_e4_interval_ms', None)
    if args.expected_binary_sha256 is not None and (
            len(args.expected_binary_sha256) != 64 or
            any(c not in '0123456789abcdef' for c in args.expected_binary_sha256)):
        parser.error('--expected-binary-sha256 requires 64 lowercase hexadecimal characters')
    if args.expected_library_sha256 is not None and (args.library_path is None or
            len(args.expected_library_sha256) != 64 or
            any(c not in '0123456789abcdef' for c in args.expected_library_sha256)):
        parser.error('--expected-library-sha256 requires --library-path and 64 lowercase hexadecimal characters')
    if args.library_path is not None and args.expected_library_sha256 is None:
        parser.error('--library-path requires an exact --expected-library-sha256')
    if args.validate_only:
        if args.native_only_diagnostic and not args.operation_id:
            parser.error('--native-only-diagnostic requires its exact authorized --operation-id')
        operation_id = args.operation_id or (
            'm4-rust-lifecycle-pair-validate-' + uuid.uuid4().hex[:12])
        if not operation_id.startswith('m4-rust-lifecycle-pair-'):
            parser.error('operation ID must use the m4-rust-lifecycle-pair- prefix')
        plan = preflight(operation_id, args.binary_path, args.expected_binary_sha256,
                         args.library_path, args.expected_library_sha256,
                         native_only_diagnostic=args.native_only_diagnostic,
                         stage_a_interval_ms=stage_a_interval_ms,
                         stage_c_interval_ms=stage_c_interval_ms,
                         stage_e2_interval_ms=stage_e2_interval_ms,
                         stage_e3_interval_ms=stage_e3_interval_ms,
                         stage_e4_interval_ms=stage_e4_interval_ms)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if not args.operation_id:
        parser.error('--operation-id is required for launch')
    result = run_pair(args.operation_id, args.binary_path, args.expected_binary_sha256,
                      args.library_path, args.expected_library_sha256,
                      native_only_diagnostic=args.native_only_diagnostic,
                      stage_a_interval_ms=stage_a_interval_ms,
                      stage_c_interval_ms=stage_c_interval_ms,
                      stage_e2_interval_ms=stage_e2_interval_ms,
                      stage_e3_interval_ms=stage_e3_interval_ms,
                      stage_e4_interval_ms=stage_e4_interval_ms)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] in {
        'PASS', 'DIAGNOSTIC_COMPLETED', 'DIAGNOSTIC_COMPLETED_WITH_CAVEATS'
    } else 2


if __name__ == '__main__':
    raise SystemExit(main())
