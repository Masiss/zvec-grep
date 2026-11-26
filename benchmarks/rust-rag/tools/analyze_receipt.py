#!/usr/bin/env python3
"""Summarize arm receipt, token counts and failure taxonomy."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, type=Path)
    ap.add_argument("--tokenizer", required=True, type=Path)
    ap.add_argument("--counter", required=True, type=Path)
    args = ap.parse_args()

    art = args.artifacts.resolve()
    receipt = json.loads((art / "results/arm-receipt.json").read_text())
    arms = receipt["arms"]
    summary: dict[str, dict] = {}
    per_case: dict[str, dict] = {}

    for arm in arms:
        pool = []
        sel = []
        bytes_ = []
        tokens = []
        lat = []
        rss = []
        cites = 0
        total_cites = 0
        for cid, row in receipt["cases"].items():
            a = row["arms"][arm]
            pool.append(a["pool_recall"])
            sel.append(a["selected_recall"])
            bytes_.append(a["pack_bytes"])
            lat.append(a["stats"]["wall"])
            rss.append(a["stats"]["rss_kb"])
            pack = art / a["pack_path"]
            cp = subprocess.run(
                [str(args.counter.resolve()), str(args.tokenizer.resolve()), str(pack)],
                capture_output=True, text=True, check=True,
            )
            t = json.loads(cp.stdout)["tokens"]
            tokens.append(t)
            for h in a["hits"][: a["selected_count"]]:
                total_cites += 1
                p = art.parent  # unused; paths are relative to corpus root in hits
                if h["path"] and h["start"] <= h["end"]:
                    cites += 1
            per_case.setdefault(cid, {})[arm] = {
                "pool": round(a["pool_recall"], 3),
                "selected": round(a["selected_recall"], 3),
                "bytes": a["pack_bytes"],
                "tokens": t,
            }
        summary[arm] = {
            "macro_pool_recall": round(sum(pool) / len(pool), 4),
            "macro_selected_recall": round(sum(sel) / len(sel), 4),
            "mean_pack_bytes": round(statistics.mean(bytes_), 1),
            "mean_potion_tokens": round(statistics.mean(tokens), 1),
            "median_potion_tokens": round(statistics.median(tokens), 1),
            "p95_latency_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 3),
            "peak_rss_kb": max(rss),
            "citation_shape_ok": f"{cites}/{total_cites}",
        }

    taxonomy = {}
    for cid, row in receipt["cases"].items():
        base = row["arms"]["baseline"]
        kinds = []
        if base["pool_recall"] < 1:
            kinds.append("acquisition")
        if base["pool_recall"] == 1 and base["selected_recall"] < 1:
            kinds.append("selection")
        if base["pool_recall"] > 0 and base["selected_recall"] == 0:
            kinds.append("pool-to-pack_zero")
        if any(h["end"] - h["start"] > 200 for h in base["hits"][: base["selected_count"]]):
            kinds.append("coarse_unit_noise")
        if not kinds:
            kinds.append("resolved")
        taxonomy[cid] = {
            "question": row["question"],
            "kinds": kinds,
            "baseline_pool": round(base["pool_recall"], 3),
            "baseline_selected": round(base["selected_recall"], 3),
            "best_selected": max(round(v["selected"], 3) for v in per_case[cid].values()),
        }

    out = {
        "schema_version": 1,
        "binary_sha256": receipt["binary_sha256"],
        "cases_sha256": receipt["cases_sha256"],
        "summary": summary,
        "per_case": per_case,
        "failure_taxonomy": taxonomy,
    }
    dest = art / "results/analysis.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print("taxonomy")
    for cid, t in taxonomy.items():
        print(cid, ",".join(t["kinds"]), "base", t["baseline_pool"], t["baseline_selected"], "best", t["best_selected"])
    print("receipt", dest)


if __name__ == "__main__":
    main()
