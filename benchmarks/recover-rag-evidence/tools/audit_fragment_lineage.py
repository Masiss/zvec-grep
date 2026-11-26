#!/usr/bin/env python3
"""Audit fragment lineage contracts from real CLI stage traces."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--split", choices=("rust", "csharp"), required=True)
    args = ap.parse_args()

    art = args.artifacts.resolve()
    cases = json.loads(args.cases.read_text())["cases"]
    if args.split == "csharp":
        cases = [c for c in cases if c["id"] in {f"D{i:02d}" for i in range(1, 13)}]
    cases = cases[:2]
    out = []
    for case in cases:
        trace = art / "lineage" / args.split / f"{case['id']}.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.unlink(missing_ok=True)
        (trace.parent / f"{case['id']}.pack.json").unlink(missing_ok=True)
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("ZVEC_GREP_OBSERVABILITY"):
                env.pop(key, None)
        env.update({
            "ZVEC_GREP_MODEL_CACHE": "/home/masis/.cache/zvec-grep/rust-rag-20261126/model-cache",
            "ZVEC_GREP_DEVICE": "cpu",
            "ZVEC_DISCOVERY_TRACE_FILE": str(trace),
            "ZVEC_RAG_FRAGMENT_FLOW": "1",
            "ZVEC_RAG_FRAGMENT_RELEVANCE": "1",
            "ZVEC_RAG_BUDGET_SELECT": "1",
            "ZVEC_RAG_EVIDENCE_PACK_FILE": str(art / "lineage" / args.split / f"{case['id']}.pack.json"),
        })
        subprocess.run([
            str(args.binary.resolve()), case["question"], "--fuse", "--limit", "50",
            "--preview", "short", "--trace", "--mode", "direct", "--refresh", "off",
            "--color", "never",
        ], cwd=str(args.corpus.resolve()), env=env, capture_output=True, text=True, check=True)
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        raw = {}
        for row in rows:
            if row.get("stage") != "raw":
                continue
            key = (row.get("fragment_id"), row.get("route"))
            raw[key] = (row.get("rank"), row.get("score"))
        fused = [r for r in rows if r.get("stage") == "fragment_fused"]
        checks = 0
        for row in fused:
            for recall in row.get("recall", []):
                key = (row.get("fragment_id"), recall.get("route_id"))
                expected = raw.get(key)
                assert expected is not None, f"missing raw lineage for {key}"
                assert expected == (recall.get("rank"), recall.get("score")), (
                    f"fragment rank/score mismatch {key}: {expected} vs "
                    f"{(recall.get('rank'), recall.get('score'))}"
                )
                checks += 1
        ranked = [r for r in rows if r.get("stage") == "ranked"]
        assert ranked and all(r.get("fragment_id") and r.get("fragment_range") and r.get("entity_range") for r in ranked)
        selected = [r for r in rows if r.get("stage") == "context_selected"]
        assert selected and all(r.get("fragment_id") and r.get("content_range") and r.get("file_sha256") for r in selected)
        out.append({
            "case": case["id"],
            "raw_fragments": len(raw),
            "fused_fragments": len(fused),
            "lineage_checks": checks,
            "ranked": len(ranked),
            "selected": len(selected),
        })
    receipt = art / "results" / f"lineage-{args.split}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    print("receipt", receipt)


if __name__ == "__main__":
    main()
