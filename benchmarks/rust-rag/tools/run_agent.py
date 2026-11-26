#!/usr/bin/env python3
"""Run a tool-free external agent on evidence packs and grade task correctness.

The agent sees only the question and the selected pack; gold is used only here
by the deterministic grader.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

CITE = re.compile(r"\[([^\[\]\s]+):(\d+)-(\d+)\]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--arms", default="baseline,h3,h4")
    ap.add_argument("--model", default="opencode-go/hy4-flash-free")
    args = ap.parse_args()

    wt = args.worktree.resolve()
    art = args.artifacts.resolve()
    receipt = json.loads((art / "results/arm-receipt.json").read_text())
    cases = {c["id"]: c for c in json.loads((wt / "benchmarks/rust-rag/cases.json").read_text())["cases"]}
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    out_dir = art / "results/agent"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"model": args.model, "arms": arms, "cases": {}}
    for arm in arms:
        for cid, gold in cases.items():
            pack_rel = receipt["cases"][cid]["arms"][arm]["pack_path"]
            pack_path = art / pack_rel
            pack = pack_path.read_text()
            valid_cites = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in CITE.finditer(pack)}
            prompt = (
                "Answer using ONLY the evidence below. Use exact [path:start-end] headers that appear in the evidence. "
                "Do not use prior knowledge and do not speculate. If the evidence is insufficient, say INSUFFICIENT.\n"
                f"Question: {gold['question']}\nEvidence:\n{pack}"
            )
            session_dir = art / "agent-sessions" / arm / cid
            session_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{cid}.{arm}.answer.txt"
            err_path = out_dir / f"{cid}.{arm}.err.txt"
            cmd = [
                "/home/masis/.pi/agent/bin/pi", "-p",
                "--no-tools", "--no-context-files", "--no-skills", "--no-extensions", "--no-mcp",
                "--session-dir", str(session_dir), "--model", args.model, "--thinking", "off",
                prompt,
            ]
            t0 = time.monotonic()
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
            wall = time.monotonic() - t0
            out_path.write_text(cp.stdout)
            err_path.write_text(cp.stderr)
            answer = cp.stdout.strip()
            usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0}
            for p in session_dir.glob("*.jsonl"):
                for line in p.read_text().splitlines():
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "message" and d.get("message", {}).get("role") == "assistant":
                        u = d["message"].get("usage") or {}
                        for k in usage:
                            usage[k] += int(u.get(k, 0))
            lowered = answer.lower()
            kw = {k.lower() for k in gold["grader_keywords"]}
            missing = sorted(k for k in kw if k not in lowered)
            cites = [(m.group(1), int(m.group(2)), int(m.group(3))) for m in CITE.finditer(answer)]
            valid = [c for c in cites if c[0] in valid_cites and valid_cites[c[0]] == (c[1], c[2])]
            results["cases"].setdefault(cid, {})[arm] = {
                "answer_path": str(out_path.relative_to(art)),
                "wall_s": round(wall, 2),
                "usage": usage,
                "insufficient": "insufficient" in lowered,
                "missing_keywords": missing,
                "task_correct": not missing and "insufficient" not in lowered,
                "citations": len(cites),
                "valid_citations": len(valid),
                "citation_valid": bool(cites) and len(valid) == len(cites),
                "rc": cp.returncode,
            }
            print(cid, arm, "correct", results["cases"][cid][arm]["task_correct"], "tokens", usage["totalTokens"])

    summary = {}
    for arm in arms:
        rows = [results["cases"][cid][arm] for cid in cases]
        summary[arm] = {
            "task_correct": sum(r["task_correct"] for r in rows),
            "citation_valid": sum(r["citation_valid"] for r in rows),
            "insufficient": sum(r["insufficient"] for r in rows),
            "mean_input_tokens": round(sum(r["usage"]["input"] for r in rows) / len(rows), 1),
            "mean_output_tokens": round(sum(r["usage"]["output"] for r in rows) / len(rows), 1),
            "mean_total_tokens": round(sum(r["usage"]["totalTokens"] for r in rows) / len(rows), 1),
            "mean_wall_s": round(sum(r["wall_s"] for r in rows) / len(rows), 2),
        }
    results["summary"] = summary
    dest = art / "results/agent-receipt.json"
    dest.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print("receipt", dest)


if __name__ == "__main__":
    main()
