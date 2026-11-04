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


PAIR_WORKLOAD = json.loads(pair.POLICY_PATH.read_text(encoding="utf-8"))["shared_collection_pair"]["workload"]


def shared_workload():
    query_count = PAIR_WORKLOAD["minimum_reader_queries_per_arm"]
    rounds = PAIR_WORKLOAD["rounds"]
    documents = PAIR_WORKLOAD["documents"]
    workload = {"documents": documents, "rounds": rounds,
                "completed_rounds": rounds, "reader_mode": PAIR_WORKLOAD["reader_mode"],
                "reader_queries": query_count,
                "reader_samples_by_cycle": [PAIR_WORKLOAD["queries_per_reader_per_round"]] *
                    (rounds * PAIR_WORKLOAD["readers"]),
                "coherence_errors": 0, "failures": 0,
                "native_live_docs": documents, "m4_live_docs": documents,
                "writer_commit_ms": [1.0] * rounds, "writer_gc_ms": [1.0] * rounds,
                "writer_cycle_ms": [2.0] * rounds}
    for field in ("reader_total_ms", "reader_thread_cpu_ms", "reader_pin_wall_ms",
                  "reader_snapshot_lock_wait_ms", "reader_m4_query_lock_wait_ms",
                  "reader_vector_stage_ms", "reader_fts_stage_ms", "reader_merge_stage_ms"):
        workload[field] = [0.1] * query_count
    return workload


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
        pair.verify_arm(result, shared_workload(), "shared-collection", PAIR_WORKLOAD)

    def test_verify_immutable_arm_requires_each_reader_cycle_minimum(self):
        result = {"status": "PASS", "workload_returncode": 0,
                  "unit_cleanup": {"verified": True}}
        count = PAIR_WORKLOAD["minimum_reader_queries_per_arm"]
        data = {"docs": PAIR_WORKLOAD["documents"], "rounds": PAIR_WORKLOAD["rounds"],
                "reader_mode": PAIR_WORKLOAD["reader_mode"], "coherence_errors": 0,
                "reader_queries": count, "queries": count,
                "reader_samples_by_cycle": [16] * (PAIR_WORKLOAD["rounds"] * PAIR_WORKLOAD["readers"]),
                "commit_samples_ms": [1.0] * PAIR_WORKLOAD["rounds"],
                "reader_samples_ms": [0.1] * count}
        pair.verify_arm(result, data, "immutable-shard", PAIR_WORKLOAD)
        data["reader_samples_by_cycle"][0] = 15
        with self.assertRaisesRegex(RuntimeError, "reader samples for every reader-cycle"):
            pair.verify_arm(result, data, "immutable-shard", PAIR_WORKLOAD)

    def test_verify_shared_arm_requires_reader_lock_and_stage_samples(self):
        result = {"status": "PASS", "workload_returncode": 0,
                  "unit_cleanup": {"verified": True}}
        workload = shared_workload()
        workload.pop("reader_m4_query_lock_wait_ms")
        with self.assertRaisesRegex(RuntimeError, "reader_m4_query_lock_wait_ms"):
            pair.verify_arm(result, workload, "shared-collection", PAIR_WORKLOAD)

    def test_verify_shared_arm_rejects_insufficient_reader_samples(self):
        result = {"status": "PASS", "workload_returncode": 0,
                  "unit_cleanup": {"verified": True}}
        workload = shared_workload()
        workload["reader_total_ms"] = workload["reader_total_ms"][:-1]
        with self.assertRaisesRegex(RuntimeError, "minimum concurrent-reader sample count"):
            pair.verify_arm(result, workload, "shared-collection", PAIR_WORKLOAD)

    def test_verify_shared_arm_rejects_wrong_reader_mode(self):
        result = {"status": "PASS", "workload_returncode": 0,
                  "unit_cleanup": {"verified": True}}
        workload = shared_workload()
        workload["reader_mode"] = "one-query-per-writer-cycle"
        with self.assertRaisesRegex(RuntimeError, "does not match reader mode"):
            pair.verify_arm(result, workload, "shared-collection", PAIR_WORKLOAD)

    def test_verify_shared_arm_rejects_a_reader_cycle_below_minimum(self):
        result = {"status": "PASS", "workload_returncode": 0,
                  "unit_cleanup": {"verified": True}}
        workload = shared_workload()
        workload["reader_samples_by_cycle"][3] -= 1
        with self.assertRaisesRegex(RuntimeError, "reader samples for every reader-cycle"):
            pair.verify_arm(result, workload, "shared-collection", PAIR_WORKLOAD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
