#!/usr/bin/env python3
"""File-only analysis of the consumed single attempt; never launches a command."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARM = HERE / 'm4-contention-soft-budget-native-20261021-1'
RAW = ARM / 'aa-first/containment'


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'Invalid retained evidence {path}: {exc}') from exc


def main():
    receipt = load(RAW / 'receipt.json')
    summary = load(RAW / 'watchdog-summary.json')
    rows = [load_json_line(line) for line in (RAW / 'runtime.jsonl').read_text().splitlines()]
    payloads = [load_json_line(line) for line in (RAW / 'stdout.log').read_text().splitlines() if line.strip()]
    start = load(RAW / 'command-start.json')
    end = load(RAW / 'command-exit.json')
    readiness = load(ARM / 'readiness.json')
    def bounds(values):
        return {'min': min(values), 'max': max(values)}
    def peak(key):
        return max(row['cgroup'][key] for row in rows if row['cgroup'].get(key) is not None)
    authority = load(HERE / 'new-attempt-ledger.json')
    old = HERE.parent / 'autonomous-campaign-20261020/campaign-ledger.json'
    old_hash = hashlib.sha256(old.read_bytes()).hexdigest()
    assert old_hash == (HERE / 'historical-ledger.sha256').read_text().strip()
    final = summary['final_cgroup']
    analysis = {
        'status': 'PARTIAL / OPERATION_BLOCKED / NEW_ATTEMPT_EXHAUSTED',
        'evidence_class': 'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING',
        'authority': authority, 'historical_ledger_unchanged': True, 'historical_ledger_sha256': old_hash,
        'readiness_pass': readiness['decision']['continuation_allowed'],
        'native_pid': start['pid'], 'native_started_utc': start['timestamp_utc'],
        'returncode': end['returncode'], 'signal': -end['returncode'] if end['returncode'] < 0 else None,
        'command_elapsed_seconds': (end['monotonic_ns'] - start['monotonic_ns']) / 1e9,
        'raw_status': receipt['status'], 'raw_measurement_quality': receipt['measurement_quality'],
        'abort': load(RAW / 'watchdog-abort.json'), 'cleanup': load(ARM / 'independent-cleanup.json'),
        'runtime_sample_count': len(rows), 'raw_workspaces_complete': summary['workspace_samples_complete'],
        'hard_safety_telemetry_complete': summary['hard_safety_telemetry_complete'],
        'workspace_verified_peak': None,
        'last_partial_workspace': rows[-1]['workspace'],
        'complete_traversal_maxima_not_lifetime_peaks': {
            'allocated_bytes': summary['max_observed_complete_traversal_allocated_bytes'],
            'apparent_bytes': summary['max_observed_complete_traversal_apparent_bytes']},
        'partial_owned_resources': {
            'cgroup_memory_peak_bytes': final['memory_peak_bytes'], 'cgroup_swap_peak_bytes': final['memory_swap_peak_bytes'],
            'final_cgroup_cpu_stat': final['cpu_stat'], 'memory_events': final['memory_events'],
            'swap_events': final['memory_swap_events'], 'task_events': final['pids_events'],
            'sampled_task_peak': peak('pids_current'), 'sampled_process_tree_pss_kib': peak('process_tree_pss_kib'),
            'sampled_process_tree_rss_kib': peak('process_tree_rss_kib'),
            'sampled_process_tree_write_bytes': peak('process_tree_write_bytes')},
        'runtime_host_context': {
            'load1': bounds([r['host']['loadavg'][0] for r in rows]),
            'memavailable_kib': bounds([r['host']['meminfo_kib']['MemAvailable_kib'] for r in rows]),
            'disk_available_bytes': bounds([r['host']['resource_root_free_bytes'] for r in rows]),
            **{family: bounds([r['host'][family][kind]['avg10'] for r in rows])
               for family, kind in [('io_pressure', 'full'), ('memory_pressure', 'full'), ('cpu_pressure', 'some')]},
            'attribution': 'contemporaneous shared-host observations, not identified incremental M4 impact'},
        'native_affinity_observation': load(ARM / 'native-affinity.json'),
        'writer_results': {'complete_stdout_payloads': len(payloads), 'p50_ms': None, 'p95_ms': None,
                           'p99_ms': None, 'stages': 'UNAVAILABLE', 'coherence_reopen': 'NO_COMPLETED_VERDICT',
                           'comparison_or_order_variability': 'UNMEASURED'},
        'interpretation': 'Readable queue after bounded drain was treated as fatal coverage loss. It does not establish observed overflow or hard resource distress. A prospective exact pending-drain accounting exception was added after this abort and synthetic-tested; raw abort, execution policy/plan and consumed budget are not reclassified. No native retry.',
        'limitations': ['Partial lifetime only; resource maxima are not full500x20 costs or isolated writer CPU/IO.',
                       'No completed cycle payload, latency distribution or correctness/coherence verdict from this arm.',
                       'No filesystem quota/aggregate hard workspace bound or deterministic overshoot guarantee.',
                       'Post-abort pending-drain correction is software-tested, not native-run validated.']}
    files = [RAW / 'receipt.json', RAW / 'watchdog-summary.json', RAW / 'watchdog-abort.json',
             RAW / 'runtime.jsonl', RAW / 'stdout.log', RAW / 'command-start.json', RAW / 'command-exit.json',
             HERE / 'approved-resource-plan.json', HERE / 'new-attempt-ledger.json', ARM / 'readiness.json']
    analysis['input_sha256'] = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (HERE / 'native-analysis.json').write_text(json.dumps(analysis, indent=2) + '\n')
    print(json.dumps({k: analysis[k] for k in ('status', 'native_pid', 'command_elapsed_seconds', 'writer_results')}))


def load_json_line(line):
    try:
        return json.loads(line)
    except ValueError as exc:
        raise ValueError('Invalid retained JSONL') from exc


if __name__ == '__main__':
    main()
