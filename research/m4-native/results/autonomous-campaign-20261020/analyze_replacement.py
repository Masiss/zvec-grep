#!/usr/bin/env python3
"""Offline R1 refusal analysis; does not rerun guards, capture data or launch work."""
import csv
import hashlib
import json
import pathlib

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def analyze():
    plan=json.loads((HERE/'resource-plan.approved-replacement-1.json').read_text())
    pair=pathlib.Path(plan['operation_root']);contained=pair/'aa-first/containment'
    with (pair/'ambient-baseline.csv').open() as stream: rows=list(csv.DictReader(stream))
    states=[json.loads(r['contention_host_json']) for r in rows]
    result=json.loads((pair/'quiescence-profile.json').read_text())['baseline_result']
    decision=json.loads((pair/'baseline-gate-decision.json').read_text())
    receipt=json.loads((contained/'receipt.json').read_text());summary=json.loads((contained/'watchdog-summary.json').read_text())
    raw=json.loads((contained/'runtime.jsonl').read_text().splitlines()[0]);cg=raw['cgroup']
    closure=json.loads((HERE/'replacement-1-execution/post-stop-verification.json').read_text())
    assert result['measurement_integrity']['status']=='VALID'
    assert receipt['benchmark_started'] is False and not (pair/'aa-second').exists()
    assert summary['abort']['reason']=='required_telemetry_missing'
    assert cg['process_io_sample_complete'] is False and cg['process_memory_sample_complete'] is True
    assert closure['verified_owned_cleanup_now'] and closure['socket_dir_absent']
    guards=json.loads((ROOT/'research/m4-native/docs/benchmark-policy.json').read_text())['resource_guards']
    def extent(k):return {'min':min(float(r[k]) for r in rows),'max':max(float(r[k]) for r in rows)}
    sources=[pair/'ambient-baseline.csv',pair/'ambient-baseline.host.json',pair/'quiescence-profile.json',
        pair/'baseline-gate-decision.json',pair/'operation-status.json',contained/'receipt.json',
        contained/'provenance.json',contained/'watchdog-config.json',contained/'watchdog-summary.json',
        contained/'watchdog-abort.json',contained/'runtime.jsonl',HERE/'replacement-1-execution/post-stop-verification.json']
    return {'operation_id':plan['operation_id'],'slot':'R1','total_attempt_index':2,'maximum_total_attempts':4,
        'original_pair_1_untouched':True,'original_slots_remaining':2,'native_arms_started':0,
        'evidence_class':'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING',
        'writer':{'samples':0,'p50_ms':None,'p95_ms':None,'p99_ms':None,
                  'reason':'native GO never released; no cycle/native/post-M4 timing or reader/coherence/reopen result'},
        'baseline':{'samples':len(rows),'duration_seconds':(int(rows[-1]['monotonic_ns'])-int(rows[0]['monotonic_ns']))/1e9,
            'integrity':result['measurement_integrity'],'capacity_decision':decision['operation_outcome'],
            'quality_status':result['status'],'quality_reason':result.get('reason'),
            'load1':extent('load1'),'cpu_some_avg10':extent('cpu_some_avg10'),'io_full_avg10':extent('io_full_avg10'),
            'memory_full_avg10':extent('memory_full_avg10'),'memavailable_kib':extent('memavailable_kib'),
            'minimum_disk_free_bytes':min(s['resource_root_free_bytes'] for s in states),
            'host_oom_kill_delta':states[-1]['vmstat']['oom_kill']-states[0]['vmstat']['oom_kill'],
            'host_swapin_pages_delta':int(rows[-1]['pswpin'])-int(rows[0]['pswpin']),
            'host_swapout_pages_delta':int(rows[-1]['pswpout'])-int(rows[0]['pswpout']),
            'raw_historical_failures':{'IOPSI':sum(float(r['io_full_avg10'])>guards['io_full_avg10_max'] for r in rows),
                'DISK':sum(s['resource_root_free_bytes']<guards['var_tmp_bytes_min'] for s in states),
                'MEM':sum(int(r['memavailable_kib'])<guards['MemAvailable_kib_min'] for r in rows),
                'LOAD1':sum(float(r['load1'])>guards['load1_max'] for r in rows)}},
        'original_operation_status':json.loads((pair/'operation-status.json').read_text()),
        'original_receipt_status':receipt['status'],'original_safety_outcome':receipt['safety_outcome'],
        'original_controller_cleanup_verified':receipt['controller_cleanup']['verified'],
        'original_unit_retirement_verified':receipt['unit_cleanup']['verified'],
        'subsequent_independent_cleanup_verified':closure['verified_owned_cleanup_now'],
        'cleanup_verification_does_not_resume_measurement':True,
        'caps_applied':receipt['limits_applied'],'admission_readiness_acknowledged':False,
        'bootstrap_only':{'sample_count':summary['samples'],'pids':cg['pids'],
            'memory_peak_bytes':summary['peak_cgroup_memory_peak_bytes_observed'],
            'swap_peak_bytes':summary['peak_cgroup_swap_peak_bytes_observed'],
            'memory_events':cg['memory_events'],'cpu_stat':cg['cpu_stat'],
            'process_io_sample_complete':cg['process_io_sample_complete'],
            'process_memory_sample_complete':cg['process_memory_sample_complete'],
            'note':'reported process IO zeros invalid because sample incomplete; not zero physical IO proof'},
        'diagnosis':'parse_kv retained trailing colons from /proc/PID/io keys; read_cgroup_metric requires read_bytes/write_bytes without colons. Deterministic mismatch guarantees incomplete IO telemetry even on a readable file. Raw gate failure remains intact.',
        'hypotheses':{'H1_order':'UNMEASURED: valid host baseline only, no paired native order data',
            'H2_vector':'UNMEASURED','H3_sync_finalize':'UNMEASURED','H4_shared_implementation':'UNRESOLVED',
            'H5_artifact':'CONFIRMED /proc IO key normalization defect; correct strict gate blocked GO; not writer-tail root cause'},
        'decision':'KEEP strict completeness requirement and full sync; independently correct parser and strengthen actual-kernel initial-sample regression; DEFER native writer-tail optimization.',
        'eligible_next_pair':False,'reason_no_next':'Formal watchdog integrity refusal; no retry/new-ID substitute under current authorization.',
        'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}


if __name__=='__main__':
    result=analyze();target=HERE/'replacement-1-analysis.json'
    if target.exists():assert json.loads(target.read_text())==result,'refuse changed analysis'
    else:target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('operation_id','writer','baseline','bootstrap_only','decision')},indent=2))
