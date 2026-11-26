#!/usr/bin/env python3
"""Re-grade saved agent answers with normalized keyword matching.

No agent is rerun. Normalization only removes underscores/hyphens and treats
INSUFFICIENT as a conclusion when it is the first non-empty line.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CITE = re.compile(r"\[([^\[\]\s]+):(\d+)-(\d+)\]")


def norm(s: str) -> str:
    return re.sub(r"[_-]", "", s.lower())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    args = ap.parse_args()

    wt = args.worktree.resolve()
    art = args.artifacts.resolve()
    receipt = json.loads((art / "results/arm-receipt.json").read_text())
    cases = {c["id"]: c for c in json.loads((wt / "benchmarks/rust-rag/cases.json").read_text())["cases"]}
    summary = {}
    rows_out = {}

    for arm in ("baseline", "h3", "h4"):
        rows = []
        for cid, gold in cases.items():
            pack = (art / receipt["cases"][cid]["arms"][arm]["pack_path"]).read_text()
            valid_cites = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in CITE.finditer(pack)}
            answer = (art / f"results/agent/{cid}.{arm}.answer.txt").read_text().strip()
            first = next((l for l in answer.splitlines() if l.strip()), "")
            insufficient = bool(re.match(r"^\s*INSUFFICIENT\b", first, re.I))
            n = norm(answer)
            missing = [k for k in gold["grader_keywords"] if norm(k) not in n]
            cites = [(m.group(1), int(m.group(2)), int(m.group(3))) for m in CITE.finditer(answer)]
            valid = [c for c in cites if valid_cites.get(c[0]) == (c[1], c[2])]
            row = {
                "answer_path": f"results/agent/{cid}.{arm}.answer.txt",
                "insufficient": insufficient,
                "missing_keywords": missing,
                "task_correct": not missing and not insufficient,
                "citations": len(cites),
                "valid_citations": len(valid),
                "citation_valid": bool(cites) and len(valid) == len(cites),
            }
            rows.append(row)
            rows_out.setdefault(cid, {})[arm] = row
        summary[arm] = {
            "task_correct": sum(r["task_correct"] for r in rows),
            "citation_valid": sum(r["citation_valid"] for r in rows),
            "insufficient": sum(r["insufficient"] for r in rows),
            "mean_citations": round(sum(r["citations"] for r in rows) / len(rows), 2),
        }

    dest = art / "results/agent-grade.json"
    dest.write_text(json.dumps({"summary": summary, "cases": rows_out}, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    for cid in cases:
        print(cid, " ".join(f"{a}={int(rows_out[cid][a]['task_correct'])}" for a in ("baseline", "h3", "h4")))
    print("receipt", dest)


if __name__ == "__main__":
    main()
