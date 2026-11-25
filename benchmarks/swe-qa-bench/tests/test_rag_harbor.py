"""Gold-blind Harbor dataset preparation for RAG evidence comparisons."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.rag_harbor import PROFILE_NAMES, prepare_rag_datasets


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RagHarborTests(unittest.TestCase):
    def test_builds_matched_gold_blind_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "corpus"
            source_path = corpus / "src/Answer.cs"
            source_path.parent.mkdir(parents=True)
            source = "alpha\nbeta\ngamma\n"
            source_path.write_text(source, encoding="utf-8")
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            prompts_path = root / "prompts.json"
            prompts = {
                "schema_version": 1,
                "gold_visibility": "grader-only",
                "answer_instructions": "Answer from evidence and cite path:lines.",
                "tasks": [
                    {
                        "task_id": "task-1",
                        "question": "What behavior is shown?",
                        "question_sha256": "1" * 64,
                        "repository": "example/repo",
                        "repository_commit": "a" * 40,
                        "split": "development",
                        "task_type": "direct_lookup",
                    }
                ],
            }
            _write(prompts_path, prompts)
            lock_path = root / "source-lock.json"
            _write(
                lock_path,
                {"repositories": [{"files": [{"path": "src/Answer.cs", "sha256": source_hash}]}]},
            )
            packs_root = root / "packs"
            profiles = (*PROFILE_NAMES, "candidate")
            for profile, line, content in zip(
                profiles,
                (1, 2, 3, 1),
                ("alpha\n", "beta\n", "gamma\n", "alpha\n"),
                strict=True,
            ):
                _write(
                    packs_root / "task-1" / f"{profile}.json",
                    {
                        "schema_version": 1,
                        "evidence_items": [
                            {
                                "rank": 1,
                                "path": "src/Answer.cs",
                                "start_line": line,
                                "end_line": line,
                                "source_sha256": source_hash,
                                "content": content,
                            }
                        ],
                        "navigation_usage": {
                            "rounds": 1,
                            "calls": 1,
                            "opened_files": 1,
                            "cumulative_source_bytes": len(content.encode()),
                        },
                        "retrieval_latency_seconds": 0.1,
                        "peak_rss_kib": 100,
                    },
                )

            output = root / "prepared"
            manifest = prepare_rag_datasets(
                prompts_path=prompts_path,
                evidence_packs_root=packs_root,
                source_lock_path=lock_path,
                corpus_root=corpus,
                output_root=output,
                profile_names=profiles,
            )

            self.assertEqual(manifest["task_count"], 1)
            instructions = [
                (output / "datasets" / profile / "task-1" / "instruction.md").read_text()
                for profile in profiles
            ]
            self.assertTrue(all("What behavior is shown?" in item for item in instructions))
            self.assertIn("alpha", instructions[0])
            self.assertIn("beta", instructions[1])
            self.assertIn("gamma", instructions[2])
            self.assertNotIn("reference_answer", "\n".join(instructions))
            self.assertNotIn("lexical-rg", "\n".join(instructions))
            task_metadata_by_profile = [
                (output / "datasets" / profile / "task-1" / "task.toml").read_text()
                for profile in profiles
            ]
            self.assertEqual(len(set(task_metadata_by_profile)), 1)
            self.assertIn("task-1", manifest["common_instruction_sha256"])
            for profile in profiles:
                task_dir = output / "datasets" / profile / "task-1"
                self.assertTrue((task_dir / "task.toml").is_file())
                task_metadata = (task_dir / "task.toml").read_text()
                self.assertNotIn("task_type", task_metadata)
                self.assertNotIn("split =", task_metadata)
                self.assertTrue((task_dir / "environment/Dockerfile").is_file())
                self.assertTrue((task_dir / "tests/test.sh").is_file())
                self.assertTrue((task_dir / "tests/test.sh").stat().st_mode & 0o111)
                self.assertIn(
                    "/logs/verifier/reward.txt",
                    (task_dir / "tests/test.sh").read_text(),
                )


if __name__ == "__main__":
    unittest.main()
