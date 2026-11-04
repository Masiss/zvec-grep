import copy
import pathlib
import unittest
from unittest import mock

import m4_containment_runner as containment
import run_qgroup_reclaim_diagnostic as diagnostic


def proof():
    q = diagnostic.QUOTA
    return {'status': 'VERIFIED', 'attestation_path': q['attestation_path'],
            'workspace_root': q['workspace_subvolume'], 'filesystem_uuid': q['filesystem_uuid'],
            'subvolume_id': q['subvolume_id'], 'qgroup_id': q['qgroup_id'],
            'max_referenced_bytes': q['max_referenced_bytes'], 'accounting_mode': q['accounting_mode'],
            'attestation_sha256': 'a' * 64,
            'live_headroom': {'required_free_bytes': q['minimum_free_bytes'],
                              'available_bytes': q['max_referenced_bytes']}}


class DiagnosticTests(unittest.TestCase):
    def context(self):
        return diagnostic.make_context(proof())

    def test_exact_context_and_quota_binding(self):
        context = self.context()
        containment.validate_contention_context(context)
        self.assertEqual(containment.registered_workspace_quota_contract(
            containment.contention_budget(context)), diagnostic.QUOTA)
        for field, value in [('operation_id', 'm4-qgroup-diagnostic-other'),
                             ('workspace_root', '/tmp/other'), ('runtime_limit_seconds', 61),
                             ('binary_path', '/tmp/other'), ('memory_max_bytes', 1),
                             ('approval_reference', 'other')]:
            bad = copy.deepcopy(context)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises((ValueError, FileNotFoundError)):
                containment.validate_contention_context(bad)
        bad = copy.deepcopy(context)
        bad['workspace_quota_proof']['qgroup_id'] = '0/258'
        with self.assertRaises(ValueError):
            containment.validate_contention_context(bad)

    def test_watchdog_requires_single_sampler_and_exact_workspace(self):
        context = self.context()
        config = {'policy_mode': containment.CONTENTION_MODE, 'policy_context': context,
                  'arm_name': 'diagnostic', 'watchdog_output': str(diagnostic.RECEIPT),
                  'workspace_root': str(diagnostic.WORKSPACE), 'resource_root': str(diagnostic.ROOT),
                  'sample_interval_ms': 1000, 'enforce_host_gates': True,
                  'budget': containment.contention_budget(context), 'runtime_limit_seconds': 60,
                  'policy_context_digest': containment.contention_context_digest(context)}
        containment.validate_watchdog_operation_binding(config)
        for field, value in [('sample_interval_ms', 500), ('workspace_root', '/tmp/other'),
                             ('arm_name', 'pair-first'), ('enforce_host_gates', False)]:
            bad = dict(config, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                containment.validate_watchdog_operation_binding(bad)

    def test_wrong_command_rejected_before_receipt_or_workspace_creation(self):
        context = self.context()
        with mock.patch.object(containment, 'make_receipt_dir') as make, self.assertRaises(ValueError):
            containment.run_arm(['/bin/true'], diagnostic.RECEIPT, diagnostic.ROOT,
                                diagnostic.WORKSPACE, diagnostic.WORKSPACE, 60,
                                sample_interval_ms=1000, budget=containment.contention_budget(context),
                                env=None, policy_context=context)
        make.assert_not_called()

    def test_fixed_schedule_skips_missed_slots_and_enforces_minimum_spacing(self):
        deadline = containment.qgroup_diagnostic_next_deadline
        self.assertEqual(deadline(0, 120, 200), 1_000_000_120)
        self.assertEqual(deadline(0, 120, 3_200_000_000), 4_000_000_000)
        self.assertEqual(deadline(0, 1_200_000_000, 1_300_000_000), 2_200_000_000)
        self.assertGreaterEqual(deadline(0, 1_200_000_000, 2_000_000_000), 2_200_000_000)

    def test_analysis_rejects_incomplete_or_too_frequent_samples(self):
        names = ['create_before', 'create_after', 'write_before', 'write_after',
                 'close_before', 'close_after', 'unlink_before', 'unlink_after',
                 'observation_start', 'observation_end']
        events = [{'event': name, 'monotonic_ns': (i + 1) * 100_000_000} for i, name in enumerate(names)]
        row = lambda t: {'workspace_quota_sample_monotonic_ns': t,
                         'workspace_quota': {'status': 'PASS', 'referenced_bytes': 1}}
        complete = diagnostic.analyze([row(700_000_000), row(1_700_000_000)], events)
        self.assertEqual(complete['classification'], 'INCONCLUSIVE')
        self.assertTrue(complete['observation_complete'])
        self.assertFalse(diagnostic.analyze([row(700_000_000), row(999_999_999)], events)['observation_complete'])
        self.assertEqual(diagnostic.analyze([row(700_000_000)], events)['classification'], 'INCONCLUSIVE')
        self.assertEqual(diagnostic.analyze([row(700_000_000), row(1_700_000_000)], events[:-1])['classification'], 'INCONCLUSIVE')

    def test_preflight_rejects_insufficient_quota_before_host_or_fixture(self):
        low = proof()
        low['live_headroom']['available_bytes'] = low['live_headroom']['required_free_bytes']
        with mock.patch.object(diagnostic.OPERATION.__class__, 'exists', return_value=False), \
             mock.patch.object(diagnostic.containment, 'verify_workspace_quota', return_value=low), \
             mock.patch.object(diagnostic.containment, 'host_snapshot') as host, \
             self.assertRaisesRegex(RuntimeError, 'insufficient qgroup'):
            diagnostic.preflight()
        host.assert_not_called()


if __name__ == '__main__':
    unittest.main()
