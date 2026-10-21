#!/usr/bin/env python3
"""Saved-receipt tests for the exploratory shared-collection pair wrapper."""

import importlib.util
import json
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("run_shared_collection_pair.py")
spec = importlib.util.spec_from_file_location("shared_pair_wrapper_test", SCRIPT)
assert spec and spec.loader
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)


class SharedPairReceiptTests(unittest.TestCase):
    def test_workload_json_selects_sustained_record_before_lifecycle_records(self):
        records = [
            {"mode": "shared_collection", "documents": 500, "rounds": 5,
             "completed_rounds": 5, "failures": 0},
            {"arm": "resources", "physical_write_bytes": 1234},
            {"arm": "post-close-reopen", "generation": 6},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "stdout.log"
            path.write_text("".join(json.dumps(record) + "\n" for record in records))
            self.assertEqual(pair.workload_json(path), records[0])

    def test_workload_json_accepts_immutable_shard_docs_field(self):
        expected = {"arm": "immutable", "docs": 500, "rounds": 5,
                    "queries": 20}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "stdout.log"
            path.write_text(json.dumps(expected) + "\n")
            self.assertEqual(pair.workload_json(path), expected)

    def test_verify_arm_accepts_verified_systemd_unit_cleanup(self):
        result = {"status": "COMPLETED_WITH_WORKSPACE_UNCERTAINTY",
                  "workload_returncode": 0, "controller_cleanup": None,
                  "unit_cleanup": {"verified": True}}
        workload = {"documents": 500, "rounds": 5, "completed_rounds": 5,
                    "coherence_errors": 0, "failures": 0,
                    "native_live_docs": 500, "m4_live_docs": 500}
        pair.verify_arm(result, workload, "shared-collection")


if __name__ == "__main__":
    unittest.main(verbosity=2)
