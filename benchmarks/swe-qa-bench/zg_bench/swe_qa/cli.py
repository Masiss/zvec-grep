"""Command line interface for the SWE-QA-Bench evaluation pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from zg_bench.core.protocol import JUDGE_MODELS, PROFILE_NAMES
from zg_bench.core.io import load_object
from zg_bench.reports.aggregate import aggregate_reports

from . import SweQaError
from .collect import collect_pair
from .judge import judge_pairs
from .rag_judge import (
    RAG_JUDGE_MODELS,
    RAG_PROFILE_NAMES,
    judge_rag_pairs,
    refresh_rag_citation_audits,
)
from .selection import CI_SCOPES, matrix_outputs
from .rag_contract import validate_rag_contract
from .validation import validate_assets
from .overnight import overnight_main, review_main


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zg_bench.swe_qa",
        description=(
            "Validate, collect, judge, and aggregate the locked SWE-QA-Bench subset."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate pinned benchmark data")
    validate.add_argument("--selection", type=Path, required=True)
    validate.add_argument("--references", type=Path, required=True)
    validate.add_argument("--dataset", type=Path, required=True)

    validate_rag = commands.add_parser(
        "validate-rag-contract", help="validate frozen RAG answer-quality assets"
    )
    validate_rag.add_argument("--prompts", type=Path, required=True)
    validate_rag.add_argument("--gold", type=Path, required=True)
    validate_rag.add_argument("--references", type=Path, required=True)
    validate_rag.add_argument("--source-lock", type=Path, required=True)
    validate_rag.add_argument("--corpus", type=Path, required=True)

    matrix = commands.add_parser("matrix", help="select the locked CI task scope")
    matrix.add_argument("--selection", type=Path, required=True)
    matrix.add_argument("--scope", choices=CI_SCOPES, required=True)

    collect = commands.add_parser("collect", help="collect one Harbor profile pair")
    collect.add_argument("--runs-dir", type=Path, required=True)
    collect.add_argument("--task", required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--expected-trials", type=int, default=1)
    collect.add_argument("--profiles", nargs="+", default=list(PROFILE_NAMES))
    collect.add_argument(
        "--evidence-packs-root",
        type=Path,
        help="prepared RAG sidecars organized as <task_id>/<profile>.json",
    )

    judge = commands.add_parser("judge", help="judge pairs and render the report")
    judge.add_argument("--pairs-root", type=Path, required=True)
    judge.add_argument("--references", type=Path, required=True)
    judge.add_argument("--output-dir", type=Path, required=True)
    judge.add_argument("--expected", nargs="+", action="append", required=True)
    judge.add_argument("--attempts", type=int, default=3)
    judge.add_argument("--model", choices=JUDGE_MODELS, default="glm-5.2")

    judge_rag = commands.add_parser(
        "judge-rag", help="judge lexical, R4, and sufficient-pack answer trials"
    )
    judge_rag.add_argument("--pairs-root", type=Path, required=True)
    judge_rag.add_argument("--references", type=Path, required=True)
    judge_rag.add_argument("--gold", type=Path, required=True)
    judge_rag.add_argument("--source-lock", type=Path, required=True)
    judge_rag.add_argument("--corpus", type=Path, required=True)
    judge_rag.add_argument("--output", type=Path, required=True)
    judge_rag.add_argument("--expected", nargs="+", action="append", required=True)
    judge_rag.add_argument("--profiles", nargs="+", default=list(RAG_PROFILE_NAMES))
    judge_rag.add_argument("--attempts", type=int, default=3)
    judge_rag.add_argument("--model", choices=RAG_JUDGE_MODELS, default="glm-5.2")
    judge_rag.add_argument(
        "--completion-backend",
        choices=("litellm", "codex-cli"),
        default="litellm",
    )

    audit_rag = commands.add_parser(
        "audit-rag-citations",
        help="refresh deterministic citation-to-pack resolution without model calls",
    )
    audit_rag.add_argument("--report", type=Path, required=True)
    audit_rag.add_argument("--pairs-root", type=Path, required=True)
    audit_rag.add_argument("--source-lock", type=Path, required=True)
    audit_rag.add_argument("--corpus", type=Path, required=True)
    audit_rag.add_argument("--output", type=Path, required=True)

    aggregate = commands.add_parser(
        "aggregate", help="combine already judged per-task reports"
    )
    aggregate.add_argument("--reports-root", type=Path, required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    aggregate.add_argument("--expected", nargs="+", action="append")
    aggregate.add_argument(
        "--allow-missing",
        action="store_true",
        help="aggregate completed task reports and record missing expected tasks",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "rag-overnight":
        return overnight_main(values[1:])
    if values and values[0] == "review-overnight":
        return review_main(values[1:])
    args = _parser().parse_args(values)
    try:
        if args.command == "validate":
            result = validate_assets(
                selection_path=args.selection,
                references_path=args.references,
                gold_path=args.gold,
                source_lock_path=args.source_lock,
                corpus_root=args.corpus,
                dataset_path=args.dataset,
            )
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "validate-rag-contract":
            prompts = load_object(args.prompts, label="RAG prompts")
            gold = load_object(args.gold, label="RAG gold")
            references = load_object(args.references, label="RAG references")
            source_lock = load_object(args.source_lock, label="source lock")
            result = validate_rag_contract(
                prompts,
                gold,
                references=references,
                source_lock=source_lock,
                corpus_root=args.corpus,
            )
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "matrix":
            print(matrix_outputs(args.selection, args.scope), end="")
        elif args.command == "collect":
            pair = collect_pair(
                runs_dir=args.runs_dir,
                task=args.task,
                output=args.output,
                expected_trials=args.expected_trials,
                profile_names=tuple(args.profiles),
                evidence_packs_root=args.evidence_packs_root,
            )
            print(
                json.dumps(
                    {
                        "valid": True,
                        "task_id": pair["task_id"],
                        "expected_trials": pair["expected_trials"],
                        "actual_trials": pair["actual_trials"],
                        "output": str(args.output),
                    }
                )
            )
        elif args.command == "judge":
            expected = [task for group in args.expected for task in group]
            report = judge_pairs(
                pairs_root=args.pairs_root,
                references_path=args.references,
                output_dir=args.output_dir,
                expected=expected,
                attempts=args.attempts,
                model=args.model,
            )
            print(
                json.dumps(
                    {
                        "gate_passed": report["gate"]["passed"],
                        "report": str(args.output_dir / "report.json"),
                    }
                )
            )
        elif args.command == "judge-rag":
            expected = [task for group in args.expected for task in group]
            report = judge_rag_pairs(
                pairs_root=args.pairs_root,
                references_path=args.references,
                gold_path=args.gold,
                source_lock_path=args.source_lock,
                corpus_root=args.corpus,
                output_path=args.output,
                expected=expected,
                profile_names=tuple(args.profiles),
                completion_backend=args.completion_backend,
                attempts=args.attempts,
                model=args.model,
            )
            print(
                json.dumps(
                    {
                        "task_count": len(report["cases"]),
                        "report": str(args.output),
                    }
                )
            )
        elif args.command == "audit-rag-citations":
            report = refresh_rag_citation_audits(
                report_path=args.report,
                pairs_root=args.pairs_root,
                source_lock_path=args.source_lock,
                corpus_root=args.corpus,
                output_path=args.output,
            )
            print(
                json.dumps(
                    {"case_count": len(report["cases"]), "output": str(args.output)}
                )
            )
        elif args.command == "aggregate":
            expected = (
                None
                if args.expected is None
                else [task for group in args.expected for task in group]
            )
            report = aggregate_reports(
                reports_root=args.reports_root,
                output_dir=args.output_dir,
                expected=expected,
                allow_missing=args.allow_missing,
            )
            print(
                json.dumps(
                    {
                        "gate_passed": report["gate"]["passed"],
                        "cases": len(report["cases"]),
                        "report": str(args.output_dir / "report.json"),
                    }
                )
            )
        else:  # pragma: no cover - argparse enforces this.
            raise SweQaError(f"unknown command: {args.command}")
    except SweQaError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
