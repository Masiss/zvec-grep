#!/usr/bin/env python3
"""Focused tests for resource guards vs measurement-quality classification."""
import importlib.util
import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
BINARY = HERE.parents[5] / ".local/m4-preserved/m4_bridge_integration"
os.environ.setdefault("M4_RESOURCE_ROOT", "/var/tmp")
os.environ.setdefault("M4_AA_BINARY", str(BINARY))
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("aa_runner", HERE / "run_host_carryover_aa.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load A/A runner for policy tests")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)


class GatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = aa.runner.THRESHOLDS
        self.state = {
            "MemAvailable_kib": self.thresholds["MemAvailable_kib_min"],
            "resource_root_available_bytes": self.thresholds["var_tmp_bytes_min"],
            "io_full_avg10": self.thresholds["io_full_avg10_max"],
            "load1": self.thresholds["load1_max"],
        }

    def test_all_existing_resource_limits_remain_operation_guards(self):
        self.assertEqual(aa.resource_guard_failures(self.state, self.thresholds), [])
        self.assertEqual(self.thresholds, {
            "MemAvailable_kib_min": 5_477_762, "var_tmp_bytes_min": 18_219_000_000,
            "io_full_avg10_max": 4.46, "load1_max": 3.14,
        })
        cases = (
            ("MemAvailable_kib", self.thresholds["MemAvailable_kib_min"] - 1),
            ("resource_root_available_bytes", self.thresholds["var_tmp_bytes_min"] - 1),
            ("io_full_avg10", self.thresholds["io_full_avg10_max"] + 0.01),
            ("load1", self.thresholds["load1_max"] + 0.01),
        )
        for key, value in cases:
            with self.subTest(metric=key):
                broken = dict(self.state, **{key: value})
                failure = aa.resource_guard_failures(broken, self.thresholds)[0]
                self.assertEqual(failure["metric"], key)
                self.assertTrue(failure["gate_id"].startswith("M4-RG-"))

    def test_baseline_applies_existing_absolute_limits_samplewise(self):
        row = {"memavailable_kib": self.thresholds["MemAvailable_kib_min"],
               "load1": self.thresholds["load1_max"],
               "io_full_avg10": self.thresholds["io_full_avg10_max"]}
        self.assertEqual(aa.resource_guard_baseline_failures([row], self.thresholds), [])
        row["io_full_avg10"] += 0.01
        failures = aa.resource_guard_baseline_failures([row], self.thresholds)
        self.assertEqual(failures[0]["metric"], "io_full_avg10")
        self.assertEqual(failures[0]["gate_id"], "M4-RG-IOPSI")

    def test_diagnostic_ignores_quality_drift_but_not_memory_swap_pressure(self):
        limits = {"swapfree_min_kib": 1000, "memory_some_avg10_max": 0.2, "memory_full_avg10_max": 0.0}
        baseline = [
            {"swapfree_min_kib": 1000, "memory_some_avg10_max": 0.1, "memory_full_avg10_max": 0.0}
            for _ in range(6)
        ]
        # load/IO changes are measurement-quality features and aren't in these safety checks.
        self.assertEqual(aa.baseline_pressure_failures(baseline, limits), [])
        baseline[-1]["memory_some_avg10_max"] = 0.3
        self.assertEqual(aa.baseline_pressure_failures(baseline, limits)[0]["window_index"], 5)

    def test_quality_failure_blocks_qualification_but_allows_guarded_exploratory_diagnostic(self):
        integrity = {"status": "VALID"}
        pressure = {"swapfree_min_kib": 1000, "memory_some_avg10_max": 0.2, "memory_full_avg10_max": 0.0}
        diagnostic = aa.baseline_gate_decision("diagnostic", integrity, pressure, [], [], False, "load drift")
        self.assertEqual(diagnostic["operation_outcome"], "OPERATION_READY_EXPLORATORY")
        self.assertEqual(diagnostic["gate_class"], "MEASUREMENT_QUALITY_ONLY")
        self.assertEqual(diagnostic["evidence_class"], "EXPLORATORY / NOT QUALIFICATION EVIDENCE")
        self.assertEqual(diagnostic["mission_status"], "NOT_EVALUATED_BY_RUNNER")
        self.assertFalse(diagnostic["mission_marked_done"])
        qualification = aa.baseline_gate_decision("qualification", integrity, pressure, [], [], False, "load drift")
        self.assertEqual(qualification["operation_outcome"], "OPERATION_BLOCKED")
        self.assertEqual(qualification["gate_id"], "M4-MQ-STABILITY")
        # A diagnostic quality waiver cannot bypass a failed absolute resource guard.
        resource_failure = [{"gate_id": "M4-RG-IOPSI", "threshold_key": "io_full_avg10_max"}]
        blocked = aa.baseline_gate_decision("diagnostic", integrity, pressure,
                                            resource_failure, [], False, "load drift")
        self.assertEqual(blocked["operation_outcome"], "OPERATION_BLOCKED")
        self.assertEqual(blocked["gate_class"], "HISTORICAL_ENVELOPE_RESOURCE_GUARD")
        self.assertEqual(blocked["gate_id"], ["M4-RG-IOPSI"])

    def test_invalid_telemetry_and_baseline_pressure_are_not_quality_passes(self):
        pressure = {"swapfree_min_kib": 1000, "memory_some_avg10_max": 0.2, "memory_full_avg10_max": 0.0}
        invalid = aa.baseline_gate_decision("diagnostic", {"status": "MEASUREMENT_INVALID"},
                                            pressure, [], [], True)
        self.assertEqual(invalid["gate_id"], "M4-MQ-INTEGRITY")
        self.assertEqual(invalid["operation_outcome"], "OPERATION_BLOCKED")
        pressure_failure = [{"window_index": 2}]
        blocked = aa.baseline_gate_decision("diagnostic", {"status": "VALID"},
                                            pressure, [], pressure_failure, False)
        self.assertEqual(blocked["gate_id"], "M4-RG-MEMSWAP-BASELINE")
        self.assertEqual(blocked["operation_outcome"], "OPERATION_BLOCKED")

    def test_operation_status_never_implies_whole_mission_done(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory(prefix="m4-operation-status-") as tmp:
            pair = pathlib.Path(tmp)
            aa.write_operation_status(pair, "OPERATION_BLOCKED", "baseline-preflight",
                                      reason="io psi high", gate_id="M4-RG-IOPSI")
            status = json.loads((pair / "operation-status.json").read_text())
        self.assertEqual(status["operation_status"], "OPERATION_BLOCKED")
        self.assertEqual(status["failure_scope"], "this benchmark/A/A operation only")
        self.assertEqual(status["mission_status"], "NOT_EVALUATED_BY_RUNNER")
        self.assertFalse(status["mission_marked_done"])

    def test_diagnostic_recovery_keeps_absolute_and_memory_swap_limits(self):
        limits = {"swapfree_min_kib": 1000, "memory_some_avg10_max": 0.2, "memory_full_avg10_max": 0.0}
        w = {"memavailable_min_kib": self.thresholds["MemAvailable_kib_min"],
             "swapfree_min_kib": 1000, "memory_some_avg10_max": 0.2,
             "memory_full_avg10_max": 0.0, "load1_max": self.thresholds["load1_max"],
             "io_full_avg10_max": self.thresholds["io_full_avg10_max"]}
        state = dict(self.state)
        state["pass"] = True
        self.assertTrue(aa.diagnostic_window_safe(w, state, limits))
        w["swapfree_min_kib"] -= 1
        self.assertFalse(aa.diagnostic_window_safe(w, state, limits))


if __name__ == "__main__":
    unittest.main()
