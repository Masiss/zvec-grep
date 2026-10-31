#!/usr/bin/env python3
"""One policy-bound qgroup reclamation diagnostic; never launches a benchmark."""
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
CONTRACT = containment.BENCHMARK_POLICY['qgroup_reclaim_diagnostic']
QUOTA = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['workspace_quota']
ROOT = pathlib.Path(CONTRACT['resource_root'])
OPERATION = ROOT / CONTRACT['operation_id']
WORKSPACE = OPERATION / 'diagnostic/workspace'
RECEIPT = OPERATION / 'diagnostic/containment'
SCRIPT = REPO / CONTRACT['fixture_script']


def make_context(proof: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    return {'schema_version': 1, 'approval_status': 'approved',
            'mode': containment.CONTENTION_MODE,
            'purpose': CONTRACT['purpose'], 'operation_id': CONTRACT['operation_id'],
            'operation_root': str(OPERATION), 'resource_root': str(ROOT),
            'workspace_root': str(WORKSPACE), 'binary_path': str(SCRIPT),
            'binary_sha256': digest, 'approved_binary_sha256': digest,
            'policy_sha256': hashlib.sha256(containment.POLICY_PATH.read_bytes()).hexdigest(),
            'resource_basis': 'one independent bounded qgroup diagnostic; unchanged lifecycle hard guards',
            'approval_reference': CONTRACT['approval_reference'],
            'execution_authorization_reference': CONTRACT['execution_authorization_reference'],
            'risk_acceptance_reference': CONTRACT['risk_acceptance_reference'],
            'workspace_quota_proof': proof, **CONTRACT['resource_controls']}


def preflight() -> dict[str, Any]:
    if OPERATION.exists():
        raise FileExistsError('diagnostic operation ID already exists; do not retry')
    if not SCRIPT.is_file() or shutil.which('systemd-run') is None or shutil.which('systemctl') is None:
        raise ValueError('diagnostic script or systemd containment unavailable')
    proof = containment.verify_workspace_quota(ROOT, QUOTA)
    live = proof['live_headroom']
    required = live['required_free_bytes'] + CONTRACT['resource_controls']['workspace_max_allocated_bytes']
    if live['available_bytes'] < required:
        raise RuntimeError('insufficient qgroup headroom for diagnostic allocation; no launch')
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


def analyze(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {row.get('event'): row for row in events}
    required = ['create_before', 'create_after', 'write_before', 'write_after',
                'close_before', 'close_after', 'unlink_before', 'unlink_after',
                'observation_start', 'observation_end']
    times = [row.get('workspace_quota_sample_monotonic_ns') for row in rows]
    valid_times = all(type(t) is int for t in times)
    spaced = valid_times and all(isinstance(a, int) and isinstance(b, int) and b - a >= 1_000_000_000
                                 for a, b in zip(times, times[1:]))
    valid_quota = bool(rows) and all((row.get('workspace_quota') or {}).get('status') == 'PASS' for row in rows)
    ordered_events = all(name in by_name and type(by_name[name].get('monotonic_ns')) is int for name in required)
    if ordered_events:
        ticks = [by_name[name]['monotonic_ns'] for name in required]
        ordered_events = ticks == sorted(ticks)
    before = []
    after = []
    if ordered_events and valid_times:
        unlink = by_name['unlink_after']['monotonic_ns']
        closed = by_name['close_after']['monotonic_ns']
        before = [r for r in rows if closed <= r['workspace_quota_sample_monotonic_ns'] < unlink]
        after = [r for r in rows if r['workspace_quota_sample_monotonic_ns'] >= unlink]
    complete = bool(ordered_events and spaced and valid_quota and before and after)
    return {'classification': 'INCONCLUSIVE', 'observation_complete': complete,
            'ordered_events': ordered_events, 'samples_spaced_at_least_one_second': spaced,
            'all_quota_samples_valid': valid_quota, 'sample_count': len(rows),
            'before_unlink_samples': len(before), 'after_unlink_samples': len(after),
            'last_before': before[-1]['workspace_quota'] if before else None,
            'after_unlink': [{'monotonic_ns': r['workspace_quota_sample_monotonic_ns'],
                              'quota': r['workspace_quota']} for r in after],
            'limitation': 'shared qgroup; observations alone do not establish the original stop\'s cause'}


def run() -> dict[str, Any]:
    plan = preflight()
    # Single fixed operation ID; never remove/reuse the operation root.
    OPERATION.mkdir(mode=0o700)
    (OPERATION / 'diagnostic').mkdir(mode=0o700)
    WORKSPACE.mkdir(mode=0o700)
    result = containment.run_arm(plan['command'], RECEIPT, ROOT, WORKSPACE, WORKSPACE,
                                 CONTRACT['resource_controls']['runtime_limit_seconds'],
                                 sample_interval_ms=CONTRACT['sample_interval_ms'],
                                 budget=containment.contention_budget(plan['context']),
                                 env=None, enforce_host_gates=True, policy_context=plan['context'])
    events = []
    stdout = RECEIPT / 'stdout.log'
    if stdout.exists():
        for line in stdout.read_text().splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and 'event' in value:
                events.append(value)
    runtime = RECEIPT / 'runtime.jsonl'
    rows = [json.loads(line) for line in runtime.read_text().splitlines()] if runtime.exists() else []
    analysis = analyze(rows, events)
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
    if (WORKSPACE / 'fixture.bin').exists():
        analysis['classification'] = 'INCONCLUSIVE'
        analysis['cleanup_failure'] = 'fixture remains; preserve for investigation'
    summary = {'operation_id': CONTRACT['operation_id'], 'runner_status': result.get('status'),
               'safety_outcome': result.get('safety_outcome'), 'unit_cleanup': result.get('unit_cleanup'),
               'events': events, 'analysis': analysis}
    (OPERATION / 'diagnostic-result.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    result = preflight() if args.validate_only else run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
