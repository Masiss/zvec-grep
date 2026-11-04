#!/usr/bin/env python3
"""Deterministic real-filesystem races; no native workload or mocked scanner."""
import argparse
import importlib.util
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock

PATH = pathlib.Path(__file__).with_name('m4_containment_runner.py').resolve()
SPEC = importlib.util.spec_from_file_location('workspace_ct', PATH)
assert SPEC and SPEC.loader
ct = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ct
SPEC.loader.exec_module(ct)


class ScandirProxy:
    """Pause after real enumeration/stat; writer thread performs actual syscalls."""
    def __init__(self, iterator, trigger, after_stat):
        self.iterator = iterator
        self.trigger = trigger
        self.after_stat = after_stat

    def __enter__(self):
        self.iterator.__enter__()
        return self

    def __exit__(self, *args):
        return self.iterator.__exit__(*args)

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self.iterator)
        if self.after_stat:
            trigger = self.trigger
            class EntryProxy:
                name = entry.name
                def stat(self, **kwargs):
                    result = entry.stat(**kwargs)
                    trigger()
                    return result
            return EntryProxy()
        self.trigger()
        return entry


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m4-workspace-observer-')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.budget = {'workspace_max_allocated_bytes': 16384, 'workspace_max_apparent_bytes': 32768}

    def observe_race(self, mutation, after_stat=False):
        enumerated = threading.Event()
        finished = threading.Event()
        failures = []
        def writer():
            if not enumerated.wait(2):
                failures.append('enumeration rendezvous timeout')
                finished.set()
                return
            try:
                mutation()
            except BaseException as exc:
                failures.append(repr(exc))
            finally:
                finished.set()
        thread = threading.Thread(target=writer)
        thread.start()
        fired = False
        def trigger():
            nonlocal fired
            if not fired:
                fired = True
                enumerated.set()
                self.assertTrue(finished.wait(2), 'writer rendezvous timeout')
        real_scandir = os.scandir
        with mock.patch.object(ct.os, 'scandir', side_effect=lambda fd: ScandirProxy(real_scandir(fd), trigger, after_stat)):
            observation = ct.workspace_usage(self.root)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures, failures)
        self.assertTrue(fired)
        self.assertFalse(observation['scan_complete'])
        self.assertFalse(observation['complete_snapshot'])
        self.assertIsNone(observation['verified_peak'])
        self.assertIsNone(observation['independent_capacity_upper_bound'])
        self.assertFalse(ct.workspace_capacity_decision(observation, self.budget)['continuation_allowed'])
        return observation

    def test_stable_traversal_is_not_snapshot_or_proof(self):
        (self.root/'file').write_bytes(b'x'*16)
        row = ct.workspace_usage(self.root)
        self.assertTrue(row['scan_complete'])
        self.assertEqual(row['inventory_status'], 'COMPLETE_TRAVERSAL_NOT_ATOMIC')
        self.assertFalse(row['complete_snapshot'])
        self.assertIsNone(row['independent_capacity_upper_bound'])
        self.assertEqual(ct.workspace_capacity_decision(row, self.budget)['state'], 'SAMPLED_NOT_EXCEEDED')
        self.assertEqual(ct.workspace_reservation_credit(row), 0)

    def test_atomic_create_rename(self):
        p = self.root/'payload.tmp.123'
        p.write_bytes(b'a')
        row = self.observe_race(lambda: p.replace(self.root/'payload'))
        self.assertEqual(row['inventory_status'], 'PARTIAL_KNOWN_RACE')
        self.assertTrue(any(i['kind']=='ENTRY_DISAPPEARED' for i in row['issues']))

    def test_temporary_unlink(self):
        p = self.root/'payload.tmp'
        p.touch()
        self.observe_race(p.unlink)

    def test_replacement_even_without_enoent(self):
        (self.root/'target').write_bytes(b'old')
        def replace():
            p = self.root/'replacement.tmp'
            p.write_bytes(b'new'*1024)
            p.replace(self.root/'target')
        row = self.observe_race(replace, after_stat=True)
        self.assertTrue(any(i['kind']=='DIRECTORY_ACTIVITY' for i in row['issues']))

    def test_directory_removal(self):
        p = self.root/'directory'
        p.mkdir()
        self.observe_race(p.rmdir)

    def test_create_after_enumeration(self):
        (self.root/'trigger').touch()
        self.observe_race(lambda: (self.root/'late-file').write_bytes(b'late'), after_stat=True)

    def test_growing_file_after_stat(self):
        p = self.root/'growing'
        p.write_bytes(b'x')
        self.observe_race(lambda: p.write_bytes(b'y'*65536), after_stat=True)

    def test_sparse_apparent_not_bounded_by_allocated_or_io(self):
        p = self.root/'sparse'
        with p.open('wb') as stream:
            stream.truncate(8*1024*1024)
        row = ct.workspace_usage(self.root)
        self.assertGreater(row['apparent_bytes'], row['allocated_st_blocks_bytes_gross'])
        decision = ct.workspace_capacity_decision(row, self.budget)
        self.assertFalse(decision['continuation_allowed'])
        self.assertEqual(decision['reason'], 'workspace_apparent_watchdog')

    def test_concurrent_allocation_near_cap(self):
        p = self.root/'growing'
        p.write_bytes(b'x'*8192)
        def grow():
            with p.open('ab') as stream:
                stream.write(b'y'*65536)
                stream.flush()
                os.fsync(stream.fileno())
        self.observe_race(grow, after_stat=True)
        row = ct.workspace_usage(self.root)
        self.assertFalse(ct.workspace_capacity_decision(row, self.budget)['continuation_allowed'])

    def test_permission_error_is_not_enoent(self):
        p = self.root/'denied'
        p.mkdir(mode=0)
        try:
            row = ct.workspace_usage(self.root)
        finally:
            p.chmod(0o700)
        self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')
        self.assertFalse(ct.workspace_capacity_decision(row, self.budget)['continuation_allowed'])
        self.assertTrue(any('Permission denied' in i['detail'] for i in row['issues']))

    def test_missing_root_never_empty_complete(self):
        row = ct.workspace_usage(self.root/'absent')
        self.assertFalse(row['scan_complete'])
        self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')

    def test_symlink_not_followed(self):
        (self.root/'outside').symlink_to('/proc')
        row = ct.workspace_usage(self.root)
        self.assertTrue(row['scan_complete'])
        self.assertEqual(row['symlink_count'], 1)
        self.assertEqual(row['file_count'], 0)

    def test_entries_depth_and_deadline_are_bounded_no_retry(self):
        for i in range(8):
            (self.root/str(i)).touch()
        policy = dict(ct.BENCHMARK_POLICY['workspace_observer'], max_entries_per_scan=2)
        with mock.patch.dict(ct.BENCHMARK_POLICY, workspace_observer=policy):
            row = ct.workspace_usage(self.root)
        self.assertLessEqual(row['entries_observed'], 2)
        self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')
        policy = dict(policy, scan_time_budget_seconds=0)
        with mock.patch.dict(ct.BENCHMARK_POLICY, workspace_observer=policy):
            row = ct.workspace_usage(self.root)
        self.assertEqual(row['entries_observed'], 0)
        self.assertFalse(row['scan_complete'])
        d = self.root/'dir'
        d.mkdir()
        (d/'nested').mkdir()
        policy = dict(ct.BENCHMARK_POLICY['workspace_observer'], max_depth=0)
        with mock.patch.dict(ct.BENCHMARK_POLICY, workspace_observer=policy):
            row = ct.workspace_usage(self.root)
        self.assertFalse(row['scan_complete'])

    def test_replaced_root_loses_scope(self):
        (self.root/'trigger').touch()
        displaced = self.root.parent/(self.root.name+'-displaced')
        def replace_root():
            self.root.rename(displaced)
            self.root.mkdir()
        try:
            row = self.observe_race(replace_root)
            self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')
        finally:
            if displaced.exists():
                shutil.rmtree(displaced)

    def test_eio_is_fatal_not_known_race(self):
        with mock.patch.object(ct.os, 'scandir', side_effect=OSError(5,'injected EIO')):
            row = ct.workspace_usage(self.root)
        self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')
        self.assertFalse(ct.workspace_capacity_decision(row, self.budget)['continuation_allowed'])

    def test_event_overflow_and_truncation_do_not_certify(self):
        for data in (struct.pack('iIII',-1,0x4000,0,0), struct.pack('iIII',1,0x100,0,32)):
            with self.subTest(data=data), mock.patch.object(ct.os,'read',return_value=data):
                row = ct.workspace_usage(self.root)
            self.assertFalse(row['scan_complete'])
            self.assertEqual(row['inventory_status'], 'UNVERIFIABLE')

    def test_malformed_counter_and_json_fail_closed(self):
        row = ct.workspace_usage(self.root)
        for invalid in (True,None,-1,float('nan')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ct.workspace_capacity_decision(dict(row,apparent_bytes=invalid),self.budget)
        with self.assertRaises(ValueError):
            ct.strict_json_text('{bad')

    def test_partial_totals_and_forged_bound_cannot_authorize(self):
        row = ct.workspace_usage(self.root/'absent')
        row['independent_capacity_upper_bound'] = {'allocated':0,'apparent':0,'verified':True}
        decision = ct.workspace_capacity_decision(row, self.budget)
        self.assertFalse(decision['continuation_allowed'])
        self.assertIsNone(decision['independent_capacity_upper_bound'])
        self.assertEqual(ct.workspace_reservation_credit(row), 0)


class ContainmentTests(unittest.TestCase):
    def test_real_unknown_and_limit_abort_cleanup(self):
        os.environ['M4_TEST_FIXTURE_MODE'] = '1'
        out = pathlib.Path(os.environ['M4_WORKSPACE_TEST_OUTPUT'])
        root = pathlib.Path(tempfile.mkdtemp(prefix='m4-workspace-contained-'))
        budget = {'approval_reference':'OWNER-APPROVED SYNTHETIC OBSERVER REPAIR ONLY',
                  'memory_max_bytes':1073741824,'memory_swap_max_bytes':0,'cpu_max':'200000 100000','pids_max':128,
                  'workspace_max_allocated_bytes':4096,'workspace_max_apparent_bytes':4096}
        try:
            for name, operation, reason in (
                ('permission', 'pathlib.Path("denied").mkdir(mode=0)', 'workspace_capacity_unverifiable'),
                ('limit', 'pass', 'workspace_allocated_watchdog')):
                work = root/name
                work.mkdir()
                if name=='limit':
                    with (work/'big').open('wb') as stream:
                        stream.write(b'x'*65536)
                        stream.flush()
                        os.fsync(stream.fileno())
                code = 'import pathlib,threading; '+operation+'; threading.Event().wait(5)'
                if name == 'limit':
                    with self.assertRaisesRegex(RuntimeError, 'did not establish baseline telemetry'):
                        ct.run_arm([sys.executable,'-c',code],out/name,root,work,work,5,
                            sample_interval_ms=20,budget=budget,enforce_host_gates=False)
                    receipt = ct.strict_json_file(out/name/'receipt.json')
                else:
                    receipt = ct.run_arm([sys.executable,'-c',code],out/name,root,work,work,5,
                        sample_interval_ms=20,budget=budget,enforce_host_gates=False)
                self.assertEqual(receipt['status'], 'WATCHDOG_ABORT' if name=='permission' else 'LAUNCH_FAILED')
                self.assertTrue(receipt['unit_cleanup']['verified'])
                summary = ct.strict_json_file(out/name/'watchdog-summary.json')
                self.assertEqual(summary['abort']['reason'], reason)
                if name=='permission':
                    self.assertEqual(receipt['measurement_quality'], 'INCOMPLETE')
                else:
                    self.assertFalse(receipt['benchmark_started'])
                    self.assertEqual(summary['abort']['state'],'OBSERVED_LIMIT_EXCEEDED')
                if name=='permission':
                    self.assertIsNone(summary['peak_sampled_workspace_apparent_bytes'])
                    self.assertIsNone(summary['workspace_verified_peak'])
                state = ct.read_text(pathlib.Path('/proc/self/cgroup'))
                self.assertTrue(state)
                print('REAL_OBSERVER_FIXTURE',name,receipt['status'],summary['abort']['reason'],receipt['unit'],flush=True)
                denied = work/'denied'
                if denied.exists():
                    denied.chmod(0o700)
        finally:
            denied = root/'permission/denied'
            if denied.exists():
                denied.chmod(0o700)
            units = ct.subprocess.check_output(['systemctl','--user','list-units','m4-contained-*','--all','--no-legend','--no-pager'],text=True)
            if not units.strip():
                shutil.rmtree(root)
            else:
                print('RETAINED_WORKSPACE_UNTIL_CLEANUP_VERIFIED',root,flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=pathlib.Path, required=True)
    parser.add_argument('--containment', action='store_true')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    os.environ['M4_WORKSPACE_TEST_OUTPUT'] = str(args.output_dir.resolve())
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceTests)
    if args.containment:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ContainmentTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
