#!/usr/bin/env python3
"""Offline final-slot analysis. No new sample, guard evaluation or workload launch."""
import csv
import hashlib
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid evidence {path}: {exc}') from exc


def analyze():
    context = read_json(HERE / 'resource-plan.approved-pair-3.json')
    pair = pathlib.Path(context['operation_root'])
    contained = pair / 'aa-first/containment'
    receipt = read_json(contained / 'receipt.json')
    watchdog = read_json(contained / 'watchdog-summary.json')
    abort = read_json(contained / 'watchdog-abort.json')
    cleanup = read_json(HERE / 'pair-3-execution/post-stop-verification.json')
    before = read_json(HERE / 'ledger-before-final-slot.json')
    ledger = read_json(HERE / 'campaign-ledger.json')
    try:
        rows = [json.loads(line) for line in (contained / 'runtime.jsonl').read_text().splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid runtime evidence {contained}: {exc}') from exc
    failed_scans = [row['workspace'] for row in rows if not row['workspace']['scan_complete']]
    assert receipt['benchmark_started'] and receipt['workload_returncode'] == -15
    assert abort['reason'] == 'workspace_telemetry_incomplete' and len(failed_scans) == 1
    assert abort['cleanup_complete'] and receipt['unit_cleanup']['verified'] and cleanup['cleanup_verified_now']
    assert not (pair / 'aa-second').exists() and (contained / 'stdout.log').stat().st_size == 0
    assert ledger['total_attempts_consumed'] == 4 and ledger['remaining_budget_slots'] == 0
    assert ledger['slots'][:2] == before['slots'][:2] and ledger['replacement_attempts'] == before['replacement_attempts']
    columns = ('load1', 'cpu_some_avg10', 'io_full_avg10', 'memory_full_avg10', 'memavailable_kib')
    ranges = {key: {'min': float('inf'), 'max': float('-inf')} for key in columns}
    baseline_first = baseline_last = None
    count = 0
    for row in csv.DictReader((pair / 'ambient-baseline.csv').open()):
        count += 1
        if baseline_first is None:
            baseline_first = row
        baseline_last = row
        for key in columns:
            value = float(row[key])
            ranges[key]['min'] = min(ranges[key]['min'], value)
            ranges[key]['max'] = max(ranges[key]['max'], value)
    assert count == 1200 and baseline_first is not None and baseline_last is not None
    baseline_result = read_json(pair / 'quiescence-profile.json')['baseline_result']
    assert baseline_result['measurement_integrity']['status'] == 'VALID'
    active = [row for row in rows if row.get('elapsed_ns') is not None]
    final_cgroup = watchdog['final_cgroup']
    paths = [HERE / 'resource-plan.approved-pair-3.json', HERE / 'campaign-ledger.json',
             HERE / 'ledger-before-final-slot.json', pair / 'ambient-baseline.csv',
             pair / 'operation-status.json', pair / 'quiescence-profile.json',
             contained / 'receipt.json', contained / 'watchdog-summary.json', contained / 'watchdog-abort.json',
             contained / 'runtime.jsonl', contained / 'command-start.json', contained / 'command-exit.json',
             HERE / 'pair-3-execution/post-stop-verification.json']
    return {
        'operation_id': context['operation_id'], 'status': 'PARTIAL / OPERATION_BLOCKED / BUDGET_EXHAUSTED',
        'evidence_class': 'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING',
        'baseline': {'samples': count, 'duration_seconds': (int(baseline_last['monotonic_ns']) - int(baseline_first['monotonic_ns'])) / 1e9,
                     'integrity': baseline_result['measurement_integrity'], 'quality_status': baseline_result['status'],
                     'quality_reason': baseline_result.get('reason'), 'host_ranges': ranges},
        'execution': {'native_arms_launched': 1, 'native_arms_completed': 0, 'second_arm_started': False,
                      'native_pid': read_json(contained / 'command-start.json')['pid'],
                      'workload_returncode': receipt['workload_returncode'], 'workload_signal': receipt['workload_signal'],
                      'runtime_samples': len(rows), 'active_samples': len(active),
                      'last_observed_active_elapsed_ms': active[-1]['elapsed_ns'] / 1e6,
                      'reason': abort['reason'], 'scan_errors': abort['errors'],
                      'raw_receipt_measurement_quality': receipt['measurement_quality'],
                      'raw_label_limitation': 'COMPLETE reflects available process/cgroup samples; workspace scan failed. Not a complete native measurement, no reclassification of raw receipt.',
                      'cleanup_verified': True},
        'partial_resources': {'scope': 'aborted partial cgroup lifetime, not full workload costs or writer isolation',
                              'observed_memory_peak_bytes': watchdog['peak_cgroup_memory_peak_bytes_observed'],
                              'observed_swap_peak_bytes': watchdog['peak_cgroup_swap_peak_bytes_observed'],
                              'final_cpu_stat': final_cgroup['cpu_stat'], 'memory_events': final_cgroup['memory_events'],
                              'swap_events': final_cgroup['memory_swap_events'], 'pids_events': final_cgroup['pids_events'],
                              'max_sampled_pss_kib': max(row['cgroup']['process_tree_pss_kib'] for row in rows),
                              'max_sampled_rss_kib': max(row['cgroup']['process_tree_rss_kib'] for row in rows),
                              'max_sampled_process_tree_write_bytes': max(row['cgroup']['process_tree_write_bytes'] for row in rows),
                              'failed_scan_partial': failed_scans[0],
                              'limits': 'sampled process IO can lose exited workers; failed workspace scan is a partial count, never a verified peak/cap proof'},
        'writer': {'complete_cycle_payloads': 0, 'p50_ms': None, 'p95_ms': None, 'p99_ms': None,
                   'stage_native_post_m4_finalize': 'UNAVAILABLE', 'reader_coherence_reopen': 'NO_COMPLETED_VERDICT',
                   'arm_order_variability': 'UNMEASURED'},
        'diagnosis': 'Workspace walker enumerated tombstones-2-1.bin.tmp.<nativePID>; stat later raised ENOENT. Existing native AtomicWrite creates .tmp.PID then renames/unlinks. Observed traversal/stat race is consistent with normal atomic publication; exact syscall not traced. Do not ignore ENOENT or declare scan complete.',
        'decision': 'KEEP native binding/caps/fullsync/strict guards; DEFER writer-tail optimization. No fifth attempt. Highest-value follow-up is bounded race-aware workspace accounting design/regression, not native retry or removal of integrity checks.',
        'accounting': {'consumed': 4, 'maximum': 4, 'remaining': 0, 'historical_original1_R1_pair2_unchanged': True},
        'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}


if __name__ == '__main__':
    target = HERE / 'final-pair-3-analysis.json'
    result = analyze()
    if target.exists():
        assert read_json(target) == result, 'Refuse changed analysis'
    else:
        target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
