#!/usr/bin/env python3
"""Offline analysis of consumed slot 1. No workload, live sampling or retry."""
import csv
import hashlib
import json
import pathlib

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[3]
PAIR=ROOT/'research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/m4-contention-campaign-20261020-pair-1'


def analyze():
    with (PAIR/'ambient-baseline.csv').open() as stream: rows=list(csv.DictReader(stream))
    states=[json.loads(r['contention_host_json']) for r in rows]
    policy=json.loads((ROOT/'research/m4-native/docs/benchmark-policy.json').read_text())
    guards=policy['resource_guards']
    profile=json.loads((PAIR/'quiescence-profile.json').read_text())['baseline_result']
    decision=json.loads((PAIR/'baseline-gate-decision.json').read_text())
    receipt=json.loads((PAIR/'aa-first/containment/receipt.json').read_text())
    stderr=(PAIR/'aa-first/containment/stderr.log').read_text()
    assert decision['operation_outcome']=='OPERATION_READY_EXPLORATORY'
    assert profile['measurement_integrity']['status']=='VALID'
    assert receipt['benchmark_started'] is False and receipt['status']=='LAUNCH_FAILED'
    assert receipt['controller_cleanup']['verified'] and receipt['unit_cleanup']['verified']
    assert "Failed to parse CPUQuota= value '200.000%'" in stderr
    assert not (PAIR/'aa-second').exists()
    def extent(key): return {'min':min(float(r[key]) for r in rows),'max':max(float(r[key]) for r in rows)}
    raw_failures={
        'M4-RG-LOAD1':sum(float(r['load1'])>guards['load1_max'] for r in rows),
        'M4-RG-MEM':sum(int(r['memavailable_kib'])<guards['MemAvailable_kib_min'] for r in rows),
        'M4-RG-IOPSI':sum(float(r['io_full_avg10'])>guards['io_full_avg10_max'] for r in rows),
        'M4-RG-DISK':sum(s['resource_root_free_bytes']<guards['var_tmp_bytes_min'] for s in states)}
    before,after=states[0],states[-1]
    sources=[PAIR/'ambient-baseline.csv',PAIR/'ambient-baseline.host.json',PAIR/'quiescence-profile.json',
             PAIR/'baseline-gate-decision.json',PAIR/'operation-status.json',
             PAIR/'aa-first/containment/receipt.json',PAIR/'aa-first/containment/stderr.log',
             HERE/'resource-plan.approved-pair-1.json']
    return {'evidence_class':'EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING',
        'pair_slot':1,'pair_completed':False,'native_workload_started':False,'second_arm_started':False,
        'baseline':{'samples':len(rows),'duration_seconds':(int(rows[-1]['monotonic_ns'])-int(rows[0]['monotonic_ns']))/1e9,
            'integrity':profile['measurement_integrity'],'capacity_decision':decision['operation_outcome'],
            'quality_status':profile['status'],'quality_reason':profile.get('reason'),
            'load1':extent('load1'),'io_full_avg10':extent('io_full_avg10'),
            'memavailable_kib':extent('memavailable_kib'),
            'minimum_disk_free_bytes':min(s['resource_root_free_bytes'] for s in states),
            'host_oom_kill_delta':after['vmstat']['oom_kill']-before['vmstat']['oom_kill'],
            'host_pswpin_pages_delta':int(rows[-1]['pswpin'])-int(rows[0]['pswpin']),
            'host_pswpout_pages_delta':int(rows[-1]['pswpout'])-int(rows[0]['pswpout']),
            'historical_raw_failed_samples':raw_failures,
            'attribution':'host/observer context without M4 native workload; cannot assign IO or order effects to M4 or isolate observer overhead'},
        'operation_receipt_preserved':json.loads((PAIR/'operation-status.json').read_text()),
        'launch_mechanism':{'direct_error':stderr.strip(),'secondary_exception':receipt['exception'],
            'diagnosis':'systemd-run rejected the integer CPU quota encoded with three decimal places before creating/bootstrap handshake; controller socket accept timed out',
            'readback_reached':False,'approved_cpu_max':'200000 100000','original_cli_cpu_quota':'200.000%',
            'raw_controller_cleanup':receipt['controller_cleanup'],'raw_unit_cleanup':receipt['unit_cleanup']},
        'hypotheses':{'H1_host_order':'DEFER: valid ambient variation, no two native arms/order comparison',
            'H2_native_vector':'DEFER: no native stages measured; not pure-HNSW attribution',
            'H3_post_m4_sync':'DEFER: no stage data; no sync removed',
            'H4_shared_host_implementation':'DEFER: no native incremental-effect/cost evidence',
            'H5_measurement_artifact':'CONFIRMED launch-property serialization defect; NOT historical writer-tail root cause'},
        'cost_and_correctness':'Native latency/CPU/PSS/RAM/IO/writes/maintenance/retrieval/freshness/coherence are NOT MEASURED by this attempt; no performance comparison or regression claim.',
        'decision':'DEFER writer-tail optimization; keep full sync; independently fix exact integer launch-property serialization and validate using tiny synthetic units only.',
        'remaining_slots':2,'eligible_next_pair':False,
        'eligibility_reason':'No valid prior native pair data; rerunning the same intended pair after repairing launch would be a disguised retry under another slot, prohibited by owner sections 5/9.',
        'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}


if __name__=='__main__':
    result=analyze();target=HERE/'pair-1-analysis.json'
    if target.exists():assert json.loads(target.read_text())==result,'refusing changed analysis'
    else:target.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('baseline','decision','remaining_slots','eligible_next_pair')},indent=2))
