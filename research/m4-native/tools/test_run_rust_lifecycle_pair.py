#!/usr/bin/env python3
"""Regression tests for Gate B lifecycle pair failure classification."""
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SCRIPT = HERE / "run_rust_lifecycle_pair.py"
spec = importlib.util.spec_from_file_location("m4_rust_lifecycle_pair", SCRIPT)
assert spec and spec.loader
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)

REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / 'benchmarks' / 'harness-v2'))
import importlib as _importlib
config = _importlib.import_module('operations.m4.rust_lifecycle.config')
context = _importlib.import_module('operations.m4.rust_lifecycle.context')
executor = _importlib.import_module('operations.m4.rust_lifecycle.executor')
preflight = _importlib.import_module('operations.m4.rust_lifecycle.preflight')


class LifecyclePairFailureClassificationTests(unittest.TestCase):
    @staticmethod
    def query_arm(role, **overrides):
        report = {
            'arm': role,
            'concurrent_query_latency_p95_comparable': True,
            'concurrent_query_total': 20,
            'concurrent_query_offered_slots': 20,
            'concurrent_query_accepted_slots': 20,
            'concurrent_query_started': 20,
            'concurrent_query_dropped_slots': 0,
            'concurrent_query_slots_not_offered_by_cap': 0,
            'concurrent_query_schedule_slots_missed': 0,
            'concurrent_query_failures': 0,
            'concurrent_query_sampling_capped_passes': 0,
            'concurrent_query_max_schedule_lag_us': 1_000,
            'reader_query_interval_ms': 25,
            'reader_query_max_samples_per_pass': 64,
            'reader_query_queue_capacity': 8,
            'query_filter': '**/*.rs',
            'query_route': 'fts',
            'concurrent_query_p95_us': 120_000 if role == 'native' else 125_000,
        }
        report.update(overrides)
        return {'role': role, 'workload_report': report}

    def test_query_gate_accepts_matched_complete_synthetic_arms_regardless_of_order(self):
        native = self.query_arm('native')
        m4 = self.query_arm('m4')
        gate = lifecycle.query_latency_pair_gate([m4, native])
        self.assertTrue(gate['comparable'])
        self.assertEqual(gate['reasons'], [])
        self.assertEqual(gate['p95_us_by_role'], {'native': 120_000, 'm4': 125_000})

    def test_query_gate_rejects_drops_missed_starts_and_excess_scheduler_lag(self):
        native = self.query_arm('native')
        m4 = self.query_arm(
            'm4',
            concurrent_query_dropped_slots=1,
            concurrent_query_schedule_slots_missed=1,
            concurrent_query_max_schedule_lag_us=25_001,
        )
        gate = lifecycle.query_latency_pair_gate([native, m4])
        self.assertFalse(gate['comparable'])
        self.assertIn('m4:offers_dropped_or_capped', gate['reasons'])
        self.assertIn('m4:scheduled_starts_missed', gate['reasons'])
        self.assertIn('m4:scheduler_lag_exceeds_one_interval', gate['reasons'])

    def test_query_gate_rejects_missing_p95_and_missing_or_invalid_protocol(self):
        for key, value in [('concurrent_query_p95_us', None), ('concurrent_query_p95_us', -1),
                           ('reader_query_interval_ms', 0), ('reader_query_interval_ms', True),
                           ('reader_query_max_samples_per_pass', None), ('reader_query_queue_capacity', 0),
                           ('query_filter', None), ('query_route', '')]:
            native, m4 = self.query_arm('native'), self.query_arm('m4')
            native['workload_report'][key] = value
            m4['workload_report'][key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(lifecycle.query_latency_pair_gate([native, m4])['comparable'])
        native, m4 = self.query_arm('native'), self.query_arm('m4')
        for arm in (native, m4):
            del arm['workload_report']['query_filter']
        self.assertFalse(lifecycle.query_latency_pair_gate([native, m4])['comparable'])

    def test_pair_artifact_hashes_stream_without_read_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'binary'
            library = root / 'libzvec_c_api.so'
            binary_bytes = b'binary-data' * 100_000
            library_bytes = b'library-data' * 10_000
            binary.write_bytes(binary_bytes)
            library.write_bytes(library_bytes)
            with mock.patch.object(pathlib.Path, 'read_bytes', side_effect=AssertionError('whole-file read forbidden')):
                artifacts = lifecycle.capture_pair_artifacts(binary, root)
                lifecycle.verify_pair_artifacts(artifacts)
            self.assertEqual(artifacts['binary_sha256'], hashlib.sha256(binary_bytes).hexdigest())
            self.assertEqual(artifacts['library_sha256'], hashlib.sha256(library_bytes).hexdigest())

    def test_pair_artifacts_detect_binary_and_library_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'binary'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary1')
            library.write_bytes(b'library1')
            artifacts = lifecycle.capture_pair_artifacts(binary, root)
            lifecycle.verify_pair_artifacts(artifacts)
            for path in (binary, library):
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'identity changed'):
                    lifecycle.verify_pair_artifacts(artifacts)
                path.write_bytes(original)
            library.unlink()
            with self.assertRaises(FileNotFoundError):
                lifecycle.verify_pair_artifacts(artifacts)

    def test_binary_discovery_uses_policy_compliant_m4_target_dir(self):
        expected_target = lifecycle.REPO / "research/m4-native/.work/build/rust-bridge-target"
        self.assertEqual(lifecycle.RUST_TARGET_DIR, expected_target)
        with tempfile.TemporaryDirectory() as temp_dir:
            target = pathlib.Path(temp_dir)
            binary = target / "debug/deps/zg_engine-fixture"
            library = target / "debug/build/zvec-rust-sys-fixture/out/zvec-prebuilt/libzvec_c_api.so"
            binary.parent.mkdir(parents=True)
            library.parent.mkdir(parents=True)
            binary.write_text("test binary fixture")
            binary.chmod(0o755)
            library.write_bytes(b"fixture")
            listing = mock.Mock(returncode=0, stdout=f"{lifecycle.TEST_NAME}: test\\n")
            with mock.patch.object(config, "RUST_TARGET_DIR", target), mock.patch.object(
                context.subprocess, "run", return_value=listing
            ):
                self.assertEqual(context.find_test_binary(), (binary.resolve(), library.parent.resolve()))

    def test_library_override_is_exact_and_confined_to_target_build_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = pathlib.Path(temp_dir)
            binary = target / 'debug/deps/zg_engine-fixture'
            library_dir = target / 'debug/build/zvec-rust-sys-custom/out/zvec-prebuilt'
            library = library_dir / 'libzvec_c_api.so'
            for path in (binary, library):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            binary.chmod(0o755)
            listing = mock.Mock(returncode=0, stdout=f'{lifecycle.TEST_NAME}: test\\n')
            with mock.patch.object(config, 'RUST_TARGET_DIR', target), mock.patch.object(
                context.subprocess, 'run', return_value=listing
            ):
                self.assertEqual(context.find_test_binary(None, library_dir),
                                 (binary.resolve(), library_dir.resolve()))
                with self.assertRaisesRegex(ValueError, 'exact artifact under debug/build'):
                    context.find_test_binary(None, target)
                symlink = target / 'debug/build/library-link'
                symlink.symlink_to(library_dir, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'must not be a symlink'):
                    context.find_test_binary(None, symlink)

    def test_measurement_binary_is_opt_in_and_must_remain_under_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = pathlib.Path(temp_dir)
            default = target / 'debug/deps/zg_engine-default'
            candidate = target / 'debug/deps/.m4-measurement/zg_engine-default-nodebug'
            library = target / 'debug/build/zvec-rust-sys-fixture/out/zvec-prebuilt/libzvec_c_api.so'
            for path in (default, candidate, library):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            default.chmod(0o755)
            candidate.chmod(0o755)
            listing = mock.Mock(returncode=0, stdout=f'{lifecycle.TEST_NAME}: test\n')
            with mock.patch.object(config, 'RUST_TARGET_DIR', target), \
                 mock.patch.object(context.subprocess, 'run', return_value=listing) as launch, \
                 mock.patch.object(config.measurement_artifact, 'load_segments', return_value=('same', [])):
                self.assertEqual(context.find_test_binary()[0], default.resolve())
                self.assertEqual(context.find_test_binary(candidate)[0], candidate.resolve())
                before = launch.call_count
                with self.assertRaises(ValueError):
                    context.find_test_binary(default)
                with mock.patch.object(config.measurement_artifact, 'load_segments',
                                       side_effect=[('original', []), ('changed', [])]):
                    with self.assertRaisesRegex(ValueError, 'load image'):
                        context.find_test_binary(candidate)
                self.assertEqual(launch.call_count, before)

    def test_preflight_refuses_reuse_of_an_existing_authorized_operation_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            operation_id = 'm4-rust-lifecycle-pair-fixture-existing-root'
            (root / operation_id).mkdir()
            binary = root / 'zg_engine-fixture'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary')
            library.write_bytes(b'library')
            quota = {'live_headroom': {'required_free_bytes': 67108864, 'available_bytes': 120000000}}
            with (
                mock.patch.dict(config.CONTRACT, {'resource_root': str(root)}),
                mock.patch.object(context, 'frozen_manifest', return_value=([], 'fixture')),
                mock.patch.object(context, 'find_test_binary', return_value=(binary, root)),
                mock.patch.object(lifecycle.containment, 'verify_workspace_quota', return_value=quota),
                mock.patch.object(preflight.shutil, 'disk_usage', return_value=mock.Mock(free=10**12)),
                self.assertRaisesRegex(ValueError, 'operation ID already has a workspace'),
            ):
                lifecycle.preflight(operation_id, binary)

    def test_opt_in_artifact_is_pinned_once_for_both_preflight_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'zg_engine-nodebug'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary')
            library.write_bytes(b'library')
            quota = {'live_headroom': {'required_free_bytes': 67108864, 'available_bytes': 120000000}}
            containment = lifecycle.containment
            with mock.patch.object(context, 'frozen_manifest', return_value=([], 'fixture')), \
                 mock.patch.object(context, 'find_test_binary', return_value=(binary, root)) as find, \
                 mock.patch.object(containment, 'verify_workspace_quota', return_value=quota), \
                 mock.patch.object(preflight.shutil, 'disk_usage', return_value=mock.Mock(free=10**12)), \
                 mock.patch.object(containment, 'validate_contention_context') as validate, \
                 mock.patch.object(containment, 'contention_budget', return_value={}), \
                 mock.patch.object(containment, 'validate_watchdog_operation_binding'):
                plan = lifecycle.preflight('m4-rust-lifecycle-pair-fixture-test', binary)
            find.assert_called_once_with(binary, None)
            contexts = [call.args[0] for call in validate.call_args_list]
            self.assertEqual(len(contexts), 2)
            for arm_context in contexts:
                self.assertEqual(arm_context['binary_path'], plan['artifacts']['binary_path'])
                self.assertEqual(arm_context['binary_sha256'], plan['artifacts']['binary_sha256'])
                self.assertEqual(arm_context['library_file'], plan['artifacts']['library_file'])
                self.assertEqual(arm_context['library_sha256'], plan['artifacts']['library_sha256'])
            self.assertFalse(plan['benchmark_started'])

    def test_native_diagnostic_authorization_is_sequential_and_capped_at_three(self):
        diagnostic = config.CONTRACT['native_query_stage_diagnostic']
        operation_ids = diagnostic['authorized_operation_ids']
        limit = diagnostic['max_authorized_operations']
        self.assertIsInstance(operation_ids, list)
        self.assertEqual(len(operation_ids), limit)
        self.assertLessEqual(limit, 3)
        self.assertEqual(len(set(operation_ids)), len(operation_ids))
        self.assertEqual(diagnostic['arm_roles'], ['native'])
        self.assertEqual(diagnostic['execution_order'], ['native'])
        self.assertTrue(diagnostic['stop_on_gate_refusal'])
        self.assertTrue(diagnostic['review_after_each_operation'])
        self.assertFalse(diagnostic['performance_eligible'])

    def test_native_only_diagnostic_preflight_binds_exactly_one_native_arm(self):
        operation_id = 'm4-rust-lifecycle-pair-fixture-native-stage-preflight'
        diagnostic = dict(config.CONTRACT['native_query_stage_diagnostic'])
        diagnostic.update(approval_status='approved', max_authorized_operations=1,
                          authorized_operation_ids=[operation_id])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'zg_engine-fixture'
            library_dir = root / 'native-library'
            library_dir.mkdir()
            binary.write_bytes(b'binary')
            library = library_dir / 'libzvec_c_api.so'
            library.write_bytes(b'library')
            quota = {'live_headroom': {
                'required_free_bytes': 67108864, 'available_bytes': 120000000}}
            with (
                mock.patch.dict(config.CONTRACT, {
                    'resource_root': str(root), 'native_query_stage_diagnostic': diagnostic}),
                mock.patch.object(context, 'frozen_manifest', return_value=([], 'fixture')),
                mock.patch.object(context, 'find_test_binary', return_value=(binary, library_dir)),
                mock.patch.object(lifecycle.containment, 'verify_workspace_quota', return_value=quota),
                mock.patch.object(preflight.shutil, 'disk_usage', return_value=mock.Mock(free=10**12)),
                mock.patch.object(lifecycle.containment, 'validate_contention_context') as validate,
                mock.patch.object(lifecycle.containment, 'contention_budget', return_value={}),
                mock.patch.object(lifecycle.containment, 'validate_watchdog_operation_binding'),
            ):
                plan = lifecycle.preflight(operation_id, native_only_diagnostic=True)
            self.assertEqual(plan['operation_mode'], 'native_query_stage_diagnostic')
            self.assertEqual(plan['execution_order'], ['native'])
            self.assertEqual([arm['role'] for arm in plan['arms']], ['native'])
            contexts = [call.args[0] for call in validate.call_args_list]
            self.assertEqual(len(contexts), 1)
            self.assertEqual(contexts[0]['operation_mode'], 'native_query_stage_diagnostic')
            self.assertEqual(contexts[0]['arm_roles'], ['native'])
            self.assertEqual(contexts[0]['arm_role'], 'native')
            trace_path = (pathlib.Path(plan['operation_root']) / 'pair-first' /
                          'containment' / 'native-profile.jsonl')
            self.assertEqual(contexts[0]['native_profile_trace_path'], str(trace_path))
            self.assertEqual(plan['arms'][0]['native_profile_trace_path'], str(trace_path))
            self.assertFalse(plan['benchmark_started'])

    def test_native_diagnostic_authorization_cannot_be_used_for_a_pair(self):
        operation_id = 'm4-rust-lifecycle-pair-fixture-native-stage-pair-refusal'
        diagnostic = dict(config.CONTRACT['native_query_stage_diagnostic'])
        diagnostic.update(approval_status='approved', max_authorized_operations=1,
                          authorized_operation_ids=[operation_id])
        with mock.patch.dict(config.CONTRACT, {'native_query_stage_diagnostic': diagnostic}), \
             mock.patch.object(context, 'frozen_manifest') as corpus, \
             mock.patch.object(lifecycle.containment, 'verify_workspace_quota') as quota, \
             self.assertRaisesRegex(ValueError, 'cannot launch a matched pair'):
            lifecycle.preflight(operation_id)
        corpus.assert_not_called()
        quota.assert_not_called()

    def test_consumed_native_diagnostic_authorization_refuses_before_preflight(self):
        operation_id = 'm4-rust-lifecycle-pair-20261029t161551z-nquery-stage1-recovery-00'
        with mock.patch.object(context, 'frozen_manifest') as corpus, \
             mock.patch.object(lifecycle.containment, 'verify_workspace_quota') as quota, \
             self.assertRaisesRegex(ValueError, 'outside the native-only diagnostic authorization'):
            lifecycle.preflight(operation_id, native_only_diagnostic=True)
        corpus.assert_not_called()
        quota.assert_not_called()

    def test_unapproved_native_diagnostic_id_refuses_before_preflight(self):
        with mock.patch.object(context, 'frozen_manifest') as corpus, \
             mock.patch.object(lifecycle.containment, 'verify_workspace_quota') as quota, \
             self.assertRaisesRegex(ValueError, 'outside the native-only diagnostic authorization'):
            lifecycle.preflight(
                'm4-rust-lifecycle-pair-20261029t151203z-nquery-r4b',
                native_only_diagnostic=True)
        corpus.assert_not_called()
        quota.assert_not_called()

    def test_expected_library_hash_mismatch_refuses_before_quota_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'zg_engine-fixture'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary')
            library.write_bytes(b'library')
            with (
                mock.patch.object(context, 'frozen_manifest', return_value=([], 'fixture')),
                mock.patch.object(context, 'find_test_binary', return_value=(binary, root)) as find,
                mock.patch.object(lifecycle.containment, 'verify_workspace_quota') as quota,
                self.assertRaisesRegex(ValueError, 'native library SHA-256 differs'),
            ):
                lifecycle.preflight('m4-rust-lifecycle-pair-fixture-test', None, None, root, '0' * 64)
            find.assert_called_once_with(None, root)
            quota.assert_not_called()

    def test_closed_owner_authorization_rejects_refused_operation_id(self):
        contract = config.CONTRACT
        self.assertEqual(contract['authorized_operation_ids'], [])
        self.assertEqual(contract['max_authorized_pair_operations'], 0)
        with self.assertRaisesRegex(ValueError, 'outside the current owner authorization'):
            lifecycle.containment.validate_rust_lifecycle_context({
                'operation_id': 'm4-rust-lifecycle-pair-20261029t151203z-nquery-r4b',
            })

    def test_expected_binary_hash_mismatch_on_default_selection_refuses_before_quota_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / 'binary'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary')
            library.write_bytes(b'library')
            with mock.patch.object(context, 'frozen_manifest', return_value=([], 'fixture')), \
                 mock.patch.object(context, 'find_test_binary', return_value=(binary, root)) as find, \
                 mock.patch.object(lifecycle.containment, 'verify_workspace_quota') as quota, \
                 self.assertRaisesRegex(ValueError, 'explicitly expected artifact'):
                lifecycle.preflight('m4-rust-lifecycle-pair-fixture-test', None, '0' * 64)
            find.assert_called_once_with(None, None)
            quota.assert_not_called()

    def test_cli_allows_expected_sha_pin_for_default_binary_discovery(self):
        expected = 'a' * 64
        argv = [
            'run_rust_lifecycle_pair.py', '--validate-only',
            '--expected-binary-sha256', expected,
        ]
        with mock.patch.object(lifecycle, 'preflight', return_value={}) as preflight, \
             mock.patch.object(sys, 'argv', argv), \
             mock.patch('builtins.print'):
            self.assertEqual(lifecycle.main(), 0)
        self.assertIsNone(preflight.call_args.args[1])
        self.assertEqual(preflight.call_args.args[2], expected)

    def test_context_accepts_only_the_configured_binary_root(self):
        containment = lifecycle.containment
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            target = root / 'target'
            binary = target / 'debug/deps/zg_engine-fixture'
            other = root / 'other/debug/deps/zg_engine-fixture'
            for path in (binary, other):
                path.parent.mkdir(parents=True)
                path.write_bytes(b'synthetic binary')
            library = target / 'debug/build/sys/out/libzvec_c_api.so'
            library.parent.mkdir(parents=True)
            library.write_bytes(b'synthetic library')
            contract = config.CONTRACT.copy()
            operation_id = 'm4-rust-lifecycle-pair-fixture-authorized'
            contract['authorized_operation_ids'] = [operation_id]
            contract['max_authorized_pair_operations'] = 1
            self.assertEqual(len(contract['authorized_operation_ids']), 1)
            self.assertEqual(contract['max_authorized_pair_operations'], 1)
            operation_root = pathlib.Path(contract['resource_root']) / operation_id
            workspace = operation_root / 'pair-first/workspace'
            quota = {'status': 'VERIFIED', 'attestation_path': 'fixture',
                     'workspace_root': contract['resource_root'], 'filesystem_uuid': 'fixture',
                     'subvolume_id': 259, 'qgroup_id': '0/259', 'max_referenced_bytes': 167772160,
                     'accounting_mode': 'squota', 'attestation_sha256': 'a' * 64,
                     'live_headroom': {'required_free_bytes': contract['workspace_quota']['minimum_free_bytes']}}
            with (
                mock.patch.object(containment, 'M4_RUST_LIFECYCLE_TARGET', target),
                mock.patch.object(containment, 'verify_workspace_quota', return_value=quota),
                mock.patch.object(config, 'CONTRACT', contract),
                mock.patch.dict(containment.BENCHMARK_POLICY,
                                {'rust_pipeline_lifecycle_pair': contract}),
            ):
                context = lifecycle.make_context(operation_id, operation_root, 'native', ['native', 'm4'],
                                                 lifecycle.capture_pair_artifacts(binary, library.parent),
                                                 workspace, workspace / 'source', quota)
                containment.validate_rust_lifecycle_context(context)
                diagnostic_id = 'm4-rust-lifecycle-pair-fixture-native-stage'
                diagnostic = dict(contract['native_query_stage_diagnostic'])
                diagnostic['approval_status'] = 'approved'
                diagnostic['authorized_operation_ids'] = [diagnostic_id]
                diagnostic['max_authorized_operations'] = 1
                contract['native_query_stage_diagnostic'] = diagnostic
                diagnostic_root = pathlib.Path(contract['resource_root']) / diagnostic_id
                diagnostic_workspace = diagnostic_root / 'pair-first/workspace'
                diagnostic_context = lifecycle.make_context(
                    diagnostic_id, diagnostic_root, 'native', ['native'],
                    lifecycle.capture_pair_artifacts(binary, library.parent),
                    diagnostic_workspace, diagnostic_workspace / 'source', quota,
                    operation_mode='native_query_stage_diagnostic')
                containment.validate_rust_lifecycle_context(diagnostic_context)
                self.assertEqual(diagnostic_context['operation_mode'], 'native_query_stage_diagnostic')
                pair_context_for_diagnostic = lifecycle.make_context(
                    diagnostic_id, diagnostic_root, 'native', ['native', 'm4'],
                    lifecycle.capture_pair_artifacts(binary, library.parent),
                    diagnostic_workspace, diagnostic_workspace / 'source', quota)
                with self.assertRaisesRegex(ValueError, 'outside the current owner authorization'):
                    containment.validate_rust_lifecycle_context(pair_context_for_diagnostic)
                unauthorized_id = 'm4-rust-lifecycle-pair-unauthorized-test'
                unauthorized_root = pathlib.Path(contract['resource_root']) / unauthorized_id
                unauthorized_workspace = unauthorized_root / 'pair-first/workspace'
                unauthorized_context = lifecycle.make_context(
                    unauthorized_id, unauthorized_root, 'native', ['native', 'm4'],
                    lifecycle.capture_pair_artifacts(binary, library.parent),
                    unauthorized_workspace, unauthorized_workspace / 'source', quota)
                with self.assertRaisesRegex(
                    ValueError, 'outside the current owner authorization'):
                    containment.validate_rust_lifecycle_context(unauthorized_context)
                library.write_bytes(b'changed library')
                with self.assertRaisesRegex(ValueError, 'explicit bounded exploratory contract'):
                    containment.validate_rust_lifecycle_context(context)
                library.write_bytes(b'synthetic library')
                context = lifecycle.make_context(operation_id, operation_root, 'native', ['native', 'm4'],
                                                 lifecycle.capture_pair_artifacts(other, library.parent),
                                                 workspace, workspace / 'source', quota)
                with self.assertRaisesRegex(ValueError, 'explicit bounded exploratory contract'):
                    containment.validate_rust_lifecycle_context(context)

    def test_native_only_result_is_diagnostic_not_a_pair_or_performance_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            operation_id = 'm4-rust-lifecycle-pair-fixture-native-stage-result'
            operation_root = root / operation_id
            operation_root.mkdir()
            contract = dict(config.CONTRACT)
            contract['resource_root'] = str(root)
            quota = {'live_headroom': {
                'available_bytes': 120000000, 'required_free_bytes': 67108864}}
            plan = {
                'operation_mode': 'native_query_stage_diagnostic',
                'operation_root': str(operation_root),
                'execution_order': ['native'],
                'arms': [{'arm_name': 'pair-first', 'role': 'native'}],
                'artifacts': {'binary_path': '/fixture/zg_engine', 'binary_sha256': 'a' * 64,
                              'library_path': '/fixture/lib', 'library_file': '/fixture/lib/libzvec_c_api.so',
                              'library_sha256': 'b' * 64},
                'corpus_manifest_sha256': 'c' * 64,
                'corpus_file_count': 64,
                'corpus_bytes': 694421,
            }
            def run_arm(*_args, policy_context, **_kwargs):
                self.assertEqual(policy_context['operation_mode'], 'native_query_stage_diagnostic')
                self.assertEqual(policy_context['arm_roles'], ['native'])
                receipt_dir = pathlib.Path(_args[1])
                receipt_dir.mkdir(parents=True, exist_ok=True)
                (receipt_dir / 'native-profile.jsonl').write_text(
                    'NATIVE_PROFILE event=schema_handle_lock_owner stage=query_search_shared_hold '
                    'object_id=0x1 item_id=-1 thread_id=2 wall_start_ns=3 wall_end_ns=4 '
                    'wall_ns=1 thread_cpu_ns=1 clock_ok=1\\n')
                pathlib.Path(policy_context['report_path']).write_text(json.dumps({
                    'arm': 'native', 'query_filter': '**/*.rs', 'query_route': 'FTS',
                    'native_fts_stage_spans': [{'stage': 'fts_search', 'wall_ms': 12.0}],
                    'passes': [],
                }))
                return {
                    'status': 'PASS', 'workload_returncode': 0,
                    'safety_outcome': 'SAFE_COMPLETE', 'benchmark_started': True,
                    'watchdog_summary': {'hard_safety_telemetry_complete': True},
                    'unit_cleanup': {'verified': True, 'unit_collected': True},
                    'postflight_decision': {'continuation_allowed': True},
                }
            with mock.patch.object(config, 'CONTRACT', contract), \
                 mock.patch.object(executor, 'extract_corpus',
                                   side_effect=lambda destination, _entries: destination.mkdir(parents=True)), \
                 mock.patch.object(context, 'verify_pair_artifacts'), \
                 mock.patch.object(context, 'build_environment', return_value={}), \
                 mock.patch.object(context, 'test_command', return_value=['/fixture']), \
                 mock.patch.object(lifecycle.containment, 'verify_workspace_quota', return_value=quota), \
                 mock.patch.object(lifecycle.containment, 'validate_contention_context'), \
                 mock.patch.object(lifecycle.containment, 'contention_budget', return_value={}), \
                 mock.patch.object(lifecycle.containment, 'run_arm', side_effect=run_arm):
                result = executor._run_pair_arms(operation_id, plan, [], operation_root, [])
            self.assertEqual(result['status'], 'DIAGNOSTIC_COMPLETED')
            self.assertEqual(result['native_profile_trace']['status'], 'PRESENT')
            self.assertEqual(result['native_profile_trace']['valid_event_lines'], 1)
            self.assertFalse(result['pair_comparison_performed'])
            self.assertFalse(result['performance_eligible'])
            self.assertNotIn('query_latency_gate', result)
            self.assertEqual(result['native_workload_report']['native_fts_stage_spans'][0]['stage'],
                             'fts_search')
            self.assertFalse((operation_root / 'pair-result.json').exists())
            self.assertTrue((operation_root / 'native-query-stage-result.json').is_file())
            self.assertFalse((operation_root / 'pair-first/workspace').exists())

    def test_library_change_during_first_arm_stops_pair_without_rediscovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            operation_root = root / 'operation'
            operation_root.mkdir()
            binary = root / 'binary'
            library = root / 'libzvec_c_api.so'
            binary.write_bytes(b'binary')
            library.write_bytes(b'library')
            artifacts = lifecycle.capture_pair_artifacts(binary, root)
            plan = {'operation_mode': 'pair',
                    'arms': [{'arm_name': 'pair-first', 'role': 'native'},
                             {'arm_name': 'pair-second', 'role': 'm4'}],
                    'execution_order': ['native', 'm4'], 'artifacts': artifacts}
            reserve = config.CONTRACT['workspace_quota']['minimum_free_bytes']
            allowance = config.CONTRACT['resource_controls']['workspace_max_allocated_bytes']
            quota = {'live_headroom': {'available_bytes': reserve + allowance + 1000,
                                       'required_free_bytes': reserve}}
            def make_arm_context(_id, _root, _role, _order, identity, workspace, _source, _quota, _mode, _interval=None):
                self.assertEqual(identity, artifacts)
                return {'report_path': str(workspace / 'gate-b-report.json'),
                        'index_root': str(workspace / 'index'), 'binary_path': str(binary)}
            def start_arm(*_args, **_kwargs):
                library.write_bytes(b'changed library')
                return {'status': 'PASS'}
            containment = lifecycle.containment
            with mock.patch.object(containment, 'verify_workspace_quota', return_value=quota), \
                 mock.patch.object(executor, 'extract_corpus'), \
                 mock.patch.object(context, 'find_test_binary') as discover, \
                 mock.patch.object(context, 'make_context', side_effect=make_arm_context), \
                 mock.patch.object(containment, 'validate_contention_context'), \
                 mock.patch.object(context, 'build_environment', return_value={}), \
                 mock.patch.object(containment, 'contention_budget', return_value={}), \
                 mock.patch.object(containment, 'run_arm', side_effect=start_arm) as run_arm, \
                 mock.patch.object(executor, 'arm_result_allows_pair_continuation', return_value=True), \
                 self.assertRaisesRegex(ValueError, 'identity changed'):
                executor._run_pair_arms('fixture', plan, [], operation_root, [])
            discover.assert_not_called()
            self.assertEqual(run_arm.call_count, 1)
            self.assertFalse((operation_root / 'pair-second').exists())

    def test_second_arm_refusal_does_not_retry_or_start_second_arm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            operation_root = pathlib.Path(temp_dir) / 'operation'
            operation_root.mkdir()
            first = {'arm_name': 'pair-first', 'role': 'native'}
            second = {'arm_name': 'pair-second', 'role': 'm4'}
            plan = {'operation_mode': 'pair', 'arms': [first, second],
                    'execution_order': ['native', 'm4'], 'artifacts': {}}
            reserve = config.CONTRACT['workspace_quota']['minimum_free_bytes']
            allowance = config.CONTRACT['resource_controls']['workspace_max_allocated_bytes']
            def quota(available):
                return {'live_headroom': {'available_bytes': available, 'required_free_bytes': reserve}}
            observations = [quota(reserve + allowance + 1000), quota(reserve + allowance + 1000),
                            quota(reserve + allowance - 1)]
            def start_arm(_command, _receipt, _resource, workspace, *_args, **_kwargs):
                (workspace / 'gate-b-report.json').write_text('{}')
                return {'status': 'PASS'}
            def make_arm_context(_id, _root, _role, _order, _artifacts, workspace, _source, _quota, _mode, _interval=None):
                return {'report_path': str(workspace / 'gate-b-report.json'),
                        'index_root': str(workspace / 'index'), 'binary_path': '/fixture'}
            containment = lifecycle.containment
            with mock.patch.object(containment, 'verify_workspace_quota', side_effect=observations) as verify, \
                 mock.patch.object(executor, 'extract_corpus'), \
                 mock.patch.object(context, 'verify_pair_artifacts'), \
                 mock.patch.object(context, 'make_context', side_effect=make_arm_context), \
                 mock.patch.object(containment, 'validate_contention_context'), \
                 mock.patch.object(context, 'build_environment', return_value={}), \
                 mock.patch.object(containment, 'contention_budget', return_value={}), \
                 mock.patch.object(containment, 'run_arm', side_effect=start_arm) as run_arm, \
                 mock.patch.object(executor, 'arm_result_allows_pair_continuation', return_value=True), \
                 self.assertRaisesRegex(RuntimeError, 'insufficient qgroup headroom'):
                executor._run_pair_arms('fixture', plan, [], operation_root, [])
            self.assertEqual(verify.call_count, 3)
            self.assertEqual(run_arm.call_count, 1)
            self.assertFalse((operation_root / 'pair-first/workspace').exists())
            self.assertFalse((operation_root / 'pair-second').exists())

    def test_observer_timing_preserves_full_validation_and_propagates_failure(self):
        containment = lifecycle.containment
        decision = {'continuation_allowed': True}
        with mock.patch.object(containment, 'contention_decision', return_value=decision) as evaluate, \
             mock.patch.object(containment.time, 'monotonic_ns', side_effect=[100, 180]), \
             mock.patch.object(containment.time, 'process_time_ns', side_effect=[10, 30]):
            actual, cost = containment.timed_lifecycle_contention_decision({}, {}, 'runtime-first', 5, 7)
        self.assertIs(actual, decision)
        self.assertEqual(cost, {'wall_ns': 80, 'cpu_ns': 20})
        evaluate.assert_called_once_with({}, {}, 'runtime-first', 5, 7)
        with mock.patch.object(containment, 'contention_decision', side_effect=ValueError('guard failed')):
            with self.assertRaisesRegex(ValueError, 'guard failed'):
                containment.timed_lifecycle_contention_decision({}, {}, 'runtime-first', 0, 0)

    def test_workspace_headroom_refusal_is_classified_as_safety_gate(self):
        error = RuntimeError(
            "insufficient qgroup headroom for the next workspace allocation: "
            "available=97935360, additional=33554432, required_free=67108864, "
            "total_required=100663296"
        )
        self.assertTrue(lifecycle.is_qgroup_headroom_gate_failure(error))

    def test_unrelated_runtime_error_is_not_a_qgroup_safety_stop(self):
        self.assertFalse(
            lifecycle.is_qgroup_headroom_gate_failure(RuntimeError("unexpected test failure"))
        )


if __name__ == "__main__":
    unittest.main()
