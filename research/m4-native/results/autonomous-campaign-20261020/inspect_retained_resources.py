#!/usr/bin/env python3
"""Offline footprint extraction from existing runs, not a new benchmark."""
import csv
import hashlib
import json
import math
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
COST = HERE.parent/'m4-search-execution-marathon-20261018/phase-b-cost-model'


def extract():
    result = {'new_measurement':False, 'arms':[], 'source_sha256':{},
        'limitations':['historical filter-identity runs are not a current full-sync performance comparison',
                      'RSS/PSS are process snapshots, not cgroup memory including file cache',
                      'retained root bytes are final logical size, not workspace allocation/apparent peak',
                      'per-arm task/thread peak and cgroup throttle counters were not captured']}
    for folder in ('sustained-multicycle-500x20-20261019','sustained-multicycle-500x20-reverse-20261019'):
        for arm in ('baseline','candidate'):
            directory = COST/folder/arm
            for name in ('stdout.jsonl','pss-samples.csv','process-sampling.json'):
                p = directory/name
                result['source_sha256'][str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
            payloads = [json.loads(line) for line in (directory/'stdout.jsonl').read_text().splitlines() if line.startswith('{')]
            stages = [p for p in payloads if 'writer_stage_timeline_ms' in p]
            resources = [p for p in payloads if 'whole_process_cpu_ms' in p]
            assert len(stages)==len(resources)==1, 'must select sustained payload, not post-close/reopen payload'
            with (directory/'pss-samples.csv').open() as stream:
                samples = list(csv.DictReader(stream))
            rsskey = 'vmrss_kb' if 'vmrss_kb' in samples[0] else 'rss_kb'
            def peak(key):
                values = [float(row[key]) for row in samples]
                assert all(math.isfinite(v) and v>=0 for v in values)
                return max(values)
            result['arms'].append({'order':folder,'arm':arm,'samples':len(samples),
                'sampled_process_peak_rss_kib':peak(rsskey),'sampled_process_peak_pss_kib':peak('pss_kb'),
                'sampled_process_peak_swap_kib':peak('swap_kb'),
                'reported_maxrss_kib_across_payloads':max(p.get('peak_rss_kb',0) for p in payloads),
                'whole_wall_ms':resources[0]['whole_wall_ms'],
                'whole_process_cpu_ms':resources[0]['whole_process_cpu_ms'],
                'reported_process_physical_write_bytes':resources[0]['physical_write_bytes'],
                'retained_root_logical_bytes':stages[0]['retained_root_logical_bytes']})
    return result


if __name__ == '__main__':
    result = extract()
    target = HERE/'retained-resource-evidence.json'
    if target.exists():
        assert json.loads(target.read_text())==result, 'retained resource evidence changed'
    else:
        target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['arms'],indent=2))
