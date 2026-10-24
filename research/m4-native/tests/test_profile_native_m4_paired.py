import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("profile_native_m4_paired.py")
spec = importlib.util.spec_from_file_location("profile_native_m4_paired", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load profiler module from {SCRIPT}")
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ProfilerAdmissionPolicyTests(unittest.TestCase):
    def test_host_pressure_load_and_memavailable_never_block_launch(self):
        snapshot = {
            "mem_available_bytes": 0,
            "loadavg": "999.0 999.0 999.0",
            "memory_pressure": "full avg10=99.0 avg60=99.0 avg300=99.0 total=1",
            "io_pressure": "full avg10=99.0 avg60=99.0 avg300=99.0 total=1",
            "disk": {"free_bytes": profiler.DISK_RESERVE_BYTES + 1024**3},
        }
        self.assertIsNone(profiler.gate(snapshot))
        observations = profiler.resource_guard_observations(snapshot)
        self.assertEqual(observations['MemAvailable_kib_min']['status'], 'FAIL')
        self.assertEqual(observations['io_full_avg10_max']['status'], 'FAIL')
        self.assertEqual(observations['load1_max']['status'], 'FAIL')
        self.assertTrue(all(observations[key]['disposition'] == 'telemetry_only_never_blocks'
                            for key in ('MemAvailable_kib_min', 'io_full_avg10_max', 'load1_max')))

    def test_missing_host_pressure_load_and_memavailable_are_unknown_not_blocking(self):
        snapshot = {
            "mem_available_bytes": None,
            "loadavg": None,
            "memory_pressure": None,
            "io_pressure": None,
            "disk": {"free_bytes": profiler.DISK_RESERVE_BYTES + 1024**3},
        }
        self.assertIsNone(profiler.gate(snapshot))
        observations = profiler.resource_guard_observations(snapshot)
        for key in ("MemAvailable_kib_min", "io_full_avg10_max", "load1_max"):
            self.assertEqual(observations[key]["status"], "UNKNOWN")
            self.assertEqual(observations[key]["disposition"], "telemetry_only_never_blocks")

    def test_compaction_and_stats_log_limits_are_bound_to_each_pair_role(self):
        workload = {
            'control_max_total_runs': 12, 'candidate_max_total_runs': 12,
            'control_max_runs_per_level': 4, 'candidate_max_runs_per_level': 4,
            'control_stats_log_threshold': 4 * 1024 * 1024,
            'candidate_stats_log_threshold': 16 * 1024 * 1024,
            'control_mutation_batch_docs': 256, 'candidate_mutation_batch_docs': 256,
            'control_coalesce_stats': False, 'candidate_coalesce_stats': True,
            'control_wal_compression_lz4': False, 'candidate_wal_compression_lz4': True,
            'control_wal_lz4_acceleration': 1, 'candidate_wal_lz4_acceleration': 4,
            'wal_replay_probe': True,
        }
        self.assertEqual(
            profiler.m4_compaction_environment('control', workload),
            {'M4_MAX_TOTAL_RUNS': '12', 'M4_MAX_RUNS_PER_LEVEL': '4',
             'M4_STATS_LOG_THRESHOLD': str(4 * 1024 * 1024),
             'M4_MUTATION_BATCH_DOCS': '256', 'M4_COALESCE_STATS': '0',
             'M4_WAL_COMPRESSION_LZ4': '0', 'M4_WAL_LZ4_ACCELERATION': '1',
             'M4_WAL_REPLAY_PROBE': '1'},
        )
        self.assertEqual(
            profiler.m4_compaction_environment('candidate', workload),
            {'M4_MAX_TOTAL_RUNS': '12', 'M4_MAX_RUNS_PER_LEVEL': '4',
             'M4_STATS_LOG_THRESHOLD': str(16 * 1024 * 1024),
             'M4_MUTATION_BATCH_DOCS': '256', 'M4_COALESCE_STATS': '1',
             'M4_WAL_COMPRESSION_LZ4': '1', 'M4_WAL_LZ4_ACCELERATION': '4',
             'M4_WAL_REPLAY_PROBE': '1'},
        )
        workload['candidate_max_runs_per_level'] = 0
        with self.assertRaisesRegex(ValueError, 'positive uint32'):
            profiler.m4_compaction_environment('candidate', workload)
        workload['candidate_max_runs_per_level'] = 4
        workload['candidate_stats_log_threshold'] = 0
        with self.assertRaisesRegex(ValueError, 'positive uint64'):
            profiler.m4_compaction_environment('candidate', workload)
        workload['candidate_stats_log_threshold'] = 16 * 1024 * 1024
        workload['candidate_mutation_batch_docs'] = 0
        with self.assertRaisesRegex(ValueError, 'must be 1..256'):
            profiler.m4_compaction_environment('candidate', workload)
        workload['candidate_mutation_batch_docs'] = 257
        with self.assertRaisesRegex(ValueError, 'ApplyBatch supports at most 256'):
            profiler.m4_compaction_environment('candidate', workload)

    def test_lz4_acceleration_profile_is_separate_from_standard_lz4_toggle(self):
        standard = profiler.write_amp_workload_profile('standard')
        acceleration = profiler.write_amp_workload_profile('lz4-acceleration-1-vs-4')
        self.assertFalse(standard['control_wal_compression_lz4'])
        self.assertTrue(standard['candidate_wal_compression_lz4'])
        self.assertTrue(acceleration['control_wal_compression_lz4'])
        self.assertTrue(acceleration['candidate_wal_compression_lz4'])
        self.assertEqual(acceleration['control_wal_lz4_acceleration'], 1)
        self.assertEqual(acceleration['candidate_wal_lz4_acceleration'], 4)
        self.assertEqual(standard['control_wal_lz4_acceleration'], 1)
        with self.assertRaisesRegex(ValueError, 'unknown immutable-write-amplification profile'):
            profiler.write_amp_workload_profile('unbound-profile')

    def test_mutation_batch_profile_is_supported_and_isolates_batch_size(self):
        standard = profiler.write_amp_workload_profile('standard')
        workload = profiler.write_amp_workload_profile('mutation-batch-128-vs-256')
        self.assertEqual(workload['files'], 600)
        self.assertEqual(workload['control_mutation_batch_docs'], 128)
        self.assertEqual(workload['candidate_mutation_batch_docs'], 256)
        self.assertFalse(workload['control_wal_compression_lz4'])
        self.assertFalse(workload['candidate_wal_compression_lz4'])
        self.assertTrue(workload['trace_mutation_latency'])
        changed = {key for key in standard if standard[key] != workload[key]}
        self.assertEqual(changed, {'files', 'control_mutation_batch_docs',
                                   'candidate_wal_compression_lz4', 'trace_mutation_latency'})
        self.assertNotIn('M4_P42_LATENCY', profiler.m4_compaction_environment('control', standard))
        self.assertEqual(
            profiler.m4_compaction_environment('control', workload)['M4_P42_LATENCY'], '1')
        with self.assertRaisesRegex(ValueError, 'must be 1..256'):
            profiler.validate_mutation_batch_docs('candidate', 512)

    def test_full_size_mutation_batch_profile_isolates_chunk_size(self):
        standard = profiler.write_amp_workload_profile('standard')
        workload = profiler.write_amp_workload_profile(
            'mutation-batch-128-vs-256-full-1200')
        self.assertEqual(workload['files'], 1200)
        self.assertEqual(workload['control_mutation_batch_docs'], 128)
        self.assertEqual(workload['candidate_mutation_batch_docs'], 256)
        self.assertFalse(workload['control_wal_compression_lz4'])
        self.assertFalse(workload['candidate_wal_compression_lz4'])
        self.assertFalse(workload['control_coalesce_stats'])
        self.assertFalse(workload['candidate_coalesce_stats'])
        self.assertTrue(workload['trace_mutation_latency'])
        changed = {key for key in standard if standard[key] != workload[key]}
        self.assertEqual(changed, {
            'control_mutation_batch_docs', 'candidate_wal_compression_lz4',
            'trace_mutation_latency',
        })
        for role, batch_docs in (('control', 128), ('candidate', 256)):
            env = profiler.m4_compaction_environment(role, workload)
            self.assertEqual(env['M4_MUTATION_BATCH_DOCS'], str(batch_docs))
            self.assertEqual(env['M4_WAL_COMPRESSION_LZ4'], '0')
            self.assertEqual(env['M4_COALESCE_STATS'], '0')
            self.assertEqual(env['M4_P42_LATENCY'], '1')

    def test_lz4_compression_profiles_isolate_batch_size_interaction(self):
        standard = profiler.write_amp_workload_profile('standard')
        for name, batch_docs, files, expected_changes in (
            ('lz4-compression-at-batch-128', 128, 600,
             {'files', 'control_mutation_batch_docs', 'candidate_mutation_batch_docs',
              'trace_mutation_latency'}),
            ('lz4-compression-at-batch-256', 256, 600, {'files', 'trace_mutation_latency'}),
            ('lz4-compression-at-batch-128-full-1200', 128, 1200,
             {'control_mutation_batch_docs', 'candidate_mutation_batch_docs',
              'trace_mutation_latency'}),
        ):
            with self.subTest(profile=name):
                workload = profiler.write_amp_workload_profile(name)
                self.assertEqual(workload['files'], files)
                self.assertEqual(workload['control_mutation_batch_docs'], batch_docs)
                self.assertEqual(workload['candidate_mutation_batch_docs'], batch_docs)
                self.assertFalse(workload['control_wal_compression_lz4'])
                self.assertTrue(workload['candidate_wal_compression_lz4'])
                self.assertFalse(workload['control_coalesce_stats'])
                self.assertFalse(workload['candidate_coalesce_stats'])
                self.assertTrue(workload['trace_mutation_latency'])
                changed = {key for key in standard if standard[key] != workload[key]}
                self.assertEqual(changed, expected_changes)
                self.assertEqual(
                    profiler.m4_compaction_environment('control', workload)['M4_MUTATION_BATCH_DOCS'],
                    str(batch_docs))
                self.assertEqual(
                    profiler.m4_compaction_environment('candidate', workload)['M4_MUTATION_BATCH_DOCS'],
                    str(batch_docs))
                self.assertEqual(
                    profiler.m4_compaction_environment('candidate', workload)['M4_P42_LATENCY'], '1')

    def test_stats_coalescing_profile_isolates_candidate_at_batch_128(self):
        standard = profiler.write_amp_workload_profile('standard')
        workload = profiler.write_amp_workload_profile(
            'stats-coalescing-at-batch-128-full-1200')
        self.assertEqual(workload['files'], 1200)
        self.assertEqual(workload['control_mutation_batch_docs'], 128)
        self.assertEqual(workload['candidate_mutation_batch_docs'], 128)
        self.assertFalse(workload['control_coalesce_stats'])
        self.assertTrue(workload['candidate_coalesce_stats'])
        self.assertFalse(workload['control_wal_compression_lz4'])
        self.assertFalse(workload['candidate_wal_compression_lz4'])
        self.assertTrue(workload['trace_mutation_latency'])
        changed = {key for key in standard if standard[key] != workload[key]}
        self.assertEqual(changed, {
            'control_mutation_batch_docs', 'candidate_mutation_batch_docs',
            'candidate_coalesce_stats', 'candidate_wal_compression_lz4',
            'trace_mutation_latency',
        })
        for role, coalescing in (('control', '0'), ('candidate', '1')):
            env = profiler.m4_compaction_environment(role, workload)
            self.assertEqual(env['M4_MUTATION_BATCH_DOCS'], '128')
            self.assertEqual(env['M4_COALESCE_STATS'], coalescing)
            self.assertEqual(env['M4_WAL_COMPRESSION_LZ4'], '0')
            self.assertEqual(env['M4_P42_LATENCY'], '1')

    def test_argument_workload_builder_matches_every_policy_field(self):
        policy = profiler.WRITE_AMP_WORKLOAD
        args = SimpleNamespace(
            files=policy['files'], runs=policy['runs'],
            control_arm=policy['control_arm'], candidate_arm=policy['candidate_arm'],
            control_max_delta_docs=policy['control_max_delta_docs'],
            candidate_max_delta_docs=policy['candidate_max_delta_docs'],
            control_max_total_runs=policy['control_max_total_runs'],
            candidate_max_total_runs=policy['candidate_max_total_runs'],
            control_max_runs_per_level=policy['control_max_runs_per_level'],
            candidate_max_runs_per_level=policy['candidate_max_runs_per_level'],
            control_stats_log_threshold=policy['control_stats_log_threshold'],
            candidate_stats_log_threshold=policy['candidate_stats_log_threshold'],
            control_mutation_batch_docs=policy['control_mutation_batch_docs'],
            candidate_mutation_batch_docs=policy['candidate_mutation_batch_docs'],
            control_coalesce_stats=1 if policy['control_coalesce_stats'] else 0,
            candidate_coalesce_stats=1 if policy['candidate_coalesce_stats'] else 0,
            control_wal_compression_lz4=1 if policy['control_wal_compression_lz4'] else 0,
            candidate_wal_compression_lz4=1 if policy['candidate_wal_compression_lz4'] else 0,
            control_wal_lz4_acceleration=policy['control_wal_lz4_acceleration'],
            candidate_wal_lz4_acceleration=policy['candidate_wal_lz4_acceleration'],
            trace_fsync=policy['trace_fsync'], m4_debt_policy=policy['m4_debt_policy'],
            m4_debt_ratio=policy['m4_debt_ratio'], m4_debt_cap=policy['m4_debt_cap'],
        )
        self.assertEqual(profiler.write_amp_workload_from_args(args), policy)

    def test_retained_disk_reserve_remains_a_launch_guard(self):
        snapshot = {"disk": {"free_bytes": profiler.DISK_RESERVE_BYTES - 1}}
        self.assertEqual(
            profiler.gate(snapshot),
            f"filesystem free below {profiler.DISK_RESERVE_BYTES} bytes",
        )


    def test_write_amplification_context_binds_workload_and_rejects_unbound_command(self):
        import shutil

        quota = profiler.BENCHMARK_POLICY['lock_contention_microbench']['workspace_quota']
        quota_root = Path(quota['workspace_subvolume'])
        quota_proof = {
            'status': 'VERIFIED', 'attestation_path': quota['attestation_path'],
            'workspace_root': quota['workspace_subvolume'],
            'filesystem_uuid': quota['filesystem_uuid'], 'subvolume_id': quota['subvolume_id'],
            'qgroup_id': quota['qgroup_id'], 'max_referenced_bytes': quota['max_referenced_bytes'],
            'accounting_mode': quota['accounting_mode'], 'attestation_sha256': 'a' * 64,
            'live_headroom': {
                'referenced_bytes': 0,
                'max_referenced_bytes': quota['max_referenced_bytes'],
                'available_bytes': quota['max_referenced_bytes'],
                'required_free_bytes': quota['minimum_free_bytes'],
            },
        }
        quota_patch = patch.object(profiler.CONTAINMENT, 'verify_workspace_quota',
                                   return_value=quota_proof)
        quota_patch.start()
        self.addCleanup(quota_patch.stop)
        with tempfile.TemporaryDirectory(prefix="m4-write-amp-contract-") as directory:
            operation_root = Path(directory) / "m4-write-amplification-pair-test"
            operation_root.mkdir()
            binary = operation_root / profiler.WRITE_AMP_CONTRACT["binary_name"]
            shutil.copyfile("/usr/bin/true", binary)
            dictionary = operation_root / "dict"
            dictionary.mkdir()
            for name in ("jieba.dict.utf8", "hmm_model.utf8"):
                (dictionary / name).write_text("fixture\n")
            workload = dict(profiler.WRITE_AMP_WORKLOAD)
            arm_workspace = operation_root / "pair-first" / "workspace"
            workspace = arm_workspace / "index"
            arm_workspace.mkdir(parents=True)
            workspace.mkdir()
            temp_dir = arm_workspace / 'tmp'
            temp_dir.mkdir()
            context = profiler.make_write_amplification_context(
                operation_root, quota_root, quota_proof, binary, dictionary, workload, "control",
                temporary_directory=temp_dir)
            self.assertEqual(profiler.CONTAINMENT.validate_contention_context(context), context)
            acceleration_workload = profiler.write_amp_workload_profile(
                'lz4-acceleration-1-vs-4')
            acceleration_context = profiler.make_write_amplification_context(
                operation_root, quota_root, quota_proof, binary, dictionary,
                acceleration_workload, 'control', temp_dir,
                workload_profile='lz4-acceleration-1-vs-4')
            self.assertEqual(
                profiler.CONTAINMENT.validate_contention_context(acceleration_context),
                acceleration_context)
            budget = profiler.CONTAINMENT.contention_budget(context)
            self.assertEqual(
                profiler.CONTAINMENT.workspace_quota_bound_bytes(budget),
                quota['max_referenced_bytes'])
            receipt = operation_root / "pair-first" / "containment"
            receipt.mkdir(parents=True)
            watchdog_config = {
                "cgroup_path": str(operation_root / "cgroup"), "cgroup_rel": "test",
                "workspace_root": str(arm_workspace), "resource_root": str(quota_root),
                "sample_interval_ms": 100, "runtime_limit_seconds": 180,
                "watchdog_output": str(receipt), "controller_pid": 1, "bootstrap_pid": 1,
                "start_marker": str(receipt / "start"), "command_done_marker": str(receipt / "done"),
                "watchdog_ready_path": str(receipt / "ready"), "socket_dir": str(receipt),
                "pressure_limits": {}, "policy_mode": profiler.CONTAINMENT.CONTENTION_MODE,
                "policy_context": context,
                "policy_context_digest": profiler.CONTAINMENT.contention_context_digest(context),
                "arm_name": "pair-first", "enforce_host_gates": True, "budget": budget,
            }
            config_path = operation_root / "watchdog-config.json"
            config_path.write_text(json.dumps(watchdog_config))
            self.assertEqual(
                profiler.CONTAINMENT.read_watchdog_config(config_path)["arm_name"], "pair-first")
            reverse_context = profiler.make_write_amplification_context(
                operation_root, quota_root, quota_proof, binary, dictionary, workload, "candidate",
                temp_dir, ["candidate", "control"])
            profiler.CONTAINMENT.validate_contention_context(reverse_context)
            self.assertEqual(
                profiler.CONTAINMENT.write_amplification_phase_role(reverse_context, "pair-first"),
                "candidate")
            self.assertEqual(
                profiler.CONTAINMENT.write_amplification_phase_role(reverse_context, "pair-second"),
                "control")
            watchdog_config.update({
                "policy_context": reverse_context,
                "policy_context_digest": profiler.CONTAINMENT.contention_context_digest(reverse_context),
                "budget": profiler.CONTAINMENT.contention_budget(reverse_context),
            })
            config_path.write_text(json.dumps(watchdog_config))
            self.assertEqual(
                profiler.CONTAINMENT.read_watchdog_config(config_path)["policy_context"]["execution_order"],
                ["candidate", "control"])
            watchdog_config["arm_name"] = "aa-first"
            config_path.write_text(json.dumps(watchdog_config))
            with self.assertRaisesRegex(ValueError, "binding mismatch"):
                profiler.CONTAINMENT.read_watchdog_config(config_path)
            receipt.rmdir()
            with self.assertRaisesRegex(ValueError, "command/budget binding mismatch"):
                profiler.CONTAINMENT.run_arm(
                    ["/usr/bin/true"], receipt, quota_root, arm_workspace, arm_workspace, 180,
                    budget=budget, env={}, policy_context=context)
            candidate_context = profiler.make_write_amplification_context(
                operation_root, quota_root, quota_proof, binary, dictionary, workload, "candidate", temp_dir,
                ["candidate", "control"])
            candidate_budget = profiler.CONTAINMENT.contention_budget(candidate_context)
            wrong_role_env = {
                'M4_MAX_DELTA_DOCS': str(workload['control_max_delta_docs']),
                'M4_MAX_TOTAL_RUNS': str(workload['candidate_max_total_runs']),
                'M4_MAX_RUNS_PER_LEVEL': str(workload['candidate_max_runs_per_level']),
                'M4_STATS_LOG_THRESHOLD': str(workload['candidate_stats_log_threshold']),
                'M4_MUTATION_BATCH_DOCS': str(workload['candidate_mutation_batch_docs']),
                'M4_COALESCE_STATS': '0' if workload['candidate_coalesce_stats'] else '1',
                'M4_WAL_COMPRESSION_LZ4': '0',
                'M4_WAL_LZ4_ACCELERATION': str(workload['candidate_wal_lz4_acceleration']),
                'M4_WAL_REPLAY_PROBE': '1',
                'TMPDIR': str(temp_dir.resolve()),
            }
            command = [str(binary), 'm4-batch', str(workspace), str(workload['files']),
                       str(dictionary.resolve())]
            wrong_tmpdir_env = dict(wrong_role_env)
            wrong_tmpdir_env['M4_WAL_COMPRESSION_LZ4'] = '1'
            wrong_tmpdir_env['M4_WAL_LZ4_ACCELERATION'] = '4'
            wrong_tmpdir_env['TMPDIR'] = str(operation_root / 'unbound-tmp')
            with self.assertRaisesRegex(ValueError, "command/budget binding mismatch"):
                profiler.CONTAINMENT.run_arm(
                    command, receipt, quota_root, arm_workspace, arm_workspace, 180,
                    budget=candidate_budget, env=wrong_tmpdir_env, policy_context=candidate_context)
            with self.assertRaisesRegex(ValueError, "command/budget binding mismatch"):
                profiler.CONTAINMENT.run_arm(
                    command, receipt, quota_root, arm_workspace, arm_workspace, 180,
                    budget=candidate_budget, env=wrong_role_env, policy_context=candidate_context)


class ProfilerContainmentTests(unittest.TestCase):
    def test_runner_accounts_only_the_arm_index_not_parent_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            resource_root = Path(directory).resolve()
            workspace = resource_root / "index"
            workspace.mkdir()
            receipt_dir = resource_root / "controller" / "containment"
            captured = {}

            def fake_run_arm(_command, _receipt, resource, work, cwd, runtime, **kwargs):
                captured.update({"resource": resource, "workspace": work, "cwd": cwd,
                                 "runtime": runtime, "budget": kwargs["budget"]})
                return {"status": "PASS"}

            with patch.object(profiler.CONTAINMENT, "run_arm", side_effect=fake_run_arm):
                receipt, samples, budget = profiler.run_contained_arm(
                    ["/bin/true"], receipt_dir, resource_root, workspace,
                    resource_root, {}, runtime_seconds=180)

        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(samples, [])
        self.assertEqual(captured["resource"], resource_root)
        self.assertEqual(captured["workspace"], workspace)
        self.assertEqual(captured["cwd"], resource_root)
        self.assertEqual(captured["runtime"], 180)
        self.assertEqual(captured["budget"], budget)
        self.assertEqual(budget["memory_max_bytes"], 1024**3)
        self.assertEqual(budget["cpu_max"], "200000 100000")


class ProfilerOutputTests(unittest.TestCase):
    def test_snapshot_parity_requires_final_reopen_snapshot(self):
        stages = ['initial', 'burst1', 'burst2', 'burst3', 'reopen']
        self.assertTrue(profiler.snapshots_complete([{'stage': stage} for stage in stages]))
        self.assertFalse(profiler.snapshots_complete([{'stage': stage} for stage in stages[:-1]]))

    def test_m4_snapshot_write_attribution_is_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.log"
            output.write_text(
                "W\tburst1\tm4\twal_bytes=11\twal_bytes_appended=44\tsegment_bytes=22\t"
                "wal_lz4_compress_cpu_ms=0.125\ttombstone_bytes=3\tstats_log_bytes=4\tsegment_bytes_written=55\t"
                "runs_level_0=2\truns_level_1=4\n"
            )
            metrics, latencies, snapshots, writes, hits, expected = profiler.parse_output(output)
        self.assertEqual(metrics, [])
        self.assertEqual(latencies, [])
        self.assertEqual(snapshots, [])
        self.assertEqual(hits, {})
        self.assertEqual(expected, {})
        self.assertEqual(writes, [{
            "stage": "burst1", "engine": "m4", "wal_bytes": 11,
            "wal_bytes_appended": 44, "segment_bytes": 22,
            "wal_lz4_compress_cpu_ms": 0.125, "tombstone_bytes": 3,
            "stats_log_bytes": 4, "segment_bytes_written": 55,
            "runs_level_0": 2, "runs_level_1": 4,
        }])


if __name__ == "__main__":
    unittest.main()
