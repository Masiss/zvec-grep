"""Tests for measurement identity, clock, and receipt primitives."""
from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
HARNESS_V2 = HERE.parents[1]
sys.path.insert(0, str(HARNESS_V2))
measurement = importlib.import_module("measurement.primitives")


class MeasurementPrimitiveTests(unittest.TestCase):
    def test_file_and_directory_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "a.txt").write_bytes(b"alpha")
            (root / "b.txt").write_bytes(b"beta")
            self.assertEqual(measurement.file_sha256(root / "a.txt"), hashlib.sha256(b"alpha").hexdigest())
            expected = hashlib.sha256()
            for name, data in (("a.txt", b"alpha"), ("b.txt", b"beta")):
                relative = name.encode("utf-8")
                expected.update(len(relative).to_bytes(8, "big"))
                expected.update(relative)
                expected.update(hashlib.sha256(data).digest())
            self.assertEqual(measurement.directory_sha256(root), expected.hexdigest())

    def test_directory_digest_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "file").write_bytes(b"x")
            (root / "link").symlink_to(root / "file")
            with self.assertRaisesRegex(ValueError, "symlink"):
                measurement.directory_sha256(root)

    def test_strict_json_rejects_malformed_content(self):
        with self.assertRaises(ValueError):
            measurement.strict_json_text("{not-json")

    def test_write_json_uses_sorted_keys_and_trailing_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nested" / "receipt.json"
            measurement.write_json(path, {"b": 2, "a": 1})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 1,\n  "b": 2\n}\n')
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": 2})

    def test_clock_helpers(self):
        stamp = measurement.utc_now()
        parsed = datetime.fromisoformat(stamp)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertGreaterEqual(measurement.boottime_ns(), 0)
        self.assertGreaterEqual(measurement.boottime_ns(), measurement.boottime_ns() - 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
