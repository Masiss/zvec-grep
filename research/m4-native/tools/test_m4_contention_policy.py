#!/usr/bin/env python3
"""Synthetic contention policy / independent-watchdog integration; no M4 execution."""

import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

BASE = pathlib.Path(__file__).resolve().parents[1]
HERE = (
    BASE
    / "results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019"
)
os.environ.setdefault("M4_RESOURCE_ROOT", "/var/tmp")  # noqa: S108 -- required research resource domain
os.environ.setdefault(
    "M4_AA_BINARY",
    str(BASE / "results/durability-restore-20261020/m4_bridge_integration.full-sync"),
)
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location(
    "contention_test_aa", HERE / "run_host_carryover_aa.py"
)
assert spec and spec.loader
aa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aa)
ct = aa.runner.containment


def context(root):
    root = pathlib.Path(root).resolve()
    return {
        "schema_version": 1,
        "mode": "contention-aware",
        "approval_status": "approved",
        "approval_reference": "SYNTHETIC ONLY",
        "execution_authorization_reference": "SYNTHETIC ONLY",
        "risk_acceptance_reference": "SYNTHETIC ONLY",
        "resource_basis": "tiny synthetic fixture, never a benchmark budget",
        "policy_sha256": hashlib.sha256(ct.POLICY_PATH.read_bytes()).hexdigest(),
        "operation_id": "m4-contention-fixture",
        "operation_root": str(root / "m4-contention-fixture"),
        "resource_root": str(root),
        "binary_sha256": ct.BENCHMARK_POLICY["contention_aware"][
            "full_sync_binary_sha256"
        ],
        "workload": {"docs": 500, "rounds": 20, "tracing": False},
        "memory_max_bytes": 64 * 1024**2,
        "memory_swap_max_bytes": 0,
        "cpu_max": "50000 100000",
        "pids_max": 128,
        "workspace_max_allocated_bytes": 16 * 1024**2,
        "workspace_max_apparent_bytes": 16 * 1024**2,
        "host_memory_reserve_bytes": 16 * 1024**2,
        "disk_reserve_bytes": 16 * 1024**2,
        "runtime_limit_seconds": 60,
        "max_monitor_gap_seconds": 2,
    }


def state():
    pressure = {
        "some": {"avg10": 30.0, "total": 10000},
        "full": {"avg10": 20.0, "total": 1000},
    }
    return {
        "timestamp_utc": "synthetic",
        "monotonic_ns": 10_000_000_000,
        "meminfo_kib": {
            "MemAvailable_kib": 1024**2,
            "SwapFree_kib": 1024**2,
            "MemTotal_kib": 8 * 1024**2,
            "SwapTotal_kib": 2 * 1024**2,
        },
        "resource_root_free_bytes": 256 * 1024**2,
        "loadavg": [12.0],
        "io_pressure": copy.deepcopy(pressure),
        "memory_pressure": copy.deepcopy(pressure),
        "cpu_pressure": copy.deepcopy(pressure),
        "vmstat": {
            "pswpin_pages": 123,
            "pswpout_pages": 321,
            "oom_kill": 0,
            "pgscan_direct": 0,
        },
        "diskstats": {"testdev": {"sectors_read": 1, "sectors_written": 2, "io_ms": 3}},
    }


def cgroup():
    pressure = {"some": {"avg10": 0.2, "total": 1}, "full": {"avg10": 0.1, "total": 1}}
    return {
        "process_io_sample_complete": True,
        "process_memory_sample_complete": True,
        "memory_current_bytes": 1024,
        "memory_swap_current_bytes": 0,
        "memory_peak_bytes": 2048,
        "memory_swap_peak_bytes": 0,
        "memory_events": {"max": 0, "oom": 0, "oom_kill": 0, "oom_group_kill": 0},
        "memory_swap_events": {"max": 0, "fail": 0},
        "pids_current": 2,
        "pids_events": {"max": 0},
        "cpu_stat": {"usage_usec": 100},
        "memory_pressure": copy.deepcopy(pressure),
        "io_pressure": copy.deepcopy(pressure),
        "cpu_pressure": copy.deepcopy(pressure),
        "io_stat": None,
        "io_max": None,
        "process_tree_pss_kib": 1,
        "process_tree_write_bytes": 0,
        "populated": 1,
    }


class ContentionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="m4-contention-unit-")
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ctx = context(self.root)

    def test_rehearsal_admission_cannot_substitute_native_or_arbitrary_code(self):
        ctx = dict(
            self.ctx,
            purpose="synthetic-containment-rehearsal",
            operation_id="m4-contention-rehearsal-test",
            operation_root=str(self.root / "m4-contention-rehearsal-test"),
            workload=dict(ct.REHEARSAL_WORKLOAD),
            binary_sha256=hashlib.sha256(
                pathlib.Path(sys.executable).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(ct.validate_contention_context(ctx), ctx)
        for change in (
            {"workload": self.ctx["workload"]},
            {"purpose": "unknown"},
            {"binary_sha256": self.ctx["binary_sha256"]},
            {"operation_id": "m4-contention-fixture"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ct.validate_contention_context(dict(ctx, **change))
        for command in (
            [sys.executable, "-c", 'print("not allowlisted")'],
            [
                "/native",
                str(self.root / "workspace/root"),
                "x",
                "--incremental-fts-sustained",
                "500",
                "20",
            ],
        ):
            with (
                self.subTest(command=command),
                self.assertRaisesRegex(ValueError, "binding mismatch"),
            ):
                ct.run_arm(
                    command,
                    pathlib.Path(str(ctx["operation_root"])) / "aa-first/containment",
                    self.root,
                    self.root / "workspace",
                    self.root / "workspace",
                    ctx["runtime_limit_seconds"],
                    budget=ct.contention_budget(ctx),
                    policy_context=ctx,
                )
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            ct.run_arm(
                [sys.executable, "-c", ct.REHEARSAL_CODE],
                pathlib.Path(self.ctx["operation_root"]) / "aa-first/containment",
                self.root,
                self.root / "workspace",
                self.root / "workspace",
                self.ctx["runtime_limit_seconds"],
                budget=ct.contention_budget(self.ctx),
                policy_context=self.ctx,
            )

    def test_m4_scalar_rehearsal_binds_84_open_workload_and_approved_host_floors(self):
        binary_path = self.root / "m4_native_scalar_open_resource_rehearsal"
        binary_path.write_bytes(b"synthetic pinned binary identity; never launched")
        ctx: dict[str, Any] = dict(
            self.ctx,
            purpose="m4-scalar-open-resource-rehearsal",
            operation_id="m4-scalar-open-rehearsal-test",
            operation_root=str(self.root / "m4-scalar-open-rehearsal-test"),
            binary_path=str(binary_path),
            binary_sha256=hashlib.sha256(binary_path.read_bytes()).hexdigest(),
            approved_binary_sha256=hashlib.sha256(binary_path.read_bytes()).hexdigest(),
            workload={"scalar_counts": list(range(7)), "vector_types": ["HNSW", "FLAT"],
                      "repetitions": 3, "api_opens": 84, "documents": 0, "queries": 0},
            host_memory_reserve_bytes=3 * 1024**3,
            disk_reserve_bytes=8 * 1024**3,
        )
        self.assertEqual(ct.validate_contention_context(ctx), ctx)
        workspace = self.root / "workspace"
        workspace.mkdir()
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            ct.run_arm(
                [str(binary_path), str(workspace / "wrong-root"), "--not-the-approved-workload"],
                pathlib.Path(ctx["operation_root"]) / "aa-first/containment",
                self.root, workspace, workspace, ctx["runtime_limit_seconds"],
                budget=ct.contention_budget(ctx), policy_context=ctx,
            )
        current = state()
        current["meminfo_kib"]["MemAvailable_kib"] = 3 * 1024**2
        current["resource_root_free_bytes"] = 8 * 1024**3
        current["loadavg"] = [1.0]
        for family in ("io_pressure", "memory_pressure", "cpu_pressure"):
            current[family]["some"]["avg10"] = 1.0
            current[family]["full"]["avg10"] = 1.0
        self.assertTrue(ct.contention_decision(current, ctx, "aa-first")["continuation_allowed"])
        current["loadavg"] = [5.0]
        observed = ct.contention_decision(current, ctx, "aa-first")
        self.assertTrue(observed["continuation_allowed"])
        load_gate = next(g for g in observed["historical_gate_decisions"] if g["gate_id"] == "M4-RG-LOAD")
        self.assertEqual(load_gate["status"], "FAIL")
        self.assertFalse(load_gate["enforced_in_selected_mode"])
        self.assertIn("not an admission or abort gate", load_gate["selected_mode_disposition"])
        current["meminfo_kib"]["MemAvailable_kib"] -= 1
        self.assertFalse(ct.contention_decision(current, ctx, "aa-first")["continuation_allowed"])
        current["meminfo_kib"]["MemAvailable_kib"] = 3 * 1024**2
        current["resource_root_free_bytes"] -= 1
        self.assertFalse(ct.contention_decision(current, ctx, "aa-first")["continuation_allowed"])
        malformed: dict[str, Any] = dict(ctx)
        malformed["workload"] = {
            "scalar_counts": list(range(7)), "vector_types": ["HNSW", "FLAT"],
            "repetitions": 3, "api_opens": 28, "documents": 0, "queries": 0,
        }
        with self.assertRaisesRegex(ValueError, "exact 84-open"):
            ct.validate_contention_context(malformed)

    def test_m4_latency_diagnostic_binds_exact_workload_and_nonqualifying_class(self):
        binary_path = self.root / "m4_native_scalar_open_latency_diagnostic"
        binary_path.write_bytes(b"synthetic pinned binary identity; never launched")
        binary_sha = hashlib.sha256(binary_path.read_bytes()).hexdigest()
        ctx = dict(
            self.ctx,
            purpose=ct.M4_SCALAR_LATENCY_PURPOSE,
            operation_id="m4-scalar-open-latency-diagnostic-test",
            operation_root=str(self.root / "m4-scalar-open-latency-diagnostic-test"),
            binary_path=str(binary_path),
            binary_sha256=binary_sha,
            approved_binary_sha256=binary_sha,
            workload={"scalar_counts": list(range(7)), "vector_types": ["HNSW", "FLAT"],
                      "repetitions": 3, "api_opens": 84, "documents": 0, "queries": 0},
            host_memory_reserve_bytes=3 * 1024**3,
            disk_reserve_bytes=8 * 1024**3,
        )
        self.assertEqual(ct.validate_contention_context(ctx), ctx)
        self.assertEqual(ct.contention_evidence_class(ctx), "EXPLORATORY / NON-QUALIFYING")
        workspace = self.root / "workspace-latency"
        workspace.mkdir()
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            ct.run_arm(
                [str(binary_path), str(workspace / "wrong-root"), "--m4-scalar-open-resource-rehearsal"],
                self.root / "m4-scalar-open-latency-diagnostic-test/aa-first/containment",
                self.root, workspace, workspace, ctx["runtime_limit_seconds"],
                budget=ct.contention_budget(ctx), policy_context=ctx,
            )
        current = state()
        current["meminfo_kib"]["MemAvailable_kib"] = 3 * 1024**2
        current["resource_root_free_bytes"] = 8 * 1024**3
        current["loadavg"] = [5.0]
        for family in ("io_pressure", "memory_pressure", "cpu_pressure"):
            current[family]["some"]["avg10"] = 1.0
            current[family]["full"]["avg10"] = 1.0
        decision = ct.contention_decision(current, ctx, "aa-first")
        self.assertTrue(decision["continuation_allowed"])
        load_gate = next(g for g in decision["historical_gate_decisions"] if g["gate_id"] == "M4-RG-LOAD")
        self.assertEqual(load_gate["status"], "FAIL")
        self.assertFalse(load_gate["enforced_in_selected_mode"])

    def test_m4_scalar_rehearsal_records_swap_reclaim_without_blocking(self):
        baseline = {"pswpin_pages": 10, "pswpout_pages": 20, "pgscan_direct": 30,
                    "pgsteal_direct": 25, "allocstall_dma": 0, "oom_kill": 0}
        observed = ct.m4_scalar_rehearsal_host_observation({"vmstat": dict(baseline)}, baseline)
        self.assertEqual(observed["blocking_failures"], [])
        changed = dict(baseline, pswpin_pages=11, pswpout_pages=21, pgscan_direct=33,
                       pgsteal_direct=27, allocstall_dma=2)
        observed = ct.m4_scalar_rehearsal_host_observation({"vmstat": changed}, baseline)
        self.assertEqual(observed["blocking_failures"], [])
        self.assertEqual(observed["baseline_to_sample_deltas"], {
            "pswpin_pages": 1, "pswpout_pages": 1, "pgscan_direct": 3,
            "pgsteal_direct": 2, "allocstall_dma": 2, "oom_kill": 0,
        })
        oom = dict(changed, oom_kill=1)
        self.assertTrue(ct.m4_scalar_rehearsal_host_observation({"vmstat": oom}, baseline)["blocking_failures"])
        reset = dict(changed, pswpout_pages=19)
        self.assertTrue(ct.m4_scalar_rehearsal_host_observation({"vmstat": reset}, baseline)["telemetry_errors"])

    def shared_collection_pair_context(self) -> dict[str, Any]:
        contract = ct.BENCHMARK_POLICY["shared_collection_pair"]
        binary_path = self.root / contract["binary_name"]
        binary_path.write_bytes(b"synthetic pinned binary; never launched")
        dictionary_path = self.root / "jieba-dict"
        dictionary_path.mkdir()
        (dictionary_path / "dict.txt").write_text("synthetic dictionary fixture\n")
        binary_sha = hashlib.sha256(binary_path.read_bytes()).hexdigest()
        dictionary_sha = ct.directory_sha256(dictionary_path)
        operation_id = "m4-shared-collection-pair-test"
        ctx: dict[str, Any] = {
            "schema_version": 1,
            "mode": ct.CONTENTION_MODE,
            "approval_status": "approved",
            "approval_reference": contract["approval_reference"],
            "execution_authorization_reference": contract["execution_authorization_reference"],
            "risk_acceptance_reference": contract["risk_acceptance_reference"],
            "resource_basis": "synthetic policy test only",
            "policy_sha256": hashlib.sha256(ct.POLICY_PATH.read_bytes()).hexdigest(),
            "operation_id": operation_id,
            "operation_root": str((self.root / operation_id).resolve()),
            "resource_root": contract["resource_root"],
            "purpose": contract["purpose"],
            "binary_path": str(binary_path.resolve()),
            "binary_sha256": binary_sha,
            "approved_binary_sha256": binary_sha,
            "dictionary_path": str(dictionary_path.resolve()),
            "dictionary_tree_sha256": dictionary_sha,
            "approved_dictionary_tree_sha256": dictionary_sha,
            "workload": contract["workload"],
            "arm_role": "immutable-shard",
            "io_pressure_mode": contract["io_pressure_mode"],
        }
        ctx.update(contract["resource_controls"])
        return ctx

    def test_shared_collection_pair_binds_approved_workload_and_input_identities(self):
        ctx = self.shared_collection_pair_context()
        self.assertEqual(ct.validate_contention_context(ctx), ctx)
        self.assertEqual(ct.contention_evidence_class(ctx), ct.CONTENTION_EVIDENCE)
        bad_workload = dict(ctx, workload={**ctx["workload"], "rounds": 20})
        with self.assertRaisesRegex(ValueError, "owner-approved operation contract"):
            ct.validate_contention_context(bad_workload)
        bad_dictionary = dict(ctx, dictionary_tree_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "owner-approved operation contract"):
            ct.validate_contention_context(bad_dictionary)
        workspace = self.root / "pair-workspace"
        workspace.mkdir()
        wrong_command = [ctx["binary_path"], str(workspace / "root"), ctx["dictionary_path"],
                         "--shared-collection-performance", "500", "5"]
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            ct.run_arm(
                wrong_command,
                pathlib.Path(ctx["operation_root"]) / "pair-first/containment",
                pathlib.Path(ctx["resource_root"]), workspace, workspace,
                ctx["runtime_limit_seconds"], budget=ct.contention_budget(ctx),
                policy_context=ctx,
            )

    def test_shared_collection_pair_enforces_capacity_and_records_io_pressure_only(self):
        ctx = self.shared_collection_pair_context()
        current = state()
        current["meminfo_kib"]["MemAvailable_kib"] = 4 * 1024**2
        current["resource_root_free_bytes"] = 32 * 1024**3
        current["io_pressure"]["full"]["avg10"] = 4.48
        current["loadavg"] = [12.0]
        decision = ct.contention_decision(current, ctx, "pair-first")
        self.assertTrue(decision["continuation_allowed"])
        self.assertTrue(any(g["gate_id"] == "M4-RG-MEM" and g["status"] == "FAIL"
                            for g in decision["historical_gate_decisions"]))
        io_gate = next(g for g in decision["historical_gate_decisions"] if g["gate_id"] == "M4-RG-IOPSI")
        self.assertEqual(io_gate["status"], "FAIL")
        self.assertFalse(io_gate["enforced_in_selected_mode"])
        self.assertIn("not an admission or abort gate", io_gate["selected_mode_disposition"])
        self.assertEqual(decision["host_io_pressure_observation"]["full_avg10_percent"], 4.48)
        self.assertIn("observational-only", decision["host_io_pressure_observation"]["disposition"])
        current["meminfo_kib"]["MemAvailable_kib"] -= 1
        self.assertFalse(ct.contention_decision(current, ctx, "pair-first")["continuation_allowed"])

    def test_all_four_historical_failures_are_raw_not_passes(self):
        d = ct.contention_decision(state(), self.ctx, "baseline")
        self.assertEqual(len(d["historical_guard_failures"]), 4)
        self.assertTrue(
            all(g["status"] == "FAIL" for g in d["historical_guard_failures"])
        )
        self.assertTrue(d["continuation_allowed"])
        self.assertEqual(
            d["evidence_class"], "EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING"
        )
        self.assertEqual(len(ct.hard_gate_failures(state())), 4)

    def test_qualification_and_default_diagnostic_still_block_mixed_history(self):
        failures = ct.contention_decision(state(), self.ctx, "baseline")[
            "historical_guard_failures"
        ]
        for mode in ("qualification", "diagnostic"):
            d = aa.baseline_gate_decision(
                mode, {"status": "VALID"}, {}, failures, [], False
            )
            self.assertEqual(d["operation_outcome"], "OPERATION_BLOCKED")
        for v, expected in ((4.46, "PASS"), (4.68, "FAIL")):
            s = state()
            s["io_pressure"]["full"]["avg10"] = v
            io = next(
                g
                for g in ct.resource_guard_decisions(s)
                if g["gate_id"] == "M4-RG-IOPSI"
            )
            self.assertEqual(io["status"], expected)

    def test_capacity_failures_do_not_become_quality_failures(self):
        for phase in (
            "baseline",
            "aa-first",
            "runtime-first",
            "recovery",
            "aa-second",
            "runtime-second",
        ):
            for resource in ("memory", "disk"):
                s = state()
                if resource == "memory":
                    s["meminfo_kib"]["MemAvailable_kib"] = 0
                else:
                    s["resource_root_free_bytes"] = 0
                d = ct.contention_decision(s, self.ctx, phase)
                self.assertFalse(d["continuation_allowed"])
                self.assertTrue(d["blocking_failures"])
                self.assertEqual(len(d["historical_guard_failures"]), 4)

    def test_swap_capacity_reserves_only_remaining_approved_workload_budget(self):
        ctx = dict(self.ctx, memory_swap_max_bytes=2 * 1024**3)
        d = ct.contention_decision(state(), ctx, "baseline")
        self.assertFalse(d["continuation_allowed"])
        self.assertEqual(d["blocking_failures"][0]["gate_id"], "M4-CA-SWAP-CAPACITY")
        d = ct.contention_decision(
            state(), ctx, "runtime-first", owned_swap_bytes=1536 * 1024**2
        )
        self.assertTrue(d["continuation_allowed"])

    def test_missing_nonfinite_nonnumeric_out_of_range_telemetry_fails_closed(self):
        for bad in (None, float("nan"), float("inf"), "4.68", True, -1, 101):
            for family in ("io_pressure", "memory_pressure", "cpu_pressure"):
                s = state()
                s[family]["some"]["avg10"] = bad
                with (
                    self.subTest(family=family, bad=bad),
                    self.assertRaises(ValueError),
                ):
                    ct.contention_decision(s, self.ctx, "baseline")
        for key in ("diskstats", "vmstat", "meminfo_kib", "loadavg"):
            s = state()
            s.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                ct.contention_decision(s, self.ctx, "baseline")

    def test_no_ambient_or_cross_operation_override(self):
        for key, value in (
            ("approval_status", "pending"),
            ("execution_authorization_reference", ""),
            ("risk_acceptance_reference", ""),
            ("policy_sha256", "stale"),
            ("binary_sha256", "57f3a19e"),
            ("mode", "qualification"),
            ("operation_id", "../other"),
            ("cpu_max", "max 100000"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ct.validate_contention_context(dict(self.ctx, **{key: value}))
        with self.assertRaises(ValueError):
            aa.baseline_gate_decision(
                "qualification",
                {"status": "VALID"},
                {},
                [],
                [],
                True,
                policy_context=self.ctx,
            )
        with self.assertRaises(ValueError):
            aa.runner.run_arm(
                "aa-first",
                pathlib.Path(os.environ["M4_AA_BINARY"]),
                mode="contention-aware",
            )

    def test_baseline_integrity_not_waived_by_contention(self):
        decisions = [ct.contention_decision(state(), self.ctx, "baseline")]
        for status, outcome in (
            ("VALID", "OPERATION_READY_EXPLORATORY"),
            ("MEASUREMENT_INVALID", "OPERATION_BLOCKED"),
        ):
            d = aa.baseline_gate_decision(
                "contention-aware",
                {"status": status},
                {},
                decisions[0]["historical_guard_failures"],
                [{"historical_pressure_drift": True}],
                False,
                policy_context=self.ctx,
                contention_decisions=decisions,
            )
            self.assertEqual(d["operation_outcome"], outcome)
            self.assertEqual(d["evidence_class"], ct.CONTENTION_EVIDENCE)

    def test_owned_limit_distress_and_incomplete_attribution_fail_closed(self):
        before = cgroup()
        for family, key in (
            ("memory_events", "oom"),
            ("memory_events", "max"),
            ("memory_swap_events", "fail"),
        ):
            after = copy.deepcopy(before)
            after[family][key] = 1
            self.assertTrue(ct.contention_runtime_failures(after, before))
        for key in ("process_io_sample_complete", "process_memory_sample_complete"):
            after = copy.deepcopy(before)
            after[key] = False
            with self.assertRaises(ValueError):
                ct.contention_runtime_failures(after, before)
        after = copy.deepcopy(before)
        after["cpu_stat"]["usage_usec"] = 0
        with self.assertRaises(ValueError):
            ct.contention_runtime_failures(after, before)

    def test_watchdog_phase_preserves_first_arm_reserve_for_pair(self):
        self.assertEqual(ct.watchdog_phase("pair-first", False), "pair-first")
        self.assertEqual(ct.watchdog_phase("pair-first", True), "runtime-first")
        self.assertEqual(ct.watchdog_phase("pair-second", False), "pair-second")
        self.assertEqual(ct.watchdog_phase("pair-second", True), "runtime-second")
        with self.assertRaises(ValueError):
            ct.watchdog_phase("unknown", True)

    def test_shared_pair_watchdog_accepts_only_pair_arm_names(self):
        ctx = self.shared_collection_pair_context()
        base = self.watchdog_config()
        for arm_name in ("pair-first", "pair-second"):
            output = pathlib.Path(ctx["operation_root"]) / arm_name / "containment"
            output.mkdir(parents=True, exist_ok=True)
            config = dict(base, policy_context=ctx, policy_context_digest=ct.contention_context_digest(ctx),
                          arm_name=arm_name, watchdog_output=str(output),
                          resource_root=ctx["resource_root"], runtime_limit_seconds=ctx["runtime_limit_seconds"],
                          budget=ct.contention_budget(ctx))
            path = self.root / f"{arm_name}.json"
            path.write_text(json.dumps(config))
            self.assertEqual(ct.read_watchdog_config(path)["arm_name"], arm_name)
        config["arm_name"] = "aa-first"
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            ct.read_watchdog_config(path)

    def test_watchdog_policy_binding_cannot_drop_or_swap_context(self):
        config = self.watchdog_config()
        path = self.root / "config.json"
        for key, value in (
            ("policy_context", None),
            ("budget", None),
            ("arm_name", "other-arm"),
            ("resource_root", "/some-other-operation"),
            ("enforce_host_gates", False),
        ):
            path.write_text(json.dumps(dict(config, **{key: value})))
            with self.subTest(key=key), self.assertRaises(ValueError):
                ct.read_watchdog_config(path)
        path.write_text(json.dumps(config))
        self.assertEqual(ct.read_watchdog_config(path)["policy_context"], self.ctx)

    def watchdog_config(self):
        cg = self.root / "cg"
        cg.mkdir(exist_ok=True)
        (cg / "cgroup.kill").touch()
        for name, key in (
            ("memory.max", "memory_max_bytes"),
            ("memory.swap.max", "memory_swap_max_bytes"),
            ("cpu.max", "cpu_max"),
            ("pids.max", "pids_max"),
        ):
            (cg / name).write_text(str(self.ctx[key]))
        out = pathlib.Path(self.ctx["operation_root"]) / "aa-first/containment"
        out.mkdir(parents=True, exist_ok=True)
        sock = self.root / "socket"
        sock.mkdir(exist_ok=True)
        (sock / "started").touch()
        return {
            "cgroup_path": str(cg),
            "cgroup_rel": "SYNTHETIC",
            "workspace_root": str(self.root),
            "resource_root": str(self.root),
            "sample_interval_ms": 20,
            "runtime_limit_seconds": 60,
            "watchdog_output": str(out),
            "controller_pid": os.getpid(),
            "bootstrap_pid": os.getpid(),
            "start_marker": str(sock / "started"),
            "command_done_marker": str(sock / "done"),
            "watchdog_ready_path": str(sock / "ready"),
            "socket_dir": str(sock),
            "pressure_limits": None,
            "policy_context": self.ctx,
            "policy_mode": "contention-aware",
            "policy_context_digest": ct.contention_context_digest(self.ctx),
            "arm_name": "aa-first",
            "enforce_host_gates": True,
            "budget": ct.contention_budget(self.ctx),
            "unit": "SYNTHETIC-NEVER-A-REAL-UNIT",
        }

    def run_watchdog(self, host_state, group=None, workspace=None):
        config = self.watchdog_config()
        path = self.root / "config.json"
        path.write_text(json.dumps(config))
        calls = 0

        def read_group(_):
            nonlocal calls
            calls += 1
            g = copy.deepcopy(group or cgroup())
            if calls >= 3:
                g["populated"] = 0
            return g

        work = workspace if workspace is not None else self.workspace_row()

        with (
            mock.patch.object(ct, "host_snapshot", return_value=host_state),
            mock.patch.object(ct, "read_cgroup_metric", side_effect=read_group),
            mock.patch.object(ct, "workspace_usage", return_value=work),
            mock.patch.object(
                ct, "inspect_shared_extents", return_value={"available": False}
            ),
            mock.patch.object(
                ct, "kill_owned_cgroup", return_value={"verified": True}
            ) as killed,
            mock.patch.object(ct.time, "sleep"),
        ):
            self.assertEqual(ct.watchdog_main(path), 0)
        out = pathlib.Path(config["watchdog_output"])
        return (
            json.loads((out / "watchdog-summary.json").read_text()),
            [json.loads(line) for line in (out / "runtime.jsonl").read_text().splitlines()],
            killed.called,
        )

    @staticmethod
    def workspace_row(**changes):
        return dict(
            {
                "scan_complete": True,
                "inventory_status": "COMPLETE_TRAVERSAL",
                "scan_errors": [],
                "apparent_bytes": 0,
                "allocated_st_blocks_bytes_gross": 0,
                "staging_named_apparent_bytes": 0,
                "generation_apparent_bytes": 0,
            },
            **changes,
        )

    def test_soft_budget_and_workspace_uncertainty_never_become_pass(self):
        self.assertEqual(
            ct.contention_completion_status("PASS", True, True, 1)[0],
            "COMPLETED_WITH_WORKSPACE_WARNINGS",
        )
        self.assertEqual(
            ct.contention_completion_status("PASS", True, False, 0)[0],
            "COMPLETED_WITH_WORKSPACE_UNCERTAINTY",
        )
        self.assertEqual(
            ct.contention_completion_status("PASS", False, False, 0)[0],
            "TELEMETRY_INCOMPLETE",
        )

    def test_soft_budgets_record_both_overages_without_hard_abort(self):
        work = self.workspace_row(
            apparent_bytes=self.ctx["workspace_max_apparent_bytes"] + 17,
            allocated_st_blocks_bytes_gross=self.ctx["workspace_max_allocated_bytes"]
            + 23,
        )
        decision = ct.workspace_capacity_decision(
            work, ct.contention_budget(self.ctx), "contention-aware"
        )
        self.assertTrue(decision["continuation_allowed"])
        self.assertEqual(decision["state"], "SOFT_BUDGET_EXCEEDED")
        self.assertEqual([w["excess_bytes"] for w in decision["warnings"]], [23, 17])
        summary, rows, killed = self.run_watchdog(state(), workspace=work)
        self.assertIsNone(summary["abort"])
        self.assertFalse(killed)
        self.assertTrue(all(r["workspace_capacity_decision"]["warnings"] for r in rows))
        self.assertTrue(all(r["workspace_capacity_decision"]["state"] == "SOFT_BUDGET_EXCEEDED" for r in rows))
        self.assertEqual(summary["measurement_quality"], "COMPLETE")
        self.assertIsNone(summary["workspace_verified_peak"])
        self.assertEqual(summary["workspace_soft_budget_warning_samples"], len(rows))
        self.assertTrue(summary["hard_safety_telemetry_complete"])

    def test_soft_race_continues_but_accounting_is_unknown_not_pass(self):
        work = self.workspace_row(
            scan_complete=False,
            inventory_status="PARTIAL_KNOWN_RACE",
            scan_errors=["ENOENT: owned atomic temporary renamed"],
        )
        summary, rows, killed = self.run_watchdog(state(), workspace=work)
        self.assertIsNone(summary["abort"])
        self.assertFalse(killed)
        self.assertTrue(summary["hard_safety_telemetry_complete"])
        self.assertFalse(summary["workspace_samples_complete"])
        self.assertEqual(summary["workspace_accounting_status"], "UNKNOWN")
        self.assertEqual(summary["measurement_quality"], "INCOMPLETE")
        self.assertIsNone(summary["workspace_verified_peak"])
        self.assertIsNone(summary["peak_sampled_workspace_apparent_bytes"])
        self.assertTrue(
            all(r["workspace_capacity_decision"]["state"] == "UNKNOWN" for r in rows)
        )
        self.assertTrue(all(not r["workspace"]["scan_complete"] for r in rows))

    def test_pending_drain_is_unknown_without_extra_drain_or_fake_complete(self):
        with mock.patch.object(ct.select, "select", return_value=([1], [], [])):
            work = ct.workspace_usage(self.root)
        self.assertFalse(work["scan_complete"])
        self.assertEqual(work["inventory_status"], "UNVERIFIABLE")
        self.assertEqual(work["issues"][-1]["detail"], "events exceed one bounded drain")
        before = copy.deepcopy(work)
        decision = ct.workspace_capacity_decision(work, ct.contention_budget(self.ctx), "contention-aware")
        self.assertTrue(decision["continuation_allowed"])
        self.assertEqual(decision["state"], "UNKNOWN")
        self.assertEqual(decision["measurement_quality"], "INCOMPLETE")
        self.assertEqual(work, before)
        self.assertFalse(ct.workspace_capacity_decision(work, ct.contention_budget(self.ctx))["continuation_allowed"])
        summary, _, killed = self.run_watchdog(state(), workspace=work)
        self.assertFalse(killed)
        self.assertIsNone(summary["abort"])
        self.assertEqual(summary["measurement_quality"], "INCOMPLETE")

    def test_pending_drain_cannot_hide_overflow_scope_error_or_missing_examples(self):
        with mock.patch.object(ct.select, "select", return_value=([1], [], [])):
            work = ct.workspace_usage(self.root)
        for kind, detail in (("MUTATION_COVERAGE_LOST", "event overflow or unmount"),
                             ("MUTATION_COVERAGE_LOST", "truncated event record"),
                             ("SCOPE_UNVERIFIABLE", "workspace root replaced"),
                             ("FILESYSTEM_ERROR", "permission or EIO"),
                             ("WORK_BUDGET_EXHAUSTED", "entry/time budget")):
            row = copy.deepcopy(work)
            row["issues"].append({"kind": kind, "detail": detail, "known_race": False})
            row["issue_count"] += 1
            self.assertFalse(ct.workspace_capacity_decision(row, ct.contention_budget(self.ctx), "contention-aware")["continuation_allowed"])
        row = dict(work, issue_count=work["issue_count"] + 1)
        self.assertFalse(ct.workspace_capacity_decision(row, ct.contention_budget(self.ctx), "contention-aware")["continuation_allowed"])
        row = dict(work, root_identity=None)
        self.assertFalse(ct.workspace_capacity_decision(row, ct.contention_budget(self.ctx), "contention-aware")["continuation_allowed"])

    def test_soft_unknown_scope_still_aborts_watchdog(self):
        work = self.workspace_row(
            scan_complete=False,
            inventory_status="UNVERIFIABLE",
            scan_errors=["permission/EIO/root replacement/coverage lost"],
        )
        summary, _, killed = self.run_watchdog(state(), workspace=work)
        self.assertEqual(summary["abort"]["reason"], "workspace_capacity_unverifiable")
        self.assertTrue(killed)

    def test_soft_race_does_not_override_hard_disk_reserve(self):
        work = self.workspace_row(
            scan_complete=False, inventory_status="PARTIAL_KNOWN_RACE"
        )
        host = state()
        host["resource_root_free_bytes"] = 0
        summary, _, killed = self.run_watchdog(host, workspace=work)
        self.assertTrue(killed)
        self.assertEqual(summary["abort"]["reason"], "existing_host_guard")
        self.assertTrue(
            any(
                f.get("gate_id") == "M4-CA-DISK-CAPACITY"
                for f in summary["abort"]["failures"]
            )
        )

    def test_soft_race_does_not_override_missing_process_telemetry(self):
        work = self.workspace_row(
            scan_complete=False, inventory_status="PARTIAL_KNOWN_RACE"
        )
        group = cgroup()
        group["process_io_sample_complete"] = False
        summary, _, killed = self.run_watchdog(state(), group=group, workspace=work)
        self.assertTrue(killed)
        self.assertEqual(summary["abort"]["reason"], "required_telemetry_missing")

    def test_soft_contract_does_not_change_other_modes(self):
        budget = ct.contention_budget(self.ctx)
        work = self.workspace_row(
            apparent_bytes=budget["workspace_max_apparent_bytes"] + 1
        )
        race = self.workspace_row(
            scan_complete=False, inventory_status="PARTIAL_KNOWN_RACE"
        )
        for mode in ("legacy", "qualification", "diagnostic"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    ct.workspace_capacity_decision(work, budget, mode)[
                        "continuation_allowed"
                    ]
                )
                self.assertFalse(
                    ct.workspace_capacity_decision(race, budget, mode)[
                        "continuation_allowed"
                    ]
                )
        self.assertEqual(ct.workspace_reservation_credit(work), 0)

    def test_soft_contract_nonfinite_measurements_still_rejected(self):
        for invalid in (True, -1, None, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ct.workspace_capacity_decision(
                    self.workspace_row(apparent_bytes=invalid),
                    ct.contention_budget(self.ctx),
                    "contention-aware",
                )

    def test_independent_watchdog_allows_historical_failures_with_correct_context(self):
        summary, rows, killed = self.run_watchdog(state())
        self.assertIsNone(summary["abort"])
        self.assertEqual(
            summary["policy_context_digest"], ct.contention_context_digest(self.ctx)
        )
        ready = json.loads((self.root / "socket/ready").read_text())
        self.assertEqual(
            ready["policy_context_digest"], ct.contention_context_digest(self.ctx)
        )
        self.assertFalse(killed)
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(
            all(
                len(r["diagnostic_decision"]["historical_guard_failures"]) == 4
                for r in rows
            )
        )
        self.assertTrue(
            all(r["diagnostic_decision"]["continuation_allowed"] for r in rows)
        )

    def test_independent_watchdog_aborts_capacity_and_invalid_telemetry(self):
        s = state()
        s["resource_root_free_bytes"] = 0
        summary, _, killed = self.run_watchdog(s)
        self.assertIsNotNone(summary["abort"])
        self.assertTrue(killed)
        abort = json.loads(
            (
                pathlib.Path(self.ctx["operation_root"])
                / "aa-first/containment/watchdog-abort.json"
            ).read_text()
        )
        self.assertTrue(abort["final_cleanup"]["success"])
        # Summary is persisted before final cleanup; the final abort receipt owns cleanup status.
        self.root = self.root / "second"
        self.root.mkdir()
        self.ctx = context(self.root)
        s = state()
        s["io_pressure"]["full"]["avg10"] = float("nan")
        summary, _, killed = self.run_watchdog(s)
        self.assertEqual(summary["abort"]["reason"], "required_telemetry_missing")
        self.assertTrue(killed)

    def test_real_oom_evidence_stops_independent_watchdog(self):
        g = cgroup()
        g["memory_events"]["oom_kill"] = 1
        summary, _, killed = self.run_watchdog(state(), g)
        self.assertEqual(summary["abort"]["reason"], "cgroup_oom_event")
        self.assertTrue(killed)

    def test_task_limit_is_required_and_cap_changes_are_detected(self):
        for value in (None, 0, -1, True, "128"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ct.validate_contention_context(dict(self.ctx, pids_max=value))
        config = self.watchdog_config()
        cg = pathlib.Path(config["cgroup_path"])
        budget = ct.contention_budget(self.ctx)
        applied = ct.apply_budget(cg, budget)
        self.assertEqual(applied["pids_max"], "128")
        self.assertIn("TasksMax=128", ct.service_properties(budget, 60))
        self.assertFalse(
            any(p.startswith("TasksMax=") for p in ct.service_properties(None, 60))
        )
        (cg / "pids.max").write_text("max")
        with self.assertRaisesRegex(ValueError, "pids.max"):
            ct.verify_contention_caps(cg, self.ctx)

    def test_task_distress_and_missing_task_telemetry_fail_closed(self):
        previous = cgroup()
        after = cgroup()
        after["pids_events"]["max"] = 1
        self.assertTrue(
            any(
                "pids_events.max" in f
                for f in ct.contention_runtime_failures(after, previous)
            )
        )
        for key in ("pids_events", "pids_current"):
            after = cgroup()
            after[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                ct.contention_runtime_failures(after, previous)

    def test_cap_mismatch_is_not_an_approved_budget(self):
        config = self.watchdog_config()
        (pathlib.Path(config["cgroup_path"]) / "memory.max").write_text("max")
        with self.assertRaises(ValueError):
            ct.verify_contention_caps(pathlib.Path(config["cgroup_path"]), self.ctx)

    def test_outer_preflight_retains_failures_without_requiring_idle(self):
        pair = pathlib.Path(self.ctx["operation_root"])
        pair.mkdir()
        with (
            mock.patch.object(aa.runner, "PAIR", pair),
            mock.patch.object(
                aa.runner,
                "read_host",
                return_value={
                    "MemAvailable_kib": state()["meminfo_kib"]["MemAvailable_kib"],
                    "load1": 12,
                    "io_full_avg10": 20,
                    "resource_root_available_bytes": 256 * 1024**2,
                    "contention_host_snapshot": state(),
                },
            ),
        ):
            self.assertTrue(aa.runner.preflight("baseline", self.ctx))
        receipt = json.loads((pair / "baseline/preflight.json").read_text())
        self.assertEqual(receipt["status"], "OPERATION_READY_EXPLORATORY")
        self.assertEqual(len(receipt["resource_guard_failures"]), 4)
        self.assertTrue(receipt["diagnostic_decision"]["continuation_allowed"])

    def test_contained_preflight_capacity_failure_prevents_any_launch(self):
        pair = pathlib.Path(self.ctx["operation_root"])
        pair.mkdir()
        work = self.root / "work"
        work.mkdir()
        cgroot = self.root / "fake-cgroup-root"
        cgroot.mkdir()
        (cgroot / "cgroup.controllers").write_text("cpu memory pids")
        s = state()
        s["meminfo_kib"]["MemAvailable_kib"] = 0
        binary = pathlib.Path(os.environ["M4_AA_BINARY"])
        with (
            mock.patch.object(ct, "CGROOT", cgroot),
            mock.patch.object(ct, "host_snapshot", return_value=s),
            mock.patch.object(ct.subprocess, "Popen") as launch,
        ):
            receipt = ct.run_arm(
                [
                    str(binary),
                    str(work / "root"),
                    str(work),
                    "--incremental-fts-sustained",
                    "500",
                    "20",
                ],
                pair / "aa-first/containment",
                self.root,
                work,
                self.root,
                60,
                budget=ct.contention_budget(self.ctx),
                policy_context=self.ctx,
            )
        self.assertFalse(receipt["benchmark_started"])
        self.assertEqual(receipt["status"], "PREFLIGHT_BLOCKED")
        self.assertFalse(receipt["diagnostic_decision"]["continuation_allowed"])
        launch.assert_not_called()

    def test_contention_cannot_write_an_existing_index(self):
        work = self.root / "work"
        work.mkdir()
        (work / "root").mkdir()
        marker = work / "root/existing-data"
        marker.write_text("must survive")
        binary = pathlib.Path(os.environ["M4_AA_BINARY"])
        with self.assertRaises(ValueError):
            ct.run_arm(
                [
                    str(binary),
                    str(work / "root"),
                    str(work),
                    "--incremental-fts-sustained",
                    "500",
                    "20",
                ],
                pathlib.Path(self.ctx["operation_root"]) / "aa-first/containment",
                self.root,
                work,
                self.root,
                60,
                budget=ct.contention_budget(self.ctx),
                policy_context=self.ctx,
            )
        self.assertEqual(marker.read_text(), "must survive")

    def test_pair_recovery_observes_contention_without_idle_matching(self):
        self.run_synthetic_pair()

    def test_pair_stops_on_recovery_capacity_failure_without_retry(self):
        self.run_synthetic_pair(fail_recovery=True)

    def run_synthetic_pair(self, fail_recovery=False):
        pair = pathlib.Path(self.ctx["operation_root"])
        pair.mkdir()
        calls = []
        captures = []
        binary = pathlib.Path(os.environ["M4_AA_BINARY"])
        identity = aa.runner.binary_runtime_identity(binary)

        def arm(name, actual_binary, mode, pressure, policy_context=None):
            self.assertEqual(actual_binary, binary)
            self.assertEqual(pressure, limits)
            self.assertEqual(mode, "contention-aware")
            self.assertEqual(policy_context, self.ctx)
            calls.append(name)
            out = pair / name
            out.mkdir()
            (out / "binary-identity.json").write_text(json.dumps(identity))
            (out / "arm-result.json").write_text(
                json.dumps({"status": "PASS", "workload_root": str(out / "root")})
            )
            return True

        def capture():
            captures.append(1)
            rows = []
            for i in range(201):
                t = (100 + 20 * len(captures)) * 10**9 + i * 50_000_000
                s = state()
                s["monotonic_ns"] = t - 100
                s["diskstats"]["testdev"] = {
                    "sectors_read": i,
                    "sectors_written": i,
                    "io_ms": i,
                }
                if fail_recovery:
                    s["meminfo_kib"]["MemAvailable_kib"] = 0
                rows.append(
                    {
                        "monotonic_ns": t,
                        "epoch_ns": 1_800_000_000_000_000_000 + t,
                        "sample_start_monotonic_ns": t - 1000,
                        "sample_duration_ns": 1000,
                        "load1": 12,
                        "memavailable_kib": s["meminfo_kib"]["MemAvailable_kib"],
                        "swapfree_kib": 1024**2,
                        "io_some_avg10": 30,
                        "io_full_avg10": 20,
                        "memory_some_avg10": 30,
                        "memory_full_avg10": 20,
                        "pswpin": 123,
                        "pswpout": 321,
                        "diskstats_json": json.dumps(
                            {
                                "testdev": {
                                    "read_sectors": i,
                                    "write_sectors": i,
                                    "io_ms": i,
                                }
                            }
                        ),
                        "contention_host_json": json.dumps(s),
                    }
                )
            return rows, {"synthetic_fixture": True}

        def snapshot():
            return {
                "pass": not fail_recovery,
                "diagnostic_decision": ct.contention_decision(
                    state(), self.ctx, "recovery"
                ),
            }
        limits = {
            "swapfree_min_kib": 2 * 1024**2,
            "memory_some_avg10_max": 0.0,
            "memory_full_avg10_max": 0.0,
        }
        with mock.patch.object(aa.runner, "EXPECTED_AA_IDENTITY", identity):
            if fail_recovery:
                with self.assertRaises(SystemExit):
                    aa.run_contained_pair(
                        pair,
                        "contention-aware",
                        limits,
                        None,
                        arm_runner=arm,
                        capture_window=capture,
                        snapshot=snapshot,
                        policy_context=self.ctx,
                    )
                self.assertEqual(calls, ["aa-first"])
                self.assertEqual(len(captures), 1)
            else:
                aa.run_contained_pair(
                    pair,
                    "contention-aware",
                    limits,
                    None,
                    arm_runner=arm,
                    capture_window=capture,
                    snapshot=snapshot,
                    policy_context=self.ctx,
                )
                self.assertEqual(calls, ["aa-first", "aa-second"])
                self.assertEqual(len(captures), 3)
                receipt = json.loads((pair / "interarm-quiescence.json").read_text())
                self.assertTrue(receipt["recovered"])
                self.assertTrue(
                    all(
                        w["contention_decisions"][0]["historical_guard_failures"]
                        for w in receipt["windows"]
                    )
                )

    def test_observation_integrity_uses_declared_resolution_not_idle_cadence(self):
        rows = []
        for i in range(11):
            t = 100_000_000_000 + i * 1_000_000_000
            s = state()
            s["monotonic_ns"] = t - 100
            rows.append(
                {
                    "monotonic_ns": t,
                    "epoch_ns": 1_800_000_000_000_000_000 + t,
                    "sample_start_monotonic_ns": t - 1000,
                    "sample_duration_ns": 1000,
                    "load1": 12,
                    "memavailable_kib": 1024**2,
                    "swapfree_kib": 1024**2,
                    "io_some_avg10": 30,
                    "io_full_avg10": 20,
                    "memory_some_avg10": 30,
                    "memory_full_avg10": 20,
                    "diskstats_json": json.dumps(
                        {"testdev": {"read_sectors": 1, "write_sectors": 2, "io_ms": 3}}
                    ),
                    "pswpin": 123,
                    "pswpout": 321,
                    "contention_host_json": json.dumps(s),
                }
            )
        self.assertEqual(
            aa.validate_contention_samples(rows, self.ctx, "recovery", 10)["status"],
            "VALID",
        )
        broken = copy.deepcopy(rows)
        broken[1]["sample_duration_ns"] = -1
        self.assertEqual(
            aa.validate_contention_samples(broken, self.ctx, "recovery", 10)["status"],
            "MEASUREMENT_INVALID",
        )
        broken = rows[:2] + rows[5:]
        self.assertEqual(
            aa.validate_contention_samples(broken, self.ctx, "recovery", 10)["status"],
            "MEASUREMENT_INVALID",
        )

    def test_contention_mode_without_plan_never_probes_host(self):
        with (
            mock.patch.object(sys, "argv", ["aa", "--mode", "contention-aware"]),
            mock.patch.object(ct, "host_snapshot") as snapshot,
            self.assertRaises(SystemExit),
        ):
            aa.main()
        snapshot.assert_not_called()

    def test_correctness_durability_failure_is_not_a_quality_exception(self):
        binary = pathlib.Path(os.environ["M4_AA_BINARY"])
        original = pathlib.Path.read_text

        def read(path, *args, **kwargs):
            if str(path).endswith("faults-corrected/summary.json"):
                return json.dumps({"status": "FAIL"})
            return original(path, *args, **kwargs)

        with (
            mock.patch.object(pathlib.Path, "read_text", read),
            self.assertRaisesRegex(ValueError, "correctness/durability"),
        ):
            aa.runner.verify_full_sync(binary)

    def test_duplicate_operation_does_not_overwrite_prior_receipt(self):
        pair = self.root / "m4-contention-existing"
        pair.mkdir()
        original = pair / "operation-status.json"
        original.write_text("original consumed attempt")
        aa.record_unhandled_operation_exit(pair, FileExistsError("consumed"))
        self.assertEqual(original.read_text(), "original consumed attempt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
