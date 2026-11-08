#!/usr/bin/env python3
"""R4-D1 deployable prototype: observed-identifier query expansion.

Read-only over frozen dev cases/B0 seeds and the existing corpus index. No gold
identifiers are inputs; no model/LSP/graph/second index.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
B0 = RAG / "results" / "b0-official.json"
DEV = RAG / "cases" / "dev.json"
CORPUS = RAG / "corpus-manifest.json"
OUT = RAG / "results" / "candidate-r4d1.json"

BIN = os.environ.get(
    "ZG_BIN",
    str(
        Path.home()
        / ".local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle/fixed-1.98.0-bin/zg"
    ),
)
CORPUS_ROOT = Path(
    os.environ.get(
        "ZG_CORPUS_ROOT",
        str(Path.home() / ".local/share/zvec-grep/rag-impact/20261108-b0-pilot/corpus"),
    )
)
MODEL_CACHE = Path.home() / ".local/share/zvec-grep/index-memory-diagnostics/laya-20261104/models"

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|\s)+"
    r"[\w<>,\[\]\?\.]+\s+(\w+)\s*\([^;]*\)\s*$"
)
TYPE_RE = re.compile(r"\b(?:class|interface|struct)\s+(\w+)")
FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")


def tokens(text: str) -> set[str]:
    return {word.lower() for word in WORD_RE.findall(text) if len(word) >= 2}


def parse_fts(text: str) -> list[tuple[str, int, int]]:
    units = []
    current = None
    for line in text.splitlines():
        match = FILE_RE.match(line)
        if match:
            current = match.group(1)
            continue
        match = RANGE_RE.match(line)
        if match and current:
            units.append((current, int(match.group(1)), int(match.group(2))))
    return units


def search(identifier: str) -> tuple[list[tuple[str, int, int]], float]:
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(Path.home() / ".local/share/zvec-grep/rollback-20261101T130300Z")
    env["ZVEC_GREP_HOME"] = str(CORPUS_ROOT.parent / "home")
    env["ZVEC_GREP_MODEL_CACHE"] = str(MODEL_CACHE)
    env["TMPDIR"] = str(CORPUS_ROOT.parent / "tmp")
    started = time.perf_counter()
    proc = subprocess.run(
        [BIN, "--fts", identifier, "--limit", "10", "--preview", "detail"],
        cwd=CORPUS_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    if proc.returncode != 0:
        return [], elapsed
    return parse_fts(proc.stdout), elapsed


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def main() -> int:
    b0 = json.loads(B0.read_text())
    dev = json.loads(DEV.read_text())
    corpus = json.loads(CORPUS.read_text())
    root = Path(corpus["source_root"])
    dev_by = {case["id"]: case for case in dev["cases"]}
    rows = []
    for b0_case in b0["quality"]["dev_real_cases"]:
        case_id = b0_case["id"]
        gold = []
        for reference in dev_by[case_id].get("source_refs", []):
            for span in reference.get("spans", []):
                gold.append((reference["path"], span["start_line"], span["end_line"]))
        query_terms = tokens(dev_by[case_id]["question"])
        seeds = []
        for call in b0_case["calls"]:
            for unit in call.get("units") or []:
                seeds.append((unit["path"], unit["start_line"], unit["end_line"]))
        seeds = list(dict.fromkeys(seeds))
        file_counts = {}
        for path, *_ in seeds:
            file_counts[path] = file_counts.get(path, 0) + 1
        seed_files = sorted(file_counts, key=lambda path: (-file_counts[path], path))[:8]
        identifiers = {}
        files_opened = 0
        bytes_read = 0
        for path in seed_files:
            target = root / path
            if not target.is_file():
                continue
            data = target.read_text(encoding="utf-8", errors="replace")
            files_opened += 1
            bytes_read += len(data.encode("utf-8"))
            for line in data.splitlines():
                for regex in (METHOD_RE, TYPE_RE):
                    match = regex.search(line)
                    if match:
                        name = match.group(1)
                        if len(name) >= 4 and name not in {"public", "private", "static"}:
                            identifiers[name] = identifiers.get(name, 0) + 1
            stem = Path(path).stem
            if len(stem) >= 4:
                identifiers[stem] = identifiers.get(stem, 0) + 1
        ranked = sorted(
            identifiers.items(),
            key=lambda item: (-(len(query_terms & tokens(item[0])) * 10 + item[1]), item[0]),
        )
        chosen = [name for name, _ in ranked[:2]]
        extra_units = []
        extra_ms = 0.0
        extra_units_count = 0
        for identifier in chosen:
            units, elapsed = search(identifier)
            extra_ms += elapsed
            extra_units_count += len(units)
            extra_units.extend(units)
        # Preserve up to 5 initial seeds and add up to 5 new units.
        initial = []
        seen = set()
        for path, start, end in seeds:
            key = (path, start, end)
            if key not in seen:
                seen.add(key)
                initial.append((path, start, end))
            if len(initial) >= 5:
                break
        additions = []
        for path, start, end in extra_units:
            key = (path, start, end)
            if key not in seen:
                seen.add(key)
                additions.append((path, start, end))
            if len(additions) >= 5:
                break
        pack = [
            {"path": path, "start_line": start, "end_line": end, "source": "initial"}
            for path, start, end in initial
        ] + [
            {"path": path, "start_line": start, "end_line": end, "source": "expansion"}
            for path, start, end in additions
        ]
        fixed = [
            (unit["path"], unit["start_line"], unit["end_line"])
            for call in b0_case["calls"]
            if call["call"] == "fts_code_tokens"
            for unit in (call.get("units") or [])
        ][:10]
        matched = sum(
            1
            for path, start, end in gold
            if any(item["path"] == path and overlap(item["start_line"], item["end_line"], start, end) for item in pack)
        )
        fixed_matched = sum(
            1
            for path, start, end in gold
            if any(path == p and overlap(start, end, s, e) for p, s, e in fixed)
        )
        rows.append(
            {
                "id": case_id,
                "gold_spans": len(gold),
                "matched_gold_spans": matched,
                "recall": matched / len(gold) if gold else 0.0,
                "fixed_matched_gold_spans": fixed_matched,
                "fixed_recall": fixed_matched / len(gold) if gold else 0.0,
                "b0_oracle_recall": b0_case["recall"],
                "identifiers": chosen,
                "files_opened": files_opened,
                "bytes_read": bytes_read,
                "extra_search_calls": len(chosen),
                "extra_units": extra_units_count,
                "extra_search_ms": extra_ms,
                "pack_files": len({item["path"] for item in pack}),
                "pack": pack,
            }
        )
    mean = lambda key: sum(row[key] for row in rows) / len(rows)  # noqa: E731
    receipt = {
        "schema_version": 1,
        "status": "R4D1_POLICY_RUN",
        "policy": "six B0 calls -> observed identifiers from seed files -> <=2 exact FTS probes -> 5 initial + 5 expansion ranges",
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "recall": mean("recall"),
            "fixed_recall": mean("fixed_recall"),
            "b0_oracle_recall": mean("b0_oracle_recall"),
            "files_opened": mean("files_opened"),
            "bytes_read": mean("bytes_read"),
            "extra_units": mean("extra_units"),
            "extra_search_ms": mean("extra_search_ms"),
            "pack_files": mean("pack_files"),
        },
        "limits": [
            "development cases only; held-out not inspected",
            "observed identifiers only; no gold identifiers as input",
            "no model/LSP/graph/second index",
            "pack files vs post-pack agent reads are reported separately",
        ],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(receipt["summary"], indent=2))
    for row in rows:
        print(
            row["id"],
            "d1=%.3f" % row["recall"],
            "fixed=%.3f" % row["fixed_recall"],
            "ids",
            row["identifiers"],
            "files",
            row["files_opened"],
            "extra_units",
            row["extra_units"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
