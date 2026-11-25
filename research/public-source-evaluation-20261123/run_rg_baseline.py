#!/usr/bin/env python3
"""Run the frozen term-coverage ripgrep baseline for one task split."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import shutil
import subprocess
import time
from pathlib import Path


STOP_WORDS = frozenset(
    "a an and are as at be been being by can could did do does for from had has have "
    "how if in into is it its may might of on or should that the their then there these "
    "this to was were what when where which while who why will with would".split()
)
TOKEN = re.compile(r"[A-Za-z0-9_]{3,}")
WINDOW_RADIUS = 4
MAX_ITEMS = 10
MAX_SOURCE_BYTES = 24_576


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def query_terms(question: str) -> list[str]:
    return sorted({term.lower() for term in TOKEN.findall(question) if term.lower() not in STOP_WORDS})


def merge_windows(matches: dict[int, dict[str, object]], line_count: int) -> list[dict[str, object]]:
    windows: list[dict[str, object]] = []
    for line in sorted(matches):
        start = max(1, line - WINDOW_RADIUS)
        end = min(line_count, line + WINDOW_RADIUS)
        hit = matches[line]
        if windows and start <= int(windows[-1]["end_line"]) + 1:
            previous = windows[-1]
            previous["end_line"] = max(int(previous["end_line"]), end)
            previous["terms"].update(hit["terms"])
            previous["match_count"] = int(previous["match_count"]) + int(hit["match_count"])
        else:
            windows.append(
                {
                    "start_line": start,
                    "end_line": end,
                    "terms": set(hit["terms"]),
                    "match_count": int(hit["match_count"]),
                }
            )
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True, help="development.json or validation-prompts.json")
    parser.add_argument("--corpus", type=Path, default=Path("/tmp/zvec-public-source-20261123"))
    parser.add_argument("--output", type=Path, required=True, help="new durable output directory")
    parser.add_argument("--quiet", action="store_true", help="print only task count and manifest hash")
    args = parser.parse_args()
    rg = shutil.which("rg")
    if rg is None:
        parser.error("ripgrep is not installed")
    if not args.corpus.is_dir():
        parser.error(f"corpus does not exist: {args.corpus}")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"output directory must be absent or empty: {args.output}")

    tasks_doc = json.loads(args.tasks.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    run_rows: list[dict[str, object]] = []
    for task in tasks_doc["tasks"]:
        task_started = time.perf_counter()
        task_id = str(task["task_id"])
        question = str(task["question"])
        terms = query_terms(question)
        if not terms:
            raise ValueError(f"no lexical terms for {task_id}")
        task_dir = args.output / task_id
        task_dir.mkdir()
        raw_path = task_dir / "rg-candidates.jsonl"
        command = [rg, "--json", "--line-number", "--ignore-case", "--fixed-strings", "--word-regexp", "--glob", "*.cs"]
        for term in terms:
            command.extend(["--regexp", term])
        command.append(".")
        time_path = (task_dir / "rg.time").resolve()
        timed_command = ["/usr/bin/time", "-f", "%e\t%M", "-o", str(time_path), *command]
        result = subprocess.run(timed_command, cwd=args.corpus, capture_output=True)
        if result.returncode not in (0, 1):
            raise RuntimeError(f"rg failed for {task_id}: {result.stderr.decode(errors='replace')}")
        time_fields = time_path.read_text().strip().split("\t")
        rg_maxrss_kb = int(time_fields[1])
        raw_path.write_bytes(result.stdout)
        (task_dir / "rg.stderr.txt").write_bytes(result.stderr)

        matches_by_file: dict[str, dict[int, dict[str, object]]] = {}
        for raw_line in result.stdout.splitlines():
            event = json.loads(raw_line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            relative_path = data["path"].get("text")
            if relative_path is None:
                continue
            relative_path = relative_path.removeprefix("./")
            line_number = int(data["line_number"])
            line_text = data["lines"].get("text", "")
            matched = {
                submatch["match"].get("text", "").lower()
                for submatch in data.get("submatches", [])
            }
            matched.intersection_update(terms)
            if not matched:
                continue
            per_line = matches_by_file.setdefault(relative_path, {})
            entry = per_line.setdefault(line_number, {"terms": set(), "match_count": 0})
            entry["terms"].update(matched)
            entry["match_count"] = int(entry["match_count"]) + len(data.get("submatches", []))

        candidates: list[dict[str, object]] = []
        file_coverage: dict[str, int] = {}
        for relative_path, matches in matches_by_file.items():
            path = args.corpus / relative_path
            file_bytes = path.read_bytes()
            lines = file_bytes.splitlines(keepends=True)
            if not lines:
                continue
            file_terms = set().union(*(entry["terms"] for entry in matches.values()))
            file_coverage[relative_path] = len(file_terms)
            for window in merge_windows(matches, len(lines)):
                start = int(window["start_line"])
                end = int(window["end_line"])
                content = b"".join(lines[start - 1 : end])
                candidates.append(
                    {
                        "path": relative_path,
                        "start_line": start,
                        "end_line": end,
                        "terms": sorted(window["terms"]),
                        "window_term_coverage": len(window["terms"]),
                        "file_term_coverage": len(file_terms),
                        "match_count": int(window["match_count"]),
                        "source_bytes": len(content),
                        "source_sha256": sha256(file_bytes),
                        "content_sha256": sha256(content),
                        "content": content.decode("utf-8"),
                    }
                )
        candidates.sort(
            key=lambda item: (
                -int(item["window_term_coverage"]),
                -int(item["file_term_coverage"]),
                -int(item["match_count"]),
                str(item["path"]),
                int(item["start_line"]),
            )
        )
        selected: list[dict[str, object]] = []
        used_bytes = 0
        for candidate_rank, item in enumerate(candidates, start=1):
            if len(selected) >= MAX_ITEMS:
                break
            size = int(item["source_bytes"])
            if used_bytes + size > MAX_SOURCE_BYTES:
                continue
            selected.append({**item, "candidate_rank": candidate_rank, "rank": len(selected) + 1})
            used_bytes += size

        (task_dir / "rg-ranked-candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2) + "\n"
        )
        delivered = {
            "task_id": task_id,
            "query_terms": terms,
            "items": selected,
            "item_count": len(selected),
            "source_content_bytes": used_bytes,
            "source_content_byte_budget": MAX_SOURCE_BYTES,
            "item_budget": MAX_ITEMS,
        }
        delivered_bytes = (json.dumps(delivered, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (task_dir / "rg-delivered.json").write_bytes(delivered_bytes)
        run_rows.append(
            {
                "task_id": task_id,
                "query_term_count": len(terms),
                "candidate_file_count": len(matches_by_file),
                "candidate_line_count": sum(len(lines) for lines in matches_by_file.values()),
                "candidate_window_count": len(candidates),
                "delivered_item_count": len(selected),
                "delivered_source_content_bytes": used_bytes,
                "raw_candidates_sha256": sha256(result.stdout),
                "delivered_sha256": sha256(delivered_bytes),
                "rg_wall_seconds": float(time_fields[0]),
                "rg_peak_rss_kb": rg_maxrss_kb,
                "ranking_end_to_end_seconds": time.perf_counter() - task_started,
                "runner_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        )

    manifest = {
        "schema_version": 1,
        "corpus_id": tasks_doc.get("corpus_id", "public-csharp-source-20261123-v1"),
        "task_input_sha256": sha256(args.tasks.read_bytes()),
        "rg_version": subprocess.run([rg, "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0],
        "fixed_stop_words_sha256": sha256(" ".join(sorted(STOP_WORDS)).encode()),
        "window_radius_lines": WINDOW_RADIUS,
        "rank_order": ["window_term_coverage_desc", "file_term_coverage_desc", "match_count_desc", "path_asc", "start_line_asc"],
        "budgets": {"items": MAX_ITEMS, "source_content_bytes": MAX_SOURCE_BYTES},
        "runs": run_rows,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_path = args.output / "run-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = sha256(manifest_bytes)
    if args.quiet:
        print(json.dumps({"tasks": len(tasks_doc["tasks"]), "manifest_sha256": manifest_sha}))
    else:
        print(json.dumps({"tasks": len(tasks_doc["tasks"]), "manifest_sha256": manifest_sha, "summary": run_rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
