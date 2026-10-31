import contextlib
import copy
import io
import pathlib
import tempfile
import unittest
from unittest import mock

import m4_containment_runner as containment
import run_observer_overhead_diagnostic as diagnostic
import observer_overhead_fixture as fixture


def proof():
    q = diagnostic.QUOTA
    return {'status': 'VERIFIED', 'attestation_path': q['attestation_path'],
            'workspace_root': q['workspace_subvolume'], 'filesystem_uuid': q['filesystem_uuid'],
            'subvolume_id': q['subvolume_id'], 'qgroup_id': q['qgroup_id'],
            'max_referenced_bytes': q['max_referenced_bytes'], 'accounting_mode': q['accounting_mode'],
            'attestation_sha256': 'a' * 64,
            'live_headroom': {'required_free_bytes': q['minimum_free_bytes'],
                              'available_bytes': q['max_referenced_bytes']}}


class ObserverDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.hash_mock = mock.patch.object(containment, 'file_sha256', return_value='b' * 64)
        self.quota_mock = mock.patch.object(containment, 'verify_workspace_quota', return_value=proof())
        self.hash_mock.start()
        self.quota_mock.start()
        self.addCleanup(self.hash_mock.stop)
        self.addCleanup(self.quota_mock.stop)

    def context(self):
        return diagnostic.make_context(proof())

    def test_idle_worker_is_fixed_and_rejects_non_operation_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            workspace = root / fixture.OPERATION / 'diagnostic' / 'workspace'
            workspace.mkdir(parents=True)
            with mock.patch.object(fixture, 'ROOT', root), \
                 mock.patch.object(fixture.sys, 'argv', ['fixture', str(workspace)]), \
                 mock.patch.object(fixture.threading, 'Event') as event, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fixture.main(), 0)
                event.return_value.wait.assert_called_once_with(8.0)
            self.assertEqual(list(workspace.iterdir()), [])
            with mock.patch.object(fixture, 'ROOT', root), \
                 mock.patch.object(fixture.sys, 'argv', ['fixture', str(root)]), \
                 self.assertRaises(ValueError):
                fixture.main()

    def test_exact_contract_and_all_lifecycle_resource_limits(self):
        context = self.context()
        containment.validate_contention_context(context)
        original = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['resource_controls']
        for key, value in original.items():
            if key != 'runtime_limit_seconds':
                self.assertEqual(context[key], value, key)
        self.assertEqual(context['runtime_limit_seconds'], 20)
        self.assertEqual(containment.registered_workspace_quota_contract(
            containment.contention_budget(context)), diagnostic.QUOTA)
        for field, value in [('operation_id', 'm4-observer-diagnostic-other'),
                             ('sample_binary_path', '/tmp/unapproved'), ('sample_library_sha256', 'wrong'),
                             ('workspace_root', '/tmp/unapproved'), ('runtime_limit_seconds', 21),
                             ('memory_swap_max_bytes', 1)]:
            bad = copy.deepcopy(context)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                containment.validate_contention_context(bad)

    def test_watchdog_binding_rejects_altered_interval_or_override(self):
        context = self.context()
        config = {'policy_mode': containment.CONTENTION_MODE, 'policy_context': context,
                  'arm_name': 'diagnostic', 'watchdog_output': str(diagnostic.RECEIPT),
                  'workspace_root': str(diagnostic.WORKSPACE), 'resource_root': str(diagnostic.ROOT),
                  'sample_interval_ms': 500, 'enforce_host_gates': True,
                  'budget': containment.contention_budget(context), 'runtime_limit_seconds': 20,
                  'policy_context_digest': containment.contention_context_digest(context)}
        containment.validate_watchdog_operation_binding(config)
        for field, value in [('sample_interval_ms', 250), ('workspace_root', '/tmp/other'),
                             ('arm_name', 'pair-first'), ('enforce_host_gates', False)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                containment.validate_watchdog_operation_binding(dict(config, **{field: value}))

    def test_wrong_command_cannot_create_receipt(self):
        context = self.context()
        with mock.patch.object(containment, 'make_receipt_dir') as make, self.assertRaises(ValueError):
            containment.run_arm(['/bin/true'], diagnostic.RECEIPT, diagnostic.ROOT,
                                diagnostic.WORKSPACE, diagnostic.WORKSPACE, 20,
                                sample_interval_ms=500, budget=containment.contention_budget(context),
                                env=None, policy_context=context)
        make.assert_not_called()

    def test_missing_or_bad_timing_is_inconclusive(self):
        valid = {'policy_decision_cost': {'wall_ns': 30, 'cpu_ns': 20},
                 'watchdog_loop_work_ns': 100, 'watchdog_loop_cpu_ns': 70}
        result = diagnostic.analyze([valid])
        self.assertEqual(result['classification'], 'DESCRIPTIVE_ONLY')
        self.assertEqual(result['rest_wall_ns_total'], 70)
        self.assertEqual(result['rest_cpu_ns_total'], 50)
        self.assertEqual(diagnostic.analyze([])['classification'], 'INCONCLUSIVE')
        self.assertEqual(diagnostic.analyze([dict(valid, watchdog_loop_work_ns=1)])['classification'], 'INCONCLUSIVE')
        self.assertEqual(diagnostic.analyze([{'watchdog_loop_work_ns': 1}])['classification'], 'INCONCLUSIVE')

    def test_quota_or_artifact_change_fails_closed(self):
        context = self.context()
        with mock.patch.object(containment, 'verify_workspace_quota', side_effect=ValueError('stale')):
            with self.assertRaisesRegex(ValueError, 'stale'):
                containment.validate_contention_context(context)
        with mock.patch.object(containment, 'file_sha256', return_value='c' * 64):
            with self.assertRaises(ValueError):
                containment.validate_contention_context(context)


if __name__ == '__main__':
    unittest.main()
