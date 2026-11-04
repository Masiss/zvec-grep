#!/usr/bin/env python3
"""One fixed real-kernel contention rehearsal; not a native pair or benchmark."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

HERE=pathlib.Path(__file__).resolve().parent
ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('rehearsal_containment',ROOT/'research/m4-native/tools/m4_containment_runner.py')
assert spec and spec.loader
ct=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ct;spec.loader.exec_module(ct)


def main():
    out=HERE/'m4-contention-rehearsal-e2e-1'
    out.mkdir(exist_ok=False)
    ctx=json.loads((HERE/'resource-plan.approved-replacement-1.json').read_text())
    ctx.update(purpose='synthetic-containment-rehearsal',operation_id=out.name,operation_root=str(out),
               binary_sha256=hashlib.sha256(pathlib.Path(sys.executable).read_bytes()).hexdigest(),
               workload=dict(ct.REHEARSAL_WORKLOAD),
               approval_reference=str(HERE/'owner-rehearsal-continuation-authorization.md'),
               execution_authorization_reference=str(HERE/'owner-rehearsal-continuation-authorization.md'))
    ctx.pop('replacement_slot');ct.validate_contention_context(ctx)
    ct.write_json(out/'approved-context.json',ctx)
    command=[sys.executable,'-c',ct.REHEARSAL_CODE]
    ct.write_json(out/'command.json',{'argv':command,'synthetic_only':True,'benchmark_evidence':False,
        'runner_sha256':hashlib.sha256((ROOT/'research/m4-native/tools/m4_containment_runner.py').read_bytes()).hexdigest(),
        'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()})
    units=subprocess.check_output(['systemctl','--user','list-units','m4-contained-*','--all','--no-legend','--no-pager'],text=True)
    assert not units.strip(),'unresolved containment unit before rehearsal'
    # Same /var/tmp Btrfs resource domain as the approved native plan; fresh owned0700 root.
    import tempfile
    workspace=pathlib.Path(tempfile.mkdtemp(prefix='m4-rehearsal-e2e-',dir=ctx['resource_root']))
    ct.write_json(out/'workspace.json',{'path':str(workspace),'retained_empty_after_cleanup':True})
    before=ct.host_snapshot(pathlib.Path(ctx['resource_root']));ct.write_json(out/'host-before.json',before)
    ct.write_json(out/'capability.json',ct.probe())
    receipt=ct.run_arm(command,out/'aa-first/containment',pathlib.Path(ctx['resource_root']),workspace,workspace,
        ctx['runtime_limit_seconds'],sample_interval_ms=100,budget=ct.contention_budget(ctx),
        policy_context=ctx,enforce_host_gates=True,env={k:v for k,v in os.environ.items() if k in
            ('PATH','HOME','LANG','LC_ALL','TZ','PYTHONDONTWRITEBYTECODE')})
    ct.write_json(out/'host-after.json',ct.host_snapshot(pathlib.Path(ctx['resource_root'])))
    contained=out/'aa-first/containment'
    cfg=json.loads((contained/'watchdog-config.json').read_text())
    state=subprocess.run(['systemctl','--user','show',receipt['unit'],'-p','LoadState','-p','ActiveState'],capture_output=True,text=True,timeout=5)
    cleanup={'unit':receipt['unit'],'unit_state':state.stdout,
             'cgroup_absent':not pathlib.Path(cfg['cgroup_path']).exists(),
             'socket_absent':not pathlib.Path(cfg['socket_dir']).exists(),
             'workspace_empty':not list(workspace.iterdir())}
    ct.write_json(out/'post-stop-verification.json',cleanup)
    # Preserve all failures before asserting; no automatic retry or raw receipt reclassification.
    assert receipt['status']=='PASS',receipt
    assert receipt['benchmark_started'] and receipt['unit_cleanup']['verified']
    assert all(cleanup[k] for k in ('cgroup_absent','socket_absent','workspace_empty'))
    assert 'LoadState=not-found' in state.stdout and 'ActiveState=inactive' in state.stdout
    rows=[json.loads(line) for line in (contained/'runtime.jsonl').read_text().splitlines()]
    previous=None
    for row in rows:
        assert not ct.contention_runtime_failures(row['cgroup'],previous)
        assert row['diagnostic_decision']['continuation_allowed']
        previous=row['cgroup']
    summary=json.loads((contained/'watchdog-summary.json').read_text())
    assert summary['abort'] is None and summary['process_tree_samples_complete']
    assert summary['policy_mode']=='contention-aware' and summary['policy_context_digest']==ct.contention_context_digest(ctx)
    payload=json.loads((contained/'stdout.log').read_text())
    io=ct.parse_kv(payload['proc_io'])
    fields=('rchar','wchar','read_bytes','write_bytes','cancelled_write_bytes')
    assert all(k in io and isinstance(io[k],int) and io[k]>=0 for k in fields)
    assert io['wchar']>=32768 and io['write_bytes']>=32768
    assert json.loads((contained/'command-exit.json').read_text())['returncode']==0
    assert receipt['limits_applied']['cpu_max']=='200000 100000'
    ct.write_json(out/'rehearsal-result.json',{'status':'PASS','synthetic_only':True,'benchmark_evidence':False,
        'native_attempts_consumed':0,'campaign_attempts_before':2,'campaign_attempts_after':2,
        'runtime_samples':len(rows),'proc_io_worker':io,'strict_runtime_contract_all_samples':True,
        'independent_watchdog_context_digest':summary['policy_context_digest'],
        'actual_caps':receipt['limits_applied'],'cleanup':cleanup,
        'limits':'fixed32KiB payload/1s hold; not native initialization/vector/durability correctness/performance or overload test'})
    print(json.dumps(json.loads((out/'rehearsal-result.json').read_text()),indent=2))


if __name__=='__main__':main()
