"""Golden-fixture compatibility tests for the v1 receipt/protocol reader."""
from __future__ import annotations

import hashlib
import importlib
import sys
import unittest
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
CONTRACTS = HERE.parent
REPO_ROOT = CONTRACTS.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CONTRACTS))

receipt_v1 = importlib.import_module("receipt_v1")
ReceiptV1 = receipt_v1.ReceiptV1
ReceiptV1Error = receipt_v1.ReceiptV1Error
UnsupportedReceiptKind = receipt_v1.UnsupportedReceiptKind
UnsupportedReceiptSchema = receipt_v1.UnsupportedReceiptSchema
ReceiptFieldTypeError = receipt_v1.ReceiptFieldTypeError
read_receipt = receipt_v1.read_receipt
fixtures = importlib.import_module("_fixtures")
manifest = fixtures.manifest


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReceiptV1GoldenTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()

    def test_every_golden_fixture_is_immutable(self):
        for entry in self.manifest["fixtures"]:
            with self.subTest(fixture=entry["id"]):
                fixture = REPO_ROOT / entry["fixture_path"]
                self.assertEqual(sha256(fixture), entry["fixture_sha256"])
                source = REPO_ROOT / entry["source_path"]
                if source.exists():
                    self.assertEqual(sha256(source), entry["source_sha256"])

    def test_every_golden_fixture_loads_with_expected_normalized_fields(self):
        for entry in self.manifest["fixtures"]:
            with self.subTest(fixture=entry["id"]):
                path = REPO_ROOT / entry["fixture_path"]
                receipt = ReceiptV1.load(path)
                self.assertEqual(receipt.kind, entry["kind"])
                self.assertEqual(receipt.purpose, entry["purpose"])
                self.assertEqual(receipt.normalized(), entry["expected"])
                self.assertEqual(receipt.path, path)

    def test_all_available_containment_receipts_remain_readable(self):
        results = REPO_ROOT / "research" / "m4-native" / "results"
        paths = sorted(results.rglob("containment/receipt.json"))
        self.assertTrue(paths, "historical containment receipts must be available")
        failures = []
        for path in paths:
            try:
                ReceiptV1.load(path)
            except ReceiptV1Error as error:
                failures.append((path, error))
        self.assertFalse(failures, failures[:5])

    def test_loading_does_not_change_the_fixture(self):
        for entry in self.manifest["fixtures"]:
            with self.subTest(fixture=entry["id"]):
                path = REPO_ROOT / entry["fixture_path"]
                before = sha256(path)
                before_mtime = path.stat().st_mtime_ns
                read_receipt(path)
                self.assertEqual(sha256(path), before)
                self.assertEqual(path.stat().st_mtime_ns, before_mtime)

    def test_unknown_schema_fails_closed(self):
        with self.assertRaises(UnsupportedReceiptSchema):
            ReceiptV1.from_mapping({"schema_version": 2, "status": "PASS"})

    def test_unknown_shape_fails_closed(self):
        with self.assertRaises(UnsupportedReceiptKind):
            ReceiptV1.from_mapping({"schema_version": 1, "status": "PASS"})

    def test_wrong_field_type_fails_closed(self):
        with self.assertRaises(ReceiptFieldTypeError):
            ReceiptV1.from_mapping(
                {"schema_version": 1, "approved_budget": {}, "status": 7}
            )

    def test_non_object_document_is_rejected(self):
        with self.assertRaises(ReceiptV1Error):
            ReceiptV1.from_mapping(cast(Any, ["not", "an", "object"]))


if __name__ == "__main__":
    unittest.main()
