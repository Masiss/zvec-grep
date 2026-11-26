#!/usr/bin/env python3
"""Run instrumented zg searches and capture per-stage JSONL lineage."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def cases_from(path: Path, split: str) -> dict[str, dict]:
    d = json.loads(path.read_text())
    items = d["cases"]
    if split == "csharp":
        items = [c for c in items if c["id"].startswith("D")]
    return {c["id"]: c for c in items}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--split", choices=("rust", "csharp"), required=True)
    ap.add_argument("--limits", default="10,25,50,100")
    ap.add_argument("--model-cache", required=True, type=Path)
    args = ap.parse_args()

    binary = args.binary.resolve()
    corpus = args.corpus.resolve()
    art = args.artifacts.resolve()
    cases = cases_from(args.cases.resolve(), args.split)
    limits = [int(x) for x in args.limits.split(",") if x.strip()]
    trace_root = art / "traces" / args.split
    out_root = art / "outputs" / args.split
    trace_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for k in list(env):
        if k.startswith("ZVEC_GREP_OBSERVABILITY"):
            env.pop(k, None)
    env["ZVEC_GREP_MODEL_CACHE"] = str(args.model_cache.resolve())
    env["ZVEC_GREP_DEVICE"] = "cpu"

    receipt = {"split": args.split, "binary": str(binary), "limits": limits, "cases": {}, "timings": []}
    for cid, case in cases.items():
        row = {}
        for limit in limits:
            trace = trace_root / f"{cid}.{limit}.jsonl"
            out = out_root / f"{cid}.{limit}.txt"
            err = out_root / f"{cid}.{limit}.err"
            tm = out_root / f"{cid}.{limit}.time"
            if trace.exists():
                trace.unlink()
            env["ZVEC_DISCOVERY_TRACE_FILE"] = str(trace)
            cmd = [
                "/usr/bin/time", "-f", "%e %U %S %M", "-o", str(tm),
                str(binary), case["question"], "--fuse", "--limit", str(limit),
                "--preview", "short", "--trace", "--mode", "direct",
                "--refresh", "off", "--color", "never",
            ]
            t0 = time.monotonic()
            cp = subprocess.run(cmd, cwd=corpus, env=env, capture_output=True, text=True)
            wall_py = time.monotonic() - t0
            out.write_text(cp.stdout)
            err.write_text(cp.stderr)
            parts = tm.read_text().split() if tm.exists() else []
            timing = {
                "case": cid, "limit": limit,
                "wall": float(parts[0]) if len(parts) > 3 else wall_py,
                "user": float(parts[1]) if len(parts) > 3 else 0.0,
                "sys": float(parts[2]) if len(parts) > 3 else 0.0,
                "rss_kb": int(parts[3]) if len(parts) > 3 else 0,
                "rc": cp.returncode,
            }
            receipt["timings"].append(timing)
            row[str(limit)] = {
                "trace_lines": len(trace.read_text().splitlines()) if trace.exists() else 0,
                "time": timing,
            }
            if cp.returncode != 0:
                raise SystemExit(f"search failed {cid} {limit}: {cp.stderr[:500]}")
        receipt["cases"][cid] = row
        print(cid, " ".join(f"L{k}={v['trace_lines']}" for k, v in row.items()))

    dest = art / f"results/run-{args.split}.json"
    dest.write_text(json.dumps(receipt, indent=2) + "\n")
    print("receipt", dest)


if __name__ == "__main__":
    main()
