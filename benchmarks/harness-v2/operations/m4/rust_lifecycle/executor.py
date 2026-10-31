"""Pure pair-continuation rules for the Rust lifecycle executor."""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tarfile
from io import BytesIO
from typing import Any

from . import config, receipt, verify  # ty: ignore[unresolved-import]
from . import context as lifecycle_context  # ty: ignore[unresolved-import]
from . import preflight as preflight_module  # ty: ignore[unresolved-import]
from .preflight import (  # ty: ignore[unresolved-import]
    is_qgroup_headroom_gate_failure, require_pair_workspace_headroom,
)


def arm_result_allows_pair_continuation(result: dict[str, Any]) -> bool:
    """Continue after clean workload completion with accounting caveats, never after a safety failure."""
    if result.get("status") not in {
        "PASS",
        "COMPLETED_WITH_WORKSPACE_UNCERTAINTY",
        "COMPLETED_WITH_WORKSPACE_WARNINGS",
    }:
        return False
    cleanup = result.get("unit_cleanup") or result.get("controller_cleanup") or {}
    summary = result.get("watchdog_summary") or {}
    postflight = result.get("postflight_decision") or {}
    return (
        result.get("workload_returncode") == 0
        and result.get("watchdog_abort") is None
        and result.get("safety_outcome")
        in {
            "SAFE_COMPLETE",
            "NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED",
        }
        and summary.get("hard_safety_telemetry_complete") is True
        and cleanup.get("verified") is True
        and cleanup.get("unit_collected") is True
        and postflight.get("continuation_allowed") is True
    )


def extract_corpus(destination: pathlib.Path, entries: list[tuple[str, str, int]]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    commit = config.WORKLOAD['source_commit']
    paths = [entry[0] for entry in entries]
    archive = lifecycle_context.git('archive', '--format=tar', commit, '--', *paths)
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
            if path.suffix not in config.CODE_EXTENSIONS:
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
    if total != config.WORKLOAD['source_bytes']:
        raise ValueError(f'materialized corpus byte count mismatch: {total}')


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
    plan = preflight_module.preflight(operation_id, binary_override, expected_binary_sha256,
                      library_override, expected_library_sha256, native_only_diagnostic,
                      stage_a_interval_ms, stage_c_interval_ms, stage_e2_interval_ms,
                      stage_e3_interval_ms, stage_e4_interval_ms)
    operation_root = pathlib.Path(plan['operation_root'])
    if operation_root.exists():
        raise FileExistsError(f'fresh operation root already exists: {operation_root}')
    entries, _ = lifecycle_context.frozen_manifest()
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
                    'receipt_path': str(receipt_path.relative_to(config.REPO)),
                    'runner': json.loads(receipt_path.read_text()),
                })
        failure = receipt.failure_receipt(
            operation_id, plan, arm_results, error_text, quota_safety_stop
        )
        filename = receipt.failure_receipt_filename(plan)
        (operation_root / filename).write_text(json.dumps(failure, indent=2) + '\n')
        raise


def _run_pair_arms(operation_id: str, plan: dict[str, Any], entries: list[tuple[str, str, int]],
                   operation_root: pathlib.Path, arm_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in plan['arms']:
        arm_name, role = item['arm_name'], item['role']
        arm_root = operation_root / arm_name
        workspace_root = arm_root / 'workspace'
        resource_root = pathlib.Path(config.CONTRACT['resource_root'])
        quota_before = config.containment.verify_workspace_quota(
            resource_root, config.CONTRACT['workspace_quota'])
        require_pair_workspace_headroom(
            quota_before, config.CONTRACT['resource_controls']['workspace_max_allocated_bytes'])
        workspace_root.mkdir(parents=True, mode=0o700)
        source_root = workspace_root / 'source'
        extract_corpus(source_root, entries)
        temp_root = workspace_root / 'tmp'
        temp_root.mkdir(mode=0o700)
        # Recheck live headroom after corpus materialization and reserve the remaining
        # workspace allowance plus the policy's minimum free space before launch.
        quota = config.containment.verify_workspace_quota(resource_root, config.CONTRACT['workspace_quota'])
        materialized_bytes = max(
            0, quota_before['live_headroom']['available_bytes'] -
            quota['live_headroom']['available_bytes'])
        remaining_workspace = max(
            0, config.CONTRACT['resource_controls']['workspace_max_allocated_bytes'] - materialized_bytes)
        require_pair_workspace_headroom(quota, remaining_workspace)
        lifecycle_context.verify_pair_artifacts(plan['artifacts'], config.containment.file_sha256)
        context = lifecycle_context.make_context(operation_id, operation_root, role, plan['execution_order'],
                               plan['artifacts'], workspace_root, source_root, quota,
                               plan['operation_mode'],
                               plan.get('reader_query_interval_ms'))
        config.containment.validate_contention_context(context)
        receipt_dir = operation_root / arm_name / 'containment'
        result = config.containment.run_arm(
            lifecycle_context.test_command(context), receipt_dir,
            pathlib.Path(config.CONTRACT['resource_root']), workspace_root, workspace_root,
            config.CONTRACT['resource_controls']['runtime_limit_seconds'],
            sample_interval_ms=config.SAMPLE_INTERVAL_MS,
            budget=config.containment.contention_budget(context),
            env=lifecycle_context.build_environment(context), enforce_host_gates=True,
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
        lifecycle_context.verify_pair_artifacts(plan['artifacts'], config.containment.file_sha256)
        report_path = pathlib.Path(context['report_path'])
        try:
            report = json.loads(report_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f'workload report is unreadable: {report_path}') from error
        report['query_filter'] = config.WORKLOAD['query_filter']
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
        post_cleanup_quota = config.containment.verify_workspace_quota(
            pathlib.Path(config.CONTRACT['resource_root']), config.CONTRACT['workspace_quota'])
        result = receipt.diagnostic_result(
            operation_id, plan, arm, post_cleanup_quota, config.WORKLOAD['source_commit']
        )
        (operation_root / 'native-query-stage-result.json').write_text(
            json.dumps(result, indent=2) + '\n')
        return result

    query_latency_gate = verify.query_latency_pair_gate(arm_results)
    result = receipt.pair_result(
        operation_id, plan, arm_results, query_latency_gate, config.WORKLOAD['source_commit']
    )
    (operation_root / 'pair-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
