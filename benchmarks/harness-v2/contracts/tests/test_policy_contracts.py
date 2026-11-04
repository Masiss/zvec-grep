"""Policy v1 contract tests (ported from the legacy policy checks)."""
from __future__ import annotations

import hashlib
import importlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE.parent
REPO_ROOT = CONTRACTS.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CONTRACTS))

policy_v1 = importlib.import_module("policy_v1")
PolicyV1 = policy_v1.PolicyV1
AmbiguousPolicyPurpose = policy_v1.AmbiguousPolicyPurpose
UnknownPolicySection = policy_v1.UnknownPolicySection
UnsupportedPolicySchema = policy_v1.UnsupportedPolicySchema
fixtures = importlib.import_module("_fixtures")
manifest = fixtures.manifest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PolicyV1Tests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        policy_info = self.manifest["policy"]
        self.policy_path = REPO_ROOT / policy_info["path"]
        self.policy = PolicyV1.load(
            self.policy_path, expected_sha256=policy_info["sha256"]
        )

    def test_frozen_policy_schema_identity_and_hash(self):
        info = self.manifest["policy"]
        self.assertEqual(self.policy.schema_version, 1)
        self.assertEqual(self.policy.policy_id, info["policy_id"])
        self.assertEqual(self.policy.sha256, info["sha256"])
        self.assertEqual(sha256(self.policy_path), info["sha256"])

    def test_reading_the_policy_does_not_change_the_file(self):
        before = sha256(self.policy_path)
        PolicyV1.load(self.policy_path)
        self.assertEqual(sha256(self.policy_path), before)

    def test_operation_purposes_map_to_declared_sections(self):
        expected = {
            "m4-rust-full-lifecycle-pair": "rust_pipeline_lifecycle_pair",
            "m4-lock-contention-microbench": "lock_contention_microbench",
            "m4-shared-collection-pair": "shared_collection_pair",
            "immutable-write-amplification-pair": "immutable_write_amplification_pair",
            "m4-scalar-open-latency-diagnostic": "resource_only_exploratory_contract",
        }
        for purpose, section_name in expected.items():
            with self.subTest(purpose=purpose):
                sections = self.policy.sections_for_purpose(purpose)
                if purpose == "m4-scalar-open-latency-diagnostic":
                    # The resource-only contract is a mode of a shared section.
                    self.assertIn("resource_only_exploratory_contract", self.policy.raw)
                    continue
                self.assertEqual(sections, (section_name,))
                self.assertEqual(self.policy.section(section_name)["purpose"], purpose)
                self.assertEqual(
                    self.policy.operation_contract(purpose),
                    self.policy.section(section_name),
                )

    def test_observer_reduced_policy_shares_its_purpose_with_the_diagnostic(self):
        sections = self.policy.sections_for_purpose("m4-observer-overhead-diagnostic")
        self.assertEqual(
            sections,
            ("observer_overhead_diagnostic", "observer_overhead_reduced_diagnostic"),
        )
        with self.assertRaises(AmbiguousPolicyPurpose):
            self.policy.operation_contract("m4-observer-overhead-diagnostic")
        self.assertIn(
            "sample_interval_ms",
            self.policy.section("observer_overhead_reduced_diagnostic"),
        )

    def test_unknown_sections_and_purposes_fail_closed(self):
        with self.assertRaises(UnknownPolicySection):
            self.policy.section("not_a_contract")
        with self.assertRaises(UnknownPolicySection):
            self.policy.operation_contract("not-a-purpose")

    def test_unsupported_schema_is_rejected(self):
        with self.assertRaises(UnsupportedPolicySchema):
            PolicyV1.from_mapping({"schema_version": 2, "policy_id": "x"})

    def test_authority_precedence_is_preserved(self):
        precedence = self.policy.raw["authority_precedence"]
        self.assertLess(
            precedence.index("current explicit user mission, authorization, and DoD"),
            precedence.index(
                "canonical handoff as state/evidence/continuity, not authority over newer user instructions"
            ),
        )
        self.assertIn("stale_handoff", self.policy.raw["continuation_rules"])

    def test_operation_contracts_keep_required_identity_fields(self):
        for purpose in (
            "m4-rust-full-lifecycle-pair",
            "m4-lock-contention-microbench",
            "m4-shared-collection-pair",
            "immutable-write-amplification-pair",
        ):
            with self.subTest(purpose=purpose):
                contract = self.policy.operation_contract(purpose)
                self.assertIsInstance(contract.get("resource_controls"), dict)
                identity_fields = (
                    "evidence_class",
                    "approval_reference",
                    "execution_authorization_reference",
                )
                self.assertTrue(
                    any(isinstance(contract.get(field), str) for field in identity_fields),
                    f"{purpose} is missing an identity/evidence field",
                )


if __name__ == "__main__":
    unittest.main()
