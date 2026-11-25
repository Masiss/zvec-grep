"""Frozen R4 answer-quality task contract validation."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from zg_bench.core.errors import SweQaError
from zg_bench.swe_qa.rag_contract import validate_rag_contract
from zg_bench.swe_qa.rag_evidence import (
    audit_answer_citations,
    score_required_steps,
    validate_source_pack,
    validate_trial_navigation_usage,
)
from zg_bench.swe_qa.cli import main
from zg_bench.swe_qa.pairs import load_pairs


def _assets() -> tuple[dict, dict]:
    prompts = {
        "schema_version": 1,
        "gold_visibility": "grader-only",
        "answer_instructions": "Answer from evidence and cite source lines.",
        "tasks": [],
    }
    gold = {"schema_version": 1, "tasks": []}
    task_types = ["abstention", "abstention"] + [
        "direct_lookup", "multi_step", "vocabulary_divergent", "direct_lookup",
        "multi_step", "direct_lookup", "multi_step", "vocabulary_divergent",
        "direct_lookup", "multi_step",
    ]
    task_types += ["abstention", "abstention"] + [
        "direct_lookup", "multi_step", "vocabulary_divergent", "direct_lookup",
        "multi_step", "direct_lookup", "multi_step", "vocabulary_divergent",
        "direct_lookup", "multi_step",
    ]
    for index in range(24):
        task_id = f"task-{index:02d}"
        question = f"Question {index}"
        split = "development" if index < 12 else "validation"
        prompts["tasks"].append(
            {
                "task_id": task_id,
                "question": question,
                "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
                "split": split,
                "repository": "example/repo",
                "repository_commit": "a" * 40,
                "task_type": task_types[index],
            }
        )
        gold["tasks"].append(
            {
                "task_id": task_id,
                "split": split,
                "outcome": "abstain" if task_types[index] == "abstention" else "answerable",
                "repository": "example/repo",
                "required_steps": [
                    {
                        "step_id": "step-1",
                        "description": "Observed behavior",
                        "accepted_range_ids": [] if task_types[index] == "abstention" else [f"{task_id}-range-1"],
                    }
                ],
                "accepted_source_ranges": [] if task_types[index] == "abstention" else [
                    {
                        "repository": "example/repo",
                        "range_id": f"{task_id}-range-1",
                        "path": "src/example.cs",
                        "start_line": 1,
                        "end_line": 2,
                        "source_sha256": "b" * 64,
                    }
                ],
            }
        )
    return prompts, gold


class RagContractTests(unittest.TestCase):
    def test_navigation_and_agent_calls_share_the_six_call_budget(self) -> None:
        within_budget = {
            "rounds": 2,
            "calls": 5,
            "opened_files": 2,
            "cumulative_source_bytes": 100,
        }
        self.assertEqual(
            validate_trial_navigation_usage(within_budget, 1)["calls"], 5
        )
        with self.assertRaisesRegex(SweQaError, "6-call budget"):
            validate_trial_navigation_usage(within_budget, 2)

    def test_valid_contract_has_balanced_splits_and_separate_gold(self) -> None:
        prompts, gold = _assets()

        result = validate_rag_contract(prompts, gold)

        self.assertTrue(result["valid"])
        self.assertEqual(result["split_counts"], {"development": 12, "validation": 12})
        self.assertTrue(result["gold_is_separate"])

    def test_question_hash_mismatch_is_rejected(self) -> None:
        prompts, gold = _assets()
        prompts["tasks"][0]["question_sha256"] = "0" * 64

        with self.assertRaisesRegex(SweQaError, "question SHA256 mismatch"):
            validate_rag_contract(prompts, gold)

    def test_path_traversal_in_gold_is_rejected(self) -> None:
        prompts, gold = _assets()
        gold["tasks"][2]["accepted_source_ranges"][0]["path"] = "../secret.cs"

        with self.assertRaisesRegex(SweQaError, "unsafe source path"):
            validate_rag_contract(prompts, gold)

    def test_nested_gold_field_in_prompt_asset_is_rejected(self) -> None:
        prompts, gold = _assets()
        prompts["tasks"][0]["metadata"] = {"required_steps": ["leaked"]}

        with self.assertRaisesRegex(SweQaError, "judge-only gold fields"):
            validate_rag_contract(prompts, gold)

    def test_validation_split_leakage_is_rejected(self) -> None:
        prompts, gold = _assets()
        gold["tasks"][12]["split"] = "development"

        with self.assertRaisesRegex(SweQaError, "split mismatch"):
            validate_rag_contract(prompts, gold)

    def test_cli_validates_frozen_prompt_and_gold_files(self) -> None:
        prompts, gold = _assets()
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir) / "repo"
            repository.mkdir()
            source = repository / "src/example.cs"
            source.parent.mkdir()
            contents = "class Example {}\n"
            source.write_text(contents, encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "init"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repository), "add", "src/example.cs"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"],
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source_hash = hashlib.sha256(contents.encode()).hexdigest()
            for task in prompts["tasks"]:
                task["repository_commit"] = commit
            for task in gold["tasks"]:
                if task["accepted_source_ranges"]:
                    task["accepted_source_ranges"][0]["source_sha256"] = source_hash
                    task["accepted_source_ranges"][0]["end_line"] = 1
            corpus = Path(temp_dir) / "corpus"
            (corpus / "src").mkdir(parents=True)
            (corpus / "src/example.cs").write_text(contents, encoding="utf-8")
            source_lock = {
                "repositories": [
                    {
                        "name": "example/repo",
                        "upstream_revision": commit,
                        "files": [{"path": "src/example.cs", "sha256": source_hash}],
                    }
                ]
            }
            prompts_path = Path(temp_dir) / "prompts.json"
            gold_path = Path(temp_dir) / "gold.json"
            references_path = Path(temp_dir) / "references.json"
            lock_path = Path(temp_dir) / "source-lock.json"
            prompts_path.write_text(json.dumps(prompts), encoding="utf-8")
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            references_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "visibility": "judge-only",
                        "references": [
                            {
                                "task_id": task["task_id"],
                                "question": prompts["tasks"][index]["question"],
                                "reference_answer": " ".join(
                                    step["description"] for step in task["required_steps"]
                                ),
                            }
                            for index, task in enumerate(gold["tasks"])
                        ],
                    }
                ),
                encoding="utf-8",
            )
            lock_path.write_text(json.dumps(source_lock), encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                result = main(
                    [
                        "validate-rag-contract",
                        "--prompts",
                        str(prompts_path),
                        "--gold",
                        str(gold_path),
                        "--references",
                        str(references_path),
                        "--source-lock",
                        str(lock_path),
                        "--corpus",
                        str(corpus),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertIn('"task_count": 24', output.getvalue())

    def test_pair_loader_accepts_three_explicit_evidence_profiles(self) -> None:
        profile_names = ("rg", "r4", "sufficient-pack")
        trial = {
            "trial_index": 1,
            "answer": "answer with source citation",
            "input_tokens": 10,
            "output_tokens": 5,
            "tool_calls": 1,
            "agent_wall_seconds": 1.0,
            "cost_usd": 0.01,
        }
        pair = {
            "task_id": "task-01",
            "valid": True,
            "profiles": {
                name: {"trials": [dict(trial)]} for name in profile_names
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pair_path = Path(temp_dir) / "pair-task-01.json"
            pair_path.write_text(json.dumps(pair), encoding="utf-8")

            loaded = load_pairs(pair_path.parent, ["task-01"], profile_names)

        self.assertEqual(set(loaded["task-01"]["profiles"]), set(profile_names))

    def test_evidence_pack_validates_source_slice_and_scores_steps(self) -> None:
        contents = "first line\nsecond line\n"
        digest = hashlib.sha256(contents.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir)
            (source_root / "src").mkdir()
            (source_root / "src/example.cs").write_text(contents, encoding="utf-8")
            source_lock = {
                "repositories": [{"files": [{"path": "src/example.cs", "sha256": digest}]}]
            }
            items = [
                {"rank": 1, "path": "src/example.cs", "start_line": 2, "end_line": 2, "source_sha256": digest, "content": "second line\n"}
            ]
            verified = validate_source_pack(items, source_root=source_root, source_lock=source_lock)

        gold = {
            "required_steps": [{"step_id": "step-1", "accepted_range_ids": ["range-1"]}],
            "accepted_source_ranges": [{"range_id": "range-1", "path": "src/example.cs", "start_line": 2, "end_line": 2}],
        }
        coverage = score_required_steps(gold, verified)
        citations = audit_answer_citations("This is supported [src/example.cs:2].", verified)

        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["covered_steps"], 1)
        self.assertTrue(citations["all_citations_in_pack"])

    def test_abstention_evidence_is_complete_only_for_an_empty_pack(self) -> None:
        contents = "unrelated source\n"
        digest = hashlib.sha256(contents.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir)
            source_path = source_root / "src/example.cs"
            source_path.parent.mkdir()
            source_path.write_text(contents, encoding="utf-8")
            source_lock = {
                "repositories": [{"files": [{"path": "src/example.cs", "sha256": digest}]}]
            }
            empty = validate_source_pack(
                [], source_root=source_root, source_lock=source_lock
            )
            nonempty = validate_source_pack(
                [
                    {
                        "rank": 1,
                        "path": "src/example.cs",
                        "start_line": 1,
                        "end_line": 1,
                        "source_sha256": digest,
                        "content": contents,
                    }
                ],
                source_root=source_root,
                source_lock=source_lock,
            )

        gold = {
            "outcome": "abstain",
            "required_steps": [
                {"step_id": "abstain", "accepted_range_ids": []}
            ],
            "accepted_source_ranges": [],
        }
        empty_result = score_required_steps(gold, empty)
        nonempty_result = score_required_steps(gold, nonempty)
        self.assertTrue(empty_result["complete"])
        self.assertTrue(empty_result["abstention_pack_empty"])
        self.assertEqual(empty_result["required_steps"], 0)
        self.assertFalse(nonempty_result["complete"])

    def test_citation_suffix_resolves_only_when_the_source_path_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir)
            rows = []
            items = []
            for rank, path in enumerate(
                ("repo-a/src/JsonWriter.cs", "repo-b/src/JsonWriter.cs"), start=1
            ):
                source_path = source_root / path
                source_path.parent.mkdir(parents=True)
                source_path.write_text("WriteValue();\n", encoding="utf-8")
                digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
                rows.append({"path": path, "sha256": digest})
                items.append(
                    {
                        "rank": rank,
                        "path": path,
                        "start_line": 1,
                        "end_line": 1,
                        "source_sha256": digest,
                        "content": "WriteValue();\n",
                    }
                )
            source_lock = {"repositories": [{"files": rows}]}
            verified = validate_source_pack(
                items, source_root=source_root, source_lock=source_lock
            )

        ambiguous = audit_answer_citations("JsonWriter.cs:1", verified)
        unique = audit_answer_citations("repo-a/src/JsonWriter.cs:1", verified)
        self.assertFalse(ambiguous["all_citations_in_pack"])
        self.assertEqual(ambiguous["citations"][0]["resolved_path"], None)
        self.assertTrue(unique["all_citations_in_pack"])
        self.assertEqual(
            unique["citations"][0]["resolved_path"], "repo-a/src/JsonWriter.cs"
        )

    def test_evidence_pack_rejects_content_that_differs_from_source(self) -> None:
        contents = "expected source\n"
        digest = hashlib.sha256(contents.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir)
            (source_root / "source.cs").write_text(contents, encoding="utf-8")
            source_lock = {"repositories": [{"files": [{"path": "source.cs", "sha256": digest}]}]}
            item = {"rank": 1, "path": "source.cs", "start_line": 1, "end_line": 1, "source_sha256": digest, "content": "forged source\n"}

            with self.assertRaisesRegex(SweQaError, "does not match"):
                validate_source_pack([item], source_root=source_root, source_lock=source_lock)


if __name__ == "__main__":
    unittest.main()
