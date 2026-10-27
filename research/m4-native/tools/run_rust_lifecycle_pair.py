#!/usr/bin/env python3
"""Run one policy-bound, quota-contained Rust Gate B lifecycle pair."""
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

import m4_containment_runner as containment

REPO = pathlib.Path(__file__).resolve().parents[3]
CONTRACT = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']
CODE_EXTENSIONS = {'.rs', '.cc', '.cpp', '.h', '.hpp', '.c', '.py'}
TEST_NAME = CONTRACT['workload']['test_name']
SAMPLE_INTERVAL_MS = 500


def git(*args: str) -> bytes:
    return subprocess.run(['git', *args], cwd=REPO, check=True, stdout=subprocess.PIPE).stdout


def frozen_manifest() -> tuple[list[tuple[str, str, int]], str]:
    commit = CONTRACT['workload']['source_commit']
    tree = git('ls-tree', '-rlz', commit)
    entries: list[tuple[str, str, int]] = []
    selection = CONTRACT['workload'].get('source_selection')
    for item in tree.split(b'\0'):
        if not item:
            continue
        meta, raw_path = item.split(b'\t', 1)
        mode, kind, blob, size = meta.decode('ascii').split()
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
    if (len(entries) != CONTRACT['workload']['source_file_count'] or
            sum(entry[2] for entry in entries) != CONTRACT['workload']['source_bytes'] or
            digest != CONTRACT['workload']['source_manifest_sha256']):
        raise ValueError('committed source corpus differs from the frozen Gate B manifest')
    return entries, digest


def find_test_binary() -> tuple[pathlib.Path, pathlib.Path]:
    candidates = sorted((REPO / 'rust/target/debug/deps').glob('zg_engine-*'))
    matches: list[tuple[pathlib.Path, pathlib.Path]] = []
    libs = sorted((REPO / 'rust/target/debug/build').glob('zvec-rust-sys-*/out/zvec-prebuilt/libzvec_c_api.so'))
    if not libs:
        raise FileNotFoundError('compiled libzvec_c_api.so is missing; build before Gate B validation')
    for binary in candidates:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            continue
        lib = libs[-1].parent
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


def make_context(operation_id: str, operation_root: pathlib.Path, arm_role: str,
                 execution_order: list[str], binary: pathlib.Path, library_path: pathlib.Path,
                 workspace_root: pathlib.Path, source_root: pathlib.Path,
                 quota_proof: dict[str, Any]) -> dict[str, Any]:
    arm_name = 'pair-first' if arm_role == execution_order[0] else 'pair-second'
    index_root = workspace_root / 'index'
    report_path = workspace_root / 'gate-b-report.json'
    temporary_directory = workspace_root / 'tmp'
    context: dict[str, Any] = {
        'schema_version': 1,
        'approval_status': 'approved',
        'mode': containment.CONTENTION_MODE,
        'policy_sha256': hashlib.sha256(containment.POLICY_PATH.read_bytes()).hexdigest(),
        'resource_basis': 'one quota-contained matched Gate B pair with preserved 18.219 GB disk reserve and enforced cgroup memory/swap/cpu/pids ceilings',
        'purpose': containment.M4_RUST_LIFECYCLE_PURPOSE,
        'operation_id': operation_id,
        'operation_root': str(operation_root),
        'approval_reference': CONTRACT['approval_reference'],
        'execution_authorization_reference': CONTRACT['execution_authorization_reference'],
        'risk_acceptance_reference': CONTRACT['risk_acceptance_reference'],
        'workload': CONTRACT['workload'],
        'reader_query_interval_ms': CONTRACT['workload']['reader_query_interval_ms'],
        'reader_query_max_samples_per_pass': CONTRACT['workload']['reader_query_max_samples_per_pass'],
        'reader_query_queue_capacity': CONTRACT['workload']['reader_query_queue_capacity'],
        'zvec_native_memory_limit_bytes': CONTRACT['workload']['zvec_native_memory_limit_bytes'],
        'arm_roles': CONTRACT['arm_roles'],
        'arm_role': arm_role,
        'execution_order': execution_order,
        'resource_root': CONTRACT['resource_root'],
        'workspace_root': str(workspace_root),
        'source_root': str(source_root),
        'index_root': str(index_root),
        'report_path': str(report_path),
        'temporary_directory': str(temporary_directory),
        'binary_path': str(binary),
        'binary_name': binary.name,
        'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'approved_binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'library_path': str(library_path),
        'io_pressure_mode': CONTRACT['io_pressure_mode'],
        'workspace_quota_proof': quota_proof,
        **CONTRACT['resource_controls'],
    }
    context['arm_name'] = arm_name
    return context


def build_environment(context: dict[str, Any]) -> dict[str, str]:
    return containment.rust_lifecycle_expected_environment(context)


def test_command(context: dict[str, Any]) -> list[str]:
    return [context['binary_path'], '--exact', TEST_NAME, '--ignored', '--nocapture']


def require_pair_workspace_headroom(quota: dict[str, Any], additional_bytes: int) -> None:
    live = quota['live_headroom']
    required = live['required_free_bytes'] + additional_bytes
    if live['available_bytes'] < required:
        raise RuntimeError(
            f'insufficient qgroup headroom for the next workspace allocation: '
            f'available={live["available_bytes"]}, additional={additional_bytes}, '
            f'required_free={live["required_free_bytes"]}, total_required={required}')


def preflight(operation_id: str) -> dict[str, Any]:
    entries, digest = frozen_manifest()
    binary, library = find_test_binary()
    resource_root = pathlib.Path(CONTRACT['resource_root'])
    controls = CONTRACT['resource_controls']
    quota = containment.verify_workspace_quota(resource_root, CONTRACT['workspace_quota'])
    require_pair_workspace_headroom(quota, controls['workspace_max_allocated_bytes'])
    if SAMPLE_INTERVAL_MS * 2 > controls['max_monitor_gap_seconds'] * 1000:
        raise ValueError('observer sample interval leaves insufficient margin under max monitor gap')
    disk_free = shutil.disk_usage(resource_root).free
    required_free = controls['disk_reserve_bytes'] + 2 * controls['workspace_max_allocated_bytes']
    if disk_free < required_free:
        raise RuntimeError(f'disk reserve/capacity gate: free={disk_free}, required={required_free}; no launch')
    operation_root = resource_root / operation_id
    order = (['native', 'm4'] if hashlib.sha256(operation_id.encode()).digest()[0] & 1
             else ['m4', 'native'])
    contexts = []
    for role in order:
        arm_name = 'pair-first' if role == order[0] else 'pair-second'
        arm_root = operation_root / arm_name / 'workspace'
        source_root = arm_root / 'source'
        context = make_context(operation_id, operation_root, role, order, binary, library,
                               arm_root, source_root, quota)
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
                    CONTRACT['workload']['reader_query_interval_ms']) or
                env['M4_GATE_B_READER_QUERY_MAX_SAMPLES'] != str(
                    CONTRACT['workload']['reader_query_max_samples_per_pass']) or
                env['M4_GATE_B_READER_QUERY_QUEUE_CAPACITY'] != str(
                    CONTRACT['workload']['reader_query_queue_capacity']) or
                env['ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES'] != str(
                    CONTRACT['workload']['zvec_native_memory_limit_bytes']) or
                pathlib.Path(env['M4_GATE_B_WORKSPACE']) != source_root):
            raise ValueError('Gate B command/environment binding failed')
        contexts.append({
            'role': role, 'arm_name': arm_name, 'command': command,
            'receipt_dir': str(receipt_dir), 'workspace_root': str(arm_root),
            'binary_sha256': context['binary_sha256'],
            'policy_context_sha256': containment.contention_context_digest(context),
        })
    return {
        'operation_id': operation_id,
        'operation_root': str(operation_root),
        'corpus_commit': CONTRACT['workload']['source_commit'],
        'corpus_file_count': len(entries), 'corpus_bytes': sum(item[2] for item in entries),
        'corpus_manifest_sha256': digest,
        'execution_order': order,
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
    commit = CONTRACT['workload']['source_commit']
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
    if total != CONTRACT['workload']['source_bytes']:
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


def run_pair(operation_id: str) -> dict[str, Any]:
    plan = preflight(operation_id)
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
        quota_safety_stop = 'insufficient live Btrfs qgroup headroom' in str(error)
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
            'status': 'OPERATION_BLOCKED' if quota_safety_stop else 'OPERATION_ERROR',
            'failure_class': 'SAFETY_STOP' if quota_safety_stop else 'IMPLEMENTATION_OR_OPERATION_ERROR',
            'operation_outcome': 'SAFETY_STOP' if quota_safety_stop else 'OPERATION_ERROR',
            'benchmark_started': any(arm.get('runner', {}).get('benchmark_started') for arm in arm_results),
            'error': error_text, 'arms': arm_results,
            'retry': 'do not automatically retry after a safety stop; any future operation requires restored live headroom and a fresh ID',
        }
        (operation_root / 'pair-result.json').write_text(json.dumps(failure, indent=2) + '\n')
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
    protocol_equal = all(native.get(key) == m4.get(key) for key in protocol_fields)
    reasons = [] if protocol_equal else ['offered_load_protocol_mismatch']
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
        binary, library = find_test_binary()
        context = make_context(operation_id, operation_root, role, plan['execution_order'],
                               binary, library, workspace_root, source_root, quota)
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
        arm_results.append({'role': role, 'arm_name': arm_name, 'runner': result})
        if not arm_result_allows_pair_continuation(result):
            pair = {'operation_id': operation_id, 'status': 'PAIR_STOPPED',
                    'stop_reason': f'{arm_name} did not complete with verified hard safety and cleanup; no retry',
                    'arms': arm_results}
            (operation_root / 'pair-result.json').write_text(json.dumps(pair, indent=2) + '\n')
            return pair
        report_path = pathlib.Path(context['report_path'])
        report = json.loads(report_path.read_text())
        report['query_filter'] = CONTRACT['workload']['query_filter']
        report['query_route'] = 'FTS'
        arm_results[-1]['workload_report'] = report
        # Capture index footprint, then release the entire generated workspace so the
        # next matched arm can retain the qgroup minimum-free reserve.
        index_root = pathlib.Path(context['index_root'])
        arm_results[-1]['index_apparent_bytes_before_cleanup'] = sum(
            p.stat().st_size for p in index_root.rglob('*') if p.is_file())
        shutil.rmtree(workspace_root)
        if workspace_root.exists():
            raise RuntimeError('operation-owned workspace cleanup failed')

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
        'corpus': {'commit': CONTRACT['workload']['source_commit'],
                   'manifest_sha256': plan['corpus_manifest_sha256'],
                   'file_count': plan['corpus_file_count'], 'bytes': plan['corpus_bytes']},
        'execution_order': plan['execution_order'], 'parity': parity, 'arms': arm_results,
        'limitations': ['one repository snapshot', 'one pair', 'no cgroup io.stat/io.max',
                        'host I/O pressure is observational', 'fixed deterministic embedding model'],
    }
    (operation_root / 'pair-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true',
                        help='validate corpus, binary, quota, policy bindings and commands without creating files or launching')
    parser.add_argument('--operation-id', help='fresh unique operation ID; required for launch')
    args = parser.parse_args()
    if args.validate_only:
        operation_id = args.operation_id or (
            'm4-rust-lifecycle-pair-validate-' + uuid.uuid4().hex[:12])
        if not operation_id.startswith('m4-rust-lifecycle-pair-'):
            parser.error('operation ID must use the m4-rust-lifecycle-pair- prefix')
        plan = preflight(operation_id)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    if not args.operation_id:
        parser.error('--operation-id is required for launch')
    result = run_pair(args.operation_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
