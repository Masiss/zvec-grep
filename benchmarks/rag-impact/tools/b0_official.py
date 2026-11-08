#!/usr/bin/env python3
"""Official B0 measurement for the frozen RAG impact experiment.

Measures the baseline (existing search + direct reads, no RAG relations) on the
frozen corpus/cases/protocol in PREREGISTRATION.md. This is a B0 receipt, not a
candidate measurement.

Safety:
- source tree is read-only; the frozen corpus is copied to a private OUT;
- private ZVEC_GREP_HOME/TMPDIR outside the repository and source tree;
- no repository/live index is written;
- held-out per-case content is written to the receipt but not printed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
DEV_CASES = RAG_DIR / "cases" / "dev.json"
HELDOUT_CASES = RAG_DIR / "cases" / "heldout.json"
CORPUS_MANIFEST = RAG_DIR / "corpus-manifest.json"
PREREG = RAG_DIR / "PREREGISTRATION.md"
SCOPE = RAG_DIR / "scope-manifest.json"

FILE_RE = re.compile(r"^File:\s+(.+?)\s*$")
RANGE_RE = re.compile(r"^\s*Range:\s+(\d+)-(\d+)\b")
RG_INLINE_RE = re.compile(r"^(.*?):(\d+):")
RG_GROUP_RE = re.compile(r"^(\S.*?)\s*$")
RG_LINE_RE = re.compile(r"^\s+(\d+):")
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
    "con", "còn", "da", "đã", "dang", "đang", "se", "sẽ", "boi", "bởi", "theo",
    "tren", "trên", "duoi", "dưới", "giua", "giữa", "sau", "truoc", "trước", "khien", "khiến",
    "lam", "làm", "cai", "cái", "chay", "chạy",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def host_state() -> dict:
    def read(path: str) -> str:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return ""

    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "loadavg": read("/proc/loadavg"),
        "meminfo": read("/proc/meminfo"),
        "disk": subprocess.run(["df", "-B1", "/home/masis"], capture_output=True, text=True).stdout,
        "top_ps": subprocess.run(
            ["ps", "-eo", "pid,comm,%cpu,%mem,rss", "--sort=-%cpu"],
            capture_output=True,
            text=True,
        ).stdout.splitlines()[:12],
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


def query_calls(binary: str, question: str) -> list[tuple[str, list[str], str]]:
    first = first_phrase(question)
    last = last_phrase(question)
    codes = code_tokens(question)
    calls = [
        ("hybrid_question", [binary, question, "--preview", "detail"], "fts"),
        ("fts_question", [binary, "--fts", question, "--preview", "detail"], "fts"),
    ]
    if first and first.lower() != question.lower():
        calls.append(("fts_first_phrase", [binary, "--fts", first, "--preview", "detail"], "fts"))
    if last and last.lower() != question.lower() and last != first:
        calls.append(("rg_last_phrase", [binary, "--rg", "-n", "-F", last], "rg"))
    if codes:
        calls.append(("fts_code_tokens", [binary, "--fts", " ".join(codes), "--preview", "detail"], "fts"))
        rg_cmd = [binary, "--rg", "-n"]
        for code in codes:
            rg_cmd += ["-F", "-e", code]
        calls.append(("rg_code_tokens", rg_cmd, "rg"))
    return calls[:6]


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
            units.append({"path": current, "start_line": int(match.group(1)), "end_line": int(match.group(2))})
    return units


def parse_rg(text: str, corpus_root: Path) -> list[dict]:
    units: list[dict] = []
    current: str | None = None
    for line in text.splitlines():
        inline = RG_INLINE_RE.match(line)
        if inline and not line.startswith(" "):
            units.append({"path": inline.group(1), "start_line": int(inline.group(2)), "end_line": int(inline.group(2))})
            continue
        grouped = RG_LINE_RE.match(line)
        if grouped and current:
            line_no = int(grouped.group(1))
            units.append({"path": current, "start_line": line_no, "end_line": line_no})
            continue
        if line and not line.startswith(" ") and not line.startswith(("Context:", "Coverage:", "Files:")):
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


def gold_from_dev(case: dict) -> list[tuple[str, int, int]]:
    gold = []
    for ref in case.get("source_refs", []):
        for span in ref.get("spans", []):
            gold.append((ref["path"], span["start_line"], span["end_line"]))
    return gold


def gold_from_heldout(case: dict) -> list[tuple[str, int, int]]:
    gold = []
    for item in case.get("source_evidence", []):
        if "spans" in item:
            for span in item["spans"]:
                gold.append((item["path"], span["start_line"], span["end_line"]))
        else:
            gold.append((item["path"], item["start_line"], item["end_line"]))
    return gold


def pss_kib(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


class PssSampler(threading.Thread):
    def __init__(self, pid: int, interval: float = 0.1):
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.samples: list[int] = []
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            value = pss_kib(self.pid)
            if value is not None:
                self.samples.append(value)
            self._stop.wait(self.interval)

    def stop(self) -> list[int]:
        self._stop.set()
        self.join(timeout=2)
        return self.samples


def server_pid(binary: str, env: dict, cwd: Path) -> int | None:
    proc = subprocess.run([binary, "--server", "status"], cwd=cwd, env=env, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("PID:"):
            return int(line.split(":", 1)[1].strip())
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--token-counter")
    parser.add_argument("--index-runs", type=int, default=3)
    parser.add_argument("--queries", type=int, default=400)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--port", type=int, default=7999)
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).resolve()
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    dev = json.loads(DEV_CASES.read_text(encoding="utf-8"))
    heldout = json.loads(HELDOUT_CASES.read_text(encoding="utf-8"))
    if manifest["totals"]["files"] != 822:
        raise SystemExit("unexpected corpus manifest totals")
    if dev["status"] != "ALL_24_CASES_FROZEN_B0_PENDING":
        raise SystemExit(f"unexpected dev status: {dev['status']}")
    if heldout["status"] != "FROZEN_REVIEWER_AUTHORED":
        raise SystemExit(f"unexpected heldout status: {heldout['status']}")

    env = os.environ.copy()
    env.setdefault("ZVEC_GREP_MODE", "direct")
    env["TMPDIR"] = str(corpus_root.parent / "tmp")
    env["ZVEC_GREP_HOME"] = str(corpus_root.parent / "home")
    token_counter = Path(args.token_counter).resolve() if args.token_counter else None
    if token_counter and not token_counter.is_file():
        raise SystemExit(f"token counter missing: {token_counter}")

    def count_tokens(text: str) -> int | None:
        if not token_counter:
            return None
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=env["TMPDIR"], delete=False) as fh:
            fh.write(text)
            path = fh.name
        try:
            proc = subprocess.run(
                [str(token_counter), str(args.tokenizer_json), path],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode == 0:
                return json.loads(proc.stdout)["tokens"]
        finally:
            os.unlink(path)
        return None

    # Set tokenizer path only when a counter is supplied.
    if token_counter:
        candidates = [
            Path.home()
            / ".local/share/zvec-grep/index-memory-diagnostics/laya-20261104/models/model2vec/minishlab--potion-code-16M-v2"
        ]
        tokenizer_json = None
        for root in candidates:
            matches = list(root.glob("*/tokenizer/tokenizer.json"))
            if matches:
                tokenizer_json = matches[0]
                break
        if tokenizer_json is None:
            raise SystemExit("tokenizer.json not found")
        args.tokenizer_json = str(tokenizer_json)

    def run_quality(case: dict, gold: list[tuple[str, int, int]], include_units: bool) -> dict:
        calls = query_calls(args.binary, case["question"])
        call_results = []
        best = -1.0
        best_call = None
        all_units: list[dict] = []
        context_bytes = 0
        context_tokens = 0
        for label, cmd, kind in calls:
            started = time.perf_counter()
            proc = subprocess.run(cmd, cwd=corpus_root, env=env, capture_output=True, text=True, timeout=args.timeout)
            elapsed = time.perf_counter() - started
            if proc.returncode != 0:
                raise RuntimeError(f"quality call failed: {label}: {proc.stderr[-300:]}")
            context_bytes += len(proc.stdout.encode("utf-8"))
            if token_counter:
                context_tokens += count_tokens(proc.stdout) or 0
            units = (parse_fts(proc.stdout, corpus_root) if kind == "fts" else parse_rg(proc.stdout, corpus_root))[:10]
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
                    "matched_gold_spans": matched,
                    "gold_spans": len(gold),
                    "recall": recall,
                    "wall_s": round(elapsed, 4),
                    "units": units if include_units else None,
                }
            )
            if recall is not None and recall > best:
                best = recall
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
            data = target.read_bytes()
            read_bytes += len(data)
            if token_counter:
                context_tokens += count_tokens(data.decode("utf-8", errors="replace")) or 0
        return {
            "id": case["id"],
            "question_sha256": sha256_text(case["question"]),
            "calls": call_results,
            "best_call": best_call,
            "recall": best if best >= 0 else None,
            "additional_source_reads": len(read_paths),
            "additional_context_bytes": context_bytes + read_bytes,
            "additional_context_tokens": context_tokens if token_counter else None,
            "citation_units_valid": sum(
                1
                for unit in all_units
                if (corpus_root / unit["path"]).is_file()
                and 1 <= unit["start_line"] <= unit["end_line"]
            ),
            "citation_units_total": len(all_units),
        }

    before = host_state()
    # Quality: development real-source cases. Synthetic cases are separate.
    dev_real = [c for c in dev["cases"] if not c["id"].startswith(("D13", "D14", "D15", "D16"))]
    dev_quality = [run_quality(c, gold_from_dev(c), include_units=True) for c in dev_real]
    heldout_quality = [run_quality(c, gold_from_heldout(c), include_units=False) for c in heldout["cases"]]
    dev_recalls = [c["recall"] for c in dev_quality if c["recall"] is not None]
    heldout_recalls = [c["recall"] for c in heldout_quality if c["recall"] is not None]

    # Indexing overhead: cold index runs in fresh private copies.
    index_runs = []
    index_base = corpus_root.parent / "index-runs"
    for i in range(1, args.index_runs + 1):
        run_root = index_base / f"run{i}"
        if run_root.exists():
            shutil.rmtree(run_root)
        shutil.copytree(corpus_root, run_root, ignore=shutil.ignore_patterns(".zvec-grep"))
        run_env = env.copy()
        run_env["ZVEC_GREP_HOME"] = str(index_base / f"home{i}")
        run_env["TMPDIR"] = str(index_base / f"tmp{i}")
        Path(run_env["ZVEC_GREP_HOME"]).mkdir(parents=True, exist_ok=True)
        Path(run_env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
        time_file = index_base / f"time{i}.txt"
        cmd = [
            "/usr/bin/time", "-v", "-o", str(time_file), args.binary, "--index", ".",
            "--embedding", "local/potion-code-16m-v2",
            "--model-cache", str(Path(env["ZVEC_GREP_MODEL_CACHE"])),
            "--device", "cpu", "--embedding-concurrency", "2",
        ]
        proc = subprocess.run(cmd, cwd=run_root, env=run_env, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"index run {i} failed: {proc.stderr[-500:]}")
        time_text = time_file.read_text()
        wall = re.search(r"Elapsed \(wall clock\).*?:\s*([0-9:.]+)", time_text)
        rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", time_text)
        added = re.search(r"files\t(\d+) scanned, (\d+) added", proc.stdout)
        index_runs.append(
            {
                "run": i,
                "wall_s": wall.group(1) if wall else None,
                "max_rss_kib": int(rss.group(1)) if rss else None,
                "scanned": int(added.group(1)) if added else None,
                "added": int(added.group(2)) if added else None,
            }
        )
    retrieval_root = index_base / "run1"
    retrieval_env = env.copy()
    retrieval_env["ZVEC_GREP_HOME"] = str(index_base / "home1")
    retrieval_env["TMPDIR"] = str(index_base / "tmp1")

    # Server for warm latency/concurrency/PSS.
    retrieval_env["ZVEC_GREP_SERVER_URL"] = f"http://127.0.0.1:{args.port}"
    retrieval_env["ZVEC_GREP_MODE"] = "server"
    server_started = False
    pid = None
    try:
        subprocess.run(
            [args.binary, "--server", "on", "--listen", f"127.0.0.1:{args.port}"],
            cwd=retrieval_root,
            env=retrieval_env,
            check=True,
            capture_output=True,
            text=True,
        )
        server_started = True
        time.sleep(1)
        pid = server_pid(args.binary, retrieval_env, retrieval_root)

        # Deterministic 400-query workload from the development six-call policy.
        base_calls = []
        for case in dev_real:
            for label, cmd, _kind in query_calls(args.binary, case["question"]):
                base_calls.append((case["id"], label, cmd))
        workload = [base_calls[i % len(base_calls)] for i in range(args.queries)]
        for _ in range(20):
            case_id, label, cmd = base_calls[0]
            subprocess.run(cmd + ["--mode", "server"], cwd=retrieval_root, env=retrieval_env, capture_output=True, text=True, timeout=args.timeout)

        idle_samples = []
        if pid:
            time.sleep(60)
            for _ in range(3):
                value = pss_kib(pid)
                if value is not None:
                    idle_samples.append(value)
                time.sleep(10)
        sampler = PssSampler(pid) if pid else None
        if sampler:
            sampler.start()
        latencies = []
        failures = []
        busy = 0
        for case_id, label, cmd in workload:
            started = time.perf_counter()
            proc = subprocess.run(cmd + ["--mode", "server"], cwd=retrieval_root, env=retrieval_env, capture_output=True, text=True, timeout=args.timeout)
            latencies.append(time.perf_counter() - started)
            if proc.returncode != 0:
                failures.append({"case": case_id, "call": label, "rc": proc.returncode})
            if "RESOURCE_BUSY" in proc.stdout or "RESOURCE_BUSY" in proc.stderr:
                busy += 1
        peak_samples = sampler.stop() if sampler else []

        # Concurrency: 4 readers x 100 queries each.
        concurrency_failures = []
        concurrency_busy = 0
        def one_query(item):
            case_id, label, cmd = item
            proc = subprocess.run(cmd + ["--mode", "server"], cwd=retrieval_root, env=retrieval_env, capture_output=True, text=True, timeout=args.timeout)
            return case_id, label, proc.returncode, ("RESOURCE_BUSY" in proc.stdout or "RESOURCE_BUSY" in proc.stderr)

        concurrent_work = [workload[i % len(workload)] for i in range(400)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for case_id, label, rc, is_busy in pool.map(one_query, concurrent_work):
                if rc != 0:
                    concurrency_failures.append({"case": case_id, "call": label, "rc": rc})
                if is_busy:
                    concurrency_busy += 1

        def percentile(values, p):
            if not values:
                return None
            ordered = sorted(values)
            idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
            return ordered[idx]

        latency_summary = {
            "count": len(latencies),
            "p50_s": percentile(latencies, 50),
            "p95_s": percentile(latencies, 95),
            "p99_s": percentile(latencies, 99),
            "max_s": max(latencies) if latencies else None,
            "failures": len(failures),
            "resource_busy": busy,
        }
        concurrency_summary = {
            "queries": len(concurrent_work),
            "readers": 4,
            "resource_busy": concurrency_busy,
            "failures": len(concurrency_failures),
        }
        memory_summary = {
            "idle_pss_kib_samples": idle_samples,
            "idle_pss_kib_median": statistics.median(idle_samples) if idle_samples else None,
            "peak_pss_kib": max(peak_samples) if peak_samples else None,
            "peak_samples": len(peak_samples),
        }
    finally:
        if server_started:
            subprocess.run(
                [args.binary, "--server", "off"],
                cwd=retrieval_root,
                env=retrieval_env,
                capture_output=True,
                text=True,
            )

    after = host_state()
    receipt = {
        "schema_version": 1,
        "status": "B0_FROZEN" if token_counter else "B0_PARTIAL_NO_TOKENIZER",
        "purpose": "Official B0 baseline for the frozen RAG impact experiment",
        "artifacts": {
            "pre_registration_sha256": sha256_file(PREREG),
            "scope_manifest_sha256": sha256_file(SCOPE),
            "corpus_manifest_sha256": sha256_file(CORPUS_MANIFEST),
            "dev_cases_sha256": sha256_file(DEV_CASES),
            "heldout_cases_sha256": sha256_file(HELDOUT_CASES),
            "binary_sha256": sha256_file(Path(args.binary)),
            "token_counter_sha256": sha256_file(token_counter) if token_counter else None,
            "tokenizer_sha256": sha256_file(Path(args.tokenizer_json)) if token_counter else None,
        },
        "quality": {
            "dev_real_cases": dev_quality,
            "dev_mean_recall": (sum(dev_recalls) / len(dev_recalls)) if dev_recalls else None,
            "heldout_cases": heldout_quality,
            "heldout_mean_recall": (sum(heldout_recalls) / len(heldout_recalls)) if heldout_recalls else None,
            "synthetic_d13_d16": "NOT_APPLICABLE_RETRIEVAL_PENDING_R2",
            "task_correctness_proxy": {
                "definition": "fraction of cases with all gold spans matched and no forbidden claim emitted",
                "dev": (sum(1 for c in dev_quality if c["recall"] == 1.0) / len(dev_quality)) if dev_quality else None,
                "heldout": (sum(1 for c in heldout_quality if c["recall"] == 1.0) / len(heldout_quality)) if heldout_quality else None,
            },
        },
        "indexing": {
            "runs": index_runs,
            "median_wall_s": statistics.median([r["wall_s"] for r in index_runs if r["wall_s"]]) if index_runs else None,
        },
        "latency": latency_summary,
        "concurrency": concurrency_summary,
        "memory": memory_summary,
        "host_before": before,
        "host_after": after,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "dev_mean_recall": receipt["quality"]["dev_mean_recall"],
                "heldout_mean_recall": receipt["quality"]["heldout_mean_recall"],
                "p95_s": latency_summary["p95_s"],
                "idle_pss_kib_median": memory_summary["idle_pss_kib_median"],
                "peak_pss_kib": memory_summary["peak_pss_kib"],
                "resource_busy": concurrency_busy,
                "concurrency_failures": len(concurrency_failures),
                "index_median_wall_s": receipt["indexing"]["median_wall_s"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
