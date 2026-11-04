#!/usr/bin/env python3
"""One real-kernel 32KiB/1s rehearsal. No native workload or measurement."""
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUNNER = ROOT / 'research/m4-native/tools/m4_containment_runner.py'
spec = importlib.util.spec_from_file_location('soft_ct', RUNNER)
assert spec and spec.loader
ct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ct
spec.loader.exec_module(ct)


def main():
    out = HERE / 'm4-contention-rehearsal-soft-1'
    out.mkdir(exist_ok=False)
    ctx = ct.strict_json_file(ROOT / 'research/m4-native/results/autonomous-campaign-20261020/resource-plan.approved-pair-3.json')
    resource = (ROOT / 'research/m4-native/.work/tmp').resolve()
    ctx.update(purpose='synthetic-containment-rehearsal', operation_id=out.name, operation_root=str(out),
               resource_root=str(resource), policy_sha256=hashlib.sha256(ct.POLICY_PATH.read_bytes()).hexdigest(),
               binary_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
               workload=dict(ct.REHEARSAL_WORKLOAD), workspace_max_allocated_bytes=4096,
               workspace_max_apparent_bytes=4096)
    for key in ('approval_reference', 'execution_authorization_reference', 'risk_acceptance_reference'):
        ctx[key] = str(HERE / 'owner-authorization.md')
    ct.validate_contention_context(ctx)
    ct.write_json(out / 'context.json', ctx)
    ct.write_json(out / 'identity.json', {'runner_sha256': hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
                                        'synthetic_only': True, 'native_attempts_consumed': 0})
    with tempfile.TemporaryDirectory(prefix='soft-budget-rehearsal-', dir=resource) as temp:
        workspace = Path(temp)
        before = ct.host_snapshot(resource)
        ct.write_json(out / 'host-before.json', before)
        receipt = ct.run_arm([sys.executable, '-c', ct.REHEARSAL_CODE], out / 'aa-first/containment',
                             resource, workspace, workspace, ctx['runtime_limit_seconds'],
                             sample_interval_ms=50, budget=ct.contention_budget(ctx),
                             policy_context=ctx, enforce_host_gates=True,
                             env={k: v for k, v in os.environ.items() if k in
                                  ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'PYTHONDONTWRITEBYTECODE')})
        ct.write_json(out / 'host-after.json', ct.host_snapshot(resource))
        summary = receipt.get('watchdog_summary') or {}
        assert receipt['status'] in ('COMPLETED_WITH_WORKSPACE_WARNINGS', 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY'), receipt
        assert summary['hard_safety_telemetry_complete'] and summary['abort'] is None
        assert summary['workspace_soft_budget_warning_samples'] > 0
        assert receipt['unit_cleanup']['verified']
        assert receipt['workload_returncode'] == 0 and receipt['benchmark_started']
        config = ct.strict_json_file(out / 'aa-first/containment/watchdog-config.json')
        assert not Path(config['cgroup_path']).exists() and not Path(config['socket_dir']).exists()
        assert not list(workspace.iterdir())
        state = subprocess.run(['systemctl', '--user', 'show', receipt['unit'], '-p', 'LoadState', '-p', 'ActiveState'],
                               capture_output=True, text=True, timeout=5)
        assert 'LoadState=not-found' in state.stdout and 'ActiveState=inactive' in state.stdout
        ct.write_json(out / 'result.json', {'test_status': 'PASS', 'workload_receipt_status': receipt['status'],
                      'measurement_quality': receipt['measurement_quality'], 'synthetic_only': True,
                      'runner_sha256': hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
                      'native_attempts_consumed': 0, 'workspace_warning_samples': summary['workspace_soft_budget_warning_samples'],
                      'cleanup_verified': True, 'unit_state': state.stdout})
        print((out / 'result.json').read_text(), flush=True)


if __name__ == '__main__':
    main()
