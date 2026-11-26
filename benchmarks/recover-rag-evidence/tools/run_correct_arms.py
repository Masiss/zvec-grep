#!/usr/bin/env python3
"""Run preregistered RAG recovery arms with stage traces and optional R1 packs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ARMS = {
    "K4": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_RRF_ONLY": "1",
        "ZVEC_RAG_FRAGMENT_SELECT_RANK_ONLY": "1",
        "ZVEC_RAG_BYTE_BUDGET": "24576",
        "ZVEC_RAG_ITEM_BUDGET": "64",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "K5": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_FRAGMENT_SELECT_RANK_ONLY": "1",
        "ZVEC_RAG_BYTE_BUDGET": "24576",
        "ZVEC_RAG_ITEM_BUDGET": "64",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "K0": {},
    "K1": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "K2": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_BYTE_BUDGET": "24576",
        "ZVEC_RAG_ITEM_BUDGET": "64",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "K3": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_RRF_ONLY": "1",
        "ZVEC_RAG_BYTE_BUDGET": "24576",
        "ZVEC_RAG_ITEM_BUDGET": "64",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "G0": {},
    "G1": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "J1": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_RRF_ONLY": "1",
        "ZVEC_RAG_RANK_RRF_SUPPORT": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "I1": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_RRF_ONLY": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "H1": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_RRF_ONLY": "1",
        "ZVEC_RAG_FRAGMENT_SELECT_V2": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "G4": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_FRAGMENT_SELECT_V2": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "G5": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_FRAGMENT_SELECT_V2": "1",
        "ZVEC_RAG_RANK_LIMIT": "200",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "G2": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
    "G3": {
        "ZVEC_RAG_FRAGMENT_FLOW": "1",
        "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
        "ZVEC_RAG_RANK_LIMIT": "200",
        "ZVEC_RAG_BUDGET_SELECT": "1",
        "ZVEC_RAG_EVIDENCE_PACK_FILE": "@pack",
    },
}


def load_cases(path: Path, split: str) -> dict[str, dict]:
    data = json.loads(path.read_text())
    items = data["cases"]
    if split == "csharp":
        items = [c for c in items if c["id"] in {f"D{i:02d}" for i in range(1, 13)}]
    return {c["id"]: c for c in items}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--split", choices=("rust", "csharp"), required=True)
    ap.add_argument("--arms", default="K0,K1,K2,K3,K4,K5")
    args = ap.parse_args()

    art = args.artifacts.resolve()
    corpus = args.corpus.resolve()
    cases = load_cases(args.cases.resolve(), args.split)
    candidate_bin = art / "build" / "zg-correct"
    previous_bin = art / "zg-previous-K1"
    baseline_bin = art / "zg-baseline-K0"
    model_cache = Path("/home/masis/.cache/zvec-grep/rust-rag-20261126/model-cache")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    receipt = {"split": args.split, "arms": arms, "cases": {}, "timings": []}
    for arm in arms:
        for cid, case in cases.items():
            trace = art / "traces2" / args.split / arm / f"{cid}.jsonl"
            pack = art / "packs" / args.split / arm / f"{cid}.json"
            out = art / "outputs2" / args.split / arm / f"{cid}.txt"
            err = art / "outputs2" / args.split / arm / f"{cid}.err"
            tm = art / "outputs2" / args.split / arm / f"{cid}.time"
            for p in (trace, pack, out, err, tm):
                p.parent.mkdir(parents=True, exist_ok=True)
                if p.exists():
                    p.unlink()
            env = os.environ.copy()
            for k in list(env):
                if k.startswith("ZVEC_GREP_OBSERVABILITY"):
                    env.pop(k, None)
            env.update({
                "ZVEC_GREP_MODEL_CACHE": str(model_cache),
                "ZVEC_GREP_DEVICE": "cpu",
                "ZVEC_DISCOVERY_TRACE_FILE": str(trace),
                "ZVEC_RAG_ITEM_BUDGET": "10",
                "ZVEC_RAG_BYTE_BUDGET": str(24 * 1024),
            })
            for key, value in ARMS[arm].items():
                env[key] = str(pack) if value == "@pack" else value
            binary = baseline_bin if arm == "K0" else previous_bin if arm == "K1" else candidate_bin
            cmd = [
                "/usr/bin/time", "-f", "%e %U %S %M", "-o", str(tm),
                str(binary), case["question"], "--fuse", "--limit", "50",
                "--preview", "short", "--trace", "--mode", "direct",
                "--refresh", "off", "--color", "never",
            ]
            cp = subprocess.run(cmd, cwd=corpus, env=env, capture_output=True, text=True)
            out.write_text(cp.stdout)
            err.write_text(cp.stderr)
            parts = tm.read_text().split() if tm.exists() else []
            timing = {
                "arm": arm, "case": cid,
                "wall": float(parts[0]) if len(parts) > 3 else 0,
                "user": float(parts[1]) if len(parts) > 3 else 0,
                "sys": float(parts[2]) if len(parts) > 3 else 0,
                "rss_kb": int(parts[3]) if len(parts) > 3 else 0,
                "rc": cp.returncode,
            }
            receipt["timings"].append(timing)
            receipt["cases"].setdefault(cid, {})[arm] = {
                "trace_lines": len(trace.read_text().splitlines()) if trace.exists() else 0,
                "pack_exists": pack.exists(),
                "time": timing,
            }
            if cp.returncode != 0:
                raise SystemExit(f"failed {args.split} {arm} {cid}: {cp.stderr[:500]}")
        print(args.split, arm, "ok")
    dest = art / "results" / f"run-rag-arms-{args.split}.json"
    dest.write_text(json.dumps(receipt, indent=2) + "\n")
    print("receipt", dest)


if __name__ == "__main__":
    main()
