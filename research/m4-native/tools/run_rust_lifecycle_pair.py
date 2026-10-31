#!/usr/bin/env python3
"""Run a policy-bound, quota-contained Rust Gate B pair or authorized diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
import uuid
from io import BytesIO
from typing import Any

import create_measurement_artifact as measurement_artifact
import m4_containment_runner as containment

REPO = pathlib.Path(__file__).resolve().parents[3]
CONTRACT = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']
WORKLOAD = CONTRACT['workload']
RUST_TARGET_DIR = containment.M4_RUST_LIFECYCLE_TARGET
CODE_EXTENSIONS = {'.rs', '.cc', '.cpp', '.h', '.hpp', '.c', '.py', '.cs'}
TEST_NAME = WORKLOAD['test_name']
SAMPLE_INTERVAL_MS = 500


def git(*args: str) -> bytes:
    source_repo = WORKLOAD.get('source_repo', str(REPO))
    return subprocess.run(['git', *args], cwd=source_repo, check=True, stdout=subprocess.PIPE).stdout


def validate_full_corpus_reference(workload: dict[str, Any]) -> None:
    """Reject an unprovenanced full-corpus reference unless it is explicitly blocked."""
    reference = workload.get('full_corpus_reference')
    if not reference:
        return
    if reference.get('provenance_status') == 'blocked_missing_source':
        return
    missing = [key for key in ('source_repo', 'source_commit', 'source_selection')
               if not reference.get(key)]
    if missing:
        raise ValueError('full corpus reference lacks provenance: ' + ', '.join(missing))


def frozen_manifest() -> tuple[list[tuple[str, str, int]], str]:
    validate_full_corpus_reference(WORKLOAD)
    commit = WORKLOAD['source_commit']
    tree = git('ls-tree', '-rlz', commit)
    entries: list[tuple[str, str, int]] = []
    selection = WORKLOAD.get('source_selection')
    for item in tree.split(b'\0'):
        if not item:
            continue
        meta, raw_path = item.split(b'\t', 1)
        _mode, kind, blob, size = meta.decode('ascii').split()
        path = raw_path.decode('utf-8')
        suffix = pathlib.PurePosixPath(path).suffix
        if selection is None:
            included = suffix in CODE_EXTENSIONS
        else:
            included = (
                path.startswith(selection['path_prefix']) and
                suffix in set(selection['extensions'])
            )
        if kind == 'blob' and included:
            entries.append((path, blob, int(size)))
    entries.sort()
    if selection is not None:
        entries = entries[:selection['max_files']]
    encoded = ''.join(f'{path}\t{blob}\t{size}\n' for path, blob, size in entries).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if (len(entries) != WORKLOAD['source_file_count'] or
            sum(entry[2] for entry in entries) != WORKLOAD['source_bytes'] or
            digest != WORKLOAD['source_manifest_sha256']):
        raise ValueError('committed source corpus differs from the frozen Gate B manifest')
    return entries, digest


def is_executable(path: pathlib.Path) -> bool:
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False


def find_test_binary(binary_override: pathlib.Path | None = None,
                     library_override: pathlib.Path | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    if binary_override is None:
        candidates = sorted((RUST_TARGET_DIR / 'debug/deps').glob('zg_engine-*'))
    else:
        binary = binary_override.resolve(strict=True)
        deps = (RUST_TARGET_DIR / 'debug/deps').resolve(strict=True)
        measurement_dir = deps / '.m4-measurement'
        allowed = measurement_dir.resolve(strict=True)
        if (measurement_dir.is_symlink() or allowed.parent != deps or
                not binary.is_relative_to(allowed) or not binary.name.startswith('zg_engine-') or
                not binary.name.endswith('-nodebug') or not binary.is_file() or not is_executable(binary)):
            raise ValueError('opt-in binary must be a nodebug executable in the measurement artifact directory')
        original = deps / binary.name.removesuffix('-nodebug')
        if (not original.is_file() or original.is_symlink() or original.resolve().parent != deps or
                measurement_artifact.load_segments(original) != measurement_artifact.load_segments(binary)):
            raise ValueError('measurement binary differs from its original ELF load image')
        candidates = [binary]
    matches: list[tuple[pathlib.Path, pathlib.Path]] = []
    build_root = (RUST_TARGET_DIR / 'debug/build').resolve(strict=True)
    if library_override is None:
        libs = sorted(build_root.glob('zvec-rust-sys-*/out/zvec-prebuilt/libzvec_c_api.so'))
        if not libs:
            raise FileNotFoundError('compiled libzvec_c_api.so is missing; build before Gate B validation')
        library_dirs = [libs[-1].parent.resolve(strict=True)]
    else:
        if library_override.is_symlink():
            raise ValueError('native library override must not be a symlink')
        library_dir = library_override.resolve(strict=True)
        library_file = library_dir / 'libzvec_c_api.so'
        if (not library_dir.is_relative_to(build_root) or not library_dir.is_dir() or
                library_file.is_symlink() or not library_file.is_file() or
                library_file.resolve(strict=True).parent != library_dir):
            raise ValueError('native library override must be an exact artifact under debug/build')
        library_dirs = [library_dir]
    for binary in candidates:
        if not binary.is_file() or not is_executable(binary):
            continue
        lib = library_dirs[0]
        env = {**os.environ, 'LD_LIBRARY_PATH': str(lib)}
        listing = subprocess.run([str(binary), '--list'], cwd=REPO, env=env, capture_output=True, text=True)
        if listing.returncode == 0 and any(line.startswith(TEST_NAME + ': test') for line in listing.stdout.splitlines()):
            matches.append((binary.resolve(), lib.resolve()))
    if not matches:
        raise ValueError('no compiled Gate B test binary found')
    matches.sort(key=lambda item: (item[0].stat().st_mtime_ns, str(item[0])))
    if (len(matches) > 1 and
            matches[-1][0].stat().st_mtime_ns == matches[-2][0].stat().st_mtime_ns):
        raise ValueError('multiple Gate B test binaries have the same newest build timestamp')
    # Cargo can leave older feature/build variants beside the current test binary.
    # Select the newest binary, then bind its exact SHA-256 into both arm contexts.
    return matches[-1]


def capture_pair_artifacts(binary: pathlib.Path, library_path: pathlib.Path) -> dict[str, str]:
    binary = binary.resolve(strict=True)
    library_path = library_path.resolve(strict=True)
    library = (library_path / 'libzvec_c_api.so').resolve(strict=True)
    if library.parent != library_path or not binary.is_file() or not library.is_file():
        raise ValueError('binary or native library path is not an exact regular artifact')
    return {'binary_path': str(binary), 'binary_sha256': containment.file_sha256(binary),
            'library_path': str(library_path), 'library_file': str(library),
            'library_sha256': containment.file_sha256(library)}


def verify_pair_artifacts(expected: dict[str, str]) -> None:
    actual = capture_pair_artifacts(pathlib.Path(expected['binary_path']), pathlib.Path(expected['library_path']))
    if actual != expected:
        raise ValueError('pair binary or native library identity changed; refusing continuation')


def make_context(operation_id: str, operation_root: pathlib.Path, arm_role: str,
                 execution_order: list[str], artifacts: dict[str, str],
                 workspace_root: pathlib.Path, source_root: pathlib.Path,
                 quota_proof: dict[str, Any], operation_mode: str = 'pair',
                 query_interval_ms: int | None = None) -> dict[str, Any]:
    arm_name = 'pair-first' if arm_role == execution_order[0] else 'pair-second'
    if operation_mode == 'stage_a_rate_ladder':
        authorization = CONTRACT['stage_a_rate_ladder']
    elif operation_mode == 'stage_c_capacity_attribution':
        authorization = CONTRACT['stage_c_capacity_attribution']
    elif operation_mode == 'stage_e2_larger_corpus':
        authorization = CONTRACT['stage_e2_larger_corpus']
    elif operation_mode == 'stage_e3_camera_421':
        authorization = CONTRACT['stage_e3_camera_421']
    elif operation_mode == 'stage_e4_camera_300':
        authorization = CONTRACT['stage_e4_camera_300']
    elif operation_mode == 'native_query_stage_diagnostic':
        authorization = CONTRACT['native_query_stage_diagnostic']
    else:
        authorization = CONTRACT
    arm_roles = authorization.get('arm_roles', CONTRACT['arm_roles'])
    index_root = workspace_root / 'index'
    report_path = workspace_root / 'gate-b-report.json'
    temporary_directory = workspace_root / 'tmp'
    if operation_mode == 'stage_a_rate_ladder':
        resource_basis = (
            'one quota-contained Stage A rate-ladder pair on the 64-file pilot; '
            'no full corpus, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_c_capacity_attribution':
        resource_basis = (
            'one quota-contained Stage C capacity-attribution pair on the 64-file pilot; '
            'no full corpus, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e2_larger_corpus':
        resource_basis = (
            'one quota-contained larger-corpus diagnostic on the 219-file Rust tree; '
            'not full-corpus Gate B, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e3_camera_421':
        resource_basis = (
            'one quota-contained 421-file camera/AttendanceSafetyCamera diagnostic; '
            'not full-corpus Gate B, no production, unchanged cgroup/qgroup caps'
        )
    elif operation_mode == 'stage_e4_camera_300':
        resource_basis = (
            'one quota-contained 300-file camera/AttendanceSafetyCamera diagnostic; '
            'not full-corpus Gate B, no production, unchanged 512 MiB envelope'
        )
    elif operation_mode == 'native_query_stage_diagnostic':
        resource_basis = (
            'one quota-contained native-only Gate B query-stage diagnostic; '
            'no M4 arm or pair comparison'
        )
    else:
        resource_basis = (
            'one quota-contained matched Gate B pair with preserved 18.219 GB disk '
            'reserve and enforced cgroup memory/swap/cpu/pids ceilings'
        )
    context: dict[str, Any] = {
        'schema_version': 1,
        'approval_status': 'approved',
        'mode': containment.CONTENTION_MODE,
        'policy_sha256': hashlib.sha256(containment.POLICY_PATH.read_bytes()).hexdigest(),
        'resource_basis': resource_basis,
        'purpose': containment.M4_RUST_LIFECYCLE_PURPOSE,
        'operation_mode': operation_mode,
        'operation_id': operation_id,
        'operation_root': str(operation_root),
        'approval_reference': authorization['approval_reference'],
        'execution_authorization_reference': authorization['execution_authorization_reference'],
        'risk_acceptance_reference': authorization['risk_acceptance_reference'],
        'workload': WORKLOAD,
        'query_filter': WORKLOAD['query_filter'],
        'corpus_extension': WORKLOAD.get('corpus_extension', '.rs'),
        'reader_query_interval_ms': (
            query_interval_ms if query_interval_ms is not None
            else WORKLOAD['reader_query_interval_ms']),
        'reader_query_max_samples_per_pass': WORKLOAD['reader_query_max_samples_per_pass'],
        'reader_query_queue_capacity': WORKLOAD['reader_query_queue_capacity'],
        'zvec_native_memory_limit_bytes': WORKLOAD['zvec_native_memory_limit_bytes'],
        'arm_roles': arm_roles,
        'arm_role': arm_role,
        'execution_order': execution_order,
        'resource_root': CONTRACT['resource_root'],
        'workspace_root': str(workspace_root),
        'source_root': str(source_root),
        'index_root': str(index_root),
        'report_path': str(report_path),
        'temporary_directory': str(temporary_directory),
        'binary_path': artifacts['binary_path'],
        'binary_name': pathlib.Path(artifacts['binary_path']).name,
        'binary_sha256': artifacts['binary_sha256'],
        'approved_binary_sha256': artifacts['binary_sha256'],
        'library_path': artifacts['library_path'],
        'library_file': artifacts['library_file'],
        'library_sha256': artifacts['library_sha256'],
        'approved_library_sha256': artifacts['library_sha256'],
        'io_pressure_mode': CONTRACT['io_pressure_mode'],
        'workspace_quota_proof': quota_proof,
        **CONTRACT['resource_controls'],
    }
    context['arm_name'] = arm_name
    if operation_mode == 'native_query_stage_diagnostic':
        context['native_profile_trace_path'] = str(
            operation_root / arm_name / 'containment' / 'native-profile.jsonl')
    return context


def build_environment(context: dict[str, Any]) -> dict[str, str]:
    return containment.rust_lifecycle_expected_environment(context)


def test_command(context: dict[str, Any]) -> list[str]:
    return [context['binary_path'], '--exact', TEST_NAME, '--ignored', '--nocapture']


def is_qgroup_headroom_gate_failure(error: BaseException) -> bool:
    return 'insufficient qgroup headroom for the next workspace allocation' in str(error)


def require_pair_workspace_headroom(quota: dict[str, Any], additional_bytes: int) -> None:
    live = quota['live_headroom']
    required = live['required_free_bytes'] + additional_bytes
    if live['available_bytes'] < required:
        raise RuntimeError(
            f'insufficient qgroup headroom for the next workspace allocation: '
            f'available={live["available_bytes"]}, additional={additional_bytes}, '
            f'required_free={live["required_free_bytes"]}, total_required={required}')


def preflight(operation_id: str, binary_override: pathlib.Path | None = None,
              expected_binary_sha256: str | None = None,
              library_override: pathlib.Path | None = None,
              expected_library_sha256: str | None = None,
              native_only_diagnostic: bool = False,
              stage_a_interval_ms: int | None = None,
              stage_c_interval_ms: int | None = None,
              stage_e2_interval_ms: int | None = None,
              stage_e3_interval_ms: int | None = None,
              stage_e4_interval_ms: int | None = None) -> dict[str, Any]:
    if stage_a_interval_ms is not None and native_only_diagnostic:
        raise ValueError('Stage A rate ladder cannot run as a native-only diagnostic')
    if stage_c_interval_ms is not None and native_only_diagnostic:
        raise ValueError('Stage C capacity attribution cannot run as a native-only diagnostic')
    if stage_e2_interval_ms is not None and native_only_diagnostic:
        raise ValueError('larger-corpus diagnostic cannot run as a native-only diagnostic')
    if stage_e3_interval_ms is not None and native_only_diagnostic:
        raise ValueError('camera 421 diagnostic cannot run as a native-only diagnostic')
    if stage_e4_interval_ms is not None and native_only_diagnostic:
        raise ValueError('camera 300 diagnostic cannot run as a native-only diagnostic')
    if len(list(filter(None, (stage_a_interval_ms, stage_c_interval_ms, stage_e2_interval_ms, stage_e3_interval_ms, stage_e4_interval_ms)))) > 1:
        raise ValueError('Stage A, Stage C, and larger-corpus intervals are mutually exclusive')
    operation_mode = ('stage_a_rate_ladder' if stage_a_interval_ms is not None else
                      'stage_c_capacity_attribution' if stage_c_interval_ms is not None else
                      'stage_e2_larger_corpus' if stage_e2_interval_ms is not None else
                      'stage_e3_camera_421' if stage_e3_interval_ms is not None else
                      'stage_e4_camera_300' if stage_e4_interval_ms is not None else
                      'native_query_stage_diagnostic' if native_only_diagnostic else 'pair')
    diagnostic = CONTRACT.get('native_query_stage_diagnostic')
    diagnostic_ids = (diagnostic.get('authorized_operation_ids')
                      if isinstance(diagnostic, dict) else [])
    if not isinstance(diagnostic_ids, list):
        diagnostic_ids = []
    if not native_only_diagnostic and operation_id in diagnostic_ids:
        raise ValueError('native-only diagnostic operation ID cannot launch a matched pair')
    if stage_a_interval_ms is not None:
        stage_a = CONTRACT.get('stage_a_rate_ladder')
        authorized_ids = stage_a.get('authorized_operation_ids') if isinstance(stage_a, dict) else None
        authorized_limit = stage_a.get('max_authorized_operations') if isinstance(stage_a, dict) else None
        intervals = stage_a.get('reader_query_intervals_ms') if isinstance(stage_a, dict) else None
        if (not isinstance(stage_a, dict) or stage_a.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_a_interval_ms not in intervals or
                stage_a.get('arm_roles') != CONTRACT['arm_roles'] or
                stage_a.get('arm_orders') != CONTRACT['arm_orders']):
            raise ValueError('operation ID or interval is outside the Stage A rate-ladder authorization')
    if stage_c_interval_ms is not None:
        stage_c = CONTRACT.get('stage_c_capacity_attribution')
        authorized_ids = stage_c.get('authorized_operation_ids') if isinstance(stage_c, dict) else None
        authorized_limit = stage_c.get('max_authorized_operations') if isinstance(stage_c, dict) else None
        intervals = stage_c.get('reader_query_intervals_ms') if isinstance(stage_c, dict) else None
        if (not isinstance(stage_c, dict) or stage_c.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_c_interval_ms not in intervals or
                stage_c.get('arm_roles') != CONTRACT['arm_roles'] or
                stage_c.get('arm_orders') != [['m4', 'native'], ['native', 'm4']]):
            raise ValueError('operation ID or interval is outside the Stage C capacity-attribution authorization')
    if stage_e2_interval_ms is not None:
        stage_e2 = CONTRACT.get('stage_e2_larger_corpus')
        authorized_ids = stage_e2.get('authorized_operation_ids') if isinstance(stage_e2, dict) else None
        authorized_limit = stage_e2.get('max_authorized_operations') if isinstance(stage_e2, dict) else None
        intervals = stage_e2.get('reader_query_intervals_ms') if isinstance(stage_e2, dict) else None
        if (not isinstance(stage_e2, dict) or stage_e2.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e2_interval_ms not in intervals or
                stage_e2.get('arm_roles') != CONTRACT['arm_roles'] or
                stage_e2.get('arm_orders') != [['m4', 'native']]):
            raise ValueError('operation ID or interval is outside the larger-corpus diagnostic authorization')
    if stage_e3_interval_ms is not None:
        stage_e3 = CONTRACT.get('stage_e3_camera_421')
        authorized_ids = stage_e3.get('authorized_operation_ids') if isinstance(stage_e3, dict) else None
        authorized_limit = stage_e3.get('max_authorized_operations') if isinstance(stage_e3, dict) else None
        intervals = stage_e3.get('reader_query_intervals_ms') if isinstance(stage_e3, dict) else None
        if (not isinstance(stage_e3, dict) or stage_e3.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e3_interval_ms not in intervals or
                stage_e3.get('arm_roles') != CONTRACT['arm_roles'] or
                stage_e3.get('arm_orders') != [['m4', 'native']]):
            raise ValueError('operation ID or interval is outside the camera 421 diagnostic authorization')
    if stage_e4_interval_ms is not None:
        stage_e4 = CONTRACT.get('stage_e4_camera_300')
        authorized_ids = stage_e4.get('authorized_operation_ids') if isinstance(stage_e4, dict) else None
        authorized_limit = stage_e4.get('max_authorized_operations') if isinstance(stage_e4, dict) else None
        intervals = stage_e4.get('reader_query_intervals_ms') if isinstance(stage_e4, dict) else None
        if (not isinstance(stage_e4, dict) or stage_e4.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e4_interval_ms not in intervals or
                stage_e4.get('arm_roles') != CONTRACT['arm_roles'] or
                stage_e4.get('arm_orders') != [['m4', 'native']]):
            raise ValueError('operation ID or interval is outside the camera 300 diagnostic authorization')
    if native_only_diagnostic:
        authorized_ids = diagnostic.get('authorized_operation_ids') if isinstance(diagnostic, dict) else None
        authorized_limit = diagnostic.get('max_authorized_operations') if isinstance(diagnostic, dict) else None
        if (not isinstance(diagnostic, dict) or diagnostic.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                diagnostic.get('arm_roles') != ['native'] or
                diagnostic.get('execution_order') != ['native']):
            raise ValueError('operation ID is outside the native-only diagnostic authorization')
    global WORKLOAD
    WORKLOAD = (CONTRACT['stage_e4_camera_300']['workload']
                if stage_e4_interval_ms is not None else
                CONTRACT['stage_e3_camera_421']['workload']
                if stage_e3_interval_ms is not None else
                CONTRACT['stage_e2_larger_corpus']['workload']
                if stage_e2_interval_ms is not None else CONTRACT['workload'])
    entries, digest = frozen_manifest()
    binary, library = find_test_binary(binary_override, library_override)
    artifacts = capture_pair_artifacts(binary, library)
    if expected_binary_sha256 is not None and artifacts['binary_sha256'] != expected_binary_sha256:
        raise ValueError('selected binary SHA-256 differs from explicitly expected artifact')
    if expected_library_sha256 is not None and artifacts['library_sha256'] != expected_library_sha256:
        raise ValueError('selected native library SHA-256 differs from explicitly expected artifact')
    resource_root = pathlib.Path(CONTRACT['resource_root'])
    controls = CONTRACT['resource_controls']
    quota = containment.verify_workspace_quota(resource_root, CONTRACT['workspace_quota'])
    require_pair_workspace_headroom(quota, controls['workspace_max_allocated_bytes'])
    if controls['max_monitor_gap_seconds'] * 1000 < SAMPLE_INTERVAL_MS * 2:
        raise ValueError('observer sample interval leaves insufficient margin under max monitor gap')
    disk_free = shutil.disk_usage(resource_root).free
    required_free = controls['disk_reserve_bytes'] + 2 * controls['workspace_max_allocated_bytes']
    if disk_free < required_free:
        raise RuntimeError(f'disk reserve/capacity gate: free={disk_free}, required={required_free}; no launch')
    operation_root = resource_root / operation_id
    if operation_root.exists():
        raise ValueError('operation ID already has a workspace; reuse is refused')
    stage_c_orders = (CONTRACT['stage_c_capacity_attribution'].get('arm_orders')
                      if stage_c_interval_ms is not None else None)
    stage_e2_orders = (CONTRACT['stage_e2_larger_corpus'].get('arm_orders')
                       if stage_e2_interval_ms is not None else None)
    stage_e3_orders = (CONTRACT['stage_e3_camera_421'].get('arm_orders')
                       if stage_e3_interval_ms is not None else None)
    stage_e4_orders = (CONTRACT['stage_e4_camera_300'].get('arm_orders')
                       if stage_e4_interval_ms is not None else None)
    order = (['native'] if native_only_diagnostic else
             stage_e4_orders[0] if stage_e4_orders else
             stage_e3_orders[0] if stage_e3_orders else
             stage_e2_orders[0] if stage_e2_orders else
             stage_c_orders[hashlib.sha256(operation_id.encode()).digest()[0] & 1]
             if stage_c_orders else
             ['native', 'm4'] if hashlib.sha256(operation_id.encode()).digest()[0] & 1
             else ['m4', 'native'])
    contexts = []
    for role in order:
        arm_name = 'pair-first' if role == order[0] else 'pair-second'
        arm_root = operation_root / arm_name / 'workspace'
        source_root = arm_root / 'source'
        context = make_context(operation_id, operation_root, role, order, artifacts,
                               arm_root, source_root, quota, operation_mode,
                               stage_e4_interval_ms if stage_e4_interval_ms is not None else
                               stage_e4_interval_ms if stage_e4_interval_ms is not None else
            stage_e3_interval_ms if stage_e3_interval_ms is not None else
                               stage_e2_interval_ms if stage_e2_interval_ms is not None else
                               stage_c_interval_ms if stage_c_interval_ms is not None else stage_a_interval_ms)
        containment.validate_contention_context(context)
        env = build_environment(context)
        receipt_dir = operation_root / arm_name / 'containment'
        containment.validate_watchdog_operation_binding({
            'policy_mode': containment.CONTENTION_MODE,
            'policy_context': context,
            'arm_name': arm_name,
            'watchdog_output': str(receipt_dir),
            'resource_root': CONTRACT['resource_root'],
            'enforce_host_gates': True,
            'budget': containment.contention_budget(context),
            'runtime_limit_seconds': context['runtime_limit_seconds'],
            'policy_context_digest': containment.contention_context_digest(context),
        })
        command = test_command(context)
        if (command != [str(binary), '--exact', TEST_NAME, '--ignored', '--nocapture'] or
                env['M4_GATE_B_ARM'] != role or
                env['M4_GATE_B_READER_QUERY_INTERVAL_MS'] != str(
                    context['reader_query_interval_ms']) or
                env['M4_GATE_B_READER_QUERY_MAX_SAMPLES'] != str(
                    WORKLOAD['reader_query_max_samples_per_pass']) or
                env['M4_GATE_B_READER_QUERY_QUEUE_CAPACITY'] != str(
                    WORKLOAD['reader_query_queue_capacity']) or
                env['M4_GATE_B_QUERY_FILTER'] != WORKLOAD['query_filter'] or
                env['M4_GATE_B_CORPUS_EXTENSION'] != WORKLOAD.get('corpus_extension', '.rs') or
                env['ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES'] != str(
                    WORKLOAD['zvec_native_memory_limit_bytes']) or
                pathlib.Path(env['M4_GATE_B_WORKSPACE']) != source_root or
                (native_only_diagnostic and pathlib.Path(
                    env.get('M4_NATIVE_PROFILE_LOG_PATH', '')).resolve() !=
                    (receipt_dir / 'native-profile.jsonl').resolve())):
            raise ValueError('Gate B command/environment binding failed')
        contexts.append({
            'role': role, 'arm_name': arm_name, 'command': command,
            'receipt_dir': str(receipt_dir), 'workspace_root': str(arm_root),
            'native_profile_trace_path': context.get('native_profile_trace_path'),
            'binary_sha256': context['binary_sha256'],
            'policy_context_sha256': containment.contention_context_digest(context),
        })
    return {
        'operation_id': operation_id,
        'operation_mode': operation_mode,
        'reader_query_interval_ms': (
            stage_e4_interval_ms if stage_e4_interval_ms is not None else
            stage_e3_interval_ms if stage_e3_interval_ms is not None else
            stage_e2_interval_ms if stage_e2_interval_ms is not None else
            stage_c_interval_ms if stage_c_interval_ms is not None else
            stage_a_interval_ms if stage_a_interval_ms is not None
            else WORKLOAD['reader_query_interval_ms']),
        'operation_root': str(operation_root),
        'corpus_commit': WORKLOAD['source_commit'],
        'corpus_file_count': len(entries), 'corpus_bytes': sum(item[2] for item in entries),
        'corpus_manifest_sha256': digest,
        'execution_order': order,
        'artifacts': artifacts,
        'workspace_quota_proof': quota,
        'resource_controls': CONTRACT['resource_controls'],
        'observer': {'sample_interval_ms': SAMPLE_INTERVAL_MS,
                     'max_monitor_gap_seconds': controls['max_monitor_gap_seconds']},
        'disk_capacity': {'free_bytes': disk_free, 'required_bytes': required_free,
                          'headroom_bytes': disk_free - required_free},
        'arms': contexts,
        'benchmark_started': False,
    }


def extract_corpus(destination: pathlib.Path, entries: list[tuple[str, str, int]]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    commit = WORKLOAD['source_commit']
    paths = [entry[0] for entry in entries]
    archive = git('archive', '--format=tar', commit, '--', *paths)
    total = 0
    with tarfile.open(fileobj=BytesIO(archive), mode='r:') as tar:
        for member in tar:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError(f'unsafe corpus archive path: {member.name}')
            if member.isdir():
                continue
            if not member.isreg():
                raise ValueError(f'unsafe/non-regular corpus archive entry: {member.name}')
            if path.suffix not in CODE_EXTENSIONS:
                continue
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f'corpus archive entry has no content: {member.name}')
            with source:
                data = source.read()
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            total += len(data)
    if total != WORKLOAD['source_bytes']:
        raise ValueError(f'materialized corpus byte count mismatch: {total}')


def arm_result_allows_pair_continuation(result: dict[str, Any]) -> bool:
    """Continue after clean workload completion with accounting caveats, never after a safety failure."""
    if result.get('status') not in {
        'PASS', 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'COMPLETED_WITH_WORKSPACE_WARNINGS'
    }:
        return False
    cleanup = result.get('unit_cleanup') or result.get('controller_cleanup') or {}
    summary = result.get('watchdog_summary') or {}
    postflight = result.get('postflight_decision') or {}
    return (
        result.get('workload_returncode') == 0
        and result.get('watchdog_abort') is None
        and result.get('safety_outcome') in {
            'SAFE_COMPLETE', 'NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED'
        }
        and summary.get('hard_safety_telemetry_complete') is True
        and cleanup.get('verified') is True
        and cleanup.get('unit_collected') is True
        and postflight.get('continuation_allowed') is True
    )


def run_pair(operation_id: str, binary_override: pathlib.Path | None = None,
             expected_binary_sha256: str | None = None,
             library_override: pathlib.Path | None = None,
             expected_library_sha256: str | None = None,
             native_only_diagnostic: bool = False,
             stage_a_interval_ms: int | None = None,
             stage_c_interval_ms: int | None = None,
             stage_e2_interval_ms: int | None = None,
             stage_e3_interval_ms: int | None = None,
             stage_e4_interval_ms: int | None = None) -> dict[str, Any]:
    plan = preflight(operation_id, binary_override, expected_binary_sha256,
                      library_override, expected_library_sha256, native_only_diagnostic,
                      stage_a_interval_ms, stage_c_interval_ms, stage_e2_interval_ms,
                      stage_e3_interval_ms, stage_e4_interval_ms)
    operation_root = pathlib.Path(plan['operation_root'])
    if operation_root.exists():
        raise FileExistsError(f'fresh operation root already exists: {operation_root}')
    entries, _ = frozen_manifest()
    operation_root.mkdir(mode=0o700)
    arm_results: list[dict[str, Any]] = []
    try:
        return _run_pair_arms(operation_id, plan, entries, operation_root, arm_results)
    except Exception as error:
        error_text = f'{type(error).__name__}: {error}'
        quota_safety_stop = is_qgroup_headroom_gate_failure(error)
        recorded_arms = {arm.get('arm_name') for arm in arm_results}
        for item in plan['arms']:
            if item['arm_name'] in recorded_arms:
                continue
            receipt_path = pathlib.Path(item['receipt_dir']) / 'receipt.json'
            if receipt_path.is_file():
                arm_results.append({
                    'role': item['role'], 'arm_name': item['arm_name'],
                    'receipt_path': str(receipt_path.relative_to(REPO)),
                    'runner': json.loads(receipt_path.read_text()),
                })
        failure = {
            'schema_version': 1, 'operation_id': operation_id,
            'operation_mode': plan['operation_mode'],
            'status': ('DIAGNOSTIC_BLOCKED' if quota_safety_stop else 'DIAGNOSTIC_ERROR')
                      if plan['operation_mode'] == 'native_query_stage_diagnostic' else
                      'OPERATION_BLOCKED' if quota_safety_stop else 'OPERATION_ERROR',
            'failure_class': 'SAFETY_STOP' if quota_safety_stop else 'IMPLEMENTATION_OR_OPERATION_ERROR',
            'operation_outcome': 'SAFETY_STOP' if quota_safety_stop else 'OPERATION_ERROR',
            'benchmark_started': any(arm.get('runner', {}).get('benchmark_started') for arm in arm_results),
            'error': error_text, 'arms': arm_results,
            'retry': 'do not automatically retry after a refusal or safety stop; any future operation requires a fresh owner authorization and ID',
        }
        filename = ('native-query-stage-result.json'
                    if plan['operation_mode'] == 'native_query_stage_diagnostic'
                    else 'pair-result.json')
        (operation_root / filename).write_text(json.dumps(failure, indent=2) + '\n')
        raise


def _query_latency_arm_reasons(role: str, report: dict[str, Any]) -> list[str]:
    reasons = []
    if report.get('arm') != role:
        reasons.append('arm_role_mismatch')
    if report.get('concurrent_query_latency_p95_comparable') is not True:
        reasons.append('arm_report_marks_p95_non_comparable')
    required_counts = (
        'concurrent_query_total', 'concurrent_query_offered_slots',
        'concurrent_query_accepted_slots', 'concurrent_query_started',
        'concurrent_query_dropped_slots', 'concurrent_query_slots_not_offered_by_cap',
        'concurrent_query_schedule_slots_missed', 'concurrent_query_failures',
        'concurrent_query_sampling_capped_passes', 'concurrent_query_max_schedule_lag_us',
        'reader_query_interval_ms',
    )
    values: dict[str, int] = {}
    for key in required_counts:
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            reasons.append('missing_or_invalid_offer_telemetry')
            return reasons
        values[key] = value
    if values['reader_query_interval_ms'] == 0:
        reasons.append('invalid_query_interval')
    p95 = report.get('concurrent_query_p95_us')
    if isinstance(p95, bool) or not isinstance(p95, int) or p95 < 0:
        reasons.append('missing_or_invalid_p95')
    if values['concurrent_query_total'] < 20:
        reasons.append('fewer_than_20_successful_queries')
    if not (values['concurrent_query_offered_slots'] == values['concurrent_query_accepted_slots'] ==
            values['concurrent_query_started'] == values['concurrent_query_total']):
        reasons.append('offers_not_all_accepted_started_and_completed')
    if values['concurrent_query_dropped_slots'] or values['concurrent_query_slots_not_offered_by_cap']:
        reasons.append('offers_dropped_or_capped')
    if values['concurrent_query_schedule_slots_missed']:
        reasons.append('scheduled_starts_missed')
    if values['concurrent_query_failures']:
        reasons.append('query_failures')
    if values['concurrent_query_sampling_capped_passes']:
        reasons.append('sampling_capped')
    if values['concurrent_query_max_schedule_lag_us'] > values['reader_query_interval_ms'] * 1000:
        reasons.append('scheduler_lag_exceeds_one_interval')
    return reasons


def query_latency_pair_gate(arm_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_role = {item.get('role'): item.get('workload_report') for item in arm_results}
    native, m4 = by_role.get('native'), by_role.get('m4')
    if not isinstance(native, dict) or not isinstance(m4, dict):
        return {'comparable': False, 'reasons': ['missing_native_or_m4_report'], 'p95_us_by_role': {}}
    protocol_fields = (
        'reader_query_interval_ms', 'reader_query_max_samples_per_pass',
        'reader_query_queue_capacity', 'query_filter', 'query_route',
    )
    def valid_protocol(report: dict[str, Any]) -> bool:
        for key in ('reader_query_interval_ms', 'reader_query_max_samples_per_pass',
                    'reader_query_queue_capacity'):
            value = report.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                return False
        return all(isinstance(report.get(key), str) and bool(report[key].strip())
                   for key in ('query_filter', 'query_route'))

    protocols_valid = valid_protocol(native) and valid_protocol(m4)
    protocol_equal = protocols_valid and all(native[key] == m4[key] for key in protocol_fields)
    reasons = [] if protocols_valid else ['missing_or_invalid_offered_load_protocol']
    if protocols_valid and not protocol_equal:
        reasons.append('offered_load_protocol_mismatch')
    native_reasons = _query_latency_arm_reasons('native', native)
    m4_reasons = _query_latency_arm_reasons('m4', m4)
    reasons.extend(f'native:{reason}' for reason in native_reasons)
    reasons.extend(f'm4:{reason}' for reason in m4_reasons)
    return {
        'comparable': protocol_equal and not native_reasons and not m4_reasons,
        'offered_load_protocol_equal': protocol_equal,
        'reasons': reasons,
        'p95_us_by_role': {
            'native': native.get('concurrent_query_p95_us'),
            'm4': m4.get('concurrent_query_p95_us'),
        },
    }


def _run_pair_arms(operation_id: str, plan: dict[str, Any], entries: list[tuple[str, str, int]],
                   operation_root: pathlib.Path, arm_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in plan['arms']:
        arm_name, role = item['arm_name'], item['role']
        arm_root = operation_root / arm_name
        workspace_root = arm_root / 'workspace'
        resource_root = pathlib.Path(CONTRACT['resource_root'])
        quota_before = containment.verify_workspace_quota(
            resource_root, CONTRACT['workspace_quota'])
        require_pair_workspace_headroom(
            quota_before, CONTRACT['resource_controls']['workspace_max_allocated_bytes'])
        workspace_root.mkdir(parents=True, mode=0o700)
        source_root = workspace_root / 'source'
        extract_corpus(source_root, entries)
        temp_root = workspace_root / 'tmp'
        temp_root.mkdir(mode=0o700)
        # Recheck live headroom after corpus materialization and reserve the remaining
        # workspace allowance plus the policy's minimum free space before launch.
        quota = containment.verify_workspace_quota(resource_root, CONTRACT['workspace_quota'])
        materialized_bytes = max(
            0, quota_before['live_headroom']['available_bytes'] -
            quota['live_headroom']['available_bytes'])
        remaining_workspace = max(
            0, CONTRACT['resource_controls']['workspace_max_allocated_bytes'] - materialized_bytes)
        require_pair_workspace_headroom(quota, remaining_workspace)
        verify_pair_artifacts(plan['artifacts'])
        context = make_context(operation_id, operation_root, role, plan['execution_order'],
                               plan['artifacts'], workspace_root, source_root, quota,
                               plan['operation_mode'],
                               plan.get('reader_query_interval_ms'))
        containment.validate_contention_context(context)
        receipt_dir = operation_root / arm_name / 'containment'
        result = containment.run_arm(
            test_command(context), receipt_dir,
            pathlib.Path(CONTRACT['resource_root']), workspace_root, workspace_root,
            CONTRACT['resource_controls']['runtime_limit_seconds'],
            sample_interval_ms=SAMPLE_INTERVAL_MS,
            budget=containment.contention_budget(context),
            env=build_environment(context), enforce_host_gates=True,
            policy_context=context)
        arm_record: dict[str, Any] = {'role': role, 'arm_name': arm_name, 'runner': result}
        trace_complete = True
        if plan['operation_mode'] == 'native_query_stage_diagnostic':
            trace_path = receipt_dir / 'native-profile.jsonl'
            valid_lines = 0
            malformed_lines = 0
            if trace_path.is_file():
                with trace_path.open('r', encoding='utf-8', errors='replace') as trace_file:
                    for line in trace_file:
                        if (line.startswith('NATIVE_PROFILE event=') and
                                all(key in line for key in (
                                    ' thread_id=', ' wall_start_ns=', ' wall_end_ns=',
                                    ' wall_ns=', ' thread_cpu_ns=', ' clock_ok='))):
                            valid_lines += 1
                        else:
                            malformed_lines += 1
            trace_complete = valid_lines > 0 and malformed_lines == 0
            arm_record['native_profile_trace'] = {
                'status': 'PRESENT' if trace_complete else 'INCOMPLETE',
                'path': str(trace_path),
                'bytes': trace_path.stat().st_size if trace_path.is_file() else 0,
                'valid_event_lines': valid_lines,
                'malformed_lines': malformed_lines,
                'sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest()
                if trace_path.is_file() else None,
            }
        arm_results.append(arm_record)
        if not arm_result_allows_pair_continuation(result) or not trace_complete:
            trace_stop = (plan['operation_mode'] == 'native_query_stage_diagnostic' and
                          not trace_complete and arm_result_allows_pair_continuation(result))
            stopped = {
                'schema_version': 1,
                'operation_id': operation_id,
                'operation_mode': plan['operation_mode'],
                'status': ('DIAGNOSTIC_STOPPED' if plan['operation_mode'] == 'native_query_stage_diagnostic'
                           else 'PAIR_STOPPED'),
                'stop_reason': (
                    'native lock trace missing or malformed; operation consumed, no automatic retry'
                    if trace_stop else
                    f'{arm_name} did not complete with verified hard safety and cleanup; no retry'),
                'benchmark_started': bool(result.get('benchmark_started')),
                'native_profile_trace': arm_record.get('native_profile_trace'),
                'arms': arm_results,
                'retry': 'do not automatically retry; a future operation requires a fresh owner authorization and ID',
            }
            filename = ('native-query-stage-result.json'
                        if plan['operation_mode'] == 'native_query_stage_diagnostic'
                        else 'pair-result.json')
            (operation_root / filename).write_text(json.dumps(stopped, indent=2) + '\n')
            return stopped
        verify_pair_artifacts(plan['artifacts'])
        report_path = pathlib.Path(context['report_path'])
        try:
            report = json.loads(report_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f'workload report is unreadable: {report_path}') from error
        report['query_filter'] = WORKLOAD['query_filter']
        report['query_route'] = 'FTS'
        arm_results[-1]['workload_report'] = report
        # Capture index footprint, then release the entire generated workspace so the
        # next matched arm can retain the qgroup minimum-free reserve.
        index_root = pathlib.Path(context['index_root'])
        arm_results[-1]['index_apparent_bytes_before_cleanup'] = sum(
            p.stat().st_size for p in index_root.rglob('*') if p.is_file())
        try:
            shutil.rmtree(workspace_root)
        except OSError as error:
            raise RuntimeError(f'operation-owned workspace cleanup failed: {workspace_root}') from error
        if workspace_root.exists():
            raise RuntimeError('operation-owned workspace cleanup failed')

    if plan['operation_mode'] == 'native_query_stage_diagnostic':
        arm = arm_results[0]
        post_cleanup_quota = containment.verify_workspace_quota(
            pathlib.Path(CONTRACT['resource_root']), CONTRACT['workspace_quota'])
        workload_report = arm['workload_report']
        runner_status = arm['runner']['status']
        result = {
            'schema_version': 1,
            'operation_id': operation_id,
            'operation_mode': plan['operation_mode'],
            'status': ('DIAGNOSTIC_COMPLETED' if runner_status == 'PASS'
                       else 'DIAGNOSTIC_COMPLETED_WITH_CAVEATS'),
            'measurement_status': runner_status,
            'scope': 'single native Gate B lifecycle arm for FTS stage attribution only',
            'pair_comparison_performed': False,
            'query_latency_comparison': 'NOT_RUN_NATIVE_ONLY_DIAGNOSTIC',
            'performance_eligible': False,
            'corpus': {'commit': WORKLOAD['source_commit'],
                       'manifest_sha256': plan['corpus_manifest_sha256'],
                       'file_count': plan['corpus_file_count'], 'bytes': plan['corpus_bytes']},
            'execution_order': ['native'],
            'artifacts': plan['artifacts'],
            'native_profile_trace': arm.get('native_profile_trace'),
            'native_arm': arm,
            'native_workload_report': workload_report,
            'post_cleanup_workspace_quota': post_cleanup_quota,
            'limitations': [
                'single native-only diagnostic; no paired M4 comparison',
                'no performance or architecture-win claim',
                'if target stalls are not reproduced or stage spans are incomplete, attribution remains inconclusive',
            ],
        }
        (operation_root / 'native-query-stage-result.json').write_text(
            json.dumps(result, indent=2) + '\n')
        return result

    by_role = {arm['role']: arm for arm in arm_results}
    native = by_role['native']['workload_report']
    m4 = by_role['m4']['workload_report']
    parity = {
        'mutations_equal': native['mutations'] == m4['mutations'],
        'indexed_file_count_equal': native['indexed_file_count'] == m4['indexed_file_count'],
        'final_marker_paths_equal': native['final_marker_paths'] == m4['final_marker_paths'],
        'post_reopen_marker_paths_equal': native['post_reopen_marker_paths'] == m4['post_reopen_marker_paths'],
        'same_pass_names': [p['name'] for p in native['passes']] == [p['name'] for p in m4['passes']],
    }
    parity['pass'] = all(parity.values())
    arm_statuses = [arm['runner']['status'] for arm in arm_results]
    all_arms_clean_pass = all(status == 'PASS' for status in arm_statuses)
    query_latency_gate = query_latency_pair_gate(arm_results)
    query_p95_comparable = query_latency_gate['comparable']
    result = {
        'schema_version': 1, 'operation_id': operation_id,
        'status': ('CORRECTNESS_MISMATCH' if not parity['pass'] else
                   'PASS' if all_arms_clean_pass else 'PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS'),
        'measurement_status': 'PASS' if all_arms_clean_pass else 'COMPLETED_WITH_WORKSPACE_CAVEATS',
        'query_latency_comparison': 'DESCRIPTIVE_ONLY' if query_p95_comparable else 'NOT_COMPARABLE_SAMPLING_INCOMPLETE',
        'query_latency_p95_comparable_across_arms': query_p95_comparable,
        'query_latency_gate': query_latency_gate,
        'performance_eligible': False,
        'performance_interpretation': 'one descriptive exploratory pair; not qualification or a causal/architecture-win claim',
        'corpus': {'commit': WORKLOAD['source_commit'],
                   'manifest_sha256': plan['corpus_manifest_sha256'],
                   'file_count': plan['corpus_file_count'], 'bytes': plan['corpus_bytes']},
        'execution_order': plan['execution_order'], 'artifacts': plan['artifacts'],
        'parity': parity, 'arms': arm_results,
        'limitations': ['one repository snapshot', 'one pair', 'no cgroup io.stat/io.max',
                        'host I/O pressure is observational', 'fixed deterministic embedding model'],
    }
    (operation_root / 'pair-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


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
