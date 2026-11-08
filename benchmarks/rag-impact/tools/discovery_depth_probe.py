#!/usr/bin/env python3
"""Read-only discovery-depth probe: six B0 queries, 50 results per call."""

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
OUT = RAG / "results" / "discovery-depth-probe.json"
BIN = os.environ.get("ZG_BIN", str(Path.home() / ".local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle/fixed-1.98.0-bin/zg"))
CORPUS = Path(os.environ.get("ZG_CORPUS_ROOT", str(Path.home() / ".local/share/zvec-grep/rag-impact/20261108-b0-pilot/corpus")))
MODEL_CACHE = Path.home() / ".local/share/zvec-grep/index-memory-diagnostics/laya-20261104/models"

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")
RG_LINE_RE = re.compile(r"^\s+(\d+):")
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


def query_calls(question: str) -> list[tuple[str, list[str], str]]:
    first = first_phrase(question)
    last = last_phrase(question)
    codes = code_tokens(question)
    calls = [
        ("hybrid_question", [BIN, question, "--limit", "50", "--preview", "detail"], "fts"),
        ("fts_question", [BIN, "--fts", question, "--limit", "50", "--preview", "detail"], "fts"),
    ]
    if first and first.lower() != question.lower():
        calls.append(("fts_first_phrase", [BIN, "--fts", first, "--limit", "50", "--preview", "detail"], "fts"))
    if last and last.lower() != question.lower() and last != first:
        calls.append(("rg_last_phrase", [BIN, "--rg", "-n", "-F", last], "rg"))
    if codes:
        calls.append(("fts_code_tokens", [BIN, "--fts", " ".join(codes), "--limit", "50", "--preview", "detail"], "fts"))
        rg_cmd = [BIN, "--rg", "-n"]
        for code in codes:
            rg_cmd += ["-F", "-e", code]
        calls.append(("rg_code_tokens", rg_cmd, "rg"))
    return calls[:6]


def parse_fts(text: str) -> list[dict]:
    units = []
    current = None
    for line in text.splitlines():
        match = FILE_RE.match(line)
        if match:
            current = match.group(1)
            continue
        match = RANGE_RE.match(line)
        if match and current:
            units.append({"path": current, "start_line": int(match.group(1)), "end_line": int(match.group(2))})
    return units


def parse_rg(text: str) -> list[dict]:
    units = []
    current = None
    for line in text.splitlines():
        inline = re.match(r"^(.*?):(\d+):", line)
        if inline and not line.startswith(" "):
            units.append({"path": inline.group(1), "start_line": int(inline.group(2)), "end_line": int(inline.group(2))})
            continue
        match = RG_LINE_RE.match(line)
        if match and current:
            units.append({"path": current, "start_line": int(match.group(1)), "end_line": int(match.group(1))})
            continue
        if line and not line.startswith(" ") and not line.startswith(("Context:", "Coverage:", "Files:")):
            current = line
    return units


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def unit_mask(unit: dict, gold: list[tuple[str, int, int]]) -> int:
    mask = 0
    for index, (path, start, end) in enumerate(gold):
        if unit["path"] == path and overlap(unit["start_line"], unit["end_line"], start, end):
            mask |= 1 << index
    return mask


def selectable(units: list[dict], gold: list[tuple[str, int, int]], budget: int = 10) -> int:
    reachable = {0: 0}
    for unit in units:
        mask = unit_mask(unit, gold)
        if mask == 0:
            continue
        updated = dict(reachable)
        for covered, count in reachable.items():
            if count < budget:
                combined = covered | mask
                if combined not in updated or updated[combined] > count + 1:
                    updated[combined] = count + 1
        reachable = updated
    return max((mask.bit_count() for mask, count in reachable.items() if count <= budget), default=0)


def main() -> int:
    b0 = json.loads(B0.read_text())
    dev = json.loads(DEV.read_text())
    dev_by = {case["id"]: case for case in dev["cases"]}
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(Path.home() / ".local/share/zvec-grep/rollback-20261101T130300Z")
    env["ZVEC_GREP_HOME"] = str(CORPUS.parent / "home")
    env["ZVEC_GREP_MODEL_CACHE"] = str(MODEL_CACHE)
    env["TMPDIR"] = str(CORPUS.parent / "tmp")
    env["ZVEC_GREP_MODE"] = "direct"
    rows = []
    for b0_case in b0["quality"]["dev_real_cases"]:
        case_id = b0_case["id"]
        gold = []
        for reference in dev_by[case_id].get("source_refs", []):
            for span in reference.get("spans", []):
                gold.append((reference["path"], span["start_line"], span["end_line"]))
        units = []
        calls = []
        started = time.perf_counter()
        for label, cmd, kind in query_calls(dev_by[case_id]["question"]):
            proc = subprocess.run(cmd, cwd=CORPUS, env=env, capture_output=True, text=True, timeout=120)
            parsed = parse_fts(proc.stdout) if kind == "fts" else parse_rg(proc.stdout)
            parsed = parsed[:50]
            calls.append({"call": label, "units": len(parsed)})
            units.extend(parsed)
        dedup = {}
        for unit in units:
            key = (unit["path"], unit["start_line"], unit["end_line"])
            dedup[key] = unit
        units = list(dedup.values())
        covered = 0
        for unit in units:
            covered |= unit_mask(unit, gold)
        rows.append(
            {
                "id": case_id,
                "gold_spans": len(gold),
                "units": len(units),
                "retrievable_gold": covered.bit_count(),
                "selectable_recall10_gold": selectable(units, gold),
                "calls": calls,
                "wall_ms": (time.perf_counter() - started) * 1000.0,
            }
        )
    total_gold = sum(row["gold_spans"] for row in rows)
    total_retrievable = sum(row["retrievable_gold"] for row in rows)
    total_selectable = sum(row["selectable_recall10_gold"] for row in rows)
    receipt = {
        "schema_version": 1,
        "status": "DISCOVERY_DEPTH_PROBE",
        "method": "B0 six query constructions with 50 results per call; union and optimal 10-item subset",
        "cases": rows,
        "totals": {"gold_spans": total_gold, "retrievable_gold": total_retrievable, "selectable_recall10_gold": total_selectable},
        "macro": {
            "retrievable_recall": sum(row["retrievable_gold"] / row["gold_spans"] for row in rows) / len(rows),
            "selectable_recall10": sum(row["selectable_recall10_gold"] / row["gold_spans"] for row in rows) / len(rows),
        },
        "limits": ["development split only", "read-only search; no model/index change", "gold measurement only", "no held-out"],
    }
    OUT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"totals": receipt["totals"], "macro": receipt["macro"]}, indent=2))
    for row in rows:
        print(row["id"], row["retrievable_gold"], "/", row["gold_spans"], "selectable", row["selectable_recall10_gold"], "units", row["units"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
