#!/usr/bin/env python3
"""One explicitly authorized final original slot, unchanged native runner/guards."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CAUSAL = ROOT / 'research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019'


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Invalid evidence {path}: {exc}') from exc


def main():
    out = HERE / 'pair-3-execution'
    out.mkdir(exist_ok=False)
    plan = HERE / 'resource-plan.approved-pair-3.json'
    context = read_json(plan)
    ledger = read_json(HERE / 'campaign-ledger.json')
    assert ledger['total_attempts_consumed'] == 4 and ledger['remaining_budget_slots'] == 0
    assert not pathlib.Path(context['operation_root']).exists(), 'Never reenter a claimed operation'
    sys.path.insert(0, str(CAUSAL))
    spec = importlib.util.spec_from_file_location('final_slot_aa', CAUSAL / 'run_host_carryover_aa.py')
    assert spec and spec.loader
    aa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aa)
    ct = aa.runner.containment
    ct.validate_contention_context(context)
    resource_root = pathlib.Path(context['resource_root']).resolve(strict=True)
    assert resource_root == aa.runner.RESOURCE_ROOT
    ct.write_json(out / 'supervisor-state.json', {'pid': os.getpid(), 'phase': 'fresh-prerequisites', 'timestamp_utc': ct.utc_now()})
    ct.write_json(out / 'host-before-prerequisites.json', ct.host_snapshot(resource_root))
    for commit in ('4f7caac', '277245a', 'cc813f9'):
        subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    assert subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip() == 'work/zg-optimization'
    assert context.get('purpose') is None and context['workload'] == {'docs': 500, 'rounds': 20, 'tracing': False}
    verified = aa.runner.verify_full_sync(aa.BINARY)
    active = subprocess.check_output(['systemctl', '--user', 'list-units', 'm4-contained-*', '--all', '--no-legend', '--no-pager'], text=True)
    assert not active.strip(), 'Unresolved owned containment'
    rehearsal = read_json(HERE / 'm4-contention-rehearsal-e2e-1/rehearsal-result.json')
    rehearsal_command = read_json(HERE / 'm4-contention-rehearsal-e2e-1/command.json')
    controller_hash = hashlib.sha256((ROOT / 'research/m4-native/tools/m4_containment_runner.py').read_bytes()).hexdigest()
    assert rehearsal['status'] == 'PASS' and controller_hash == rehearsal_command['runner_sha256']
    fresh = ct.host_snapshot(resource_root)
    decision = ct.contention_decision(fresh, context, 'baseline')
    ct.write_json(out / 'prerequisites.json', {
        'timestamp_utc': ct.utc_now(), 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'controller_sha256': controller_hash, 'static_provenance': verified, 'capability': ct.probe(),
        'active_owned_units': active, 'rehearsal_pass_same_controller': True,
        'host_snapshot': fresh, 'capacity_decision': decision, 'native_launch_not_yet_started': True})
    if not decision['continuation_allowed']:
        ct.write_json(out / 'operation-outcome.json', {
            'classification': 'SUPERVISOR_PREFLIGHT_RECEIPT', 'operation_status': 'OPERATION_BLOCKED',
            'phase': 'fresh-capacity', 'blocking_failures': decision['blocking_failures'],
            'native_command_invoked': False, 'native_arms_launched': 0, 'retry_performed': False,
            'mission_status': 'NOT_EVALUATED_BY_RUNNER', 'timestamp_utc': ct.utc_now()})
        print('FINAL_SLOT_FORMAL_CAPACITY_REFUSAL ' + json.dumps(decision['blocking_failures']), flush=True)
        return 2
    cmd = ['flock', '--nonblock', str(HERE / 'campaign-execution.lock'),
           'timeout', '--signal=TERM', '--kill-after=5s', '355s', sys.executable,
           str(CAUSAL / 'run_host_carryover_aa.py'), '--mode', 'contention-aware', '--contention-plan', str(plan)]
    ct.write_json(out / 'launch-command.json', {
        'argv': cmd, 'cwd': str(ROOT), 'approved_plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
        'slot': 3, 'total_attempt_index': 4, 'timestamp_utc': ct.utc_now(), 'no_auto_retry': True})
    print('FINAL_SLOT_PREREQUISITES_PASS_NATIVE_PAIR_START', flush=True)
    with (out / 'runner.log').open('w') as stream:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, env=os.environ.copy())
        ct.write_json(out / 'supervisor-state.json', {
            'pid': os.getpid(), 'supervised_pid': proc.pid, 'phase': 'native-pair', 'timestamp_utc': ct.utc_now()})
        rc = proc.wait()
    ct.write_json(out / 'exit.json', {'exit_code': rc, 'timestamp_utc': ct.utc_now()})
    ct.write_json(out / 'host-after-supervisor.json', ct.host_snapshot(resource_root))
    print('FINAL_SLOT_NATIVE_PAIR_EXIT ' + str(rc), flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
