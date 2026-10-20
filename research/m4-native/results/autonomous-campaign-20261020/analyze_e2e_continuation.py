#!/usr/bin/env python3
"""Offline reconciliation of rehearsal PASS and slot2 capacity refusal."""
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def read_json(path):
    """Report corrupt/missing evidence explicitly; never substitute or retry."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot analyze evidence {path}: {exc}') from exc


def analyze():
    rehearsal = HERE / 'm4-contention-rehearsal-e2e-1'
    result = read_json(rehearsal / 'rehearsal-result.json')
    summary = read_json(rehearsal / 'aa-first/containment/watchdog-summary.json')
    receipt = read_json(rehearsal / 'aa-first/containment/receipt.json')
    prerequisite = HERE / 'pair-2-execution/after-import-correction/prerequisites.json'
    pre = read_json(prerequisite)
    decision = pre['capacity_decision']
    closure = read_json(HERE / 'pair-2-execution/post-crash-reconciliation/reconciliation.json')
    ledger = read_json(HERE / 'campaign-ledger.json')
    before = read_json(HERE / 'ledger-before-e2e-continuation.json')
    assert result['status'] == 'PASS' and result['synthetic_only'] and not result['benchmark_evidence']
    assert summary['measurement_quality'] == 'COMPLETE' and summary['abort'] is None
    assert receipt['benchmark_started'] and receipt['unit_cleanup']['verified']
    assert not decision['continuation_allowed'] and len(decision['blocking_failures']) == 1
    failure = decision['blocking_failures'][0]
    assert failure['gate_id'] == 'M4-CA-MEM-CAPACITY' and failure['observed_bytes'] < failure['required_bytes']
    assert closure['native_command_invoked'] is False and closure['no_unresolved_owned_containment']
    assert ledger['total_attempts_consumed'] == 3 and ledger['remaining_budget_slots'] == 1
    assert ledger['slots'][0] == before['slots'][0] and ledger['replacement_attempts'] == before['replacement_attempts']
    plan = read_json(HERE / 'resource-plan.approved-pair-2.json')
    assert not pathlib.Path(plan['operation_root']).exists()
    assert not (HERE / 'pair-2-execution/after-import-correction/launch-command.json').exists()
    paths = [rehearsal / 'rehearsal-result.json', rehearsal / 'aa-first/containment/watchdog-summary.json',
             rehearsal / 'aa-first/containment/receipt.json', prerequisite, HERE / 'campaign-ledger.json',
             HERE / 'ledger-before-e2e-continuation.json',
             HERE / 'pair-2-execution/post-crash-reconciliation/reconciliation.json']
    return {
        'classification': 'PARTIAL / OPERATION_BLOCKED; native writer-tail objective NOT fulfilled',
        'rehearsal': {
            'status': 'PASS', 'benchmark_evidence': False, 'runtime_samples': summary['samples'],
            'strict_contention_watchdog': True, 'caps': result['actual_caps'],
            'proc_io_worker': result['proc_io_worker'],
            'memory_peak_bytes': summary['peak_cgroup_memory_peak_bytes_observed'],
            'swap_peak_bytes': summary['peak_cgroup_swap_peak_bytes_observed'],
            'workspace_allocated_peak_bytes': summary['peak_sampled_workspace_allocated_st_blocks_bytes_gross'],
            'generic_benchmark_started_field': 'synthetic fixed Python command only, NOT native execution'},
        'native_slot2': {
            'outcome': 'NO_LAUNCH_FORMAL_MEMORY_CAPACITY_REFUSAL',
            'timestamp_utc': decision['timestamp_utc'], 'gate_id': failure['gate_id'],
            'observed_bytes': failure['observed_bytes'], 'required_bytes': failure['required_bytes'],
            'shortfall_bytes': failure['required_bytes'] - failure['observed_bytes'],
            'native_command_invoked': False, 'native_arms_launched': 0, 'completed_native_pairs': 0,
            'baseline_capture_started': False, 'writer_samples': 0,
            'writer_p50_ms': None, 'writer_p95_ms': None, 'writer_p99_ms': None,
            'causal_host_crash_attribution': 'UNKNOWN; no native workload launched, job exit/log not retained after crash'},
        'accounting': {
            'consumed': 3, 'maximum': 4, 'remaining_conditional': 1, 'rehearsal_native_slots': 0,
            'original1_and_R1_unchanged': True, 'final_slot_eligible_now': False},
        'decision': 'KEEP control/parser correction and fixed real strict rehearsal; DEFER latency optimization. Preserve formal refusal, no retry/current-host replacement decision/automatic last-slot substitution.',
        'known_limits': [
            'tiny synthetic not native initialization,20-cycle pressure,reader/reopen/durability test or performance evidence',
            'real per-process physical IO aggregation may lose exited workers; char/cancelled counters verified in parser/worker dump, not new aggregate performance counters',
            'no hard IO cap or hard observer CPU/RAM budget; sampled workspace bounds unchanged',
            'post-crash host snapshot delayed, cannot reconstruct crash cause or original exit code'],
        'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}


if __name__ == '__main__':
    target = HERE / 'e2e-continuation-analysis.json'
    result = analyze()
    if target.exists():
        assert read_json(target) == result, 'do not overwrite changed analysis'
    else:
        target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
