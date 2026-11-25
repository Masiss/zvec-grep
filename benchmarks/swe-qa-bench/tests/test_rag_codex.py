"""Host Codex RAG answer runner tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.swe_qa.pairs import load_pairs
from zg_bench.swe_qa.rag_harbor import PROFILE_NAMES, prepare_rag_datasets
from zg_bench.swe_qa.run_rag_codex import run_rag_codex


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RagCodexRunnerTests(unittest.TestCase):
    def test_writes_paired_answer_trials_with_source_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "corpus"
            source_path = corpus / "src/Answer.cs"
            source_path.parent.mkdir(parents=True)
            source = "public void Reset() {}\n"
            source_path.write_text(source, encoding="utf-8")
            digest = hashlib.sha256(source.encode()).hexdigest()
            prompt_path = root / "prompts.json"
            _write(
                prompt_path,
                {
                    "schema_version": 1,
                    "gold_visibility": "grader-only",
                    "answer_instructions": "Answer from source evidence and cite lines.",
                    "tasks": [
                        {
                            "task_id": "task-1",
                            "question": "What does Reset do?",
                            "question_sha256": "0" * 64,
                            "repository": "example/repo",
                            "repository_commit": "a" * 40,
                            "split": "development",
                            "task_type": "direct_lookup",
                        }
                    ],
                },
            )
            lock_path = root / "source-lock.json"
            _write(
                lock_path,
                {"repositories": [{"files": [{"path": "src/Answer.cs", "sha256": digest}]}]},
            )
            packs = root / "packs"
            for profile in PROFILE_NAMES:
                _write(
                    packs / "task-1" / f"{profile}.json",
                    {
                        "schema_version": 1,
                        "evidence_items": [
                            {
                                "rank": 1,
                                "path": "src/Answer.cs",
                                "start_line": 1,
                                "end_line": 1,
                                "source_sha256": digest,
                                "content": source,
                            }
                        ],
                        "navigation_usage": {
                            "rounds": 1,
                            "calls": 1,
                            "opened_files": 1,
                            "cumulative_source_bytes": len(source.encode()),
                        },
                        "retrieval_latency_seconds": 0.1,
                        "peak_rss_kib": 100,
                    },
                )
            prepared = root / "prepared"
            prepare_rag_datasets(
                prompts_path=prompt_path,
                evidence_packs_root=packs,
                source_lock_path=lock_path,
                corpus_root=corpus,
                output_root=prepared,
            )

            def completion(**_kwargs):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "Reset calls Seek(0). Citation: src/Answer.cs:1."
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    "_codex_meta": {"tool_calls": 0, "wall_seconds": 0.5},
                }

            with patch(
                "zg_bench.swe_qa.run_rag_codex.shutil.which",
                return_value="/usr/bin/codex",
            ), patch(
                "zg_bench.swe_qa.run_rag_codex.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    ["/usr/bin/codex", "--version"], 0, stdout="codex-cli 0.144.4\n"
                ),
            ):
                pairs_root = root / "pairs"
                run_rag_codex(
                    datasets_root=prepared / "datasets",
                    evidence_packs_root=packs,
                    source_lock_path=lock_path,
                    corpus_root=corpus,
                    output_root=pairs_root,
                    run_id="unit-codex",
                    model="gpt-6-luna",
                    completion_fn=completion,
                )
                run_rag_codex(
                    datasets_root=prepared / "datasets",
                    evidence_packs_root=packs,
                    source_lock_path=lock_path,
                    corpus_root=corpus,
                    output_root=pairs_root,
                    run_id="unit-codex",
                    model="gpt-6-luna",
                    completion_fn=lambda **_kwargs: self.fail(
                        "resume must reuse the completed task pair"
                    ),
                    resume=True,
                )

            loaded = load_pairs(pairs_root, ["task-1"], PROFILE_NAMES)

        for profile in PROFILE_NAMES:
            trial = loaded["task-1"]["profiles"][profile]["trials"][0]
            self.assertEqual(trial["model"], "gpt-6-luna")
            self.assertEqual(trial["input_tokens"], 100)
            self.assertEqual(trial["tool_calls"], 0)
            self.assertEqual(trial["evidence_items"][0]["path"], "src/Answer.cs")
            self.assertEqual(trial["retrieval_latency_seconds"], 0.1)


if __name__ == "__main__":
    unittest.main()
