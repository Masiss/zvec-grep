#!/usr/bin/env python3
"""RAG Discovery requalification on the merged Foundation (Index v3).

This is a new, bounded experiment. It does not overwrite any frozen B0/H1-H7
receipt. It re-runs the frozen six-call B0 query policy and explicit
FTS/Vector/Hybrid routes on the frozen 822-file corpus and 12 real development
cases.

Measurement contract (RAG path; E6 does not automatically cover this path):
- binary/probe SHA-256, corpus tree hash, dataset and pre-registration hashes;
- index compatibility/version/model/device and zero failed/pending files;
- explicit engine routes with `query=None` and `auto_update=false`;
- actual `diagnostics.index.routes` must equal the requested routes, with one
  group and no duplicates;
- managed rg is recorded separately as an exhaustive lexical call.

Usage:
  requalify_20261115.py --root INDEXED_CORPUS --binary ZG --probe PROBE \
      --out RECEIPT --native-lib-dir DIR --home DIR --tmp DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
DEV = RAG / "cases" / "dev.json"
CORPUS_MANIFEST = RAG / "corpus-manifest.json"
PREREG = RAG / "PREREGISTRATION.md"
DEV_BY_ID = {}

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")
RG_INLINE_RE = re.compile(r"^(.*?):(\d+):")
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def policy_calls(question: str) -> list[dict]:
    first = first_phrase(question)
    last = last_phrase(question)
    codes = code_tokens(question)
    calls = [
        {"call": "hybrid_question", "mode": "hybrid", "query": question},
        {"call": "fts_question", "mode": "fts", "query": question},
    ]
    if first and first.lower() != question.lower():
        calls.append({"call": "fts_first_phrase", "mode": "fts", "query": first})
    if last and last.lower() != question.lower() and last != first:
        calls.append({"call": "rg_last_phrase", "mode": "rg", "query": last})
    if codes:
        calls.append({"call": "fts_code_tokens", "mode": "fts", "query": " ".join(codes)})
        calls.append(
            {
                "call": "rg_code_tokens",
                "mode": "rg",
                "query": " ".join(codes),
                "patterns": codes,
            }
        )
    return calls[:6]


def parse_rg(text: str, corpus_root: Path) -> list[dict]:
    units = []
    current = None
    for line in text.splitlines():
        inline = RG_INLINE_RE.match(line)
        if inline and not line.startswith(" "):
            units.append(
                {
                    "path": inline.group(1),
                    "start_line": int(inline.group(2)),
                    "last_line": int(inline.group(2)),
                }
            )
            continue
        grouped = RG_LINE_RE.match(line)
        if grouped and current:
            line_no = int(grouped.group(1))
            units.append({"path": current, "start_line": line_no, "last_line": line_no})
            continue
        if line and not line.startswith(" ") and not line.startswith(
            ("Context:", "Coverage:", "Files:")
        ):
            current = line
    for unit in units:
        path = Path(unit["path"])
        if path.is_absolute():
            try:
                unit["path"] = str(path.relative_to(corpus_root))
            except ValueError:
                pass
    return units


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def gold_for(case: dict) -> list[tuple[str, int, int]]:
    gold = []
    for ref in case.get("source_refs", []):
        for span in ref.get("spans", []):
            gold.append((ref["path"], span["start_line"], span["end_line"]))
    return gold


def span_coverage(units: list[dict], gold: list[tuple[str, int, int]]) -> list[tuple[float, float, int]]:
    """Best (gold coverage, unit precision, unit lines) per gold span."""
    out = []
    for path, start, end in gold:
        best = (0.0, 0.0, 0)
        for u in units:
            if u.get("path") != path or u.get("start_line") is None or u.get("last_line") is None:
                continue
            if not overlap(u["start_line"], u["last_line"], start, end):
                continue
            inter = min(u["last_line"], end) - max(u["start_line"], start) + 1
            gold_len = end - start + 1
            unit_len = u["last_line"] - u["start_line"] + 1
            coverage = inter / gold_len
            precision = inter / unit_len if unit_len else 0.0
            if (coverage, precision) > (best[0], best[1]):
                best = (coverage, precision, unit_len)
        out.append(best)
    return out


def strict_hits(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    """A hit must mostly cover the gold span and not be a huge container."""
    hit = set()
    for index, (coverage, _precision, unit_len) in enumerate(span_coverage(units, gold)):
        gold_len = gold[index][2] - gold[index][1] + 1
        if coverage >= 0.5 and unit_len <= max(100, 3 * gold_len):
            hit.add(index)
    return hit


def matched_spans(units: list[dict], gold: list[tuple[str, int, int]]) -> set[int]:
    hit = set()
    for index, (path, start, end) in enumerate(gold):
        if any(
            u["path"] == path
            and u.get("start_line") is not None
            and u.get("last_line") is not None
            and overlap(u["start_line"], u["last_line"], start, end)
            for u in units
        ):
            hit.add(index)
    return hit


def selectable_ceiling(units: list[dict], gold: list[tuple[str, int, int]], budget: int = 10) -> int:
    """Exact max gold covered by <= budget units (gold masks <= 2^6)."""
    masks = []
    for u in units:
        mask = 0
        if u.get("start_line") is None or u.get("last_line") is None:
            continue
        for index, (path, start, end) in enumerate(gold):
            if u["path"] == path and overlap(u["start_line"], u["last_line"], start, end):
                mask |= 1 << index
        if mask:
            masks.append(mask)
    full = 1 << len(gold)
    best = [budget + 1] * full
    best[0] = 0
    for mask in masks:
        for state in range(full):
            if best[state] + 1 < best[state | mask]:
                best[state | mask] = best[state] + 1
    return max((bin(state).count("1") for state, cost in enumerate(best) if cost <= budget), default=0)


def run(cmd, cwd, env, timeout=120):
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return proc, (time.perf_counter() - started) * 1000.0


def verify_corpus(root: Path, manifest: dict) -> tuple[list[str], str, str]:
    failures = []
    tree = hashlib.sha256()
    expected = hashlib.sha256()
    for item in sorted(manifest["files"], key=lambda f: f["path"]):
        expected.update(item["path"].encode())
        expected.update(b"\0")
        expected.update(bytes.fromhex(item["sha256"]))
        path = root / item["path"]
        if not path.is_file():
            failures.append(f"missing corpus file {item['path']}")
            continue
        digest = sha256_file(path)
        if digest != item["sha256"]:
            failures.append(f"corpus hash mismatch {item['path']}")
        tree.update(item["path"].encode())
        tree.update(b"\0")
        tree.update(bytes.fromhex(digest))
    return failures, tree.hexdigest(), expected.hexdigest()


def contract_check(row: dict) -> list[str]:
    failures = []
    expected = [(r["mode"], r["query"]) for r in row["routes"]]
    index = row.get("index") or {}
    actual = [(r.get("mode"), r.get("query")) for r in index.get("routes", [])]
    if actual != expected:
        failures.append(f"{row['id']}: routes {actual!r} != requested {expected!r}")
    if len(index.get("query_groups", [])) != 1:
        failures.append(f"{row['id']}: expected one query group")
    if len(set(actual)) != len(actual):
        failures.append(f"{row['id']}: duplicate executed routes")
    if row["requested_mode"] == "fts" and any(mode != "fts" for mode, _ in actual):
        failures.append(f"{row['id']}: FTS request executed a non-FTS route")
    if row["requested_mode"] == "vector" and any(mode != "vector" for mode, _ in actual):
        failures.append(f"{row['id']}: Vector request executed a non-vector route")
    if row["requested_mode"] == "hybrid" and not row.get("fuse"):
        failures.append(f"{row['id']}: hybrid request is not fused")
    for item in row["items"]:
        start, last = item.get("start_line"), item.get("last_line")
        if start is not None and last is not None and start > last:
            failures.append(f"{row['id']}: inverted range {start}-{last}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--native-lib-dir", required=True, type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--tmp", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    corpus_manifest = json.loads(CORPUS_MANIFEST.read_text())
    dev = json.loads(DEV.read_text())
    cases = [c for c in dev["cases"] if c["id"] in {f"D{i:02d}" for i in range(1, 13)}]
    for case in cases:
        DEV_BY_ID[case["id"]] = case

    work = Path(tempfile.mkdtemp(prefix="rag-requal-", dir=args.tmp))
    home = args.home or work / "home"
    tmp = args.tmp or work / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ZVEC_GREP_HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    env["ZVEC_GREP_MODE"] = "direct"
    env["LD_LIBRARY_PATH"] = str(args.native_lib_dir)
    env.pop("ZVEC_GREP_DEVICE", None)

    queries = []
    for case in cases:
        for call in policy_calls(case["question"]):
            if call["mode"] != "rg":
                queries.append(
                    {
                        "id": case["id"],
                        "call": call["call"],
                        "mode": call["mode"],
                        "query": call["query"],
                    }
                )
        # Explicit vector route for route-level parity (not in the B0 policy).
        queries.append(
            {"id": case["id"], "call": "vector_question", "mode": "vector", "query": case["question"]}
        )
    query_file = work / "queries.json"
    query_file.write_text(json.dumps({"queries": queries}, ensure_ascii=False, indent=1))
    probe_out = work / "probe.json"
    probe_cmd = [
        str(args.probe),
        "--root",
        str(args.root),
        "--input",
        str(query_file),
        "--out",
        str(probe_out),
        "--limit",
        str(args.limit),
    ]
    probe_proc, probe_wall = run(probe_cmd, args.root, env, timeout=1800)
    if probe_proc.returncode != 0:
        print(probe_proc.stderr, file=sys.stderr)
        return 2
    raw_probe = probe_out.read_bytes()
    raw_probe_path = args.out.parent / "raw-probe.json"
    raw_probe_path.parent.mkdir(parents=True, exist_ok=True)
    raw_probe_path.write_bytes(raw_probe)
    probe = json.loads(raw_probe)
    by_key = {(row["id"], row["call"]): row for row in probe["queries"]}

    failures, corpus_tree, expected_tree = verify_corpus(args.root, corpus_manifest)
    if corpus_tree != expected_tree:
        failures.append(f"corpus tree {corpus_tree} != frozen manifest {expected_tree}")
    info = probe.get("info", {})
    compatibility = info.get("compatibility")
    if not isinstance(compatibility, dict) or compatibility.get("version") != 3:
        failures.append(f"index compatibility is not v3: {compatibility!r}")
    status = info.get("status") or {}
    for field in ("files_failed", "files_pending", "files_added", "files_modified", "files_deleted"):
        if status.get(field, 0) != 0:
            failures.append(f"index status {field}={status.get(field)}")
    if status.get("files_indexed", 0) < 800:
        failures.append(f"index has only {status.get('files_indexed')} files")

    rows = []
    rg_rows = []
    for case in cases:
        calls = policy_calls(case["question"])
        case_rows = []
        for call in calls:
            if call["mode"] == "rg":
                if "patterns" in call:
                    cmd = [str(args.binary), "--rg", "-n"]
                    for pattern in call["patterns"]:
                        cmd += ["-F", "-e", pattern]
                else:
                    cmd = [str(args.binary), "--rg", "-n", "-F", call["query"]]
                proc, wall_ms = run(cmd, args.root, env, timeout=300)
                units = parse_rg(proc.stdout, args.root)
                row = {
                    "id": case["id"],
                    "call": call["call"],
                    "mode": "rg",
                    "query": call["query"],
                    "exit": proc.returncode,
                    "wall_ms": wall_ms,
                    "units": units,
                }
                if proc.returncode != 0:
                    failures.append(f"{case['id']}:{call['call']} rg exit {proc.returncode}")
                rg_rows.append(row)
                case_rows.append(row)
            else:
                row = by_key[(case["id"], call["call"])]
                failures.extend(contract_check(row))
                row = dict(row)
                row["mode"] = row["requested_mode"]
                row["units"] = row.pop("items")
                case_rows.append(row)
        vector_row = by_key[(case["id"], "vector_question")]
        failures.extend(contract_check(vector_row))
        vector_row = dict(vector_row)
        vector_row["mode"] = vector_row["requested_mode"]
        vector_row["units"] = vector_row.pop("items")
        case_rows.append(vector_row)

        gold = gold_for(case)
        for row in case_rows:
            top10 = row["units"][:10]
            hit10 = matched_spans(top10, gold)
            hit_all = matched_spans(row["units"], gold)
            strict10 = strict_hits(top10, gold)
            row["gold_spans"] = len(gold)
            row["matched"] = len(hit10)
            row["recall"] = len(hit10) / len(gold) if gold else None
            row["strict_matched"] = len(strict10)
            row["strict_recall"] = len(strict10) / len(gold) if gold else None
            row["union_matched"] = len(hit_all)
            row["union_recall"] = len(hit_all) / len(gold) if gold else None
            row["top10_lines"] = sum(
                (u.get("line_count") or 0) for u in top10
            )
            row["top10_chars"] = sum((u.get("content_chars") or 0) for u in top10)
            row["all_lines"] = sum((u.get("line_count") or 0) for u in row["units"])
        union = []
        seen = set()
        for row in case_rows:
            for unit in row["units"]:
                key = (unit["path"], unit.get("start_line"), unit.get("last_line"))
                if key not in seen:
                    seen.add(key)
                    union.append(unit)
        union_hit = matched_spans(union, gold)
        discovery_miss = []
        range_miss = []
        for index, (path, start, end) in enumerate(gold):
            if index in union_hit:
                continue
            same_file = any(u["path"] == path for u in union)
            (range_miss if same_file else discovery_miss).append([path, start, end])
        best = max(case_rows, key=lambda r: (r["recall"] or 0.0, r["call"]))
        fixed = next((r for r in case_rows if r["call"] == "fts_code_tokens"), None)
        rows.append(
            {
                "id": case["id"],
                "question_sha256": sha256_text(case["question"]),
                "gold_spans": len(gold),
                "calls": [
                    {
                        "call": r["call"],
                        "mode": r["mode"],
                        "query": r["query"],
                        "wall_ms": r["wall_ms"],
                        "units": len(r["units"]),
                        "matched_spans": r["matched"],
                        "recall": r["recall"],
                        "strict_matched_spans": r["strict_matched"],
                        "strict_recall": r["strict_recall"],
                        "union_matched_spans": r["union_matched"],
                        "top10_lines": r["top10_lines"],
                        "top10_chars": r["top10_chars"],
                        "all_lines": r["all_lines"],
                    }
                    for r in case_rows
                ],
                "best_call": best["call"],
                "best_recall": best["recall"],
                "best_strict_recall": best["strict_recall"],
                "fixed_fts_code_tokens_recall": fixed["recall"] if fixed else 0.0,
                "fixed_fts_code_tokens_strict": fixed["strict_recall"] if fixed else 0.0,
                "union_retrievable": len(union_hit),
                "selectable_at_10": selectable_ceiling(union, gold, 10),
                "discovery_miss": discovery_miss,
                "range_miss": range_miss,
            }
        )

    def mean(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else None

    def macro(key):
        return mean([row[key] for row in rows])

    route_rows = {}
    for name, mode in [("fts_question", "fts"), ("vector_question", "vector"), ("hybrid_question", "hybrid")]:
        route_rows[name] = mean(
            [
                next(r["recall"] for r in case["calls"] if r["call"] == name)
                for case in rows
                if any(r["call"] == name for r in case["calls"])
            ]
        )

    binary_version = run([str(args.binary), "--version"], args.root, env)[0].stdout.strip()
    receipt = {
        "schema": "rag-requal-receipt/v1",
        "status": "PASS" if not failures else "FAIL",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity": {
            "binary": str(args.binary),
            "binary_sha256": sha256_file(args.binary),
            "binary_version": binary_version,
            "probe": str(args.probe),
            "probe_sha256": sha256_file(args.probe),
            "index_root": str(args.root),
            "index_manifest_sha256": sha256_file(args.root / ".zvec-grep" / "manifest.json"),
            "corpus_manifest_sha256": sha256_file(CORPUS_MANIFEST),
            "dev_cases_sha256": sha256_file(DEV),
            "preregistration_sha256": sha256_file(PREREG),
            "query_list_sha256": sha256_file(query_file),
            "raw_probe": str(raw_probe_path),
            "raw_probe_sha256": sha256_file(raw_probe_path),
            "corpus_files": len(corpus_manifest["files"]),
            "corpus_bytes": sum(f["bytes"] for f in corpus_manifest["files"]),
            "corpus_tree_sha256": corpus_tree,
            "corpus_tree_expected": expected_tree,
            "limit": args.limit,
            "routes": "explicit; engine query=None; auto_update=false; rg exhaustive",
        },
        "index": {
            "compatibility": compatibility,
            "status": status,
            "workspace_index": info.get("workspace_index"),
        },
        "contract_failures": failures,
        "route_macro_recall_at_10": route_rows,
        "policy": {
            "dev_cases": len(rows),
            "b0_oracle_best_call": macro("best_recall"),
            "b0_oracle_best_call_strict": macro("best_strict_recall"),
            "fixed_fts_code_tokens": macro("fixed_fts_code_tokens_recall"),
            "fixed_fts_code_tokens_strict": macro("fixed_fts_code_tokens_strict"),
            "union_retrievable_macro": mean([r["union_retrievable"] / r["gold_spans"] for r in rows]),
            "selectable_at_10_macro": mean([r["selectable_at_10"] / r["gold_spans"] for r in rows]),
            "discovery_miss_spans": sum(len(r["discovery_miss"]) for r in rows),
            "range_miss_spans": sum(len(r["range_miss"]) for r in rows),
            "top10_lines_mean": mean([c["top10_lines"] for r in rows for c in r["calls"]]),
            "top10_chars_mean": mean([c["top10_chars"] for r in rows for c in r["calls"]]),
        },
        "rows": rows,
        "rg_calls": rg_rows,
        "probe_wall_ms": probe_wall,
        "probe_peak_rss_kib": probe.get("peak_rss_kib"),
        "limits": [
            "dev split only; held-out not inspected",
            "single run; no variance study",
            "model2vec CPU index; no server-mode latency claim",
            "rg units are line hits, not ranked ranges",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: receipt[k] for k in ("status", "policy", "route_macro_recall_at_10")}, indent=1))
    for row in rows:
        print(
            row["id"],
            "gold",
            row["gold_spans"],
            "union",
            row["union_retrievable"],
            "selectable10",
            row["selectable_at_10"],
            "best",
            row["best_recall"],
            "strict",
            row["best_strict_recall"],
            "fixed",
            row["fixed_fts_code_tokens_recall"],
            "disc",
            len(row["discovery_miss"]),
            "range",
            len(row["range_miss"]),
        )
    return 0 if not failures else 3


if __name__ == "__main__":
    raise SystemExit(main())
