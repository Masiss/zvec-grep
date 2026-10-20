#!/usr/bin/env python3
"""First native invocation of original slot2 after correcting wrapper-only import setup.

Initial wrapper261 failed importing the sibling module before any resource check or
native operation directory/command. Not a failed policy guard or a native retry.
Accounting stays3/4; same approved operation ID and plan, no extra slot or reset.
"""
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

B=pathlib.Path(__file__).resolve().parent
ROOT=B.parents[3]
CAUSAL=ROOT/'research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019'


def main():
    original=B/'pair-2-execution'
    error=json.loads((original/'prerequisite-or-launch-error.json').read_text())
    assert error['type']=='ModuleNotFoundError' and not (original/'host-before-prerequisites.json').exists()
    assert json.loads((B/'campaign-ledger.json').read_text())['total_attempts_consumed']==3
    out=original/'after-import-correction';out.mkdir(exist_ok=False)
    plan=B/'resource-plan.approved-pair-2.json';p=json.loads(plan.read_text())
    assert not pathlib.Path(p['operation_root']).exists(),'Already claimed native operation; never retry'
    sys.path.insert(0,str(CAUSAL))
    spec=importlib.util.spec_from_file_location('pair2_static_aa',CAUSAL/'run_host_carryover_aa.py')
    assert spec and spec.loader
    aa=importlib.util.module_from_spec(spec);spec.loader.exec_module(aa);ct=aa.runner.containment
    ct.write_json(out/'host-before-prerequisites.json',ct.host_snapshot(pathlib.Path('/var/tmp')))
    for commit in ('65f59b8','4f7caac','277245a'):
        subprocess.run(['git','merge-base','--is-ancestor',commit,'HEAD'],check=True)
    assert subprocess.check_output(['git','branch','--show-current'],text=True).strip()=='work/zg-optimization'
    ct.validate_contention_context(p);verified=aa.runner.verify_full_sync(aa.BINARY)
    active=subprocess.check_output(['systemctl','--user','list-units','m4-contained-*','--all','--no-legend','--no-pager'],text=True)
    assert not active.strip(),'Unresolved containment'
    assert json.loads((B/'m4-contention-rehearsal-e2e-1/rehearsal-result.json').read_text())['status']=='PASS'
    fresh=ct.host_snapshot(pathlib.Path('/var/tmp'));decision=ct.contention_decision(fresh,p,'baseline')
    ct.write_json(out/'prerequisites.json',{'timestamp_utc':ct.utc_now(),'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'controller_sha256':hashlib.sha256((ROOT/'research/m4-native/tools/m4_containment_runner.py').read_bytes()).hexdigest(),
        'static_provenance':verified,'capability':ct.probe(),'active_owned_units':active,
        'host_snapshot':fresh,'capacity_decision':decision,'rehearsal_pass':True,'native_launch_not_yet_started':True})
    assert decision['continuation_allowed'],'Formal capacity refusal; no launch or retry'
    cmd=['flock','--nonblock',str(B/'campaign-execution.lock'),'timeout','--signal=TERM','--kill-after=5s','355s',
         sys.executable,str(CAUSAL/'run_host_carryover_aa.py'),'--mode','contention-aware','--contention-plan',str(plan)]
    ct.write_json(out/'launch-command.json',{'argv':cmd,'cwd':str(ROOT),'timestamp_utc':ct.utc_now(),
        'approved_plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),'slot':2,'total_attempt_index':3,
        'first_native_invocation':True,'no_auto_retry':True})
    with (out/'runner.log').open('w') as stream:
        result=subprocess.run(cmd,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,env=os.environ.copy())
    ct.write_json(out/'exit.json',{'exit_code':result.returncode,'timestamp_utc':ct.utc_now()})
    print('NATIVE_PAIR_2_EXIT',result.returncode,flush=True)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
