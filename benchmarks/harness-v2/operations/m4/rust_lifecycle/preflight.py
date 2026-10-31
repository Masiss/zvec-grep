"""Pure preflight/safety predicates for the Rust lifecycle pair."""
from __future__ import annotations

import hashlib
import pathlib
import shutil
from typing import Any

from . import config  # ty: ignore[unresolved-import]
from . import context as lifecycle_context  # ty: ignore[unresolved-import]


def is_qgroup_headroom_gate_failure(error: BaseException) -> bool:
    return "insufficient qgroup headroom for the next workspace allocation" in str(error)


def require_pair_workspace_headroom(quota: dict[str, Any], additional_bytes: int) -> None:
    live = quota["live_headroom"]
    required = live["required_free_bytes"] + additional_bytes
    if live["available_bytes"] < required:
        raise RuntimeError(
            "insufficient qgroup headroom for the next workspace allocation: "
            f"available={live['available_bytes']}, additional={additional_bytes}, "
            f"required_free={live['required_free_bytes']}, total_required={required}"
        )


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
    diagnostic = config.CONTRACT.get('native_query_stage_diagnostic')
    diagnostic_ids = (diagnostic.get('authorized_operation_ids')
                      if isinstance(diagnostic, dict) else [])
    if not isinstance(diagnostic_ids, list):
        diagnostic_ids = []
    if not native_only_diagnostic and operation_id in diagnostic_ids:
        raise ValueError('native-only diagnostic operation ID cannot launch a matched pair')
    if stage_a_interval_ms is not None:
        stage_a = config.CONTRACT.get('stage_a_rate_ladder')
        authorized_ids = stage_a.get('authorized_operation_ids') if isinstance(stage_a, dict) else None
        authorized_limit = stage_a.get('max_authorized_operations') if isinstance(stage_a, dict) else None
        intervals = stage_a.get('reader_query_intervals_ms') if isinstance(stage_a, dict) else None
        if (not isinstance(stage_a, dict) or stage_a.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_a_interval_ms not in intervals or
                stage_a.get('arm_roles') != config.CONTRACT['arm_roles'] or
                stage_a.get('arm_orders') != config.CONTRACT['arm_orders']):
            raise ValueError('operation ID or interval is outside the Stage A rate-ladder authorization')
    if stage_c_interval_ms is not None:
        stage_c = config.CONTRACT.get('stage_c_capacity_attribution')
        authorized_ids = stage_c.get('authorized_operation_ids') if isinstance(stage_c, dict) else None
        authorized_limit = stage_c.get('max_authorized_operations') if isinstance(stage_c, dict) else None
        intervals = stage_c.get('reader_query_intervals_ms') if isinstance(stage_c, dict) else None
        if (not isinstance(stage_c, dict) or stage_c.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_c_interval_ms not in intervals or
                stage_c.get('arm_roles') != config.CONTRACT['arm_roles'] or
                stage_c.get('arm_orders') != [['m4', 'native'], ['native', 'm4']]):
            raise ValueError('operation ID or interval is outside the Stage C capacity-attribution authorization')
    if stage_e2_interval_ms is not None:
        stage_e2 = config.CONTRACT.get('stage_e2_larger_corpus')
        authorized_ids = stage_e2.get('authorized_operation_ids') if isinstance(stage_e2, dict) else None
        authorized_limit = stage_e2.get('max_authorized_operations') if isinstance(stage_e2, dict) else None
        intervals = stage_e2.get('reader_query_intervals_ms') if isinstance(stage_e2, dict) else None
        if (not isinstance(stage_e2, dict) or stage_e2.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e2_interval_ms not in intervals or
                stage_e2.get('arm_roles') != config.CONTRACT['arm_roles'] or
                stage_e2.get('arm_orders') != [['m4', 'native']]):
            raise ValueError('operation ID or interval is outside the larger-corpus diagnostic authorization')
    if stage_e3_interval_ms is not None:
        stage_e3 = config.CONTRACT.get('stage_e3_camera_421')
        authorized_ids = stage_e3.get('authorized_operation_ids') if isinstance(stage_e3, dict) else None
        authorized_limit = stage_e3.get('max_authorized_operations') if isinstance(stage_e3, dict) else None
        intervals = stage_e3.get('reader_query_intervals_ms') if isinstance(stage_e3, dict) else None
        if (not isinstance(stage_e3, dict) or stage_e3.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e3_interval_ms not in intervals or
                stage_e3.get('arm_roles') != config.CONTRACT['arm_roles'] or
                stage_e3.get('arm_orders') != [['m4', 'native']]):
            raise ValueError('operation ID or interval is outside the camera 421 diagnostic authorization')
    if stage_e4_interval_ms is not None:
        stage_e4 = config.CONTRACT.get('stage_e4_camera_300')
        authorized_ids = stage_e4.get('authorized_operation_ids') if isinstance(stage_e4, dict) else None
        authorized_limit = stage_e4.get('max_authorized_operations') if isinstance(stage_e4, dict) else None
        intervals = stage_e4.get('reader_query_intervals_ms') if isinstance(stage_e4, dict) else None
        if (not isinstance(stage_e4, dict) or stage_e4.get('approval_status') != 'approved' or
                not isinstance(authorized_limit, int) or isinstance(authorized_limit, bool) or
                not isinstance(authorized_ids, list) or len(authorized_ids) != authorized_limit or
                len(set(authorized_ids)) != authorized_limit or operation_id not in authorized_ids or
                not isinstance(intervals, list) or stage_e4_interval_ms not in intervals or
                stage_e4.get('arm_roles') != config.CONTRACT['arm_roles'] or
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
    config.WORKLOAD = (config.CONTRACT['stage_e4_camera_300']['workload']
                if stage_e4_interval_ms is not None else
                config.CONTRACT['stage_e3_camera_421']['workload']
                if stage_e3_interval_ms is not None else
                config.CONTRACT['stage_e2_larger_corpus']['workload']
                if stage_e2_interval_ms is not None else config.CONTRACT['workload'])
    entries, digest = lifecycle_context.frozen_manifest()
    binary, library = lifecycle_context.find_test_binary(binary_override, library_override)
    artifacts = lifecycle_context.capture_pair_artifacts(
        binary, library, config.containment.file_sha256
    )
    if expected_binary_sha256 is not None and artifacts['binary_sha256'] != expected_binary_sha256:
        raise ValueError('selected binary SHA-256 differs from explicitly expected artifact')
    if expected_library_sha256 is not None and artifacts['library_sha256'] != expected_library_sha256:
        raise ValueError('selected native library SHA-256 differs from explicitly expected artifact')
    resource_root = pathlib.Path(config.CONTRACT['resource_root'])
    controls = config.CONTRACT['resource_controls']
    quota = config.containment.verify_workspace_quota(resource_root, config.CONTRACT['workspace_quota'])
    require_pair_workspace_headroom(quota, controls['workspace_max_allocated_bytes'])
    if controls['max_monitor_gap_seconds'] * 1000 < config.SAMPLE_INTERVAL_MS * 2:
        raise ValueError('observer sample interval leaves insufficient margin under max monitor gap')
    disk_free = shutil.disk_usage(resource_root).free
    required_free = controls['disk_reserve_bytes'] + 2 * controls['workspace_max_allocated_bytes']
    if disk_free < required_free:
        raise RuntimeError(f'disk reserve/capacity gate: free={disk_free}, required={required_free}; no launch')
    operation_root = resource_root / operation_id
    if operation_root.exists():
        raise ValueError('operation ID already has a workspace; reuse is refused')
    stage_c_orders = (config.CONTRACT['stage_c_capacity_attribution'].get('arm_orders')
                      if stage_c_interval_ms is not None else None)
    stage_e2_orders = (config.CONTRACT['stage_e2_larger_corpus'].get('arm_orders')
                       if stage_e2_interval_ms is not None else None)
    stage_e3_orders = (config.CONTRACT['stage_e3_camera_421'].get('arm_orders')
                       if stage_e3_interval_ms is not None else None)
    stage_e4_orders = (config.CONTRACT['stage_e4_camera_300'].get('arm_orders')
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
        context = lifecycle_context.make_context(operation_id, operation_root, role, order, artifacts,
                               arm_root, source_root, quota, operation_mode,
                               stage_e4_interval_ms if stage_e4_interval_ms is not None else
                               stage_e4_interval_ms if stage_e4_interval_ms is not None else
            stage_e3_interval_ms if stage_e3_interval_ms is not None else
                               stage_e2_interval_ms if stage_e2_interval_ms is not None else
                               stage_c_interval_ms if stage_c_interval_ms is not None else stage_a_interval_ms)
        config.containment.validate_contention_context(context)
        env = lifecycle_context.build_environment(context)
        receipt_dir = operation_root / arm_name / 'containment'
        config.containment.validate_watchdog_operation_binding({
            'policy_mode': config.containment.CONTENTION_MODE,
            'policy_context': context,
            'arm_name': arm_name,
            'watchdog_output': str(receipt_dir),
            'resource_root': config.CONTRACT['resource_root'],
            'enforce_host_gates': True,
            'budget': config.containment.contention_budget(context),
            'runtime_limit_seconds': context['runtime_limit_seconds'],
            'policy_context_digest': config.containment.contention_context_digest(context),
        })
        command = lifecycle_context.test_command(context)
        if (command != [str(binary), '--exact', config.TEST_NAME, '--ignored', '--nocapture'] or
                env['M4_GATE_B_ARM'] != role or
                env['M4_GATE_B_READER_QUERY_INTERVAL_MS'] != str(
                    context['reader_query_interval_ms']) or
                env['M4_GATE_B_READER_QUERY_MAX_SAMPLES'] != str(
                    config.WORKLOAD['reader_query_max_samples_per_pass']) or
                env['M4_GATE_B_READER_QUERY_QUEUE_CAPACITY'] != str(
                    config.WORKLOAD['reader_query_queue_capacity']) or
                env['M4_GATE_B_QUERY_FILTER'] != config.WORKLOAD['query_filter'] or
                env['M4_GATE_B_CORPUS_EXTENSION'] != config.WORKLOAD.get('corpus_extension', '.rs') or
                env['ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES'] != str(
                    config.WORKLOAD['zvec_native_memory_limit_bytes']) or
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
            'policy_context_sha256': config.containment.contention_context_digest(context),
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
            else config.WORKLOAD['reader_query_interval_ms']),
        'operation_root': str(operation_root),
        'corpus_commit': config.WORKLOAD['source_commit'],
        'corpus_file_count': len(entries), 'corpus_bytes': sum(item[2] for item in entries),
        'corpus_manifest_sha256': digest,
        'execution_order': order,
        'artifacts': artifacts,
        'workspace_quota_proof': quota,
        'resource_controls': config.CONTRACT['resource_controls'],
        'observer': {'sample_interval_ms': config.SAMPLE_INTERVAL_MS,
                     'max_monitor_gap_seconds': controls['max_monitor_gap_seconds']},
        'disk_capacity': {'free_bytes': disk_free, 'required_bytes': required_free,
                          'headroom_bytes': disk_free - required_free},
        'arms': contexts,
        'benchmark_started': False,
    }
