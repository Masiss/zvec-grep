"""Codex OAuth completion adapter tests."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from zg_bench.engines.codex import codex_completion


class CodexCompletionTests(unittest.TestCase):
    def test_converts_codex_jsonl_to_chat_completion_shape(self) -> None:
        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text('{"correctness":2}\n', encoding="utf-8")
            event = {
                "type": "turn.completed",
                "usage": {"input_tokens": 120, "output_tokens": 6},
            }
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(event) + "\n", stderr=""
            )

        with (
            patch("zg_bench.engines.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("zg_bench.engines.codex.subprocess.run", side_effect=fake_run),
        ):
            response = codex_completion("gpt-6.1-sol")(
                messages=[{"role": "user", "content": "Return a score."}]
            )

        self.assertEqual(
            response["choices"][0]["message"]["content"], '{"correctness":2}'
        )
        self.assertEqual(response["usage"]["prompt_tokens"], 120)
        self.assertEqual(response["usage"]["completion_tokens"], 6)
        self.assertIsNone(response["_hidden_params"]["response_cost"])


if __name__ == "__main__":
    unittest.main()
