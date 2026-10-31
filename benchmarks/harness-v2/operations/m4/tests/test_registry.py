"""Contract tests for the P4 operation registry and receipt adapters."""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
M4 = HERE.parent
HARNESS = M4.parents[1]
REPO = HARNESS.parents[1]
CONTRACTS = HARNESS / "contracts"
GOLDEN = CONTRACTS / "golden" / "fixtures"
sys.path.insert(0, str(M4))
sys.path.insert(0, str(CONTRACTS))

registry = importlib.import_module("registry")
lock_contention = importlib.import_module("lock_contention")
write_amplification = importlib.import_module("write_amplification")
diagnostics = importlib.import_module("diagnostics")
policy_v1 = importlib.import_module("policy_v1")
receipt_v1 = importlib.import_module("receipt_v1")


class OperationRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = policy_v1.PolicyV1.load(
            REPO / "research/m4-native/docs/benchmark-policy.json"
        )

    def receipt(self, name):
        return receipt_v1.ReceiptV1.load(GOLDEN / name)

    def test_registry_covers_every_policy_purpose(self):
        registry.validate(self.policy)
        self.assertEqual(registry.get("rust_lifecycle").status, "active")
        self.assertEqual(registry.get("lock_contention_microbench").status, "active")
        self.assertEqual(
            registry.get("write_amplification").status,
            "historical_only",
        )
        sections = {
            item["policy_section"]
            for item in registry.describe_all()
            if item["policy_section"]
        }
        policy_sections = {
            name
            for name, value in self.policy.raw.items()
            if isinstance(value, dict) and value.get("purpose")
        }
        self.assertEqual(sections, policy_sections)

    def test_write_amplification_cannot_execute(self):
        descriptor = registry.get("write_amplification")
        self.assertFalse(descriptor.execution_allowed)
        with self.assertRaises(write_amplification.OperationUnavailable):
            write_amplification.require_runner_provenance()
        summary = write_amplification.summarize_historical(
            self.receipt("immutable-write-amplification-pair.receipt.json")
        )
        self.assertTrue(summary["historical_only"])
        self.assertFalse(summary["execution_allowed"])
        self.assertFalse(summary["performance_eligible"])

    def test_lock_contention_adapter_preserves_nonqualifying_status(self):
        summary = lock_contention.summarize(
            self.receipt("m4-lock-contention-microbench.receipt.json")
        )
        self.assertEqual(summary["status"], "WATCHDOG_ABORT")
        self.assertTrue(summary["benchmark_started"])
        self.assertFalse(summary["performance_eligible"])

    def test_diagnostic_adapter_never_claims_performance(self):
        summary = diagnostics.summarize(
            self.receipt("m4-scalar-open-latency-diagnostic.receipt.json"),
            "m4-scalar-open-latency-diagnostic",
        )
        self.assertTrue(summary["diagnostic"])
        self.assertFalse(summary["performance_eligible"])
        with self.assertRaises(diagnostics.DiagnosticAdapterError):
            diagnostics.summarize(
                self.receipt("m4-scalar-open-latency-diagnostic.receipt.json"),
                "m4-observer-overhead-diagnostic",
            )

    def test_scalar_diagnostic_is_unregistered_and_not_executable(self):
        descriptor = registry.get("scalar_open_latency")
        self.assertEqual(descriptor.status, "unregistered")
        self.assertIsNone(descriptor.policy_section)
        self.assertFalse(descriptor.execution_allowed)


if __name__ == "__main__":
    unittest.main()
