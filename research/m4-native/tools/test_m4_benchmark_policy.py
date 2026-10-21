#!/usr/bin/env python3
"""Regression tests for the shared research M4 operation/gate policy."""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import m4_containment_runner as runner  # noqa: E402

POLICY_PATH = HERE.parent / "docs" / "benchmark-policy.json"


class BenchmarkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

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
