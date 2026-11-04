#!/usr/bin/env python3
"""Offline stage/cgroup counter bounds. Never launches or samples a workload."""
import argparse
import hashlib
import json
import math
import pathlib

COUNTERS = {
    'owned_cpu_usec': ('cpu_stat','usage_usec'),
    'owned_cpu_pressure_some_usec': ('cpu_pressure','some','total'),
    'owned_io_pressure_some_usec': ('io_pressure','some','total'),
    'owned_memory_pressure_some_usec': ('memory_pressure','some','total'),
    'owned_throttled_usec': ('cpu_stat','throttled_usec'),
}
STAGES = {
    'vector_total': ('vector_start_ms','vector_end_ms','native_vector_build_writer_thread_cpu_samples_ms'),
    'vector_open': ('vector_open_start_ms','vector_open_end_ms','native_vector_open_writer_thread_cpu_samples_ms'),
    'vector_flush': ('vector_flush_start_ms','vector_flush_end_ms','native_vector_flush_writer_thread_cpu_samples_ms'),
    'post_m4_sync': ('post_m4_sync_start_ms','post_m4_sync_end_ms','post_m4_sync_tree_writer_thread_cpu_samples_ms'),
    'snapshot_finalize': ('snapshot_finalize_start_ms','snapshot_finalize_end_ms','snapshot_finalize_writer_thread_cpu_samples_ms'),
}


def numeric(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('invalid counter/time value')
    return value


def read_counter(row, path):
    value = row['cgroup']
    for key in path:
        value = value[key]
    return numeric(value)


def serialized_clock_error_ns(start_ms, end_ms):
    """Pinned bridge emits 8 significant decimal digits; also cover ns rounding."""
    magnitude=max(1e-12,abs(start_ms),abs(end_ms))
    return math.ceil(0.5*10**(math.floor(math.log10(magnitude))-7)*1e6)+1


def counter_bounds(samples, start_ns, end_ns, path, boundary_error_ns=0):
    """Counter reads occur somewhere inside each bracket: no linear interpolation."""
    numeric(start_ns); numeric(end_ns); numeric(boundary_error_ns)
    if end_ns<=start_ns or len(samples)<2:
        raise ValueError('empty stage or insufficient counter samples')
    spans=[]; previous=None
    for row in samples:
        left=numeric(row['sample_start_monotonic_ns']); right=numeric(row['monotonic_ns'])
        value=read_counter(row,path)
        if left>right or (previous is not None and (left<previous[1] or value<previous[2])):
            raise ValueError('nonmonotonic/overlapping read brackets or counter reset')
        if previous is not None:
            spans.append((previous[0],right,value-previous[2]))
        previous=(left,right,value)
    inner_start=start_ns+boundary_error_ns; inner_end=end_ns-boundary_error_ns
    outer_start=start_ns-boundary_error_ns; outer_end=end_ns+boundary_error_ns
    lower=sum(delta for left,right,delta in spans if left>=inner_start and right<=inner_end)
    upper=sum(delta for left,right,delta in spans if right>outer_start and left<outer_end)
    covered=(samples[0]['monotonic_ns']<=outer_start and samples[-1]['sample_start_monotonic_ns']>=outer_end)
    return {'lower':lower,'upper':upper if covered else None,
            'boundary_coverage':covered,'status':'BOUNDED' if covered else 'UNBOUNDED',
            'stage_boundary_error_ns':boundary_error_ns,
            'intersecting_counter_intervals':sum(right>outer_start and left<outer_end for left,right,_ in spans)}


def analyze(payload, samples, operation_id, max_gap_seconds):
    events=payload['writer_stage_timeline_ms']; origin=payload['whole_start_monotonic_ns']
    if len(events)!=20 or isinstance(origin,bool) or not isinstance(origin,int) or origin<=0:
        raise ValueError('expected complete 20-cycle monotonic stage payload')
    numeric(max_gap_seconds)
    if not isinstance(operation_id,str) or not operation_id:
        raise ValueError('missing operation identity')
    if len(samples)<2 or max_gap_seconds<=0:
        raise ValueError('missing counter coverage')
    for index,row in enumerate(samples):
        if row['diagnostic_decision']['operation_id']!=operation_id or row['diagnostic_decision']['mode']!='contention-aware':
            raise ValueError('mixed operation/mode counter telemetry')
        if row['monotonic_ns']-row['sample_start_monotonic_ns']>max_gap_seconds*1e9:
            raise ValueError('overlong counter read bracket')
        if index and row['monotonic_ns']-samples[index-1]['monotonic_ns']>max_gap_seconds*1e9:
            raise ValueError('counter monitoring gap exceeds declared resolution')
    result={'operation_id':operation_id,'evidence_class':'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING',
            'method':'bounds on observed counter increments: read spans inside the clock-error-shrunk stage give lower bounds; spans intersecting the clock-error-expanded stage give upper bounds only with boundary coverage; not strict physical execution-time bounds',
            'limitations':['owned cgroup includes readers/native workers/bootstrap, NOT isolated writer CPU',
                           'observer CPU is outside the owned cgroup',
                           'CPU/IO/memory PSI and throttling overlap; do not add them or equate wall-minus-caller-CPU with I/O wait',
                           'kernel CPU/PSI accounting can batch or lag execution; read brackets do not bound that accounting latency',
                           'IO PSI reports marked owned-task stalls, not syscall/file/device attribution; zero does not exclude unmarked fsync/lock waits',
                           'short stages may yield broad or unbounded intervals; no point-estimate interpolation'],
            'cycles':[]}
    for index,event in enumerate(events):
        if event['round']!=index:
            raise ValueError('missing/duplicate/out-of-order stage round')
        cycle={'round':event['round'],'compacted':event['compacted'],'stages':{}}
        for name,(start_key,end_key,cpu_key) in STAGES.items():
            start=event.get(start_key); end=event.get(end_key)
            if start is None and end is None or start==end==-1:
                cycle['stages'][name]={'status':'NOT_EMITTED'}; continue
            numeric(start); numeric(end)
            if end<=start:
                raise ValueError('invalid emitted stage duration: '+name)
            start_ns=origin+round(start*1e6); end_ns=origin+round(end*1e6)
            clock_error_ns=serialized_clock_error_ns(start,end)
            item={'elapsed_ms':end-start,'start_monotonic_ns':start_ns,'end_monotonic_ns':end_ns,
                  'counter_bounds':{}}
            cpu=payload.get(cpu_key)
            if cpu is not None:
                if len(cpu)!=20: raise ValueError('truncated caller CPU array')
                item['caller_cpu_ms']=numeric(cpu[index])
            for counter,path in COUNTERS.items():
                try:
                    item['counter_bounds'][counter]=counter_bounds(samples,start_ns,end_ns,path,clock_error_ns)
                except KeyError:
                    if counter!='owned_throttled_usec': raise
                    item['counter_bounds'][counter]={'status':'UNAVAILABLE'}
            cycle['stages'][name]=item
        result['cycles'].append(cycle)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stdout',type=pathlib.Path,required=True)
    parser.add_argument('--runtime',type=pathlib.Path,required=True)
    parser.add_argument('--config',type=pathlib.Path,required=True)
    parser.add_argument('--output',type=pathlib.Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('refusing to overwrite prior analysis')
    payloads=[json.loads(line) for line in args.stdout.read_text().splitlines() if line.startswith('{')]
    payloads=[p for p in payloads if 'writer_stage_timeline_ms' in p]
    if len(payloads)!=1: raise ValueError('ambiguous/missing sustained stage payload')
    samples=[json.loads(line) for line in args.runtime.read_text().splitlines()]
    config=json.loads(args.config.read_text()); context=config['policy_context']
    digest=hashlib.sha256(json.dumps(context,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if config.get('policy_mode')!='contention-aware' or config.get('policy_context_digest')!=digest:
        raise ValueError('counter context/mode digest mismatch')
    result=analyze(payloads[0],samples,context['operation_id'],context['max_monitor_gap_seconds'])
    result['input_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.stdout,args.runtime,args.config)}
    args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':
    main()
