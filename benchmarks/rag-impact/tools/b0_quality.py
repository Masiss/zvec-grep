#!/usr/bin/env python3
"""B0 quality pilot using the frozen six-call query policy (development cases).

This is a quality pilot, not the full frozen B0 receipt:
- real-source development cases only; D13-D16 synthetic fixtures are pending;
- held-out cases are not inspected or run;
- context token counting is NOT_MEASURED (no frozen tokenizer on this host);
- performance, memory and concurrency are separate measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
DEV_CASES = RAG_DIR / "cases" / "dev.json"
CORPUS_MANIFEST = RAG_DIR / "corpus-manifest.json"

FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")
RG_GROUP_RE = re.compile(r"^(\S.*?)\s*$")
RG_LINE_RE = re.compile(r"^\s+(\d+):")
RG_INLINE_RE = re.compile(r"^(.*?):(\d+):")

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
STOPWORDS = {
    "la", "là", "co", "có", "cua", "của", "va", "và", "nhung", "những", "nao", "nào",
    "gi", "gì", "di", "đi", "qua", "đâu", "khi", "thì", "cho", "trong", "ra", "sao",
    "nhu", "như", "the", "thế", "mot", "một", "cac", "các", "duoc", "được", "bi", "bị",
    "tu", "từ", "voi", "với", "hay", "khong", "không", "phai", "phải", "moi", "mọi",
    "lan", "lần", "sua", "sửa", "doi", "đổi", "the", "thể", "nhung", "nhưng",
    "anh", "ảnh", "huong", "hưởng", "ngoai", "ngoài", "vao", "vào", "noi", "nơi",
    "nay", "này", "do", "đó", "o", "ở", "tai", "tại", "bang", "bằng", "de", "để",
    "neu", "nếu", "vi", "vì", "nen", "nên", "rang", "rằng", "cung", "cũng", "chi", "chỉ",
    "con", "còn", "da", "đã", "dang", "đang", "se", "sẽ", "boi", "bởi", "theo", "theo",
    "tren", "trên", "duoi", "dưới", "giua", "giữa", "sau", "truoc", "trước", "khien", "khiến",
    "lam", "làm", "cai", "cái", "chay", "chạy",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_fts(text: str, corpus_root: Path) -> list[dict]:
    units: list[dict] = []
    current: str | None = None
    for line in text.splitlines():
        match = FILE_RE.match(line)
        if match:
            current = match.group(1)
            continue
        match = RANGE_RE.match(line)
        if match and current:
            path = Path(current)
            if path.is_absolute():
                try:
                    current = str(path.relative_to(corpus_root))
                except ValueError:
                    pass
            units.append(
                {
                    "path": current,
                    "start_line": int(match.group(1)),
                    "end_line": int(match.group(2)),
                }
            )
    return units


def parse_rg(text: str, corpus_root: Path) -> list[dict]:
    units: list[dict] = []
    current: str | None = None
    for line in text.splitlines():
        inline = RG_INLINE_RE.match(line)
        if inline and not line.startswith(" "):
            path, line_no = inline.group(1), int(inline.group(2))
            units.append({"path": path, "start_line": line_no, "end_line": line_no})
            continue
        grouped = RG_LINE_RE.match(line)
        if grouped and current:
            line_no = int(grouped.group(1))
            units.append({"path": current, "start_line": line_no, "end_line": line_no})
            continue
        if line and not line.startswith(" ") and not line.startswith("Context:") and not line.startswith("Coverage:") and not line.startswith("Files:"):
            current = line
    normalized = []
    for unit in units:
        path = Path(unit["path"])
        if path.is_absolute():
            try:
                unit["path"] = str(path.relative_to(corpus_root))
            except ValueError:
                pass
        normalized.append(unit)
    return normalized


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def content_words(question: str) -> list[str]:
    return [w for w in WORD_RE.findall(question) if w.lower() not in STOPWORDS]


def content_sequence(question: str) -> list[str]:
    return [w for w in WORD_RE.findall(question) if w.lower() not in STOPWORDS]


def first_phrase(question: str) -> str:
    return " ".join(content_sequence(question)[:3])


def last_phrase(question: str) -> str:
    return " ".join(content_sequence(question)[-3:])


def code_tokens(question: str) -> list[str]:
    tokens = []
    for word in WORD_RE.findall(question):
        if len(word) < 3:
            continue
        if word[0].isupper() or "_" in word or any(ch.isupper() for ch in word[1:]):
            tokens.append(word)
    return tokens[:2]


def run_cmd(cmd: list[str], cwd: Path, env: dict, timeout: int) -> tuple[str, float, str]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(cmd)}: {proc.stderr[-500:]}")
    return proc.stdout, elapsed, proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).resolve()
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    dev = json.loads(DEV_CASES.read_text(encoding="utf-8"))
    if manifest["totals"]["files"] != 822:
        raise SystemExit("unexpected corpus manifest totals")
    if dev["status"] != "ALL_24_CASES_FROZEN_B0_PENDING":
        raise SystemExit(f"unexpected dev status: {dev['status']}")

    env = os.environ.copy()
    env.setdefault("ZVEC_GREP_MODE", "direct")
    env["TMPDIR"] = str(corpus_root.parent / "tmp")

    cases = []
    for case in dev["cases"]:
        if case["id"] in {"D13", "D14", "D15", "D16"}:
            continue
        if not case.get("source_refs"):
            continue
        question = case["question"]
        first = first_phrase(question)
        last = last_phrase(question)
        codes = code_tokens(question)
        calls = [
            ("hybrid_question", [args.binary, question, "--preview", "detail"], "fts"),
            ("fts_question", [args.binary, "--fts", question, "--preview", "detail"], "fts"),
        ]
        if first and first.lower() != question.lower():
            calls.append(("fts_first_phrase", [args.binary, "--fts", first, "--preview", "detail"], "fts"))
        if last and last.lower() != question.lower() and last != first:
            calls.append(("rg_last_phrase", [args.binary, "--rg", "-n", "-F", last], "rg"))
        if codes:
            calls.append(("fts_code_tokens", [args.binary, "--fts", " ".join(codes), "--preview", "detail"], "fts"))
            rg_cmd = [args.binary, "--rg", "-n"]
            for code in codes:
                rg_cmd += ["-F", "-e", code]
            calls.append(("rg_code_tokens", rg_cmd, "rg"))
        calls = calls[:6]

        gold = []
        for ref in case["source_refs"]:
            for span in ref["spans"]:
                gold.append((ref["path"], span["start_line"], span["end_line"]))

        call_results = []
        best_recall = -1.0
        best_call = None
        all_units: list[dict] = []
        context_bytes = 0
        for label, cmd, kind in calls:
            output, elapsed, stderr = run_cmd(cmd, corpus_root, env, args.timeout)
            context_bytes += len(output.encode("utf-8"))
            units = (parse_fts(output, corpus_root) if kind == "fts" else parse_rg(output, corpus_root))[:10]
            all_units.extend(units)
            matched = sum(
                1
                for path, start, end in gold
                if any(u["path"] == path and overlap(u["start_line"], u["end_line"], start, end) for u in units)
            )
            recall = (matched / len(gold)) if gold else None
            call_results.append(
                {
                    "call": label,
                    "top10_units": units,
                    "gold_spans": len(gold),
                    "matched_gold_spans": matched,
                    "recall": recall,
                    "wall_s": round(elapsed, 4),
                    "stderr_tail": stderr[-200:],
                }
            )
            if recall is not None and recall > best_recall:
                best_recall = recall
                best_call = label

        read_paths: list[str] = []
        read_bytes = 0
        for unit in all_units:
            if unit["path"] in read_paths:
                continue
            if len(read_paths) >= 32 or read_bytes >= 64 * 1024:
                break
            target = corpus_root / unit["path"]
            if not target.is_file():
                continue
            read_paths.append(unit["path"])
            read_bytes += target.stat().st_size
        context_bytes += read_bytes

        cases.append(
            {
                "id": case["id"],
                "question_sha256": sha256_text(question),
                "first_phrase": first,
                "last_phrase": last,
                "code_tokens": codes,
                "calls": call_results,
                "best_call": best_call,
                "recall": best_recall if best_recall >= 0 else None,
                "additional_source_reads": len(read_paths),
                "additional_context_bytes": context_bytes,
            }
        )

    recalls = [c["recall"] for c in cases if c["recall"] is not None]
    receipt = {
        "schema_version": 1,
        "status": "PILOT_SIX_CALL_POLICY_NOT_FROZEN_B0",
        "purpose": "B0 quality pilot using the frozen six-call policy; development cases only",
        "corpus_manifest_sha256": hashlib.sha256(CORPUS_MANIFEST.read_bytes()).hexdigest(),
        "dev_cases_sha256": hashlib.sha256(DEV_CASES.read_bytes()).hexdigest(),
        "binary": str(Path(args.binary).resolve()),
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "mean_recall": (sum(recalls) / len(recalls)) if recalls else None,
            "mean_additional_source_reads": (
                sum(c["additional_source_reads"] for c in cases) / len(cases)
            ),
            "mean_additional_context_bytes": (
                sum(c["additional_context_bytes"] for c in cases) / len(cases)
            ),
            "context_tokens": "NOT_MEASURED_NO_TOKENIZER",
            "heldout": "NOT_RUN_IMPLEMENTER_MUST_NOT_INSPECT",
            "synthetic_d13_d16": "NOT_AUTHORED",
        },
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(receipt["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
