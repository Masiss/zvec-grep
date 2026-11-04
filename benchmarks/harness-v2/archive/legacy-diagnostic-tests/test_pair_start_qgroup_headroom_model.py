#!/usr/bin/env python3
"""Offline model test for a conservative two-arm qgroup start reserve.

Reads only the pinned policy and historical receipts. It does not call the
lifecycle runner, inspect live qgroups, create workspace data, or launch work.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = json.loads((ROOT / "research/m4-native/docs/benchmark-policy.json").read_text())[
    "rust_pipeline_lifecycle_pair"
]
R2E_PREFLIGHT = json.loads(
    (ROOT / "research/m4-native/results/native-query-timeline-20261029/r2e-preflight.json").read_text()
)
R2E_RESULT = json.loads(
    (ROOT / "research/m4-native/.work/m4-native-workspace-quota/"
     "m4-rust-lifecycle-pair-20261029t024751z-nquery-r2e/pair-result.json").read_text()
)
R2A_PREFLIGHT = json.loads(
    (ROOT / "research/m4-native/results/native-flush-timeline-20261029/r2-final-preflight.json").read_text()
)
R2A_RESULT = json.loads(
    (ROOT / "research/m4-native/.work/m4-native-workspace-quota/"
     "m4-rust-lifecycle-pair-20261029t014553z-nflushcpu-r2a/pair-result.json").read_text()
)


def pair_start_required_bytes(per_arm_max_bytes: int, required_free_bytes: int,
                              arm_count: int = 2) -> int:
    """Worst-case start reserve if each arm's maximum allocation may remain referenced."""
    values = (per_arm_max_bytes, required_free_bytes, arm_count)
    if any(type(value) is not int or value < 0 for value in values) or arm_count < 1:
        raise ValueError("pair-headroom inputs must be nonnegative integers and arm_count >= 1")
    return arm_count * per_arm_max_bytes + required_free_bytes


class PairStartQgroupHeadroomModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.per_arm = POLICY["resource_controls"]["workspace_max_allocated_bytes"]
        self.reserve = POLICY["workspace_quota"]["minimum_free_bytes"]
        self.one_arm_required = self.per_arm + self.reserve
        self.two_arm_required = pair_start_required_bytes(self.per_arm, self.reserve)

    def test_candidate_two_arm_threshold_and_boundary(self):
        self.assertEqual(self.two_arm_required, 134_217_728)
        self.assertGreaterEqual(self.two_arm_required, self.one_arm_required)
        self.assertTrue(134_217_728 >= self.two_arm_required)
        self.assertFalse(134_217_727 >= self.two_arm_required)

    def test_r2e_would_be_rejected_before_first_arm_by_candidate_guard(self):
        available = R2E_PREFLIGHT["workspace_quota_proof"]["live_headroom"]["available_bytes"]
        self.assertEqual(available, 125_739_008)
        self.assertGreaterEqual(available, self.one_arm_required)
        self.assertLess(available, self.two_arm_required)
        self.assertEqual(self.two_arm_required - available, 8_478_720)

    def test_candidate_guard_also_rejects_r2a_despite_its_pair_completing(self):
        available = R2A_PREFLIGHT["workspace_quota_proof"]["live_headroom"]["available_bytes"]
        first = next(arm for arm in R2A_RESULT["arms"] if arm["arm_name"] == "pair-first")
        post = first["runner"]["postflight_workspace_quota"]
        self.assertEqual(available, 127_381_504)
        self.assertLess(available, self.two_arm_required)
        self.assertEqual(self.two_arm_required - available, 6_836_224)
        self.assertGreaterEqual(post["available_bytes"], self.one_arm_required)
        self.assertEqual(post["available_bytes"] - self.one_arm_required, 212_992)
        self.assertEqual(R2A_RESULT["status"], "PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS")

    def test_same_index_footprint_does_not_explain_qgroup_counter_variation(self):
        r2a = next(arm for arm in R2A_RESULT["arms"] if arm["arm_name"] == "pair-first")
        r2e = R2E_RESULT["arms"][0]
        r2a_q = r2a["runner"]["postflight_workspace_quota"]["referenced_bytes"]
        r2e_q = r2e["runner"]["postflight_workspace_quota"]["referenced_bytes"]
        self.assertEqual(r2a["index_apparent_bytes_before_cleanup"], 21_644_736)
        self.assertEqual(r2e["index_apparent_bytes_before_cleanup"], 21_644_736)
        self.assertEqual(r2a["runner"]["postflight_workspace"]["allocated_st_blocks_bytes_gross"], 23_453_696)
        self.assertEqual(r2e["runner"]["postflight_workspace"]["allocated_st_blocks_bytes_gross"], 23_445_504)
        self.assertEqual(r2e_q - r2a_q, 3_534_848)

    def test_r2e_second_arm_gate_is_consistent_with_one_arm_reserve(self):
        refusal = R2E_RESULT["error"]
        self.assertIn("available=97341440", refusal)
        self.assertIn("additional=33554432", refusal)
        self.assertIn("required_free=67108864", refusal)
        self.assertIn("total_required=100663296", refusal)
        self.assertEqual(R2E_RESULT["status"], "OPERATION_BLOCKED")
        self.assertEqual(R2E_RESULT["failure_class"], "SAFETY_STOP")
        self.assertEqual(len(R2E_RESULT["arms"]), 1)


if __name__ == "__main__":
    unittest.main()
