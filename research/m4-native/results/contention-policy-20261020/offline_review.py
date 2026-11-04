#!/usr/bin/env python3
"""Reproduce the retained offline statistics; no host sampling or M4 execution."""
import collections
import csv
import hashlib
import json
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = HERE.parent / 'm4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/writer-cycles.csv'
KEYS = ['commit_wall_ms', 'writer_thread_cpu_ms', 'vector_build_wall_ms',
        'vector_build_writer_thread_cpu_ms', 'm4_end_to_build_end_residual_ms', 'compaction_wall_ms']


def stats(rows):
    return {key: {'min': min(float(r[key]) for r in rows),
                  'median': statistics.median(float(r[key]) for r in rows),
                  'max': max(float(r[key]) for r in rows)} for key in KEYS}


def main():
    with SOURCE.open() as stream:
        rows = list(csv.DictReader(stream))
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(row['pair'], row['arm'])].append(row)
    result = {'source': str(SOURCE.relative_to(ROOT)),
              'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'scope': 'offline replay of historical filter-identity A/B, NOT new full-sync measurement or durability-equivalent performance proof',
              'new_benchmark_launched': False, 'groups': {}}
    for group, items in groups.items():
        noncompacting = [r for r in items if r['compacted'].lower() == 'false']
        slowest = sorted(items, key=lambda r: float(r['commit_wall_ms']), reverse=True)[:5]
        result['groups']['/'.join(group)] = {
            'cycles': len(items), 'noncompacting_cycles': len(noncompacting),
            'all_cycles_ms': stats(items), 'noncompacting_ms': stats(noncompacting),
            'slowest_five': [{key: r[key] for key in ['cycle_id', 'compacted', *KEYS]} for r in slowest]}
    retained = json.loads((HERE / 'offline-writer-tail.json').read_text())
    if result != retained:
        raise ValueError('offline replay differs from retained evidence')
    print(f'PASS: exact offline replay of {len(rows)} historical cycles; no measurement')


if __name__ == '__main__':
    main()
