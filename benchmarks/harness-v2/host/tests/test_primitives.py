"""Synthetic no-launch, cleanup, watchdog, and telemetry-integrity tests."""
from __future__ import annotations

import importlib
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
HARNESS_V2 = HERE.parents[1]
sys.path.insert(0, str(HARNESS_V2))
host = importlib.import_module("host.primitives")


class HostPrimitiveTests(unittest.TestCase):
    def test_parse_kv_and_pressure(self):
        self.assertEqual(host.parse_kv("populated 0\nfrozen 1\nbad value\n"), {"populated": 0, "frozen": 1})
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "pressure"
            path.write_text("some avg10=1.00 avg60=2.00 avg300=3.00 total=4\n", encoding="utf-8")
            parsed = host.parse_pressure(path)
            self.assertEqual(
                parsed,
                {"some": {"avg10": 1.0, "avg60": 2.0, "avg300": 3.0, "total": 4.0}},
            )
            self.assertIsNone(host.parse_pressure(pathlib.Path(directory) / "missing"))

    @unittest.skipUnless(pathlib.Path("/proc/meminfo").exists(), "Linux host telemetry required")
    def test_host_snapshot_exposes_context_without_launching(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = host.host_snapshot(pathlib.Path(directory))
        for key in (
            "timestamp_utc",
            "monotonic_ns",
            "meminfo_kib",
            "loadavg",
            "io_pressure",
            "memory_pressure",
            "resource_root_free_bytes",
            "vmstat",
            "diskstats",
            "cpu_pressure",
        ):
            self.assertIn(key, snapshot)
        self.assertIn("MemAvailable_kib", snapshot["meminfo_kib"])

    def test_service_properties_preserve_approved_budget(self):
        props = host.service_properties(
            {
                "pids_max": 128,
                "memory_max_bytes": 1_073_741_824,
                "memory_swap_max_bytes": 0,
                "cpu_max": "200000 100000",
            },
            10.0,
        )
        self.assertIn("RuntimeMaxSec=15.000s", props)
        self.assertIn("TasksMax=128", props)
        self.assertIn("MemoryMax=1073741824", props)
        self.assertIn("MemorySwapMax=0", props)
        self.assertIn("CPUQuota=200%", props)
        self.assertIn("Delegate=yes", props)
        self.assertIn("KillMode=control-group", props)

    def test_service_properties_without_budget_adds_no_cap(self):
        props = host.service_properties(None, 2.0)
        self.assertNotIn(True, [item.startswith(("TasksMax=", "MemoryMax=", "CPUQuota=")) for item in props])
        self.assertIn("RuntimeMaxSec=7.000s", props)

    def test_cgroup_populated_reads_events(self):
        with tempfile.TemporaryDirectory() as directory:
            cg = pathlib.Path(directory)
            (cg / "cgroup.events").write_text("populated 0\nfrozen 0\n", encoding="utf-8")
            self.assertEqual(host.cgroup_populated(cg), 0)

    def test_cleanup_of_empty_owned_cgroup_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            cg = pathlib.Path(directory)
            (cg / "cgroup.procs").write_text("", encoding="utf-8")
            result = host.kill_owned_cgroup(cg, "owned/path", grace_seconds=0.05)
        self.assertEqual(result["term_pids"], [])
        self.assertEqual(result["kill_pids"], [])
        self.assertFalse(result["kill_fallback"])
        self.assertEqual(result["errors"], [])

    def test_cleanup_preserves_bootstrap_pid_without_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            cg = pathlib.Path(directory)
            (cg / "cgroup.procs").write_text("", encoding="utf-8")
            result = host.kill_owned_cgroup(cg, "owned/path", grace_seconds=0.05, preserve_pids={os.getpid()})
        self.assertEqual(result["preserved_pids"], [])
        self.assertEqual(result["term_pids"], [])

    def test_watchdog_phase_and_deadline(self):
        self.assertEqual(host.watchdog_phase("aa-first", False), "aa-first")
        self.assertEqual(host.watchdog_phase("aa-first", True), "runtime-first")
        self.assertEqual(host.watchdog_phase("pair-second", True), "runtime-second")
        with self.assertRaises(ValueError):
            host.watchdog_phase("unknown", True)
        self.assertEqual(host.qgroup_diagnostic_next_deadline(0, 0, 1), 1_000_000_000)
        self.assertEqual(
            host.qgroup_diagnostic_next_deadline(0, 2_500_000_000, 2_600_000_000),
            3_500_000_000,
        )

    def test_watchdog_observer_summary_flags_incomplete_telemetry(self):
        incomplete = host.watchdog_observer_summary([{"watchdog_sample_start_monotonic_ns": 1}])
        self.assertFalse(incomplete["telemetry_complete"])
        complete = host.watchdog_observer_summary(
            [
                {
                    "watchdog_sample_start_monotonic_ns": 1,
                    "watchdog_loop_cpu_ns": 10,
                    "watchdog_loop_work_ns": 5,
                },
                {
                    "watchdog_sample_start_monotonic_ns": 11,
                    "watchdog_loop_cpu_ns": 20,
                    "watchdog_loop_work_ns": 10,
                },
            ]
        )
        self.assertTrue(complete["telemetry_complete"])
        self.assertEqual(complete["sample_start_gap_max_ns"], 10)
        self.assertEqual(complete["watchdog_loop_cpu_ns_sum"], 30)
        self.assertEqual(complete["watchdog_cpu_to_active_work_ratio"], 2.0)

    def test_quota_attestation_valid_and_tamper_fails_closed(self):
        now = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
        expected = {
            "attestation_owner_uid": 0,
            "filesystem_uuid": "uuid",
            "subvolume_id": 259,
            "qgroup_id": "0/259",
            "max_referenced_bytes": 134_217_728,
            "accounting_mode": "squota",
            "workspace_subvolume": "/workspace",
            "max_attestation_age_seconds": 3600,
        }
        attestation = {
            "schema_version": 1,
            "mountpoint": "/",
            "verified_at_utc": (now - timedelta(minutes=1)).isoformat(),
            **{key: expected[key] for key in (
                "filesystem_uuid",
                "subvolume_id",
                "qgroup_id",
                "max_referenced_bytes",
                "accounting_mode",
                "workspace_subvolume",
            )},
        }
        proof = host.validate_workspace_quota_attestation(
            attestation, expected, owner_uid=0, mode=stat.S_IRUSR | stat.S_IWUSR, now=now
        )
        self.assertEqual(proof["status"], "VERIFIED")
        tampered = dict(attestation, qgroup_id="0/260")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            host.validate_workspace_quota_attestation(
                tampered, expected, owner_uid=0, mode=stat.S_IRUSR | stat.S_IWUSR, now=now
            )
        stale = dict(attestation, verified_at_utc=(now - timedelta(hours=2)).isoformat())
        with self.assertRaisesRegex(ValueError, "stale"):
            host.validate_workspace_quota_attestation(
                stale, expected, owner_uid=0, mode=stat.S_IRUSR | stat.S_IWUSR, now=now
            )


if __name__ == "__main__":
    unittest.main()
