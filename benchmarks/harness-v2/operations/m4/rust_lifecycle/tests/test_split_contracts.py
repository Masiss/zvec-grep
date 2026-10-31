"""Equivalence checks between the P3 modules and the legacy CLI shim."""
from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
MODULES = HERE.parent
REPO = MODULES.parents[4]
TOOLS = REPO / "research" / "m4-native" / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(REPO / "benchmarks" / "harness-v2"))

PACKAGE = "operations.m4.rust_lifecycle"
executor = importlib.import_module(f"{PACKAGE}.executor")
preflight = importlib.import_module(f"{PACKAGE}.preflight")
receipt = importlib.import_module(f"{PACKAGE}.receipt")
verify = importlib.import_module(f"{PACKAGE}.verify")


def load_legacy():
    spec = importlib.util.spec_from_file_location(
        "legacy_rust_lifecycle_pair", TOOLS / "run_rust_lifecycle_pair.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SplitEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = load_legacy()

    @staticmethod
    def query_arm(role, **overrides):
        report = {
            "arm": role,
            "concurrent_query_latency_p95_comparable": True,
            "concurrent_query_total": 20,
            "concurrent_query_offered_slots": 20,
            "concurrent_query_accepted_slots": 20,
            "concurrent_query_started": 20,
            "concurrent_query_dropped_slots": 0,
            "concurrent_query_slots_not_offered_by_cap": 0,
            "concurrent_query_schedule_slots_missed": 0,
            "concurrent_query_failures": 0,
            "concurrent_query_sampling_capped_passes": 0,
            "concurrent_query_max_schedule_lag_us": 1_000,
            "reader_query_interval_ms": 25,
            "reader_query_max_samples_per_pass": 64,
            "reader_query_queue_capacity": 8,
            "query_filter": "**/*.rs",
            "query_route": "fts",
            "concurrent_query_p95_us": 120_000 if role == "native" else 125_000,
        }
        report.update(overrides)
        return {"role": role, "workload_report": report}

    def test_query_gate_matches_legacy_for_clean_and_refused_arms(self):
        for m4_overrides in ({}, {"concurrent_query_dropped_slots": 1, "concurrent_query_p95_us": None}):
            with self.subTest(m4_overrides=m4_overrides):
                arms = [self.query_arm("m4", **m4_overrides), self.query_arm("native")]
                self.assertEqual(
                    verify.query_latency_pair_gate(arms),
                    self.legacy.query_latency_pair_gate(arms),
                )

    def test_continuation_rule_matches_legacy(self):
        safe = {
            "status": "PASS",
            "workload_returncode": 0,
            "watchdog_abort": None,
            "safety_outcome": "SAFE_COMPLETE",
            "watchdog_summary": {"hard_safety_telemetry_complete": True},
            "unit_cleanup": {"verified": True, "unit_collected": True},
            "postflight_decision": {"continuation_allowed": True},
        }
        for patch in ({}, {"safety_outcome": "CONTAINMENT_UNVERIFIED"}, {"workload_returncode": -9}):
            result = {**safe, **patch}
            with self.subTest(patch=patch):
                self.assertEqual(
                    executor.arm_result_allows_pair_continuation(result),
                    self.legacy.arm_result_allows_pair_continuation(result),
                )

    def test_headroom_refusal_matches_legacy(self):
        quota = {"live_headroom": {"available_bytes": 10, "required_free_bytes": 5}}
        self.legacy.require_pair_workspace_headroom(quota, 5)
        preflight.require_pair_workspace_headroom(quota, 5)
        with self.assertRaises(RuntimeError) as legacy_error:
            self.legacy.require_pair_workspace_headroom(quota, 6)
        with self.assertRaises(RuntimeError) as new_error:
            preflight.require_pair_workspace_headroom(quota, 6)
        self.assertEqual(str(new_error.exception), str(legacy_error.exception))

    def test_failure_receipt_refusal_paths(self):
        plan = {"operation_mode": "stage_a_rate_ladder"}
        failure = receipt.failure_receipt(
            "m4-rust-lifecycle-pair-synthetic", plan, [], "RuntimeError: refusal", True
        )
        self.assertEqual(failure["status"], "OPERATION_BLOCKED")
        self.assertEqual(failure["failure_class"], "SAFETY_STOP")
        self.assertFalse(failure["benchmark_started"])
        self.assertIn("do not automatically retry", failure["retry"])
        self.assertEqual(receipt.failure_receipt_filename(plan), "pair-result.json")

        diagnostic = {"operation_mode": "native_query_stage_diagnostic"}
        diagnostic_failure = receipt.failure_receipt(
            "m4-rust-lifecycle-pair-synthetic", diagnostic, [], "ValueError: bad", False
        )
        self.assertEqual(diagnostic_failure["status"], "DIAGNOSTIC_ERROR")
        self.assertEqual(
            receipt.failure_receipt_filename(diagnostic), "native-query-stage-result.json"
        )

    def _pair_fixture(self, native_mutations=3):
        plan = {
            "operation_mode": "stage_a_accelerated",
            "corpus_manifest_sha256": "manifest",
            "corpus_file_count": 4,
            "corpus_bytes": 1_000,
            "execution_order": ["native", "m4"],
            "artifacts": {"binary_sha256": "binary"},
        }
        native_report = {
            "mutations": native_mutations,
            "indexed_file_count": 4,
            "final_marker_paths": ["a", "b"],
            "post_reopen_marker_paths": ["a", "b"],
            "passes": [{"name": "p1"}],
        }
        m4_report = {**native_report, "mutations": 3}
        arms = [
            {"role": "native", "runner": {"status": "PASS"}, "workload_report": native_report},
            {"role": "m4", "runner": {"status": "PASS"}, "workload_report": m4_report},
        ]
        return plan, arms

    def test_synthetic_pair_and_diagnostic_receipt_fields(self):
        plan, arms = self._pair_fixture()
        gate = verify.query_latency_pair_gate(
            [self.query_arm("native"), self.query_arm("m4")]
        )
        result = receipt.pair_result("synthetic-pair", plan, arms, gate, "commit")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["parity"]["pass"])
        self.assertEqual(result["query_latency_comparison"], "DESCRIPTIVE_ONLY")
        self.assertFalse(result["performance_eligible"])
        self.assertEqual(result["corpus"]["commit"], "commit")
        self.assertEqual(result["arms"], arms)

        plan_mismatch, mismatch_arms = self._pair_fixture(native_mutations=4)
        mismatch = receipt.pair_result(
            "synthetic-pair", plan_mismatch, mismatch_arms, gate, "commit"
        )
        self.assertEqual(mismatch["status"], "CORRECTNESS_MISMATCH")
        self.assertFalse(mismatch["parity"]["pass"])

        diagnostic_plan = {**plan, "operation_mode": "native_query_stage_diagnostic"}
        diagnostic = receipt.diagnostic_result(
            "synthetic-diagnostic", diagnostic_plan, arms[0], {"live_headroom": {}}, "commit"
        )
        self.assertEqual(diagnostic["status"], "DIAGNOSTIC_COMPLETED")
        self.assertFalse(diagnostic["performance_eligible"])
        self.assertFalse(diagnostic["pair_comparison_performed"])


if __name__ == "__main__":
    unittest.main()
