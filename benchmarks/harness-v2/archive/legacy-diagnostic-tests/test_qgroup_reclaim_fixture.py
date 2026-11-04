import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import qgroup_reclaim_fixture as fixture


class FixtureTests(unittest.TestCase):
    def test_policy_preserves_existing_limits_and_diagnostic_scope(self):
        policy_path = pathlib.Path(__file__).resolve().parents[1] / 'docs/benchmark-policy.json'
        policy = json.loads(policy_path.read_text())
        contract = policy['qgroup_reclaim_diagnostic']
        lifecycle = policy['rust_pipeline_lifecycle_pair']
        self.assertEqual(contract['purpose'], 'm4-qgroup-reclaim-diagnostic')
        self.assertEqual(contract['operation_id'], 'm4-qgroup-diagnostic-20261028t033016z')
        self.assertEqual(contract['resource_root'], lifecycle['workspace_quota']['workspace_subvolume'])
        self.assertEqual(contract['fixture_bytes'], fixture.FIXTURE_BYTES)
        self.assertEqual(contract['sample_interval_ms'], 1000)
        self.assertEqual(contract['resource_controls']['runtime_limit_seconds'], 60)
        for key, value in lifecycle['resource_controls'].items():
            if key != 'runtime_limit_seconds':
                self.assertEqual(contract['resource_controls'][key], value, key)

    def test_events_and_operation_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                fixture.run_fixture(workspace, 4096, 0)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([row['event'] for row in rows], [
                'create_before', 'create_after', 'write_before', 'write_after',
                'close_before', 'close_after', 'unlink_before', 'unlink_after'])
            self.assertEqual(sorted(row['monotonic_ns'] for row in rows),
                             [row['monotonic_ns'] for row in rows])
            self.assertEqual(list(workspace.iterdir()), [])

    def test_existing_file_is_never_overwritten_or_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory)
            path = workspace / 'fixture.bin'
            path.write_bytes(b'preserve')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
                fixture.run_fixture(workspace, 4, 0)
            self.assertEqual(path.read_bytes(), b'preserve')

    def test_write_failure_cleans_only_owned_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory)
            with mock.patch.object(fixture.os, 'write', side_effect=OSError('write failure')), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
                fixture.run_fixture(workspace, 4, 0)
            self.assertEqual(list(workspace.iterdir()), [])

    def test_main_accepts_only_registered_operation_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            good = root / 'm4-qgroup-diagnostic-fixture' / 'diagnostic' / 'workspace'
            bad = root / 'other-operation' / 'diagnostic' / 'workspace'
            good.mkdir(parents=True)
            bad.mkdir(parents=True)
            with mock.patch.object(fixture, 'RESOURCE_ROOT', root), \
                 mock.patch.object(fixture, 'run_fixture') as run, \
                 mock.patch.object(fixture.threading, 'Event') as event, \
                 mock.patch.object(fixture.signal, 'signal'), \
                 mock.patch.object(fixture.sys, 'argv', ['worker', str(good)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fixture.main(), 0)
                run.assert_called_once_with(good)
                event.return_value.wait.assert_called_once()
            with mock.patch.object(fixture, 'RESOURCE_ROOT', root), \
                 mock.patch.object(fixture.sys, 'argv', ['worker', str(bad)]), \
                 self.assertRaises(ValueError):
                fixture.main()

    def test_excessive_size_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = pathlib.Path(directory)
            with self.assertRaises(ValueError):
                fixture.run_fixture(workspace, fixture.FIXTURE_BYTES + 1)
            self.assertEqual(list(workspace.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
