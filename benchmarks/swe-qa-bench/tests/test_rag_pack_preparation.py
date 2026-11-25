"""Preparation of source-verified evidence packs for the frozen RAG tasks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zg_bench.swe_qa.prepare_rag_packs import (
    _select_rank_stratified_candidates,
    prepare_rag_packs,
)
from zg_bench.swe_qa.rag_evidence import validate_source_pack


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RagPackPreparationTests(unittest.TestCase):
    def test_candidate_interleaves_sources_and_skips_overlapping_ranges(self) -> None:
        lexical = [
            {"rank": 1, "path": "src/A.cs", "start_line": 1, "end_line": 3, "content": "abc"},
            {"rank": 2, "path": "src/B.cs", "start_line": 1, "end_line": 1, "content": "def"},
        ]
        r4 = [
            {"rank": 1, "path": "src/A.cs", "start_line": 2, "end_line": 4, "content": "overlap"},
            {"rank": 2, "path": "src/C.cs", "start_line": 1, "end_line": 1, "content": "ghi"},
        ]

        selected = _select_rank_stratified_candidates(lexical, r4)

        self.assertEqual([item["path"] for item in selected], ["src/A.cs", "src/B.cs", "src/C.cs"])
        self.assertEqual([item["rank"] for item in selected], [1, 2, 3])

    def test_normalizes_all_profiles_and_verifies_exact_source_slices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "corpus"
            source_path = corpus / "src/Example.cs"
            source_path.parent.mkdir(parents=True)
            source = "class Example\n{\n    void Reset() {}\n}\n"
            source_path.write_text(source, encoding="utf-8")
            source_bytes = source.encode()
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            reset_offset = source_bytes.index(b"Reset")
            prompts_path = root / "prompts.json"
            gold_path = root / "gold.json"
            lock_path = root / "source-lock.json"
            prompts = {
                "tasks": [
                    {"task_id": "task-1", "split": "development", "question": "What does Reset do?"}
                ]
            }
            gold = {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "outcome": "answerable",
                        "required_steps": [
                            {"step_id": "step-1", "accepted_range_ids": ["range-1"]}
                        ],
                        "accepted_source_ranges": [
                            {
                                "range_id": "range-1",
                                "path": "src/Example.cs",
                                "start_line": 3,
                                "end_line": 3,
                                "source_sha256": source_hash,
                            }
                        ],
                    }
                ]
            }
            lock = {
                "repositories": [
                    {"files": [{"path": "src/Example.cs", "sha256": source_hash}]}
                ]
            }
            _write(prompts_path, prompts)
            _write(gold_path, gold)
            _write(lock_path, lock)
            task_input_sha256 = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
            lexical_root = root / "lexical"
            r4_root = root / "r4"
            exact_content = "{\n    void Reset() {}\n"
            _write(
                lexical_root / "task-1/rg-delivered.json",
                {
                    "items": [
                        {
                            "rank": 1,
                            "path": "src/Example.cs",
                            "start_line": 2,
                            "end_line": 3,
                            "source_sha256": source_hash,
                            "content": exact_content,
                        }
                    ]
                },
            )
            _write(
                lexical_root / "run-manifest.json",
                {
                    "task_input_sha256": task_input_sha256,
                    "runs": [
                        {
                            "task_id": "task-1",
                            "ranking_end_to_end_seconds": 0.1,
                            "runner_peak_rss_kb": 100,
                        }
                    ]
                },
            )
            _write(
                r4_root / "task-1/hybrid-delivered.json",
                {
                    "items": [
                        {
                            "rank": 1,
                            "path": "src/Example.cs",
                            "content_range": [3, 3],
                            "content_columns": [9, 14],
                            "raw_file_byte_offsets": [reset_offset, reset_offset + 5],
                            "sha256": source_hash,
                            "content": "Reset",
                        }
                    ]
                },
            )
            _write(
                r4_root / "run-manifest.json",
                {
                    "task_input_sha256": task_input_sha256,
                    "source_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                    "runs": [
                        {
                            "task_id": "task-1",
                            "mode": "hybrid",
                            "elapsed_seconds": 0.2,
                            "peak_rss_kb": 200,
                        }
                    ]
                },
            )

            output = root / "packs"
            manifest = prepare_rag_packs(
                prompts_path=prompts_path,
                gold_path=gold_path,
                source_lock_path=lock_path,
                corpus_root=corpus,
                lexical_root=lexical_root,
                r4_root=r4_root,
                output_root=output,
            )

            self.assertEqual(manifest["task_count"], 1)
            self.assertEqual(
                manifest["evidence_sufficiency"]["sufficient-pack"][
                    "complete_evidence_task_count"
                ],
                1,
            )
            for profile in ("lexical-rg", "r4-hybrid", "sufficient-pack"):
                sidecar = json.loads((output / "task-1" / f"{profile}.json").read_text())
                verified = validate_source_pack(
                    sidecar["evidence_items"],
                    source_root=corpus,
                    source_lock=lock,
                )
                self.assertTrue(verified["valid"])
                self.assertEqual(
                    sidecar["navigation_usage"]["calls"],
                    0 if profile == "sufficient-pack" else 1,
                )
                self.assertEqual(sidecar["navigation_usage"]["opened_files"], len({item["path"] for item in sidecar["evidence_items"]}))
                self.assertEqual(
                    sidecar["navigation_usage"]["cumulative_source_bytes"],
                    verified["source_bytes"],
                )
            self.assertEqual(
                json.loads((output / "task-1/r4-hybrid.json").read_text())[
                    "retrieval_latency_seconds"
                ],
                0.2,
            )
            r4_item = json.loads((output / "task-1/r4-hybrid.json").read_text())[
                "evidence_items"
            ][0]
            self.assertEqual(r4_item["content"], "Reset")
            self.assertEqual(r4_item["source_byte_offsets"], [reset_offset, reset_offset + 5])
            sufficient = json.loads((output / "task-1/sufficient-pack.json").read_text())
            self.assertIn("void Reset", sufficient["evidence_items"][0]["content"])


if __name__ == "__main__":
    unittest.main()
