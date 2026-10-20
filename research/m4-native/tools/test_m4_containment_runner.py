#!/usr/bin/env python3
"""Tiny, bounded executable checks for the research cgroup containment runner."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ['M4_TEST_FIXTURE_MODE'] = '1'
RUNNER_PATH = pathlib.Path(__file__).with_name('m4_containment_runner.py').resolve()
spec = importlib.util.spec_from_file_location('m4_containment_runner', RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class ContainmentRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.output_root = pathlib.Path(os.environ['M4_CONTAINMENT_TEST_OUTPUT']).resolve()
        cls.output_root.mkdir(parents=True, exist_ok=True)
        cls.fixture_root = pathlib.Path(tempfile.mkdtemp(prefix='m4-containment-fixtures-'))
        (cls.fixture_root / 'workspace').mkdir()
        cls.unrelated_pid = os.getpid()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.fixture_root)
        os.kill(cls.unrelated_pid, 0)

    def run_fixture(self, name: str, command: list[str], timeout: float = 5.0,
                    budget: dict | None = None) -> tuple[dict, pathlib.Path]:
        receipt_dir = self.output_root / name
        receipt = runner.run_arm(
            command, receipt_dir, self.fixture_root, self.fixture_root / 'workspace',
            self.fixture_root.parent, timeout, sample_interval_ms=20,
            enforce_host_gates=False, budget=budget,
        )
        self.assertEqual(json.loads((receipt_dir / 'receipt.json').read_text()), receipt)
        self.assertTrue((receipt_dir / 'runtime.jsonl').exists())
        self.assertTrue((receipt_dir / 'host-before.json').exists())
        self.assertTrue((receipt_dir / 'host-after.json').exists())
        self.assertTrue((receipt_dir / 'systemd-final.txt').exists())
        self.assertTrue(receipt['unit_cleanup']['verified'])
        self.assertTrue(receipt['unit_cleanup']['unit_collected'])
        state = subprocess.run(
            ['systemctl', '--user', 'show', receipt['unit'], '-p', 'LoadState', '--value'],
            capture_output=True, text=True, timeout=3)
        self.assertEqual(state.stdout.strip(), 'not-found')
        return receipt, receipt_dir

    def test_terminal_classes_distinguish_oom_and_abort_types(self) -> None:
        self.assertEqual(runner.failure_class(reason='cgroup_oom_event'), 'OOM_ABORT')
        self.assertEqual(runner.failure_class(reason='runtime_timeout'), 'TIMEOUT_ABORT')
        self.assertEqual(runner.failure_class(reason='supervisor_lost'), 'CONTROLLER_CRASH_ABORT')

    def test_hard_gate_block_never_launches_command(self) -> None:
        receipt_dir = self.output_root / 'hard-gate-refusal'
        marker = self.fixture_root / 'must-not-exist'
        state = runner.host_snapshot(self.fixture_root)
        state['meminfo_kib']['MemAvailable_kib'] = runner.GUARDS['memavailable_min_kib'] - 1
        with mock.patch.object(runner, 'host_snapshot', return_value=state):
            receipt = runner.run_arm(
                ['/usr/bin/touch', str(marker)], receipt_dir, self.fixture_root,
                self.fixture_root / 'workspace', self.fixture_root.parent, 5.0,
                pressure_limits={
                    'swapfree_min_kib': 0, 'memory_some_avg10_max': 1000.0,
                    'memory_full_avg10_max': 1000.0,
                },
            )
        self.assertEqual(receipt['status'], 'PREFLIGHT_BLOCKED')
        self.assertEqual(receipt['failure_class'], 'OPERATION_GUARD_REFUSAL')
        self.assertFalse(receipt['benchmark_started'])
        self.assertFalse(marker.exists())
        self.assertEqual(receipt['safety_outcome'], 'NOT_STARTED')
        self.assertEqual(receipt['measurement_quality'], 'NOT_APPLICABLE')
        self.assertEqual(receipt['operation_outcome'], 'OPERATION_BLOCKED')
        self.assertEqual(receipt['mission_status'], 'NOT_EVALUATED_BY_RUNNER')
        self.assertEqual(receipt['resource_guard_failures'][0]['gate_id'], 'M4-RG-MEM')
        self.assertFalse(receipt['benchmark_started'])

    def test_memory_swap_pressure_baseline_is_enforced(self) -> None:
        limits = {
            'swapfree_min_kib': 1000, 'memory_some_avg10_max': 0.2,
            'memory_full_avg10_max': 0.0,
        }
        state = {
            'meminfo_kib': {'SwapFree_kib': 1000},
            'memory_pressure': {
                'some': {'avg10': 0.2}, 'full': {'avg10': 0.0},
            },
        }
        self.assertEqual(runner.pressure_baseline_failures(state, limits), [])
        state['meminfo_kib']['SwapFree_kib'] = 999
        self.assertIn('SwapFree', runner.pressure_baseline_failures(state, limits)[0])
        state['meminfo_kib']['SwapFree_kib'] = 1000
        state['memory_pressure']['some']['avg10'] = 0.21
        self.assertIn('memory PSI some', runner.pressure_baseline_failures(state, limits)[0])

    def test_missing_telemetry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix='m4-cgroup-empty-') as root, \
                self.assertRaisesRegex(RuntimeError, 'memory.current'):
            runner.validate_required_cgroup_metrics(pathlib.Path(root))

    def test_guard_override_is_blocked_outside_explicit_fixture_mode(self) -> None:
        receipt_dir = self.output_root / 'unapproved-fixture-override'
        marker = self.fixture_root / 'must-not-exist-override'
        with mock.patch.dict(os.environ, {'M4_TEST_FIXTURE_MODE': ''}):
            receipt = runner.run_arm(
                [sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                receipt_dir, self.fixture_root, self.fixture_root / 'workspace',
                self.fixture_root.parent, 5.0, enforce_host_gates=False,
            )
        self.assertEqual(receipt['status'], 'PREFLIGHT_BLOCKED')
        self.assertEqual(receipt['failure_class'], 'TEST_FIXTURE_OVERRIDE_REFUSED')
        self.assertFalse(receipt['benchmark_started'])
        self.assertFalse(marker.exists())

    def test_missing_preflight_telemetry_writes_no_launch_receipt(self) -> None:
        receipt_dir = self.output_root / 'telemetry-incomplete'
        marker = self.fixture_root / 'must-not-exist-telemetry'
        with mock.patch.object(runner, 'host_snapshot', side_effect=OSError('fixture telemetry missing')):
            receipt = runner.run_arm(
                ['/usr/bin/touch', str(marker)], receipt_dir, self.fixture_root,
                self.fixture_root / 'workspace', self.fixture_root.parent, 5.0,
                enforce_host_gates=True,
                pressure_limits={'swapfree_min_kib': 0, 'memory_some_avg10_max': 1000.0,
                                 'memory_full_avg10_max': 1000.0},
            )
        self.assertEqual(receipt['status'], 'TELEMETRY_INCOMPLETE')
        self.assertFalse(receipt['benchmark_started'])
        self.assertEqual(receipt['operation_outcome'], 'OPERATION_BLOCKED')
        self.assertEqual(receipt['mission_status'], 'NOT_EVALUATED_BY_RUNNER')
        self.assertTrue((receipt_dir / 'receipt.json').is_file())
        self.assertFalse(marker.exists())

    def test_integer_cpu_quota_and_complete_budget_readback(self) -> None:
        budget = {'approval_reference':'SYNTHETIC CAP-SERIALIZATION REGRESSION ONLY',
                  'memory_max_bytes':1073741824,'memory_swap_max_bytes':0,
                  'cpu_max':'200000 100000','pids_max':128,
                  'workspace_max_allocated_bytes':536870912,
                  'workspace_max_apparent_bytes':1073741824}
        self.assertIn('CPUQuota=200%',runner.service_properties(budget,5))
        self.assertIn('CPUQuota=50%',runner.service_properties(dict(budget,cpu_max='50000 100000'),5))
        command = [sys.executable,'-c',(
            'import pathlib,json,threading; '
            'rel=pathlib.Path("/proc/self/cgroup").read_text().strip().split("::",1)[1]; '
            'cg=pathlib.Path("/sys/fs/cgroup")/rel.lstrip("/"); '
            'print(json.dumps({k:(cg/k).read_text().strip() for k in '
            '("cpu.max","memory.max","memory.swap.max","pids.max")}),flush=True); '
            'threading.Event().wait(0.15)')]
        receipt,directory=self.run_fixture('integer-cpu-complete-budget',command,budget=budget)
        self.assertEqual(receipt['status'],'PASS')
        caps=json.loads((directory/'stdout.log').read_text())
        self.assertEqual(caps,{'cpu.max':'200000 100000','memory.max':'1073741824',
                              'memory.swap.max':'0','pids.max':'128'})
        self.assertEqual(receipt['limits_applied']['cpu_max'],'200000 100000')

    def test_explicit_task_budget_is_applied_to_owned_unit(self) -> None:
        command = [sys.executable, '-c', (
            'import pathlib,threading; '
            'rel=pathlib.Path("/proc/self/cgroup").read_text().strip().split("::",1)[1]; '
            'cg=pathlib.Path("/sys/fs/cgroup")/rel.lstrip("/"); '
            'print("TASK_LIMIT="+(cg/"pids.max").read_text().strip(),flush=True); '
            'threading.Event().wait(0.15)'
        )]
        receipt, directory = self.run_fixture('task-budget', command, budget={
            'approval_reference':'SYNTHETIC TASK-CAP TEST ONLY', 'pids_max':64})
        self.assertEqual(receipt['status'],'PASS')
        self.assertEqual(receipt['limits_applied']['pids_max'],'64')
        self.assertIn('TASK_LIMIT=64', (directory/'stdout.log').read_text())
        runtime = [json.loads(line) for line in (directory/'runtime.jsonl').read_text().splitlines()]
        self.assertTrue(all(isinstance(row['cgroup']['pids_current'],int) for row in runtime))
        self.assertTrue(all(row['cgroup']['pids_events']['max']==0 for row in runtime))

    def test_normal_exit_and_descendant_share_owned_cgroup(self) -> None:
        command = [sys.executable, '-c', (
            'import subprocess,sys; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.35)"]); '
            'print("CHILD_PID="+str(p.pid),flush=True); raise SystemExit(p.wait())'
        )]
        receipt, directory = self.run_fixture('normal-child', command)
        self.assertEqual(receipt['status'], 'PASS')
        self.assertEqual(receipt['failure_class'], 'NORMAL_EXIT')
        self.assertEqual(receipt['safety_outcome'], 'SAFE_COMPLETE')
        self.assertEqual(receipt['measurement_quality'], 'COMPLETE')
        self.assertEqual(receipt['io_control']['status'],
                         'AVAILABLE' if receipt['cgroup_io_controller_available'] else 'UNAVAILABLE')
        self.assertIn('not a per-arm physical-write attribution', receipt['io_control']['fallback'])
        child_pid = int(next(line.split('=', 1)[1] for line in (directory / 'stdout.log').read_text().splitlines() if line.startswith('CHILD_PID=')))
        samples = [json.loads(line) for line in (directory / 'runtime.jsonl').read_text().splitlines()]
        self.assertTrue(any(child_pid in row['cgroup']['pids'] for row in samples), 'descendant was not observed in arm cgroup')
        self.assertTrue(any('process_tree_pss_kib' in row['cgroup'] and 'memory_peak_bytes' in row['cgroup'] for row in samples))
        self.assertIn('diskstats', samples[0]['host'])
        self.assertEqual(receipt['cgroup_relative_path'].split('/')[-1], receipt['unit'])
        self.assertIsNone(receipt['watchdog_abort'])

    def test_retirement_keeps_failed_unit_when_cleanup_is_unverified(self) -> None:
        directory = self.output_root / 'unverified-retirement'
        directory.mkdir(exist_ok=True)
        cg = self.fixture_root / 'nonempty-cgroup'
        cg.mkdir()
        (cg / 'cgroup.procs').write_text('123\n')
        (cg / 'cgroup.events').write_text('populated 1\n')
        receipt: dict = {'status': 'PASS', 'performance_eligible': True}
        runner.write_json(directory / 'receipt.json', receipt)
        (directory / 'systemd-final.txt').write_text('Result=exit-code\nExecMainStatus=7\n')
        for state in ('LoadState=loaded\nActiveState=failed\n', 'LoadState=not-found\nActiveState=inactive\n'):
            with self.subTest(state=state):
                receipt = {'status': 'SIGNAL_EXIT', 'workload_returncode': -15, 'performance_eligible': False}
                runner.write_json(directory / 'receipt.json', receipt)
                response = subprocess.CompletedProcess([], 0, state, '')
                with mock.patch.object(runner.subprocess, 'run', return_value=response) as run:
                    runner.retire_unit('owned-fixture.service', cg, directory, receipt)
                self.assertFalse(any('reset-failed' in call.args[0] for call in run.call_args_list))
                self.assertFalse(receipt['unit_cleanup']['verified'])
                self.assertEqual(receipt['status'], 'CLEANUP_FAILED')
                self.assertEqual(receipt['pre_retirement_outcome']['status'], 'SIGNAL_EXIT')
                self.assertEqual(receipt['workload_returncode'], -15)
                self.assertEqual(receipt['operation_outcome'], 'SAFETY_STOP')
                self.assertFalse(receipt['performance_eligible'])
                self.assertEqual(json.loads((directory / 'receipt.json').read_text()), receipt)

    def test_nonzero_exit_is_not_pass(self) -> None:
        receipt, directory = self.run_fixture('nonzero', [sys.executable, '-c', 'raise SystemExit(7)'])
        self.assertEqual(receipt['status'], 'NONZERO_EXIT')
        self.assertEqual(receipt['failure_class'], 'NONZERO_EXIT')
        self.assertEqual(receipt['workload_returncode'], 7)
        self.assertIsNone(receipt['workload_signal'])
        self.assertEqual(receipt['bootstrap_exit_status'], 7)
        self.assertEqual(receipt['process_returncode'], 7)
        self.assertIn('ExecMainStatus=7\n', (directory / 'systemd-final.txt').read_text())
        self.assertEqual(receipt['measurement_quality'], 'COMPLETE')

    def test_sigterm_and_sigkill_are_not_pass(self) -> None:
        for name, sig in [('sigterm', signal.SIGTERM), ('sigkill', signal.SIGKILL)]:
            command = [sys.executable, '-c', f'import os,signal; os.kill(os.getpid(), {sig})']
            receipt, directory = self.run_fixture(name, command)
            self.assertEqual(receipt['status'], 'SIGNAL_EXIT')
            self.assertEqual(receipt['failure_class'], 'SIGNAL_EXIT')
            self.assertEqual(receipt['workload_returncode'], -sig)
            self.assertEqual(receipt['workload_signal'], sig)
            self.assertEqual(receipt['bootstrap_exit_status'], 128 + sig)
            self.assertEqual(receipt['process_returncode'], 128 + sig)
            self.assertIn(f'ExecMainStatus={128 + sig}\n', (directory / 'systemd-final.txt').read_text())
            self.assertEqual(json.loads((directory / 'command-exit.json').read_text())['returncode'], -sig)
            self.assertEqual(receipt['safety_outcome'], 'WORKLOAD_FAILED')

    def test_supervisor_crash_is_detected_and_arm_is_cleaned(self) -> None:
        receipt_dir = self.output_root / 'supervisor-crash'
        worker_code = r'''import importlib.util,pathlib,sys
p=pathlib.Path(sys.argv[1]);s=importlib.util.spec_from_file_location('crash_runner',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
root=pathlib.Path(sys.argv[3])
cmd=[sys.executable,'-c','import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print("CHILD_PID="+str(p.pid),flush=True); time.sleep(30)']
m.run_arm(cmd,pathlib.Path(sys.argv[2]),root.parent,root,root.parent,30,20,enforce_host_gates=False)
'''
        worker = subprocess.Popen(
            [sys.executable, '-c', worker_code, str(RUNNER_PATH), str(receipt_dir), str(self.fixture_root / 'workspace')],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            state_path = receipt_dir / 'run-state.json'
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if (state_path.exists() and (receipt_dir / 'watchdog-config.json').exists()
                        and (receipt_dir / 'stdout.log').exists()
                        and 'CHILD_PID=' in (receipt_dir / 'stdout.log').read_text()):
                    break
                if worker.poll() is not None:
                    self.fail(f'worker exited before contained workload started: {worker.returncode}')
                time.sleep(0.02)
            else:
                self.fail('contained workload did not reach its observable active state')
            child_pid = int(next(line.split('=', 1)[1] for line in (receipt_dir / 'stdout.log').read_text().splitlines() if line.startswith('CHILD_PID=')))
            config = json.loads((receipt_dir / 'watchdog-config.json').read_text())
            socket_dir = pathlib.Path(config['socket_dir'])
            worker.kill()
            worker.wait(timeout=5)
            abort_path = receipt_dir / 'watchdog-abort.json'
            deadline = time.monotonic() + 10
            abort = None
            while time.monotonic() < deadline:
                if abort_path.exists():
                    abort = json.loads(abort_path.read_text())
                    if abort.get('cleanup_complete'):
                        break
                time.sleep(0.02)
            self.assertIsNotNone(abort, 'independent watchdog did not persist an abort receipt')
            assert abort is not None
            self.assertTrue(abort.get('cleanup_complete'), 'independent watchdog cleanup did not finish')
            self.assertTrue(abort.get('socket_cleanup_verified'), 'independent watchdog socket cleanup not verified')
            self.assertEqual(abort['reason'], 'supervisor_lost')
            self.assertEqual(abort['failure_class'], 'CONTROLLER_CRASH_ABORT')
            self.assertIn(child_pid, abort['termination']['term_pids'])
            self.assertTrue((receipt_dir / 'watchdog-summary.json').exists())
            self.assertFalse(socket_dir.exists(), 'watchdog did not clean the abandoned controller socket directory')
            unit = json.loads(state_path.read_text())['unit']
            cgroup_path = runner.CGROOT / json.loads(state_path.read_text())['cgroup_relative_path']
            deadline = time.monotonic() + 5
            unit_state = ''
            while time.monotonic() < deadline:
                unit_state = subprocess.run(
                    ['systemctl', '--user', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'ControlGroup'],
                    capture_output=True, text=True, check=True, timeout=5,
                ).stdout
                if ('ActiveState=inactive' in unit_state or 'ActiveState=failed' in unit_state) and 'ControlGroup=\n' in unit_state and not cgroup_path.exists():
                    break
                time.sleep(0.02)
            self.assertTrue('ActiveState=inactive' in unit_state or 'ActiveState=failed' in unit_state)
            self.assertIn('ControlGroup=\n', unit_state)
            self.assertFalse(cgroup_path.exists(), 'owned cgroup was not removed after the transient unit stopped')
            # The killed controller cannot retire its own failed unit. After
            # retaining watchdog evidence and verifying cleanup, the fixture
            # owner must retire it rather than polluting user-systemd state.
            receipt: dict = {'unit': unit, 'status': 'WATCHDOG_ABORT',
                       'failure_class': 'CONTROLLER_CRASH_ABORT',
                       'safety_outcome': 'ABORTED_CONTAINED', 'performance_eligible': False,
                       'watchdog_abort': abort}
            runner.write_json(receipt_dir / 'receipt.json', receipt)
            runner.retire_unit(unit, cgroup_path, receipt_dir, receipt)
            self.assertTrue(receipt['unit_cleanup']['verified'])
            self.assertTrue(receipt['unit_cleanup']['unit_collected'])
            self.assertEqual(receipt['failure_class'], 'CONTROLLER_CRASH_ABORT')
            os.kill(self.unrelated_pid, 0)
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=5)

    def test_watchdog_abort_kills_descendants_and_records_reason(self) -> None:
        command = [sys.executable, '-c', (
            'import subprocess,sys,time; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'print("CHILD_PID="+str(p.pid),flush=True); time.sleep(30)'
        )]
        receipt, directory = self.run_fixture('watchdog-timeout', command, timeout=0.5)
        self.assertEqual(receipt['status'], 'WATCHDOG_ABORT')
        self.assertEqual(receipt['safety_outcome'], 'ABORTED_CONTAINED')
        self.assertTrue(receipt['watchdog_abort']['final_cleanup']['success'])
        self.assertEqual(receipt['reason'], 'runtime_timeout')
        self.assertEqual(receipt['failure_class'], 'TIMEOUT_ABORT')
        self.assertEqual(receipt['watchdog_abort']['reason'], 'runtime_timeout')
        self.assertTrue(receipt['watchdog_abort']['timestamp_utc'])
        self.assertTrue(receipt['watchdog_abort']['termination']['term_pids'])
        self.assertEqual(receipt['measurement_quality'], 'COMPLETE')
        child_pid = int(next(line.split('=', 1)[1] for line in (directory / 'stdout.log').read_text().splitlines() if line.startswith('CHILD_PID=')))
        self.assertIn(child_pid, receipt['watchdog_abort']['termination']['term_pids'])
        unit_state = subprocess.run(
            ['systemctl', '--user', 'show', receipt['unit'], '-p', 'LoadState', '-p', 'ActiveState'],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout
        self.assertIn('LoadState=not-found', unit_state)
        os.kill(self.unrelated_pid, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=pathlib.Path, required=True)
    args = parser.parse_args()
    os.environ['M4_CONTAINMENT_TEST_OUTPUT'] = str(args.output_dir.resolve())
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContainmentRunnerTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'successful': result.wasSuccessful(),
        'python': sys.version,
        'runner': str(RUNNER_PATH),
        'output_dir': str(args.output_dir.resolve()),
        'host_gates': 'not applied to tiny synthetic fixtures; no production benchmark was run',
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'test-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
