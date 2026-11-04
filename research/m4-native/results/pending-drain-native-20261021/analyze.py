#!/usr/bin/env python3
"""File-only analysis of one completed exploratory500x20 arm; no execution/retry."""
import collections
import csv
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARM = HERE / 'm4-contention-pending-drain-native-20261021-1'
RAW = ARM / 'aa-first/containment'


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid retained JSON: {path}') from exc


def lines(path):
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid retained JSONL: {path}') from exc


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    assert values and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          and math.isfinite(v) and v >= 0 for v in values)
    ordered = sorted(values)
    return {'n': len(values), 'min': ordered[0], 'p50': ordered[math.ceil(.50 * len(values)) - 1],
            'p95': ordered[math.ceil(.95 * len(values)) - 1],
            'p99': ordered[math.ceil(.99 * len(values)) - 1], 'max': ordered[-1],
            'mean': sum(values) / len(values), 'sum': sum(values)}


def main():
    receipt = load(RAW / 'receipt.json')
    summary = load(RAW / 'watchdog-summary.json')
    samples = lines(RAW / 'runtime.jsonl')
    main_payload, resources, reopen = lines(RAW / 'stdout.log')
    ledger = load(HERE / 'new-attempt-ledger.json')
    identities = load(HERE / 'historical-identities.json')
    for name, expected in identities['history_sha256'].items():
        assert sha(Path(name)) == expected, name
    assert ledger['consumed'] == ledger['maximum'] == 1 and ledger['remaining'] == 0
    assert receipt['status'] == 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY'
    assert receipt['workload_returncode'] == 0 and receipt['watchdog_abort'] is None
    assert receipt['measurement_quality'] == summary['measurement_quality'] == 'INCOMPLETE'
    assert summary['hard_safety_telemetry_complete'] and not summary['workspace_samples_complete']
    assert receipt['unit_cleanup']['verified'] and summary['workspace_verified_peak'] is None
    assert main_payload['docs'] == 500 and main_payload['rounds'] == 20
    assert main_payload['queries'] == 80 and main_payload['coherence_errors'] == 0
    assert main_payload['published_generation_id'] == reopen['generation'] == 21
    assert reopen['fts_live_docs'] == 500 and reopen['vector_fts_coherence_errors'] == 0
    assert len(main_payload['writer_stage_timeline_ms']) == 20
    assert len(main_payload['reader_stage_timeline_ms']) == 80
    assert all(row['diagnostic_decision']['continuation_allowed'] for row in samples)
    assert all(row['cgroup']['process_io_sample_complete'] and row['cgroup']['process_memory_sample_complete'] for row in samples)
    array_keys = [
        'commit_samples_ms', 'writer_commit_thread_cpu_samples_ms',
        'native_vector_build_wall_samples_ms', 'native_vector_build_writer_thread_cpu_samples_ms',
        'native_vector_open_wall_samples_ms', 'native_vector_document_conversion_wall_samples_ms',
        'native_vector_upsert_wall_samples_ms', 'native_vector_flush_wall_samples_ms',
        'm4_update_wall_samples_ms', 'm4_update_writer_thread_cpu_samples_ms',
        'm4_clone_wall_samples_ms', 'm4_open_wall_samples_ms',
        'm4_open_analyzer_fingerprint_wall_ms', 'm4_open_fingerprint_verification_wall_ms',
        'm4_apply_wall_samples_ms', 'm4_flush_wall_samples_ms',
        'generation_metadata_wall_samples_ms', 'post_m4_sync_tree_wall_samples_ms',
        'snapshot_finalize_wall_samples_ms', 'generation_publish_samples_ms', 'retirement_samples_ms']
    timing = {}
    for key in array_keys:
        assert len(main_payload[key]) == 20, key
        timing[key] = distribution(main_payload[key])
    for quantile in ('p50', 'p95', 'p99'):
        assert math.isclose(timing['commit_samples_ms'][quantile], main_payload[f'commit_{quantile}_ms'], abs_tol=1e-5)
    timing['reader_samples_ms'] = distribution(main_payload['reader_samples_ms'])
    post_m4 = [event['round_end_ms'] - event['m4_build_end_ms']
               for event in main_payload['writer_stage_timeline_ms']]
    timing['post_m4_to_round_end_wall_ms'] = distribution(post_m4)
    per_round = []
    for i in range(20):
        event = main_payload['writer_stage_timeline_ms'][i]
        assert event['round'] == i and event['m4_build_end_ms'] <= event['round_end_ms']
        per_round.append({'round_zero_based': i, 'compacted': event['compacted'],
                          **{key: main_payload[key][i] for key in array_keys},
                          'post_m4_to_round_end_wall_ms': post_m4[i]})
    with (HERE / 'per-round.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(per_round[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(per_round)
    compacted = [r for r in per_round if r['compacted']]
    regular = [r for r in per_round if not r['compacted']]
    final = summary['final_cgroup']
    def peak(key):
        return max(row['cgroup'][key] for row in samples if row['cgroup'].get(key) is not None)
    def host_values(fn):
        return distribution([fn(row['host']) for row in samples])
    host = {
        'load1': host_values(lambda h: h['loadavg'][0]),
        'memavailable_kib': host_values(lambda h: h['meminfo_kib']['MemAvailable_kib']),
        'disk_available_bytes': host_values(lambda h: h['resource_root_free_bytes']),
        **{f'{family}.{kind}.avg10': host_values(lambda h, f=family, k=kind: h[f][k]['avg10'])
           for family, kind in [('io_pressure', 'full'), ('io_pressure', 'some'),
                                ('memory_pressure', 'full'), ('memory_pressure', 'some'), ('cpu_pressure', 'some')]}}
    gaps = [(b['monotonic_ns'] - a['monotonic_ns']) / 1e9 for a, b in zip(samples[:-1], samples[1:], strict=True)]
    assert min(gaps) > 0 and max(gaps) <= 2
    observed_inventory = collections.Counter(row['workspace']['inventory_status'] for row in samples)
    pending_rows = [row for row in samples if any(issue['detail'] == 'events exceed one bounded drain'
                                                for issue in row['workspace']['issues'])]
    assert pending_rows and all(not r['workspace']['scan_complete'] and
                               r['workspace_capacity_decision']['state'] == 'UNKNOWN' and
                               r['workspace_capacity_decision']['continuation_allowed'] for r in pending_rows)
    cleanup = load(ARM / 'independent-cleanup.json')
    assert cleanup['cgroup_absent'] and cleanup['socket_absent'] and not cleanup['owned_units'].strip()
    assert 'LoadState=not-found' in cleanup['unit_show'] and 'ActiveState=inactive' in cleanup['unit_show']
    analysis = {
        'status': 'EXPLORATORY_NATIVE_DIAGNOSTIC_COMPLETED_WITH_WORKSPACE_UNCERTAINTY',
        'qualification_eligible': False, 'arm_count': 1, 'no_repeat_or_comparator': True,
        'raw_receipt_status': receipt['status'], 'raw_measurement_quality': receipt['measurement_quality'],
        'native_pid': receipt['command_start']['pid'], 'command_start': receipt['command_start'],
        'command_end': receipt['command_end'],
        'command_wall_seconds': (receipt['command_end']['monotonic_ns'] - receipt['command_start']['monotonic_ns']) / 1e9,
        'completed_rounds': 20, 'completed_queries': 80, 'coherence_errors': 0, 'post_close_reopen': reopen,
        'percentile_definition': 'Source PercentileMs nearest-rank ceil(q*n)-1; n20 p95 is second-largest, p99=max, single-run exploratory only',
        'timings_ms': timing,
        'commit_by_compaction': {'compacted': distribution([r['commit_samples_ms'] for r in compacted]),
                                 'not_compacted': distribution([r['commit_samples_ms'] for r in regular])},
        'slowest_three_rounds': sorted(per_round, key=lambda r: r['commit_samples_ms'], reverse=True)[:3],
        'stage_sum_shares_of_summed_commit_time': {
            key: timing[key]['sum'] / timing['commit_samples_ms']['sum']
            for key in ('native_vector_build_wall_samples_ms', 'm4_update_wall_samples_ms',
                        'post_m4_sync_tree_wall_samples_ms', 'post_m4_to_round_end_wall_ms')},
        'native_resource_payload_before_reopen': resources,
        'process_reported_write_bytes_main_plus_reopen': resources['physical_write_bytes'] + reopen['reopen_physical_write_bytes'],
        'owned_cgroup_whole_lifetime': {
            'memory_peak_bytes': final['memory_peak_bytes'], 'swap_peak_bytes': final['memory_swap_peak_bytes'],
            'cpu_stat': final['cpu_stat'], 'memory_events': final['memory_events'],
            'swap_events': final['memory_swap_events'], 'tasks_events': final['pids_events'],
            'sampled_tasks_peak': peak('pids_current'), 'sampled_process_tree_pss_kib': peak('process_tree_pss_kib'),
            'sampled_process_tree_rss_kib': peak('process_tree_rss_kib'),
            'sampled_process_tree_write_bytes': peak('process_tree_write_bytes')},
        'runtime_host_context': host,
        'host_vmstat_deltas_first_last': {
            key: samples[-1]['host']['vmstat'][key] - samples[0]['host']['vmstat'][key]
            for key in ('pswpin_pages', 'pswpout_pages', 'oom_kill', 'pgscan_direct')},
        'observer_cost_wall_ms_not_cpu': {
            'workspace_scan': distribution([row['workspace']['scan_duration_ns'] / 1e6 for row in samples]),
            'whole_sample': distribution([row['sample_duration_ns'] / 1e6 for row in samples]),
            'limitations': 'External observer cost/perturbation not isolated by a comparator; timings are not observer CPU time.'},
        'host_diskstat_sector_deltas_first_last': {
            device: {key: samples[-1]['host']['diskstats'][device][key] - counters[key]
                     for key in ('sectors_read', 'sectors_written', 'io_ms')}
            for device, counters in samples[0]['host']['diskstats'].items()
            if device in samples[-1]['host']['diskstats']},
        'workspace_accounting': {
            'sample_count': len(samples), 'inventory_counts': dict(observed_inventory),
            'pending_after_drain_unknown_samples': len(pending_rows),
            'soft_budget_warning_samples': summary['workspace_soft_budget_warning_samples'],
            'verified_peak': None, 'reservation_credit': 0,
            'complete_traversal_max_allocated_bytes_not_peak': summary['max_observed_complete_traversal_allocated_bytes'],
            'complete_traversal_max_apparent_bytes_not_peak': summary['max_observed_complete_traversal_apparent_bytes']},
        'max_runtime_sample_gap_seconds': max(gaps), 'hard_safety_telemetry_complete': True,
        'cleanup': cleanup, 'ledger': ledger, 'old_histories_unchanged': True,
        'limitations': ['One arm and20commits only; p99=max is not a stable population-tail estimate.',
                       'No comparison, statistical repeat, arm-order variability or candidate performance claim.',
                       'Workspace remains UNKNOWN/INCOMPLETE, no allocated/apparent lifetime proof or hard cap.',
                       'Process write_bytes is kernel-accounted write traffic, not physical device attribution; payload label physical_write_bytes retained.',
                       'Cgroup/process scope includes workers/readers/base/reopen; not isolated writer CPU/IO/RAM.',
                       'Host contention/PSI/diskstats observed contemporaneously; no identified causal host-impact attribution or stationary baseline.']}
    input_files = [RAW / 'stdout.log', RAW / 'runtime.jsonl', RAW / 'receipt.json', RAW / 'watchdog-summary.json',
                   ARM / 'readiness.json', ARM / 'independent-cleanup.json', HERE / 'approved-resource-plan.json',
                   HERE / 'new-attempt-ledger.json', HERE / 'historical-identities.json']
    analysis['input_sha256'] = {str(p.relative_to(HERE)): sha(p) for p in input_files}
    (HERE / 'analysis.json').write_text(json.dumps(analysis, indent=2) + '\n')
    print(json.dumps({'status': analysis['status'], 'writer_ms': timing['commit_samples_ms'],
                      'post_m4_to_round_end_ms': timing['post_m4_to_round_end_wall_ms'],
                      'stage_shares': analysis['stage_sum_shares_of_summed_commit_time']}))


if __name__ == '__main__':
    main()
