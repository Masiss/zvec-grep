#!/usr/bin/env python3
"""R4-D2 deployable policy prototype: file-hit to source-range local retrieval.

Read-only over the frozen dev cases and B0 search seeds. No gold identifiers are
used as policy input; gold is used only for measurement.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
B0 = RAG / "results" / "b0-official.json"
DEV = RAG / "cases" / "dev.json"
CORPUS = RAG / "corpus-manifest.json"
OUT = RAG / "results" / "candidate-r4d2.json"

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|\s)+"
    r"[\w<>,\[\]\?\.]+\s+(\w+)\s*\([^;]*\)\s*$"
)
STOPWORDS = {
    "la", "là", "co", "có", "cua", "của", "va", "và", "nhung", "những", "nao", "nào",
    "gi", "gì", "di", "đi", "qua", "đâu", "khi", "thì", "cho", "trong", "ra", "sao",
    "nhu", "như", "the", "thế", "mot", "một", "cac", "các", "duoc", "được", "bi", "bị",
    "tu", "từ", "voi", "với", "hay", "khong", "không", "phai", "phải", "moi", "mọi",
    "lan", "lần", "sua", "sửa", "doi", "đổi", "the", "thể", "nhung", "nhưng",
    "anh", "ảnh", "huong", "hưởng", "ngoai", "ngoài", "vao", "vào", "noi", "nơi",
    "nay", "này", "do", "đó", "o", "ở", "tai", "tại", "bang", "bằng", "de", "để",
    "neu", "nếu", "vi", "vì", "nen", "nên", "rang", "rằng", "cung", "cũng", "chi", "chỉ",
    "con", "còn", "da", "đã", "dang", "đang", "se", "sẽ", "boi", "bởi", "theo",
    "tren", "trên", "duoi", "dưới", "giua", "giữa", "sau", "truoc", "trước", "khien", "khiến",
    "lam", "làm", "cai", "cái", "chay", "chạy",
}


def strip_vietnamese(text: str) -> str:
    mapping = str.maketrans(
        "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
        "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ",
        "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd"
        "AAAAAAAAAAAAAAAAAEEEEEEEEEEEIIIIIOOOOOOOOOOOOOOOOOUUUUUUUUUUUYYYYYD",
    )
    return text.translate(mapping)


def tokens(text: str) -> list[str]:
    normalized = strip_vietnamese(text)
    result = []
    for raw in normalized.split():
        if len(raw) >= 2:
            result.append(raw.lower())
    return result


def method_ranges(lines: list[str]) -> list[tuple[str, int, int, str]]:
    methods = []
    for start in range(len(lines)):
        match = METHOD_RE.match(lines[start])
        if not match:
            continue
        name = match.group(1)
        brace_line = start
        while brace_line < len(lines) and "{" not in lines[brace_line]:
            brace_line += 1
            if brace_line - start > 3:
                break
        if brace_line >= len(lines) or "{" not in lines[brace_line]:
            continue
        depth = 0
        end = brace_line
        for index in range(brace_line, len(lines)):
            depth += lines[index].count("{")
            depth -= lines[index].count("}")
            end = index
            if depth <= 0:
                break
        body = "\n".join(lines[start : end + 1])
        methods.append((name, start + 1, end + 1, body))
    return methods


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
        query = dev_by[case_id]["question"]
        query_terms = set(tokens(query))
        seeds = []
        for call in b0_case["calls"]:
            for unit in call.get("units") or []:
                seeds.append((unit["path"], unit["start_line"], unit["end_line"]))
        seeds = list(dict.fromkeys(seeds))
        file_counts = {}
        for path, *_ in seeds:
            file_counts[path] = file_counts.get(path, 0) + 1
        seed_files = sorted(file_counts, key=lambda path: (-file_counts[path], path))[:8]
        items = []
        files_opened = 0
        bytes_read = 0
        candidates = 0
        started = time.perf_counter()
        for path in seed_files:
            target = root / path
            if not target.is_file():
                continue
            data = target.read_text(encoding="utf-8", errors="replace")
            files_opened += 1
            bytes_read += len(data.encode("utf-8"))
            lines = data.splitlines()
            for name, start, end, body in method_ranges(lines):
                candidates += 1
                name_hits = len(query_terms & set(tokens(name)))
                body_hits = len(query_terms & set(tokens(body)))
                seed_overlap = any(
                    seed_path == path and overlap(seed_start, seed_end, start, end)
                    for seed_path, seed_start, seed_end in seeds
                )
                score = 10 + name_hits * 20 + body_hits * 5 + (100 if seed_overlap else 0)
                items.append(
                    {
                        "path": path,
                        "start_line": start,
                        "end_line": end,
                        "score": score,
                        "reason": "local method",
                    }
                )
        for path, start, end in seeds:
            items.append(
                {
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "score": 100,
                    "reason": "search seed",
                }
            )
        # Deduplicate exact ranges, keep highest score.
        dedup = {}
        for item in items:
            key = (item["path"], item["start_line"], item["end_line"])
            if key not in dedup or item["score"] > dedup[key]["score"]:
                dedup[key] = item
        items = list(dedup.values())
        # Select at most 3 files by maximum score, then pack top 10 ranges.
        by_file = {}
        for item in items:
            by_file.setdefault(item["path"], []).append(item)
        file_scores = sorted(
            ((max(i["score"] for i in group), path) for path, group in by_file.items()),
            reverse=True,
        )
        selected_files = {path for _, path in file_scores[:3]}
        pack = [item for item in items if item["path"] in selected_files]
        pack.sort(key=lambda item: (-item["score"], item["path"], item["start_line"]))
        pack = pack[:10]
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        matched = sum(
            1
            for path, start, end in gold
            if any(
                item["path"] == path
                and overlap(item["start_line"], item["end_line"], start, end)
                for item in pack
            )
        )
        fixed = [
            {"path": unit["path"], "start_line": unit["start_line"], "end_line": unit["end_line"]}
            for call in b0_case["calls"]
            if call["call"] == "fts_code_tokens"
            for unit in (call.get("units") or [])
        ][:10]
        fixed_matched = sum(
            1
            for path, start, end in gold
            if any(
                item["path"] == path
                and overlap(item["start_line"], item["end_line"], start, end)
                for item in fixed
            )
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
                "files_opened": files_opened,
                "bytes_read": bytes_read,
                "method_candidates": candidates,
                "pack_files": len({item["path"] for item in pack}),
                "pack": pack,
                "elapsed_ms": elapsed_ms,
            }
        )
    mean = lambda key: sum(row[key] for row in rows) / len(rows)  # noqa: E731
    receipt = {
        "schema_version": 1,
        "status": "R4D2_POLICY_RUN",
        "policy": "six B0 calls -> up to 8 seed files -> local method ranges -> <=3 files, 10 ranges",
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "recall": mean("recall"),
            "fixed_recall": mean("fixed_recall"),
            "b0_oracle_recall": mean("b0_oracle_recall"),
            "files_opened": mean("files_opened"),
            "bytes_read": mean("bytes_read"),
            "method_candidates": mean("method_candidates"),
            "pack_files": mean("pack_files"),
            "elapsed_ms": mean("elapsed_ms"),
        },
        "limits": [
            "development cases only; held-out not inspected",
            "no model/LSP/graph/second index; local source reads only",
            "gold used for measurement only, never as policy input",
            "pack files vs post-pack agent reads are reported separately",
        ],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(receipt["summary"], indent=2))
    for row in rows:
        print(
            row["id"],
            "d2=%.3f" % row["recall"],
            "fixed=%.3f" % row["fixed_recall"],
            "files",
            row["files_opened"],
            "bytes",
            row["bytes_read"],
            "ms=%.2f" % row["elapsed_ms"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
