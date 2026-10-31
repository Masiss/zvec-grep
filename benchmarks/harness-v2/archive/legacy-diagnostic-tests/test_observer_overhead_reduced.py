import copy
import pathlib
import unittest
from unittest import mock

import m4_containment_runner as containment
import run_observer_overhead_reduced as diagnostic
from test_observer_overhead_diagnostic import proof


class ReducedObserverTests(unittest.TestCase):
    def digest(self, path):
        if pathlib.Path(path) == diagnostic.BINARY:
            return diagnostic.CONTRACT['sample_binary_sha256']
        if pathlib.Path(path) == diagnostic.LIBRARY:
            return diagnostic.CONTRACT['sample_library_sha256']
        return 'b' * 64

    def test_exact_registration_and_fail_closed_pins(self):
        with mock.patch.object(containment, 'file_sha256', side_effect=self.digest), \
             mock.patch.object(containment, 'verify_workspace_quota', return_value=proof()):
            context = diagnostic.make_context(proof())
            containment.validate_contention_context(context)
            controls = containment.BENCHMARK_POLICY['rust_pipeline_lifecycle_pair']['resource_controls']
            for key, value in controls.items():
                if key != 'runtime_limit_seconds':
                    self.assertEqual(context[key], value)
            self.assertEqual(context['runtime_limit_seconds'], 20)
            for field in ('sample_binary_sha256', 'sample_library_sha256', 'operation_id'):
                changed = copy.deepcopy(context)
                changed[field] = 'wrong'
                with self.subTest(field=field), self.assertRaises(ValueError):
                    containment.validate_contention_context(changed)
            config = {'policy_mode': containment.CONTENTION_MODE, 'policy_context': context,
                      'arm_name': 'diagnostic', 'watchdog_output': str(diagnostic.RECEIPT),
                      'workspace_root': str(diagnostic.WORKSPACE), 'resource_root': str(diagnostic.ROOT),
                      'sample_interval_ms': 500, 'enforce_host_gates': True,
                      'budget': containment.contention_budget(context), 'runtime_limit_seconds': 20,
                      'policy_context_digest': containment.contention_context_digest(context)}
            containment.validate_watchdog_operation_binding(config)
            with self.assertRaises(ValueError):
                containment.validate_watchdog_operation_binding(dict(config, sample_interval_ms=250))

    def test_process_snapshot_has_self_affinity_and_resource_fields(self):
        sample = containment.process_resource_sample(__import__('os').getpid())
        self.assertIn('Cpus_allowed_list', sample['status'])
        self.assertIn('VmRSS', sample['status'])
        self.assertGreaterEqual(sample['utime_ticks'], 0)
        self.assertGreaterEqual(sample['stime_ticks'], 0)
        snapshot = containment.host_process_snapshot()
        self.assertTrue(snapshot['controller_affinity'])
        self.assertTrue(snapshot['top_processes'])


if __name__ == '__main__':
    unittest.main()
