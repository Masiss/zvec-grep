"""P0 runtime provenance closure tests."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
CONTRACTS = HERE.parent
REPO = CONTRACTS.parents[2]
sys.path.insert(0, str(CONTRACTS))

import provenance  # noqa: E402

MANIFEST = CONTRACTS / "golden" / "runtime-provenance.json"


class RuntimeProvenanceTests(unittest.TestCase):
    def test_manifest_is_tracked_and_hashes_match(self):
        document = provenance.load(MANIFEST)
        provenance.verify(document, REPO)
        paths = [item.path for item in provenance.files(document)]
        self.assertIn(
            "research/m4-native/tools/create_measurement_artifact.py", paths
        )
        self.assertIn(
            "benchmarks/harness-v2/operations/m4/execution.py", paths
        )
        tracked = set(
            subprocess.check_output(
                ["git", "ls-files"], cwd=REPO, text=True
            ).splitlines()
        )
        self.assertEqual(set(paths) - tracked, set())

    def test_unknown_schema_and_duplicate_paths_fail_closed(self):
        document = provenance.load(MANIFEST)
        bad = {**document, "schema_version": 2}
        with self.assertRaises(provenance.ProvenanceError):
            provenance.load(self._write_temp(bad))
        duplicate = {
            **document,
            "files": document["files"] + [document["files"][0]],
        }
        with self.assertRaises(provenance.ProvenanceError):
            provenance.load(self._write_temp(duplicate))

    def test_hash_mismatch_fails_closed(self):
        document = provenance.load(MANIFEST)
        changed = json.loads(json.dumps(document))
        changed["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(provenance.ProvenanceError):
            provenance.verify(changed, REPO)

    def _write_temp(self, document):
        path = REPO / ".tmp" / "runtime-provenance-test.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(document))
        self.addCleanup(path.unlink, missing_ok=True)
        return path


if __name__ == "__main__":
    unittest.main()
