#!/usr/bin/env python3
"""Tiny real-systemd integration of the A/A sequence and per-arm containment."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
os.environ["M4_TEST_FIXTURE_MODE"] = "1"
os.environ.setdefault("M4_RESOURCE_ROOT", "/var/tmp")
BINARY = HERE.parents[5] / ".local/m4-preserved/m4_bridge_integration"
os.environ.setdefault("M4_AA_BINARY", str(BINARY))
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("aa_containment", HERE / "run_host_carryover_aa.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot import same-binary A/A orchestrator")
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)


class ContainedPairIntegrationTests(unittest.TestCase):
    def test_two_ordered_arms_wait_for_recovery_and_leave_receipts(self):
        runner = aa.runner
        containment = runner.containment
        real_contained_run = containment.run_arm
        old_state = (runner.PAIR, runner.RESOURCE_ROOT, runner.DICT, aa.BINARY)
        old_preflight = runner.preflight
        with tempfile.TemporaryDirectory(prefix="m4-aa-contained-pair-test-") as td:
            root = pathlib.Path(td)
            resource_root = root / "resource"
            resource_root.mkdir()
            dictionary = root / "dict"
            dictionary.mkdir()
            pair = root / "pair"
            pair.mkdir()
            binary = resource_root / "tiny-index-arm"
            binary.write_text("""#!/usr/bin/env python3
import json,pathlib,sys,time
root=pathlib.Path(sys.argv[1]); root.mkdir(parents=True)
(root/'fixture-marker').write_text('synthetic-only\\n')
time.sleep(0.12)
now=time.monotonic_ns()
print(json.dumps({'whole_start_monotonic_ns':now,'writer_stage_timeline_ms':[],
                  'commit_samples_ms':[],'commit_write_bytes':0}),flush=True)
""")
            binary.chmod(0o755)
            runner.PAIR, runner.RESOURCE_ROOT, runner.DICT, aa.BINARY = pair, resource_root, dictionary, binary

            def fixture_preflight(name):
                (pair / name).mkdir(parents=True, exist_ok=False)
                return True

            runner.preflight = fixture_preflight

            def contained_fixture(*args, **kwargs):
                # Test-only: tiny fixtures validate wiring on a busy host; safety limits remain
                # covered by the runner's separate hard-gate refusal tests.
                kwargs["enforce_host_gates"] = False
                kwargs["pressure_limits"] = None
                return real_contained_run(*args, **kwargs)

            containment.run_arm = contained_fixture
            recovery_call = 0
            interval_ns = int(aa.host.SAMPLE_S * 1e9)
            base_ns = 10_000_000_000
            pressure_limits = {
                "swapfree_min_kib": 100,
                "memory_some_avg10_max": 10.0,
                "memory_full_avg10_max": 10.0,
            }

            def sample_window(bad_swap=False):
                rows = []
                for i in range(201):
                    t = base_ns + recovery_call * 20_000_000_000 + i * interval_ns
                    start = t - 100_000
                    rows.append({
                        "monotonic_ns": t, "epoch_ns": 1_800_000_000_000_000_000 + t,
                        "sample_start_monotonic_ns": start,
                        "sample_duration_ns": t - start, "load1": 0.1,
                        "memavailable_kib": runner.THRESHOLDS["MemAvailable_kib_min"],
                        "swapfree_kib": 0 if bad_swap else 200,
                        "pswpin": 0, "pswpout": 0,
                        "io_some_avg10": 0.0, "io_full_avg10": 0.0,
                        "memory_some_avg10": 0.0, "memory_full_avg10": 0.0,
                        "diskstats_json": json.dumps({"testdev": {
                            "read_sectors": i, "write_sectors": i, "io_ms": i,
                        }}),
                    })
                return rows, {"duration_seconds": 10.0, "sample_interval_seconds": aa.host.SAMPLE_S,
                              "rows": len(rows), "synthetic_fixture": True}

            def capture_window():
                nonlocal recovery_call
                bad = recovery_call == 0
                recovery_call += 1
                return sample_window(bad_swap=bad)

            def safe_snapshot():
                return {"pass": True, "load1": 0.1, "io_full_avg10": 0.0,
                        "MemAvailable_kib": runner.THRESHOLDS["MemAvailable_kib_min"],
                        "resource_root_available_bytes": runner.THRESHOLDS["var_tmp_bytes_min"],
                        "hard_safety_failures": []}

            try:
                result = aa.run_contained_pair(
                    pair, "diagnostic", pressure_limits, None,
                    capture_window=capture_window, snapshot=safe_snapshot,
                    timeout_seconds=20,
                )
                self.assertEqual(recovery_call, 4, "failed recovery window must reset the consecutive counter")
                first, second = result["first_arm_identity"], result["second_arm_identity"]
                self.assertEqual(first["arm"], "aa-first")
                self.assertEqual(second["arm"], "aa-second")
                self.assertEqual(first["binary_sha256"], second["binary_sha256"])
                self.assertEqual(first["runtime_identity"], second["runtime_identity"])
                self.assertTrue(result["same_binary_source_and_native_library_identity"])
                self.assertNotEqual(first["private_workdir"], second["private_workdir"])
                self.assertNotEqual(first["workload_root"], second["workload_root"])
                self.assertEqual([w["consecutive_recovered_windows"] for w in result["windows"]], [0, 1, 2, 3])

                for identity in (first, second):
                    arm = pair / identity["arm"]
                    receipt = json.loads((arm / "containment/receipt.json").read_text())
                    self.assertEqual(receipt["status"], "PASS")
                    self.assertEqual(receipt["operation_outcome"], "OPERATION_COMPLETE")
                    self.assertEqual(receipt["mission_status"], "NOT_EVALUATED_BY_RUNNER")
                    self.assertTrue(receipt["test_fixture_mode"])
                    self.assertFalse(receipt["performance_eligible"])
                    self.assertTrue(all(not gate["enforced"] for gate in receipt["gate_decisions"]))
                    self.assertEqual(receipt["policy_id"], "m4-benchmark-operation-policy-v1")
                    self.assertEqual(identity["status"], "PASS")
                    self.assertEqual((pathlib.Path(identity["workload_root"]) / "fixture-marker").read_text().strip(),
                                     "synthetic-only")
                    self.assertEqual(receipt["io_control"]["status"], "UNAVAILABLE")
                    self.assertFalse(receipt["io_control"]["io_limit_applied"])
                    self.assertIn("not a per-arm physical-write attribution", receipt["io_control"]["fallback"])
                    runtime = [json.loads(line) for line in
                               (arm / "containment/runtime.jsonl").read_text().splitlines()]
                    self.assertTrue(runtime)
                    self.assertIn("diskstats", runtime[0]["host"])
                    self.assertIn("process_tree_write_bytes", runtime[0]["cgroup"])
                    self.assertIn("process_tree_pss_kib", runtime[0]["cgroup"])
                    self.assertIn("memory_peak_bytes", runtime[0]["cgroup"])
                    with (arm / "runtime-samples.csv").open(newline="") as stream:
                        compat = list(csv.DictReader(stream))
                    self.assertTrue(compat)
                    self.assertIn("cgroup_cpu_usage_usec", compat[0])
                    self.assertIn("diskstats_json", compat[0])
                    self.assertTrue((arm / "pss-samples.csv").is_file())
                    self.assertTrue((arm / "SHA256SUMS.txt").is_file())
                    cg_path = containment.CGROOT / receipt["cgroup_relative_path"]
                    self.assertFalse(cg_path.exists(), f"arm cgroup leaked: {cg_path}")
                    final = (arm / "containment/systemd-final.txt").read_text()
                    self.assertIn("ControlGroup=", final)
                    self.assertNotIn("ControlGroup=/user.slice", final)
            finally:
                containment.run_arm = real_contained_run
                runner.PAIR, runner.RESOURCE_ROOT, runner.DICT, aa.BINARY = old_state
                runner.preflight = old_preflight


if __name__ == "__main__":
    unittest.main(verbosity=2)
