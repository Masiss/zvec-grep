#!/usr/bin/env python3
"""Tool-free agent task-correctness check on recovered evidence packs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ARMS = ("F0", "F2", "F4")


def norm(s: str) -> str:
    return re.sub(r"[_-]", "", s.lower())


STOP = {"the", "and", "for", "with", "from", "into", "when", "that", "this", "not", "none", "not_proven"}


def fallback_keywords(case: dict) -> list[str]:
    text = " ".join([str(case.get("expected_evidence", ""))] + [str(f) for f in case.get("facts", [])])
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", text)
    seen = []
    for w in words:
        if w.lower() in STOP or w in seen:
            continue
        seen.append(w)
        if len(seen) >= 5:
            break
    return seen


def load_cases(path: Path, split: str) -> dict[str, dict]:
    data = json.loads(path.read_text())
    items = data["cases"]
    if split == "csharp":
        items = [c for c in items if c["id"] in {f"D{i:02d}" for i in range(1, 13)}]
    result = {}
    for c in items:
        c.setdefault("grader_keywords", fallback_keywords(c))
        result[c["id"]] = c
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--split", choices=("rust", "csharp"), required=True)
    ap.add_argument("--model", default="opencode-go/hy4-flash-free")
    args = ap.parse_args()

    art = args.artifacts.resolve()
    cases = load_cases(args.cases.resolve(), args.split)
    out_dir = art / "results" / "task-agent" / args.split
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"split": args.split, "model": args.model, "cases": {}}

    for arm in ARMS:
        for cid, case in cases.items():
            pack_path = art / "results" / f"pack-{args.split}-{arm}-{cid}.txt"
            pack = pack_path.read_text()
            prompt = (
                "Answer using ONLY the evidence below. Cite exact [path:start-end] headers from the evidence. "
                "If insufficient, say INSUFFICIENT on the first line. Do not speculate. "
                "Answer in at most 120 words.\n"
                f"Question: {case['question']}\nEvidence:\n{pack}"
            )
            session = art / "agent-sessions" / args.split / arm / cid
            session.mkdir(parents=True, exist_ok=True)
            ans = out_dir / f"{cid}.{arm}.txt"
            cmd = [
                "/home/masis/.pi/agent/bin/pi", "-p", "--no-tools", "--no-context-files",
                "--no-skills", "--no-extensions", "--no-mcp", "--session-dir", str(session),
                "--model", args.model, "--thinking", "off", prompt,
            ]
            if ans.exists():
                answer = ans.read_text()
                wall = 0.0
            else:
                t0 = time.monotonic()
                cp = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
                wall = time.monotonic() - t0
                ans.write_text(cp.stdout)
                answer = cp.stdout
            usage = {"input": 0, "output": 0, "cacheRead": 0, "totalTokens": 0}
            for p in session.glob("*.jsonl"):
                for line in p.read_text().splitlines():
                    d = json.loads(line)
                    if d.get("type") == "message" and d.get("message", {}).get("role") == "assistant":
                        u = d["message"].get("usage") or {}
                        for k in usage:
                            usage[k] += int(u.get(k, 0))
            answer = answer.strip()
            first = next((l for l in answer.splitlines() if l.strip()), "")
            insufficient = bool(re.match(r"^\s*INSUFFICIENT\b", first, re.I))
            missing = [k for k in case["grader_keywords"] if norm(k) not in norm(answer)]
            receipt["cases"].setdefault(cid, {})[arm] = {
                "answer": str(ans.relative_to(art)),
                "correct": not missing and not insufficient,
                "insufficient": insufficient,
                "missing": missing,
                "usage": usage,
                "wall_s": round(wall, 2),
            }
        print(args.split, arm, "done")

    receipt["summary"] = {}
    for arm in ARMS:
        rows = [receipt["cases"][c][arm] for c in cases]
        receipt["summary"][arm] = {
            "correct": sum(r["correct"] for r in rows),
            "insufficient": sum(r["insufficient"] for r in rows),
            "total_tokens": sum(r["usage"]["totalTokens"] for r in rows),
            "mean_wall_s": round(sum(r["wall_s"] for r in rows) / len(rows), 2),
        }
    dest = art / "results" / f"task-agent-fragment-final-{args.split}.json"
    dest.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt["summary"], indent=2))
    print("receipt", dest)


if __name__ == "__main__":
    main()
