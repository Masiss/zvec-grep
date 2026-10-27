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
import threading
import time
import unittest
from typing import Any
from unittest import mock

os.environ['M4_TEST_FIXTURE_MODE'] = '1'
RUNNER_PATH = pathlib.Path(__file__).with_name('m4_containment_runner.py').resolve()
spec = importlib.util.spec_from_file_location('m4_containment_runner', RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
PAIR_RUNNER_PATH = pathlib.Path(__file__).with_name('run_rust_lifecycle_pair.py').resolve()
pair_spec = importlib.util.spec_from_file_location('run_rust_lifecycle_pair', PAIR_RUNNER_PATH)
assert pair_spec and pair_spec.loader
pair_runner = importlib.util.module_from_spec(pair_spec)
sys.modules[pair_spec.name] = pair_runner
pair_spec.loader.exec_module(pair_runner)


class ContainmentRunnerTests(unittest.TestCase):
    def test_rust_pipeline_lifecycle_profile_is_one_bounded_pair(self) -> None:
        contract = runner.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']
        self.assertTrue(contract['single_pair_only'])
        self.assertEqual(contract['workload']['source_file_count'], 64)
        self.assertEqual(contract['workload']['source_bytes'], 694_421)
        self.assertEqual(contract['workload']['source_selection']['path_prefix'],
                         'rust/crates/zg-engine/src/')
        self.assertEqual(contract['workload']['profile_label'],
                         '64-file Rust engine pilot; not full-corpus Gate B')
        self.assertEqual(contract['workload']['full_corpus_reference']['source_file_count'], 421)
        self.assertEqual(contract['workload']['reader_query_queue_capacity'], 8)
        self.assertEqual(contract['workload']['zvec_native_memory_limit_bytes'], 134_217_728)
        self.assertEqual(contract['arm_roles'], ['native', 'm4'])
        self.assertEqual(contract['resource_controls']['memory_max_bytes'], 536_870_912)
        self.assertEqual(contract['resource_controls']['memory_swap_max_bytes'], 0)
        self.assertEqual(contract['resource_controls']['cpu_max'], '200000 100000')
        self.assertEqual(contract['resource_controls']['workspace_max_allocated_bytes'], 33_554_432)
        self.assertEqual(contract['workspace_quota']['max_referenced_bytes'], 134_217_728)
        self.assertIn('io.max unavailable', contract['io_pressure_mode'])

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

    def test_watchdog_observer_summary_reports_cadence_and_cpu_separately(self) -> None:
        summary = runner.watchdog_observer_summary([
            {
                'watchdog_sample_start_monotonic_ns': 1_000,
                'watchdog_loop_cpu_ns': 80,
                'watchdog_loop_work_ns': 200,
            },
            {
                'watchdog_sample_start_monotonic_ns': 1_500,
                'watchdog_loop_cpu_ns': 40,
                'watchdog_loop_work_ns': 100,
            },
        ])
        self.assertEqual(summary['sample_start_gap_max_ns'], 500)
        self.assertEqual(summary['watchdog_loop_cpu_ns_sum'], 120)
        self.assertEqual(summary['watchdog_loop_work_ns_sum'], 300)
        self.assertAlmostEqual(summary['watchdog_cpu_to_active_work_ratio'], 0.4)
        self.assertEqual(summary['watchdog_loop_cpu_ns_max'], 80)
        self.assertEqual(summary['watchdog_loop_work_ns_max'], 200)

    def test_rust_lifecycle_environment_binds_internal_memory_budget(self) -> None:
        context = {
            'arm_role': 'native', 'source_root': str(self.fixture_root / 'src'),
            'index_root': str(self.fixture_root / 'index'),
            'report_path': str(self.fixture_root / 'report.json'),
            'temporary_directory': str(self.fixture_root / 'tmp'),
            'library_path': '/opt/zvec/lib',
            'reader_query_interval_ms': 25,
            'reader_query_max_samples_per_pass': 256,
            'reader_query_queue_capacity': 8,
            'zvec_native_memory_limit_bytes': 134_217_728,
        }
        environment = runner.rust_lifecycle_expected_environment(context)
        self.assertEqual(environment['ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES'], '134217728')
        self.assertEqual(environment['M4_GATE_B_ARM'], 'native')
        self.assertEqual(environment['M4_GATE_B_READER_QUERY_INTERVAL_MS'], '25')
        self.assertEqual(environment['M4_GATE_B_READER_QUERY_MAX_SAMPLES'], '256')
        self.assertEqual(environment['M4_GATE_B_READER_QUERY_QUEUE_CAPACITY'], '8')
        self.assertEqual(environment['LD_LIBRARY_PATH'], '/opt/zvec/lib')

    def test_registered_qgroup_bound_allows_unknown_inventory_but_not_scope_or_quota_errors(self) -> None:
        contract = runner.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['workspace_quota']
        budget = {
            'purpose': runner.M4_RUST_LIFECYCLE_PURPOSE,
            'workspace_max_allocated_bytes': runner.BENCHMARK_POLICY[
                'rust_pipeline_lifecycle_pair']['resource_controls']['workspace_max_allocated_bytes'],
            'workspace_max_apparent_bytes': runner.BENCHMARK_POLICY[
                'rust_pipeline_lifecycle_pair']['resource_controls']['workspace_max_apparent_bytes'],
            'workspace_quota_proof': {
                'status': 'VERIFIED', 'attestation_path': contract['attestation_path'],
                'workspace_root': contract['workspace_subvolume'],
                'filesystem_uuid': contract['filesystem_uuid'], 'subvolume_id': contract['subvolume_id'],
                'qgroup_id': contract['qgroup_id'],
                'max_referenced_bytes': contract['max_referenced_bytes'],
                'accounting_mode': contract['accounting_mode'], 'attestation_sha256': 'a' * 64,
            },
        }
        observation = runner.workspace_quota_bounded_observation(self.fixture_root / 'workspace')
        sample = {
            'status': 'PASS', 'referenced_bytes': contract['max_referenced_bytes'] - contract['minimum_free_bytes'],
            'max_referenced_bytes': contract['max_referenced_bytes'],
            'available_bytes': contract['minimum_free_bytes'],
            'required_free_bytes': contract['minimum_free_bytes'],
        }
        decision = runner.workspace_capacity_decision(observation, budget, runner.CONTENTION_MODE, sample)
        self.assertTrue(decision['continuation_allowed'])
        self.assertEqual(decision['measurement_quality'], 'HARD_QUOTA_BOUND')
        self.assertFalse(decision['workspace_inventory_complete'])
        self.assertEqual(decision['indeterminate_overages'][0]['observed_bytes'], None)

        no_headroom = dict(sample, available_bytes=contract['minimum_free_bytes'] - 1)
        blocked = runner.workspace_capacity_decision(observation, budget, runner.CONTENTION_MODE, no_headroom)
        self.assertFalse(blocked['continuation_allowed'])
        self.assertEqual(blocked['reason'], 'workspace_quota_telemetry_unverified')

        scope_error = dict(observation, inventory_status='UNVERIFIABLE',
                           issues=[{'kind': 'ROOT_OR_TRAVERSAL_ERROR', 'detail': 'denied', 'known_race': False}],
                           issue_count=1)
        denied = runner.workspace_capacity_decision(scope_error, budget, runner.CONTENTION_MODE, sample)
        self.assertFalse(denied['continuation_allowed'])

    def test_query_latency_gate_preserves_role_labels_and_rejects_omission_traces(self) -> None:
        def report(role: str, p95_us: int) -> dict:
            return {
                'arm': role, 'reader_query_interval_ms': 25,
                'reader_query_max_samples_per_pass': 256, 'reader_query_queue_capacity': 8,
                'query_filter': '**/*.rs', 'query_route': 'FTS',
                'concurrent_query_latency_p95_comparable': True,
                'concurrent_query_total': 24, 'concurrent_query_offered_slots': 24,
                'concurrent_query_accepted_slots': 24, 'concurrent_query_started': 24,
                'concurrent_query_dropped_slots': 0,
                'concurrent_query_slots_not_offered_by_cap': 0,
                'concurrent_query_schedule_slots_missed': 0,
                'concurrent_query_failures': 0,
                'concurrent_query_sampling_capped_passes': 0,
                'concurrent_query_max_schedule_lag_us': 7_000,
                'concurrent_query_p95_us': p95_us,
            }

        native = {'role': 'native', 'workload_report': report('native', 105_000)}
        m4 = {'role': 'm4', 'workload_report': report('m4', 122_000)}
        gate = pair_runner.query_latency_pair_gate([m4, native])
        self.assertTrue(gate['comparable'])
        self.assertTrue(gate['offered_load_protocol_equal'])
        self.assertEqual(gate['p95_us_by_role'], {'native': 105_000, 'm4': 122_000})

        for key, value in (
            ('concurrent_query_dropped_slots', 1),
            ('concurrent_query_slots_not_offered_by_cap', 1),
            ('concurrent_query_schedule_slots_missed', 1),
            ('concurrent_query_failures', 1),
            ('concurrent_query_sampling_capped_passes', 1),
            ('concurrent_query_max_schedule_lag_us', 25_001),
            ('concurrent_query_total', 19),
        ):
            overloaded = report('m4', 122_000)
            overloaded[key] = value
            rejected = pair_runner.query_latency_pair_gate([
                {'role': 'native', 'workload_report': report('native', 105_000)},
                {'role': 'm4', 'workload_report': overloaded},
            ])
            self.assertFalse(rejected['comparable'], key)

        mismatched = report('m4', 122_000)
        mismatched['reader_query_queue_capacity'] = 4
        protocol_rejected = pair_runner.query_latency_pair_gate([
            native, {'role': 'm4', 'workload_report': mismatched},
        ])
        self.assertFalse(protocol_rejected['comparable'])
        self.assertIn('offered_load_protocol_mismatch', protocol_rejected['reasons'])

    def test_pair_continues_after_accounting_uncertainty_only_with_clean_hard_safety(self) -> None:
        receipt = {
            'status': 'COMPLETED_WITH_WORKSPACE_UNCERTAINTY',
            'workload_returncode': 0,
            'watchdog_abort': None,
            'safety_outcome': 'NO_OBSERVED_HARD_SAFETY_FAILURE_WORKSPACE_NOT_CERTIFIED',
            'watchdog_summary': {'hard_safety_telemetry_complete': True},
            'unit_cleanup': {'verified': True, 'unit_collected': True},
            'postflight_decision': {'continuation_allowed': True},
        }
        self.assertTrue(pair_runner.arm_result_allows_pair_continuation(receipt))
        receipt['workload_returncode'] = -15
        self.assertFalse(pair_runner.arm_result_allows_pair_continuation(receipt))
        receipt['workload_returncode'] = 0
        receipt['unit_cleanup']['verified'] = False
        self.assertFalse(pair_runner.arm_result_allows_pair_continuation(receipt))

    def test_terminal_classes_distinguish_oom_and_abort_types(self) -> None:
        self.assertEqual(runner.failure_class(reason='cgroup_oom_event'), 'OOM_ABORT')
        self.assertEqual(runner.failure_class(reason='runtime_timeout'), 'TIMEOUT_ABORT')
        self.assertEqual(runner.failure_class(reason='supervisor_lost'), 'CONTROLLER_CRASH_ABORT')
        status, reason = runner.contention_completion_status(
            'WATCHDOG_ABORT', True, True, 0, 'runtime_timeout')
        self.assertEqual((status, reason), ('WATCHDOG_ABORT', 'runtime_timeout'))
        self.assertEqual(runner.failure_class(status, reason, returncode=-15), 'TIMEOUT_ABORT')
        self.assertEqual(runner.failure_class('SIGNAL_EXIT', returncode=-15), 'SIGNAL_EXIT')

    def test_missing_host_pressure_and_memavailable_are_optional_observations(self) -> None:
        statvfs = type('StatVfs', (), {'f_bavail': 50_000_000, 'f_frsize': 4096})()
        with (
            mock.patch.object(runner, 'read_meminfo', return_value={'SwapFree_kib': 1024, 'SwapTotal_kib': 2048}),
            mock.patch.object(runner, 'parse_pressure', return_value=None),
            mock.patch.object(runner, 'read_vmstat', return_value={'pswpin_pages': 0, 'pswpout_pages': 0,
                                                                    'oom_kill': 0, 'pgscan_direct': 0}),
            mock.patch.object(runner, 'read_diskstats', return_value={'testdev': {'sectors_read': 0,
                'sectors_written': 0, 'io_ms': 0}}),
            mock.patch.object(runner.os, 'statvfs', return_value=statvfs),
        ):
            snapshot = runner.host_snapshot(self.fixture_root)
        self.assertIsNone(snapshot['io_pressure'])
        self.assertIsNone(snapshot['memory_pressure'])
        self.assertNotIn('MemAvailable_kib', snapshot['meminfo_kib'])
        decisions = runner.resource_guard_decisions(snapshot)
        self.assertFalse(any(item['enforced'] and item['status'] == 'FAIL' for item in decisions))

    def test_host_pressure_thresholds_are_observations_not_launch_gates(self) -> None:
        receipt_dir = self.output_root / 'pressure-observation-only'
        with tempfile.TemporaryDirectory(prefix='pressure-observation-fixture-', dir=self.output_root) as root:
            fixture_root = pathlib.Path(root)
            workspace_root = fixture_root / 'workspace'
            workspace_root.mkdir()
            marker = fixture_root / 'pressure-observation-launched'
            state = runner.host_snapshot(fixture_root)
            state['meminfo_kib']['MemAvailable_kib'] = 0
            state['resource_root_free_bytes'] = runner.GUARDS['var_tmp_free_min_bytes'] + 1024**3
            state['io_pressure']['full']['avg10'] = 99.0
            state['memory_pressure']['full']['avg10'] = 99.0
            state['loadavg'] = [999.0]
            with mock.patch.object(runner, 'host_snapshot', return_value=state):
                receipt = runner.run_arm(
                    ['/usr/bin/python3', '-c',
                     'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(2.0)', str(marker)],
                    receipt_dir, fixture_root, workspace_root, fixture_root, 5.0,
                    pressure_limits={
                        'swapfree_min_kib': 0, 'memory_some_avg10_max': 0.0,
                        'memory_full_avg10_max': 0.0,
                    },
                )
            self.assertTrue(receipt['benchmark_started'])
            self.assertTrue(marker.exists())
            self.assertFalse(any(g['status'] == 'FAIL' and g.get('enforced')
                                 for g in receipt['gate_decisions']))
            self.assertTrue(any(g['status'] == 'FAIL' and not g['enforced']
                                for g in receipt['gate_decisions']))
            self.assertTrue(receipt['unit_cleanup']['verified'])

    def test_pressure_baseline_requires_swap_floor_but_treats_psi_as_optional(self) -> None:
        with tempfile.TemporaryDirectory(prefix='pressure-baseline-optional-psi-') as root:
            baseline = pathlib.Path(root) / 'baseline.json'
            baseline.write_text('{"swapfree_min_kib": 0, "memory_some_avg10_max": null}')
            loaded = runner.load_pressure_baseline(baseline)
        self.assertEqual(loaded['swapfree_min_kib'], 0)
        self.assertIsNone(loaded['memory_some_avg10_max'])
        self.assertIsNone(loaded['memory_full_avg10_max'])
    def test_baseline_pressure_enforces_swap_capacity_but_not_host_psi(self) -> None:
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
        state['memory_pressure']['some']['avg10'] = 99.0
        state['memory_pressure']['full']['avg10'] = 99.0
        self.assertEqual(runner.pressure_baseline_failures(state, limits), [])

    def test_missing_telemetry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix='m4-cgroup-empty-', dir=self.output_root) as root, \
                self.assertRaisesRegex(RuntimeError, 'memory.current'):
            runner.validate_required_cgroup_metrics(pathlib.Path(root))

    def test_read_cgroup_metric_retries_process_exit_race(self) -> None:
        with tempfile.TemporaryDirectory(prefix='m4-cgroup-exit-race-', dir=self.output_root) as root:
            cgroup_root = pathlib.Path(root)
            cg = cgroup_root / 'cgroup'
            cg.mkdir()
            for name, value in {
                'cgroup.procs': '123\n', 'cgroup.events': 'populated 0\n',
                'memory.current': '0\n', 'memory.peak': '0\n', 'memory.events': 'oom 0\n',
                'memory.swap.current': '0\n', 'memory.swap.peak': '0\n',
                'memory.swap.events': 'oom 0\n', 'cpu.stat': 'usage_usec 0\n',
                'memory.pressure': '', 'io.pressure': '', 'cgroup.controllers': 'cpu memory pids\n',
            }.items():
                (cg / name).write_text(value)
            original_read_text = runner.read_text
            procs_reads = iter(('123\n', '', '', ''))

            def read_cgroup_file(path):
                if pathlib.Path(path) == cg / 'cgroup.procs':
                    return next(procs_reads)
                return original_read_text(path)

            original_path_read_text = pathlib.Path.read_text

            def read_proc_file(path, *args, **kwargs):
                if str(path) in ('/proc/123/io', '/proc/123/smaps_rollup'):
                    raise FileNotFoundError(str(path))
                return original_path_read_text(path, *args, **kwargs)

            with (
                mock.patch.object(runner, 'CGROOT', cgroup_root),
                mock.patch.object(runner, 'read_text', side_effect=read_cgroup_file),
                mock.patch.object(runner, 'proc_cgroup_contains', return_value=True),
                mock.patch.object(runner, 'parse_pressure', return_value=None),
                mock.patch.object(pathlib.Path, 'read_text', read_proc_file),
            ):
                metrics = runner.read_cgroup_metric(cg)

        self.assertEqual(metrics['pids'], [])
        self.assertTrue(metrics['process_io_sample_complete'])
        self.assertTrue(metrics['process_memory_sample_complete'])

    def test_read_cgroup_metric_stays_incomplete_for_unreadable_live_process(self) -> None:
        with tempfile.TemporaryDirectory(prefix='m4-cgroup-unreadable-', dir=self.output_root) as root:
            cgroup_root = pathlib.Path(root)
            cg = cgroup_root / 'cgroup'
            cg.mkdir()
            for name, value in {
                'cgroup.procs': '123\n', 'cgroup.events': 'populated 1\n',
                'memory.current': '0\n', 'memory.peak': '0\n', 'memory.events': 'oom 0\n',
                'memory.swap.current': '0\n', 'memory.swap.peak': '0\n',
                'memory.swap.events': 'oom 0\n', 'cpu.stat': 'usage_usec 0\n',
                'memory.pressure': '', 'io.pressure': '', 'cgroup.controllers': 'cpu memory pids\n',
            }.items():
                (cg / name).write_text(value)
            original_path_read_text = pathlib.Path.read_text

            def read_proc_file(path, *args, **kwargs):
                if str(path) in ('/proc/123/io', '/proc/123/smaps_rollup'):
                    raise FileNotFoundError(str(path))
                return original_path_read_text(path, *args, **kwargs)

            with (
                mock.patch.object(runner, 'CGROOT', cgroup_root),
                mock.patch.object(runner, 'proc_cgroup_contains', return_value=True),
                mock.patch.object(runner, 'parse_pressure', return_value=None),
                mock.patch.object(pathlib.Path, 'read_text', read_proc_file),
            ):
                metrics = runner.read_cgroup_metric(cg)

        self.assertFalse(metrics['process_io_sample_complete'])
        self.assertFalse(metrics['process_memory_sample_complete'])

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

    def test_proc_io_colon_keys_are_normalized_without_changing_cgroup_keys(self) -> None:
        self.assertEqual(runner.parse_kv('rchar: 12\nwchar: 34\nread_bytes: 4096\nwrite_bytes: 8192\ncancelled_write_bytes: 0\n'),
                         {'rchar':12,'wchar':34,'read_bytes':4096,'write_bytes':8192,'cancelled_write_bytes':0})
        self.assertEqual(runner.parse_kv('usage_usec 42\noom 0\nmax 0\n'),
                         {'usage_usec':42,'oom':0,'max':0})

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
        first=json.loads((directory/'runtime.jsonl').read_text().splitlines()[0])['cgroup']
        self.assertTrue(first['process_io_sample_complete'])
        self.assertTrue(first['process_memory_sample_complete'])
        self.assertEqual(runner.contention_runtime_failures(first,None),[])

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

    def test_operator_stop_is_recorded_before_owned_unit_cleanup(self) -> None:
        receipt_dir = self.output_root / 'operator-stop'
        outcome: dict[str, Any] = {}

        def run_arm() -> None:
            try:
                outcome['receipt'] = runner.run_arm(
                    [sys.executable, '-c', 'import time; time.sleep(20)'],
                    receipt_dir, self.fixture_root, self.fixture_root / 'workspace',
                    self.fixture_root.parent, 10.0, sample_interval_ms=20,
                    enforce_host_gates=False,
                )
            except BaseException as exc:
                outcome['error'] = exc

        thread = threading.Thread(target=run_arm, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5.0
        request = None
        while time.monotonic() < deadline and thread.is_alive():
            state_path = receipt_dir / 'run-state.json'
            if state_path.is_file():
                state = json.loads(state_path.read_text())
                if state.get('state') == 'delegated_and_armed':
                    config_path = receipt_dir / 'watchdog-config.json'
                    if config_path.is_file():
                        config = json.loads(config_path.read_text())
                        if (pathlib.Path(config['watchdog_ready_path']).is_file()
                                and pathlib.Path(config['start_marker']).is_file()):
                            with self.assertRaisesRegex(ValueError, 'confirmation unit'):
                                runner.request_operator_stop(receipt_dir, 'wrong-unit.service')
                            request = runner.request_operator_stop(receipt_dir, state['unit'])
                            break
            time.sleep(0.02)
        thread.join(timeout=10.0)
        self.assertFalse(thread.is_alive(), 'contained runner did not finish after operator stop')
        self.assertNotIn('error', outcome, repr(outcome.get('error')))
        self.assertIsNotNone(request, 'operation never reached the armed state')
        receipt = outcome['receipt']
        self.assertEqual(receipt['status'], 'WATCHDOG_ABORT')
        self.assertEqual(receipt['reason'], 'operator_requested_stop')
        self.assertEqual(receipt['failure_class'], 'OPERATOR_ABORT')
        self.assertEqual(receipt['safety_outcome'], 'ABORTED_CONTAINED')
        self.assertEqual(receipt['watchdog_abort']['operator_stop_request'], request)
        self.assertTrue(receipt['unit_cleanup']['verified'])
        self.assertTrue(receipt['unit_cleanup']['unit_collected'])

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
        observer = json.loads((directory / 'watchdog-summary.json').read_text())['observer']
        self.assertIsInstance(observer['sample_start_gap_max_ns'], int)
        self.assertGreaterEqual(observer['watchdog_loop_cpu_ns_sum'], 0)
        self.assertGreaterEqual(observer['watchdog_loop_work_ns_sum'], observer['watchdog_loop_cpu_ns_sum'])
        self.assertTrue(all('watchdog_loop_cpu_ns' in row and 'watchdog_loop_work_ns' in row for row in samples))
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
