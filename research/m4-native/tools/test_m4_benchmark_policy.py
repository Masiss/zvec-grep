#!/usr/bin/env python3
"""Regression tests for the shared research M4 operation/gate policy."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m4_containment_runner as runner  # noqa: E402

SCRIPT = HERE / "run_lock_contention_microbench.py"
spec = importlib.util.spec_from_file_location("m4_lock_contention_runner", SCRIPT)
assert spec and spec.loader
lock_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock_runner)

POLICY_PATH = HERE.parent / "docs" / "benchmark-policy.json"


class BenchmarkPolicyTests(unittest.TestCase):
    def test_contention_output_parser_accepts_pretty_printed_json(self):
        payload = {"mode": "m4_compaction_contention", "arms": []}
        with tempfile.TemporaryDirectory() as tmp:
            stdout = pathlib.Path(tmp) / "stdout.log"
            stdout.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(lock_runner.read_workload(stdout), payload)

    def setUp(self):
        self.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def _valid_contention_receipt(self, include_compaction_timing=True,
                                  timing_schema=4):
        contract = self.policy["lock_contention_microbench"]
        workload = contract["workload"]
        rounds = workload["rounds"]
        writer_series = (
            "writer_samples", "state_wait_samples", "state_wait_cpu_samples",
            "state_hold_samples", "state_hold_cpu_samples",
            "wal_append_samples", "wal_append_cpu_samples", "query_wait_samples",
            "apply_samples", "auto_flush_samples", "normalize_samples",
            "normalize_cpu_samples", "wal_encode_samples", "wal_encode_cpu_samples")
        if timing_schema >= 6:
            writer_series += ("wal_write_samples", "wal_fsync_samples")
        timing_fields = (
            "total_ms", "total_cpu_ms", "state_mutex_wait_ms",
            "state_mutex_wait_cpu_ms", "state_mutex_hold_ms",
            "state_mutex_hold_cpu_ms", "query_mutex_wait_ms",
            "query_mutex_wait_cpu_ms", "pending_flush_ms", "pending_flush_cpu_ms",
            "merge_prepare_ms", "merge_prepare_cpu_ms", "merge_build_ms",
            "merge_build_cpu_ms", "publish_state_mutex_wait_ms",
            "publish_state_mutex_wait_cpu_ms", "publish_query_mutex_wait_ms",
            "publish_query_mutex_wait_cpu_ms", "publication_ms",
            "publication_cpu_ms")
        arms = []
        compaction_modes = list(workload.get(
            "compaction_modes", ("default", "snapshot")))
        if not include_compaction_timing:
            compaction_modes = [name for name in compaction_modes
                                if name != "apply_batch"]
        for name in compaction_modes:
            batch_api = name == "apply_batch"
            api_calls = (workload["writers"] if include_compaction_timing and batch_api
                         else rounds)
            arm = {key: [0.25] * api_calls for key in writer_series}
            if timing_schema >= 6:
                arm["wal_write_samples"] = [0.01] * api_calls
                arm["wal_fsync_samples"] = [0.01] * api_calls
            if timing_schema >= 7:
                arm["compaction_tracked_peak_bytes"] = 1024
            arm.update({
                "name": name,
                "compaction_status": "ok",
                "writer_failures": 0,
                "reader_failures": 0,
                "durability_failures": 0,
                "runs_after_flush": 1,
                "reader_samples": [0.25] * (workload["readers"] * workload["queries_per_reader"]),
                "reader_mutex_wait_samples": [0.0] * (workload["readers"] * workload["queries_per_reader"]),
            })
            if include_compaction_timing:
                arm.update({
                    "writer_api": "ApplyBatch" if batch_api else "Upsert",
                    "writer_api_calls": api_calls,
                    "logical_upserts": rounds,
                })
                flush_stage_fields = (
                    "segment_build_ms", "segment_build_cpu_ms",
                    "segment_write_ms", "segment_write_cpu_ms",
                    "segment_open_ms", "segment_open_cpu_ms",
                    "query_mutex_wait_ms", "query_mutex_wait_cpu_ms",
                    "stats_append_ms", "stats_append_cpu_ms",
                    "tombstone_write_ms", "tombstone_write_cpu_ms",
                    "manifest_write_ms", "manifest_write_cpu_ms",
                    "wal_reset_ms", "wal_reset_cpu_ms", "gc_ms",
                    "gc_cpu_ms", "delta_reset_ms", "delta_reset_cpu_ms")
                if timing_schema >= 5:
                    flush_stage_fields += (
                        "query_mutex_hold_ms", "query_mutex_hold_cpu_ms")
                arm["compaction_diagnostics"] = {
                    **dict.fromkeys(timing_fields, 0.25),
                    "flush_stage_timing": {
                        "schema_version": 2 if timing_schema >= 5 else 1,
                        **dict.fromkeys(flush_stage_fields, 0.01),
                    },
                }
                if timing_schema >= 5:
                    arm["post_writer_flush_diagnostics"] = {
                        "total_ms": 0.5, "total_cpu_ms": 0.2,
                        "state_mutex_wait_ms": 0.01,
                        "state_mutex_wait_cpu_ms": 0.001,
                        "state_mutex_hold_ms": 0.3,
                        "state_mutex_hold_cpu_ms": 0.1,
                        "query_mutex_wait_ms": 0.01,
                        "query_mutex_wait_cpu_ms": 0.001,
                        "pending_flush_ms": 0.2,
                        "pending_flush_cpu_ms": 0.1,
                        "flush_stage_timing": {
                            "schema_version": 2,
                            **dict.fromkeys(flush_stage_fields, 0.005),
                        },
                    }
            arms.append(arm)
        data = {
            "mode": "m4_compaction_contention",
            "documents": workload["documents"],
            "rounds": rounds,
            "writers": workload["writers"],
            "ops_per_writer": workload["ops_per_writer"],
            "writer_operations": rounds,
            "reader_queries": workload["readers"] * workload["queries_per_reader"],
            "coherence_errors": 0,
            "failures": 0,
            "arm_order": list(compaction_modes),
            "arms": arms,
        }
        if include_compaction_timing:
            data["timing_schema_version"] = timing_schema
        result = {"status": "PASS", "workload_returncode": 0,
                  "controller_cleanup": {"verified": True}}
        return result, data, contract

    def test_contention_verifier_requires_post_writer_flush_diagnostics(self):
        result, data, contract = self._valid_contention_receipt(timing_schema=5)
        lock_runner.verify(result, data, contract)
        data["arms"][0].pop("post_writer_flush_diagnostics")
        with self.assertRaisesRegex(RuntimeError, "post-writer flush diagnostics"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_rejects_post_writer_flush_stages_outside_duration(self):
        result, data, contract = self._valid_contention_receipt(timing_schema=5)
        flush = data["arms"][0]["post_writer_flush_diagnostics"]
        flush["flush_stage_timing"]["segment_write_ms"] = 0.25
        with self.assertRaisesRegex(RuntimeError, "post-writer flush stages exceed"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_requires_bounded_wal_substages(self):
        result, data, contract = self._valid_contention_receipt(timing_schema=6)
        lock_runner.verify(result, data, contract)
        data["arms"][0]["wal_fsync_samples"][0] = 1.0
        with self.assertRaisesRegex(RuntimeError, "WAL write/fsync substage"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_requires_compaction_memory_peak(self):
        result, data, contract = self._valid_contention_receipt(timing_schema=7)
        lock_runner.verify(result, data, contract)
        data["arms"][0]["compaction_tracked_peak_bytes"] = -1
        with self.assertRaisesRegex(RuntimeError, "compaction tracked memory peak"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_checks_new_compaction_timing_fields(self):
        result, data, contract = self._valid_contention_receipt()
        lock_runner.verify(result, data, contract)
        data["arms"][0]["compaction_diagnostics"]["merge_build_ms"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "compaction phase timing"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_bounds_flush_query_lock_hold(self):
        result, data, contract = self._valid_contention_receipt(timing_schema=5)
        stages = data["arms"][0]["compaction_diagnostics"]["flush_stage_timing"]
        stages["query_mutex_hold_ms"] = 0.3
        with self.assertRaisesRegex(RuntimeError, "flush query-lock hold"):
            lock_runner.verify(result, data, contract)

        result, data, contract = self._valid_contention_receipt(timing_schema=5)
        stages = data["arms"][0]["post_writer_flush_diagnostics"]["flush_stage_timing"]
        stages["query_mutex_hold_ms"] = 0.3
        with self.assertRaisesRegex(RuntimeError, "query-lock hold exceeds"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_checks_optional_flush_stage_timing(self):
        result, data, contract = self._valid_contention_receipt()
        lock_runner.verify(result, data, contract)
        stages = data["arms"][0]["compaction_diagnostics"]["flush_stage_timing"]
        stages["segment_write_ms"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "flush-stage timing"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_rejects_flush_stages_exceeding_pending_flush(self):
        result, data, contract = self._valid_contention_receipt()
        stages = data["arms"][0]["compaction_diagnostics"]["flush_stage_timing"]
        stages["segment_build_ms"] = 0.25
        with self.assertRaisesRegex(RuntimeError, "exceeds pending-flush"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_requires_state_hold_series_for_schema4(self):
        result, data, contract = self._valid_contention_receipt()
        data["arms"][0].pop("state_hold_samples")
        with self.assertRaisesRegex(RuntimeError, "writer timing series"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_binds_apply_batch_api_and_call_counts(self):
        result, data, contract = self._valid_contention_receipt()
        lock_runner.verify(result, data, contract)
        batch = next(arm for arm in data["arms"] if arm["name"] == "apply_batch")
        batch["writer_api_calls"] += 1
        with self.assertRaisesRegex(RuntimeError, "writer API/count metadata"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_requires_batch_timing_per_api_call(self):
        result, data, contract = self._valid_contention_receipt()
        batch = next(arm for arm in data["arms"] if arm["name"] == "apply_batch")
        batch["writer_samples"] = [0.25] * contract["workload"]["rounds"]
        with self.assertRaisesRegex(RuntimeError, "writer timing series"):
            lock_runner.verify(result, data, contract)

    def test_contention_verifier_accepts_schema3_three_arm_receipt(self):
        result, data, contract = self._valid_contention_receipt()
        data["timing_schema_version"] = 3
        data["arms"] = [arm for arm in data["arms"]
                        if arm["name"] != "apply_batch"]
        data["arm_order"] = [name for name in data["arm_order"]
                             if name != "apply_batch"]
        for arm in data["arms"]:
            for key in ("writer_api", "writer_api_calls", "logical_upserts"):
                arm.pop(key)
        lock_runner.verify(result, data, contract)

    def test_contention_verifier_keeps_historical_receipt_compatibility(self):
        result, data, contract = self._valid_contention_receipt(False)
        for arm in data["arms"]:
            for key in ("normalize_samples", "normalize_cpu_samples",
                        "wal_encode_samples", "wal_encode_cpu_samples"):
                arm.pop(key)
        lock_runner.verify(result, data, contract)

    def test_existing_thresholds_are_unchanged_and_runner_uses_policy_source(self):
        expected = {
            "MemAvailable_kib_min": 5_477_762,
            "var_tmp_bytes_min": 18_219_000_000,
            "io_full_avg10_max": 4.46,
            "load1_max": 3.14,
        }
        self.assertEqual(self.policy["resource_guards"], expected)
        self.assertEqual(runner.RESOURCE_THRESHOLDS, expected)
        self.assertEqual(runner.GUARDS, {
            "memavailable_min_kib": expected["MemAvailable_kib_min"],
            "var_tmp_free_min_bytes": expected["var_tmp_bytes_min"],
            "io_full_avg10_max": expected["io_full_avg10_max"],
            "load1_max": expected["load1_max"],
        })
        self.assertIn("not a universal", self.policy["authority"]["historical_envelope_source"])
        self.assertIn("performance_eligible=false", self.policy["containment"]["test_fixture_override"])
        self.assertTrue(all(item["owner_approval_required_to_change"]
                            for item in self.policy["resource_guard_metadata"].values()))

    def test_lock_microbench_contract_reduces_only_the_workload_reservation(self):
        pair = self.policy["shared_collection_pair"]["resource_controls"]
        micro = self.policy["lock_contention_microbench"]["resource_controls"]
        self.assertEqual(micro["host_memory_reserve_bytes"], pair["host_memory_reserve_bytes"])
        self.assertLess(micro["memory_max_bytes"], pair["memory_max_bytes"])
        self.assertEqual(micro["memory_swap_max_bytes"], 0)
        self.assertEqual(micro["disk_reserve_bytes"], pair["disk_reserve_bytes"])
        self.assertEqual(micro["max_monitor_gap_seconds"], pair["max_monitor_gap_seconds"])
        self.assertEqual(micro["host_memory_reserve_bytes"] + micro["memory_max_bytes"],
                         3_758_096_384)
        self.assertTrue(self.policy["lock_contention_microbench"]["no_automatic_retry"])

    def test_memory_probe_is_a_non_comparable_same_caps_profile(self):
        contract = self.policy["lock_contention_microbench"]
        probe = contract["memory_probe"]
        self.assertEqual(probe["profile_id"], "memory_probe")
        self.assertEqual(probe["workload"]["documents"], 4096)
        self.assertEqual({key: value for key, value in probe["workload"].items()
                          if key != "documents"},
                         {key: value for key, value in contract["workload"].items()
                          if key != "documents"})
        self.assertIn("non-comparable", probe["measurement_semantics"])
        self.assertIn("not latency", probe["measurement_semantics"])
        self.assertTrue(contract["no_automatic_retry"])

    def test_lock_contention_policy_matches_wal_preserving_rebase(self):
        contract = self.policy["lock_contention_microbench"]
        semantics = contract["measurement_semantics"]
        self.assertIn("does not flush pending writer deltas at publication", semantics)
        self.assertIn("prior durable WAL sequence", semantics)
        self.assertIn("WAL disabled", semantics)
        self.assertNotIn("flushes pending writer deltas at publication", semantics)
        markdown = (HERE.parent / "docs" / "benchmark-policy.md").read_text(encoding="utf-8")
        self.assertIn("without synchronously flushing pending deltas at publication", markdown)

    def test_io_psi_is_an_operation_guard_not_universal_safety_claim(self):
        state = {
            "meminfo_kib": {"MemAvailable_kib": 5_477_762},
            "resource_root_free_bytes": 18_219_000_000,
            "io_pressure": {"full": {"avg10": 4.52}},
            "loadavg": [3.14],
        }
        decisions = {item["gate_id"]: item for item in runner.resource_guard_decisions(state)}
        self.assertEqual(decisions["M4-RG-IOPSI"]["status"], "FAIL")
        self.assertEqual(decisions["M4-RG-IOPSI"]["observed"], 4.52)
        self.assertEqual(decisions["M4-RG-IOPSI"]["threshold"], 4.46)
        self.assertIn("not a universal", decisions["M4-RG-IOPSI"]["evidence_class"])
        self.assertIn("current arm", decisions["M4-RG-IOPSI"]["failure_scope"])
        self.assertEqual(decisions["M4-RG-IOPSI"]["status"], "FAIL")
        self.assertTrue(any("M4-RG-IOPSI" in reason for reason in runner.hard_gate_failures(state)))

    def test_resource_only_contract_reclassifies_load_and_host_deltas_only_in_scope(self):
        contract = self.policy["resource_only_exploratory_contract"]
        self.assertIn("observation-only", contract["load1"])
        self.assertIn("observation-only", self.policy["resource_guard_metadata"]["M4-RG-LOAD"]["enforcement"])
        self.assertIn("not distress", contract["host_swap_reclaim"])
        self.assertIn("OOM", contract["hard_stop_signals"])
        self.assertIn("UNKNOWN/INCOMPLETE", contract["workspace"])
        self.assertIn("never report a soft-budget observation as PASS", contract["workspace"])
        self.assertIn("no effect on qualification", contract["scope"])

    def test_policy_separates_integrity_quality_baseline_pressure_and_correctness(self):
        self.assertEqual(self.policy["measurement_quality"]["telemetry_integrity"]["gate_id"], "M4-MQ-INTEGRITY")
        self.assertEqual(self.policy["measurement_quality"]["stability_envelope"]["gate_id"], "M4-MQ-STABILITY")
        self.assertEqual(self.policy["baseline_pressure_guard"]["gate_id"], "M4-RG-MEMSWAP-BASELINE")
        self.assertIn("candidate/operation remains NO-GO", self.policy["correctness_and_durability"]["failure_scope"])
        self.assertEqual(set(self.policy["operation_outcomes"]), {
            "SUCCESS", "PARTIAL", "OPERATION_BLOCKED", "OWNER_DECISION_REQUIRED", "SAFETY_STOP", "EXHAUSTED"
        })
        self.assertIn("infer whole-mission SUCCESS", self.policy["runner_mission_semantics"])

    def test_markdown_is_explanatory_not_a_second_threshold_source(self):
        markdown = (HERE.parent / "docs" / "benchmark-policy.md").read_text(encoding="utf-8")
        self.assertIn("sole machine-readable source", markdown)
        for value in ("5,477,762", "18,219,000,000", "4.46%", "3.14"):
            self.assertNotIn(value, markdown)

    def test_active_and_legacy_harnesses_load_the_json_source(self):
        runner_source = (HERE / "m4_containment_runner.py").read_text(encoding="utf-8")
        sync_pair = HERE.parent / "results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/run_sync_reduction_pair.py"
        host_pair = sync_pair.with_name("run_host_carryover_aa.py")
        legacy_paths = [
            sync_pair.with_name("run_diag_aa_v2.py"),
            sync_pair.with_name("run_matched_evaluator_pair.py"),
        ]
        self.assertIn("benchmark-policy.json", runner_source)
        sync_source = sync_pair.read_text(encoding="utf-8")
        self.assertIn("BENCHMARK_POLICY = containment.BENCHMARK_POLICY", sync_source)
        self.assertIn("REPO_ROOT = HERE.parents[5]", sync_source)
        expected_repo = HERE.parents[2]
        self.assertEqual(sync_pair.parents[6].resolve(), expected_repo.resolve())
        host_source = host_pair.read_text(encoding="utf-8")
        self.assertIn("import run_sync_reduction_pair as runner", host_source)
        self.assertIn("runner.THRESHOLDS", host_source)
        for path in legacy_paths:
            source = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                self.assertIn('"benchmark-policy.json"', source)
                self.assertIn("parents[4]", source)
                self.assertEqual((path.resolve().parents[4] / "docs" / "benchmark-policy.json"),
                                 POLICY_PATH.resolve())
                self.assertNotIn('5_477_762', source)
                self.assertNotIn('18_219_000_000', source)
                self.assertNotIn('4.46', source)
                self.assertNotIn('3.14', source)


if __name__ == "__main__":
    unittest.main()
