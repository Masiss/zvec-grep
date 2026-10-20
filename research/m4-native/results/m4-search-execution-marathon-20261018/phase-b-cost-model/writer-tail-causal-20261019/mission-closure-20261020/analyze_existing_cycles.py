#!/usr/bin/env python3
"""Offline only: join the four existing raw arms by round, no workload launched."""
import csv
import hashlib
import json
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent
BASE = HERE.parent.parent
PAIRS = ('sustained-multicycle-500x20-20261019',
         'sustained-multicycle-500x20-reverse-20261019')
rows = []
summaries = []
inputs = {}
for pair in PAIRS:
    for arm in ('baseline', 'candidate'):
        path = BASE / pair / arm / 'stdout.jsonl'
        inputs[str(path.relative_to(BASE))] = hashlib.sha256(path.read_bytes()).hexdigest()
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        d = records[0]
        assert len(d['commit_samples_ms']) == len(d['writer_stage_timeline_ms']) == 20
        assert d['coherence_errors'] == 0
        assert records[-1]['vector_fts_coherence_errors'] == 0
        arm_rows = []
        for n, event in enumerate(d['writer_stage_timeline_ms']):
            assert event['round'] == n
            readers = [r for r in d['reader_stage_timeline_ms'] if r['round'] == n]
            assert len(readers) == 4
            last_reader_end = max(r['snapshot_release_end_ms'] for r in readers)
            # Durations are paired within a single cycle; never subtract percentiles.
            vector = d['native_vector_build_wall_samples_ms'][n]
            m4 = d['m4_update_wall_samples_ms'][n]
            post = event['build_end_ms'] - event['m4_build_end_ms']
            publish = event['manifest_publish_end_ms'] - event['manifest_publish_start_ms']
            retire = event['retirement_end_ms'] - event['retirement_start_ms']
            commit = d['commit_samples_ms'][n]
            residual = commit - vector - m4 - post - publish - retire
            assert post >= 0 and residual > -0.2
            row = dict(pair=pair, arm=arm, round=n, compacted=event['compacted'],
                       commit_ms=commit, vector_span_ms=vector,
                       vector_writer_thread_cpu_ms=d['native_vector_build_writer_thread_cpu_samples_ms'][n],
                       m4_span_ms=m4, post_m4_ms=post, publish_ms=publish, retire_ms=retire,
                       residual_ms=residual,
                       reader_window_ms=last_reader_end-event['query_release_ms'],
                       readers_done_before_m4=last_reader_end < event['m4_build_start_ms'],
                       readers_done_before_publish=last_reader_end < event['manifest_publish_start_ms'],
                       vector_rows=d['vector_rows_written_per_round'][n],
                       m4_mutations=d['m4_mutations_applied_per_round'][n])
            rows.append(row)
            arm_rows.append(row)
        top = sorted(arm_rows, key=lambda r: r['commit_ms'], reverse=True)[:3]
        sampling_path = path.with_name('process-sampling.json')
        inputs[str(sampling_path.relative_to(BASE))] = hashlib.sha256(sampling_path.read_bytes()).hexdigest()
        summaries.append(dict(pair=pair, arm=arm, cycles=20,
            resource_record=records[1], process_sampling=json.loads(sampling_path.read_text()),
            readers_done_before_m4=sum(r['readers_done_before_m4'] for r in arm_rows),
            readers_done_before_publish=sum(r['readers_done_before_publish'] for r in arm_rows),
            first_four_commit_median_ms=statistics.median(r['commit_ms'] for r in arm_rows[:4]),
            later_commit_median_ms=statistics.median(r['commit_ms'] for r in arm_rows[4:]),
            max_reader_window_ms=max(r['reader_window_ms'] for r in arm_rows),
            top_commit_cycles=top))
with (HERE / 'cycles.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
(HERE / 'cycle-summary.json').write_text(json.dumps(dict(
    classification='HISTORICAL BINARY / OFFLINE ONLY', input_sha256=inputs,
    caveats=['Vector span includes shard scaffolding, conversion, API operations, flush and durable shard-row metadata.',
             'Post-M4 contains sync and snapshot finalization; residual is unassigned, not a new stage.',
             'Wall minus writer-thread CPU is not pure I/O wait; native worker CPU is missing.',
             'No contemporaneous per-cycle host PSI; no cross-arm timestamp alignment.'],
    arms=summaries), indent=2)+'\n')
print('validated 80 cycles, 320 reader events; wrote cycles.csv and cycle-summary.json')
