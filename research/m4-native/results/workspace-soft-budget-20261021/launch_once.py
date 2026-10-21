#!/usr/bin/env python3
"""Exactly one new owner-authorized full-sync arm. No retries, no old ledger writes."""
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
CAUSAL = ROOT / 'research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019'
BINARY = ROOT / 'research/m4-native/results/durability-restore-20261020/m4_bridge_integration.full-sync'
OLD_LEDGER = ROOT / 'research/m4-native/results/autonomous-campaign-20261020/campaign-ledger.json'
os.environ.update(M4_RESOURCE_ROOT='/var/tmp', M4_SYNC_CONTROL_BINARY=str(BINARY), M4_SYNC_TREATMENT_BINARY=str(BINARY))  # noqa: S108 -- approved Btrfs benchmark domain; mkdtemp below is private0700
sys.path.insert(0, str(CAUSAL))
spec = importlib.util.spec_from_file_location('one_sync', CAUSAL / 'run_sync_reduction_pair.py')
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
ct = runner.containment


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def top():
    p = subprocess.run(['ps', '-eo', 'pid,ppid,comm,pcpu,rss,etimes', '--sort=-pcpu'],
                       capture_output=True, text=True, timeout=5)
    return {'returncode': p.returncode, 'stdout': '\n'.join(p.stdout.splitlines()[:21]),
            'stderr': p.stderr, 'observer_affinity': sorted(os.sched_getaffinity(0))}


def main():
    ctx = ct.strict_json_file(HERE / 'approved-resource-plan.json')
    out = Path(ctx['operation_root'])
    out.mkdir(exist_ok=False)  # permanent single-use claim, even if readiness refuses
    prior = ct.strict_json_file(HERE / 'new-attempt-ledger.json')
    assert prior['consumed'] == 0 and prior['maximum'] == 1
    ledger = dict(prior, consumed=1, remaining=0, state='READINESS_CHECK', operation_id=ctx['operation_id'])
    ct.write_json(HERE / 'new-attempt-ledger.json', ledger)
    old_hash = sha(OLD_LEDGER)
    started = False
    try:
        ct.validate_contention_context(ctx)
        assert ctx['workload'] == {'docs': 500, 'rounds': 20, 'tracing': False}
        assert (ctx['memory_max_bytes'], ctx['memory_swap_max_bytes'], ctx['cpu_max'], ctx['pids_max'],
                ctx['workspace_max_allocated_bytes'], ctx['workspace_max_apparent_bytes'],
                ctx['host_memory_reserve_bytes'], ctx['disk_reserve_bytes'], ctx['runtime_limit_seconds']) == (
                    1073741824, 0, '200000 100000', 128, 536870912, 1073741824, 1073741824, 2147483648, 115)
        rehearsal = ct.strict_json_file(HERE / 'm4-contention-rehearsal-soft-1/result.json')
        assert rehearsal['test_status'] == 'PASS' and rehearsal['cleanup_verified']
        assert rehearsal['runner_sha256'] == sha(ROOT / 'research/m4-native/tools/m4_containment_runner.py')
        provenance = runner.verify_full_sync(BINARY)  # read-only; no M4 executable invocation
        units = subprocess.check_output(['systemctl', '--user', 'list-units', 'm4-contained-*', '--all',
                                         '--no-legend', '--no-pager'], text=True)
        assert not units.strip(), 'unresolved owned containment unit'
        capability = ct.probe()
        host = ct.host_snapshot(Path(ctx['resource_root']))
        decision = ct.contention_decision(host, ctx, 'aa-first')
        ct.write_json(out / 'readiness.json', {'timestamp_utc': ct.utc_now(), 'provenance': provenance,
                      'controller_sha256': rehearsal['runner_sha256'], 'capability': capability,
                      'host': host, 'top': top(), 'decision': decision, 'old_ledger_sha256': old_hash,
                      'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                      'native_started': False, 'same_controller_actual_rehearsal': True})
        if not decision['continuation_allowed']:
            ledger.update(state='READINESS_REFUSED', native_arms_launched=0, native_arms_completed=0)
            ct.write_json(out / 'outcome.json', dict(ledger, decision=decision, no_retry=True))
            return 2
        workspace = Path(tempfile.mkdtemp(prefix='m4-soft-native-', dir=ctx['resource_root']))
        assert workspace.stat().st_mode & 0o777 == 0o700
        temp = workspace / 'tmp'
        temp.mkdir()
        command = [str(BINARY), str(workspace / 'root'), str(runner.DICT), '--incremental-fts-sustained', '500', '20']
        env = {k: v for k, v in os.environ.items() if not k.startswith('M4_') and k != 'LD_PRELOAD'}
        env.update(TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp))
        ct.write_json(out / 'command.json', {'argv': command, 'workspace': str(workspace), 'retained': True,
                      'native_environment_names': sorted(env), 'no_fault_no_tracing': True,
                      'temp_data_inside_monitored_workspace': True, 'outer_timeout_seconds': 150})
        stop = threading.Event()
        def observe_native():
            marker = out / 'aa-first/containment/command-start.json'
            while not stop.is_set():
                try:
                    if marker.exists():
                        info = ct.strict_json_file(marker)
                        pid = info['pid']
                        if Path(f'/proc/{pid}/exe').resolve(strict=True) == BINARY.resolve():
                            ct.write_json(out / 'native-affinity.json', {'timestamp_utc': ct.utc_now(), 'pid': pid,
                                          'affinity': sorted(os.sched_getaffinity(pid)),
                                          'proc_status': Path(f'/proc/{pid}/status').read_text(), 'top': top()})
                            return
                except (OSError, ValueError) as exc:
                    ct.write_json(out / 'aux-observation-error.json', {'error': str(exc), 'not_hard_safety_telemetry': True})
                    return
                stop.wait(0.1)
        observer = threading.Thread(target=observe_native, daemon=True)
        observer.start()
        print('ONE_NEW_NATIVE_DIAGNOSTIC_READINESS_PASS', flush=True)
        try:
            result = ct.run_arm(command, out / 'aa-first/containment', Path(ctx['resource_root']), workspace,
                                ROOT, ctx['runtime_limit_seconds'], sample_interval_ms=50,
                                budget=ct.contention_budget(ctx), env=env, enforce_host_gates=True, policy_context=ctx)
        finally:
            stop.set()
            observer.join(timeout=6)
        started = result.get('benchmark_started', False)
        good = (result['status'] in ('PASS', 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY', 'COMPLETED_WITH_WORKSPACE_WARNINGS')
                and result.get('workload_returncode') == 0 and result.get('unit_cleanup', {}).get('verified') is True)
        ledger.update(state='DIAGNOSTIC_COMPLETED_WITH_REPORTED_CAVEATS' if good else 'OPERATION_BLOCKED',
                      native_arms_launched=int(started), native_arms_completed=int(good),
                      receipt_status=result['status'], measurement_quality=result['measurement_quality'])
        ct.write_json(out / 'host-after.json', {'host': ct.host_snapshot(Path(ctx['resource_root'])), 'top': top()})
        ct.write_json(out / 'outcome.json', dict(ledger, no_retry=True, qualification_eligible=False,
                      workspace=str(workspace), old_ledger_unchanged=sha(OLD_LEDGER) == old_hash))
        print(ledger, flush=True)
        return 0 if good else 3
    except BaseException as exc:
        ledger.update(state='BLOCKED_EXCEPTION', error=f'{type(exc).__name__}: {exc}')
        ct.write_json(out / 'exception.json', dict(ledger, no_retry=True, native_start_in_memory=started))
        raise
    finally:
        assert sha(OLD_LEDGER) == old_hash, 'historical ledger changed'
        ct.write_json(HERE / 'new-attempt-ledger.json', ledger)


if __name__ == '__main__':
    raise SystemExit(main())
