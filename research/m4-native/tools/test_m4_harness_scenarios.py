#!/usr/bin/env python3
"""Contract checks for the M4 agent-policy review scenarios (not an LLM eval)."""
import json
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
POLICY = json.loads((HERE.parent / "docs" / "benchmark-policy.json").read_text(encoding="utf-8"))
SCENARIOS = HERE.parent / "docs" / "harness-review-scenarios.md"
ROOT_AGENT = HERE.parents[2] / "AGENTS.md"
SCOPED_AGENT = HERE.parent / "AGENTS.md"


class HarnessScenarioContractTests(unittest.TestCase):
    def test_scenarios_a_through_j_have_explicit_expected_outcomes(self):
        text = SCENARIOS.read_text(encoding="utf-8")
        for label in "ABCDEFGHIJ":
            with self.subTest(scenario=label):
                self.assertIn(f"| {label} |", text)
        self.assertIn("not proof of LLM behavior", text)
        self.assertIn("No acceptance scenario authorizes a real 500×20 benchmark", text)
        self.assertIn("| J |", text)

    def test_current_user_mission_precedes_stale_handoff(self):
        precedence = POLICY["authority_precedence"]
        self.assertLess(precedence.index("current explicit user mission, authorization, and DoD"),
                        precedence.index("canonical handoff as state/evidence/continuity, not authority over newer user instructions"))
        self.assertIn("stale_handoff", POLICY["continuation_rules"])
        instructions = ROOT_AGENT.read_text(encoding="utf-8")
        self.assertTrue(
            "active user request and its acceptance criteria" in instructions
            or "Follow the active user request" in instructions
        )
        self.assertTrue(
            "Existing handoffs and prior plans provide continuity/evidence, not standing authorization" in instructions
            or "Existing handoffs, plans, and reports provide continuity and evidence, not standing execution authorization." in instructions
        )
        scoped = SCOPED_AGENT.read_text(encoding="utf-8")
        self.assertTrue(
            "durable design and workflow constraints only" in scoped
            or "This file adds M4-specific constraints." in scoped
        )
        self.assertNotIn("20:56:34", scoped)
        self.assertNotIn("4.52%", scoped)

    def test_gate_failure_does_not_imply_mission_failure_or_success(self):
        rules = POLICY["continuation_rules"]
        self.assertIn("stop the affected benchmark/arm", rules["operation_guard_failure"])
        self.assertIn("continue only already-authorized independent safe work", rules["operation_guard_failure"])
        self.assertIn("requires reconciliation of every active mission DoD item", rules["mission_completion"])
        scenarios = (HERE.parent / "docs" / "harness-review-scenarios.md").read_text(encoding="utf-8")
        self.assertIn("request owner approval", scenarios)
        self.assertIn("identify the exact recovery condition", scenarios)
        root = ROOT_AGENT.read_text(encoding="utf-8")
        self.assertTrue(
            "If no valid action remains, report the precise blocker" in root
            or "If no meaningful authorized action remains, report the blocker and stop" in root
        )
        self.assertTrue("remaining work" in root or "acceptance criteria remain unmet" in root)

    def test_no_unapproved_gate_change_or_delegated_authority(self):
        self.assertIn("Requires explicit owner approval", POLICY["authority"]["threshold_change"])
        self.assertIn("sub-agent inherits the same scope", POLICY["continuation_rules"]["delegation"])
        self.assertIn("never bypass", POLICY["runner_mission_semantics"])


if __name__ == "__main__":
    unittest.main()
