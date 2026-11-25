from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zg_bench.core.errors import SweQaError
from zg_bench.swe_qa.overnight import OvernightLedger, overnight_main


class OvernightLedgerTests(unittest.TestCase):
    def test_completed_call_is_reused_after_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = {"choices": [{"message": {"content": "answer"}}], "usage": {"prompt_tokens": 4}}
            with OvernightLedger(root) as ledger:
                result = ledger.call(
                    role="answer",
                    model="gpt-6-luna",
                    messages=[{"role": "user", "content": "question"}],
                    invoke=lambda: first,
                )
            self.assertEqual(result, first)
            with OvernightLedger(root, resume=True) as ledger:
                result = ledger.call(
                    role="answer",
                    model="gpt-6-luna",
                    messages=[{"role": "user", "content": "question"}],
                    invoke=lambda: self.fail("completed request must be cached"),
                )
                self.assertEqual(ledger.usage()["answer"], 1)
            self.assertEqual(result, first)

    def test_usage_counts_indeterminate_request_as_spent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeError):
                with OvernightLedger(root) as ledger:
                    ledger.call(
                        role="reviewer",
                        model="gpt-6.1-sol",
                        messages=[{"role": "user", "content": "review"}],
                        invoke=lambda: (_ for _ in ()).throw(RuntimeError("lost")),
                    )
            with OvernightLedger(root, resume=True) as ledger:
                self.assertEqual(ledger.usage()["reviewer"], 1)

    def test_interrupted_call_is_marked_indeterminate_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                with OvernightLedger(root) as ledger:
                    ledger.call(
                        role="grader",
                        model="gpt-6.1-sol",
                        messages=[{"role": "user", "content": "grade"}],
                        invoke=lambda: (_ for _ in ()).throw(RuntimeError("lost response")),
                    )
            with OvernightLedger(root, resume=True) as ledger:
                with self.assertRaisesRegex(SweQaError, "indeterminate"):
                    ledger.call(
                        role="grader",
                        model="gpt-6.1-sol",
                        messages=[{"role": "user", "content": "grade"}],
                        invoke=lambda: self.fail("indeterminate request must not retry"),
                    )
                self.assertEqual(ledger.usage()["grader"], 1)

    def test_dry_run_reports_limits_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "new-run"
            status = overnight_main([
                "--run-id", "test-run",
                "--output-root", str(output),
                "--dry-run",
            ])
            self.assertEqual(status, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
