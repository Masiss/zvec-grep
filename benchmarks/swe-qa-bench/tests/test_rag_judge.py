"""Paired RAG answer judge tests using a deterministic model stub."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zg_bench.engines.judge import RAG_SCORE_KEYS
from zg_bench.swe_qa.rag_judge import (
    RAG_CANDIDATE_PROFILE,
    RAG_PROFILE_NAMES,
    _validation_gate,
    judge_rag_pairs,
    refresh_rag_citation_audits,
)


class RagJudgeTests(unittest.TestCase):
    def test_candidate_gate_compares_both_frozen_baselines(self) -> None:
        profile_names = (*RAG_PROFILE_NAMES, RAG_CANDIDATE_PROFILE)
        cases = []
        gold_by_id = {}
        for index in range(12):
            task_id = f"val-{index:02d}"
            gold_by_id[task_id] = {"split": "validation", "outcome": "answerable"}
            profiles = {}
            for profile in profile_names:
                scores = {key: 2 for key in RAG_SCORE_KEYS}
                if profile == "lexical-rg" and index >= 8:
                    scores["correctness"] = 1
                elif profile == "r4-hybrid" and index >= 7:
                    scores["correctness"] = 1
                elif profile == RAG_CANDIDATE_PROFILE and index >= 10:
                    scores["correctness"] = 1
                profiles[profile] = {"judge": {"scores": scores}}
            cases.append({"task_id": task_id, "profiles": profiles})

        gate = _validation_gate(cases, gold_by_id, profile_names)

        self.assertTrue(gate["evaluated"])
        self.assertTrue(gate["passed"])
        self.assertEqual(
            gate["comparison_to_baselines"]["lexical-rg"]["fully_correct_gain"], 2
        )
        self.assertEqual(
            gate["comparison_to_baselines"]["r4-hybrid"]["fully_correct_gain"], 3
        )

        cases[10]["profiles"][RAG_CANDIDATE_PROFILE]["judge"]["scores"][
            "unsupported_claims"
        ] = 1
        rejected = _validation_gate(cases, gold_by_id, profile_names)
        self.assertFalse(rejected["passed"])
        self.assertFalse(
            rejected["comparison_to_baselines"]["lexical-rg"][
                "no_increase_in_unsupported_claims"
            ]
        )

    def test_three_profiles_report_answer_scores_evidence_and_citations(self) -> None:
        source = "public void Reset()\n{\n    Seek(0);\n}\n"
        digest = hashlib.sha256(source.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "corpus"
            (corpus / "src").mkdir(parents=True)
            (corpus / "src/Stream.cs").write_text(source, encoding="utf-8")
            lock = {
                "repositories": [
                    {"name": "repo", "files": [{"path": "src/Stream.cs", "sha256": digest}]}
                ]
            }
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            prompts = {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "repository": "repo",
                        "repository_commit": "a" * 40,
                        "question": "What does Reset do?",
                    }
                ]
            }
            gold = {
                "tasks": [
                    {
                        "task_id": "task-1",
                        "split": "validation",
                        "outcome": "answerable",
                        "required_steps": [
                            {"step_id": "s1", "accepted_range_ids": ["r1"]}
                        ],
                        "accepted_source_ranges": [
                            {"range_id": "r1", "path": "src/Stream.cs", "start_line": 1, "end_line": 4}
                        ],
                    }
                ]
            }
            gold_path = root / "gold.json"
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            reference_path = root / "references.json"
            reference_path.write_text(
                json.dumps(
                    {
                        "references": [
                            {
                                "task_id": "task-1",
                                "question": "What does Reset do?",
                                "reference_answer": "Reset seeks to position zero.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            pack_item = {
                "rank": 1,
                "path": "src/Stream.cs",
                "start_line": 1,
                "end_line": 4,
                "source_sha256": digest,
                "content": source,
            }
            profiles = {
                name: {
                    "trials": [
                        {
                            "trial_index": 1,
                            "answer": "Reset calls Seek(0). Citation: src/Stream.cs:1-4.",
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "tool_calls": 1,
                            "agent_wall_seconds": 1.0,
                            "retrieval_latency_seconds": 0.25,
                            "peak_rss_kib": 2048,
                            "cost_usd": 0.01,
                            "navigation_usage": {
                                "rounds": 1,
                                "calls": 1,
                                "opened_files": 1,
                                "cumulative_source_bytes": len(source.encode()),
                            },
                            "evidence_items": [pack_item],
                        }
                    ]
                }
                for name in RAG_PROFILE_NAMES
            }
            pairs = root / "pairs"
            pairs.mkdir()
            (pairs / "pair-task-1.json").write_text(
                json.dumps({"task_id": "task-1", "valid": True, "profiles": profiles}),
                encoding="utf-8",
            )
            output = root / "report.json"
            runtime = SimpleNamespace(
                api_key="stub-key",
                configuration=SimpleNamespace(base_url="https://judge.invalid"),
            )
            def completion_stub(**kwargs):
                prompt = kwargs["messages"][0]["content"]
                candidate_ids = sorted(
                    set(re.findall(r'"candidate_id":\s*"([^"]+)"', prompt))
                )
                content = json.dumps(
                    {
                        "answers": [
                            {
                                "candidate_id": candidate_id,
                                "scores": {key: 2 for key in RAG_SCORE_KEYS},
                            }
                            for candidate_id in candidate_ids
                        ]
                    }
                )
                return {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

            with patch("zg_bench.swe_qa.rag_judge.resolve_opencode_model", return_value=runtime):
                report = judge_rag_pairs(
                    pairs_root=pairs,
                    references_path=reference_path,
                    gold_path=gold_path,
                    source_lock_path=lock_path,
                    corpus_root=corpus,
                    output_path=output,
                    expected=["task-1"],
                    completion_fn=completion_stub,
                )
            output_was_written = output.is_file()
            refreshed = refresh_rag_citation_audits(
                report_path=output,
                pairs_root=pairs,
                source_lock_path=lock_path,
                corpus_root=corpus,
                output_path=root / "report-citations-v2.json",
            )
            refreshed_citations = refreshed["cases"][0]["evidence"]["r4-hybrid"][0][
                "citations"
            ]

        self.assertEqual(report["profiles"], list(RAG_PROFILE_NAMES))
        self.assertEqual(report["judge"]["usage"]["calls"], 1)
        self.assertEqual(report["judge"]["usage"]["input_tokens"], 10)
        self.assertEqual(report["cases"][0]["judge_batch_usage"]["calls"], 1)
        self.assertFalse(report["validation_gate"]["evaluated"])
        self.assertIsNone(report["validation_gate"]["passed"])
        self.assertEqual(
            report["validation_gate"]["status"],
            "not_evaluated_no_intervention_candidate",
        )
        self.assertTrue(report["cases"][0]["evidence"]["r4-hybrid"][0]["evidence_complete"])
        citation = report["cases"][0]["evidence"]["r4-hybrid"][0]["citations"]
        self.assertTrue(citation["all_citations_in_pack"])
        metrics = report["cases"][0]["profiles"]["r4-hybrid"]["metrics"]
        self.assertEqual(metrics["retrieval_latency_seconds"], 0.25)
        self.assertEqual(metrics["peak_rss_kib"], 2048)
        self.assertGreater(
            report["cases"][0]["profiles"]["r4-hybrid"]["judge"]["mean_wall_seconds"],
            0,
        )
        self.assertTrue(output_was_written)
        self.assertTrue(refreshed_citations["all_citations_in_pack"])


if __name__ == "__main__":
    unittest.main()
