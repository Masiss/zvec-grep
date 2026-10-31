#!/usr/bin/env python3
"""One contained idle observer diagnostic; never executes an index benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
from typing import Any

import m4_containment_runner as containment

REPO = pathlib.Path(__file__).resolve().parents[3]
CONTRACT = containment.BENCHMARK_POLICY['observer_overhead_diagnostic']
QUOTA = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['workspace_quota']
ROOT = pathlib.Path(CONTRACT['resource_root'])
OPERATION = ROOT / CONTRACT['operation_id']
WORKSPACE = OPERATION / 'diagnostic/workspace'
RECEIPT = OPERATION / 'diagnostic/containment'
SCRIPT = REPO / CONTRACT['fixture_script']
BINARY = REPO / CONTRACT['sample_binary']
LIBRARY = REPO / CONTRACT['sample_library']


def make_context(proof: dict[str, Any]) -> dict[str, Any]:
    digest = containment.file_sha256(SCRIPT)
    return {'schema_version': 1, 'approval_status': 'approved',
            'mode': containment.CONTENTION_MODE, 'purpose': CONTRACT['purpose'],
            'operation_id': CONTRACT['operation_id'], 'operation_root': str(OPERATION),
            'resource_root': str(ROOT), 'workspace_root': str(WORKSPACE),
            'binary_path': str(SCRIPT), 'binary_sha256': digest, 'approved_binary_sha256': digest,
            'sample_binary_path': str(BINARY), 'sample_binary_sha256': containment.file_sha256(BINARY),
            'sample_library_path': str(LIBRARY), 'sample_library_sha256': containment.file_sha256(LIBRARY),
            'policy_sha256': hashlib.sha256(containment.POLICY_PATH.read_bytes()).hexdigest(),
            'resource_basis': 'one independent idle observer diagnostic with unchanged lifecycle hard guards',
            'approval_reference': CONTRACT['approval_reference'],
            'execution_authorization_reference': CONTRACT['execution_authorization_reference'],
            'risk_acceptance_reference': CONTRACT['risk_acceptance_reference'],
            'workspace_quota_proof': proof, **CONTRACT['resource_controls']}


def preflight() -> dict[str, Any]:
    if OPERATION.exists():
        raise FileExistsError('diagnostic operation ID already exists; do not retry')
    if any(not path.is_file() for path in (SCRIPT, BINARY, LIBRARY)):
        raise ValueError('required diagnostic or sampled artifact is missing')
    if shutil.which('systemd-run') is None or shutil.which('systemctl') is None:
        raise ValueError('systemd containment unavailable')
    proof = containment.verify_workspace_quota(ROOT, QUOTA)
    live = proof['live_headroom']
    required = live['required_free_bytes'] + CONTRACT['resource_controls']['workspace_max_allocated_bytes']
    if live['available_bytes'] < required:
        raise RuntimeError('insufficient qgroup headroom for observer diagnostic; no launch')
    context = make_context(proof)
    containment.validate_contention_context(context)
    state = containment.host_snapshot(ROOT)
    decision = containment.contention_decision(state, context, 'baseline')
    if not decision['continuation_allowed']:
        raise RuntimeError(f'host resource gate refused diagnostic: {decision["blocking_failures"]}')
    budget = containment.contention_budget(context)
    containment.validate_watchdog_operation_binding({
        'policy_mode': containment.CONTENTION_MODE, 'policy_context': context,
        'arm_name': 'diagnostic', 'watchdog_output': str(RECEIPT), 'workspace_root': str(WORKSPACE),
        'resource_root': str(ROOT), 'sample_interval_ms': CONTRACT['sample_interval_ms'],
        'enforce_host_gates': True, 'budget': budget,
        'runtime_limit_seconds': CONTRACT['resource_controls']['runtime_limit_seconds'],
        'policy_context_digest': containment.contention_context_digest(context),
    })
    return {'context': context, 'host_before': state, 'host_decision': decision,
            'command': [sys.executable, '-I', str(SCRIPT), str(WORKSPACE)],
            'quota_required_bytes': required, 'diagnostic_started': False}


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def nonnegative(value: Any) -> int:
        if type(value) is not int or value < 0:
            raise ValueError('missing or invalid timing')
        return value

    measurements: list[dict[str, int]] = []
    for row in rows:
        cost = row.get('policy_decision_cost') or {}
        try:
            wall, cpu = nonnegative(cost.get('wall_ns')), nonnegative(cost.get('cpu_ns'))
            loop_wall = nonnegative(row.get('watchdog_loop_work_ns'))
            loop_cpu = nonnegative(row.get('watchdog_loop_cpu_ns'))
        except (ValueError, AttributeError):
            return {'classification': 'INCONCLUSIVE', 'reason': 'missing or inconsistent same-loop timing'}
        if wall > loop_wall or cpu > loop_cpu:
            return {'classification': 'INCONCLUSIVE', 'reason': 'missing or inconsistent same-loop timing'}
        measurements.append({'policy_wall_ns': wall, 'policy_cpu_ns': cpu,
                             'rest_wall_ns': loop_wall - wall, 'rest_cpu_ns': loop_cpu - cpu})
    if not measurements:
        return {'classification': 'INCONCLUSIVE', 'reason': 'no complete watchdog timing samples'}
    return {'classification': 'DESCRIPTIVE_ONLY', 'samples': len(measurements),
            'policy_wall_ns_total': sum(row['policy_wall_ns'] for row in measurements),
            'policy_cpu_ns_total': sum(row['policy_cpu_ns'] for row in measurements),
            'rest_wall_ns_total': sum(row['rest_wall_ns'] for row in measurements),
            'rest_cpu_ns_total': sum(row['rest_cpu_ns'] for row in measurements),
            'policy_wall_ns_max': max(row['policy_wall_ns'] for row in measurements),
            'policy_cpu_ns_max': max(row['policy_cpu_ns'] for row in measurements),
            'limitation': 'idle diagnostic on a shared host; no inference about offers or pair performance'}


def run() -> dict[str, Any]:
    plan = preflight()
    OPERATION.mkdir(mode=0o700)
    (OPERATION / 'diagnostic').mkdir(mode=0o700)
    WORKSPACE.mkdir(mode=0o700)
    result = containment.run_arm(plan['command'], RECEIPT, ROOT, WORKSPACE, WORKSPACE,
                                 CONTRACT['resource_controls']['runtime_limit_seconds'],
                                 sample_interval_ms=CONTRACT['sample_interval_ms'],
                                 budget=containment.contention_budget(plan['context']),
                                 env=None, enforce_host_gates=True, policy_context=plan['context'])
    runtime = RECEIPT / 'runtime.jsonl'
    rows = [json.loads(line) for line in runtime.read_text().splitlines()] if runtime.exists() else []
    stdout = RECEIPT / 'stdout.log'
    events = []
    if stdout.exists():
        for line in stdout.read_text().splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get('event') in ('start', 'end'):
                events.append(item)
    window_valid = (len(events) == 2 and [x.get('event') for x in events] == ['start', 'end'] and
                    all(type(x.get('monotonic_ns')) is int for x in events) and
                    8_000_000_000 <= events[1]['monotonic_ns'] - events[0]['monotonic_ns'] <= 20_000_000_000)
    row_timing_valid = all(type(row.get('watchdog_sample_start_monotonic_ns')) is int and
                           type(row.get('watchdog_loop_work_ns')) is int and
                           row['watchdog_sample_start_monotonic_ns'] >= 0 and
                           row['watchdog_loop_work_ns'] >= 0 for row in rows)
    if window_valid and row_timing_valid:
        start, end = (x['monotonic_ns'] for x in events)
        rows = [row for row in rows if start <= row['watchdog_sample_start_monotonic_ns'] and
                row['watchdog_sample_start_monotonic_ns'] + row['watchdog_loop_work_ns'] <= end]
    else:
        rows = []
    analysis = analyze(rows)
    analysis['fixed_window_valid'] = window_valid
    analysis['row_timing_valid'] = row_timing_valid
    cleanup = result.get('unit_cleanup') or result.get('controller_cleanup') or {}
    safe = (result.get('status') in {'PASS', 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY',
                                     'COMPLETED_WITH_WORKSPACE_WARNINGS'} and
            result.get('workload_returncode') == 0 and result.get('watchdog_abort') is None and
            result.get('safety_outcome') in {'SAFE_COMPLETE',
                'NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED'} and
            (result.get('watchdog_summary') or {}).get('hard_safety_telemetry_complete') is True and
            cleanup.get('verified') is True and cleanup.get('unit_collected') is True and
            (result.get('postflight_decision') or {}).get('continuation_allowed') is True)
    if not safe:
        analysis['classification'] = 'INCONCLUSIVE'
    summary = {'operation_id': CONTRACT['operation_id'], 'runner_status': result.get('status'),
               'safety_outcome': result.get('safety_outcome'), 'unit_cleanup': cleanup,
               'events': events, 'analysis': analysis}
    (OPERATION / 'diagnostic-result.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(preflight() if args.validate_only else run(), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
