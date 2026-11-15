#!/usr/bin/env python3
"""Negative tests for the R0 verifier. Each mutation must be rejected."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(verifier: Path, analysis: Path, raw: Path, dataset: Path, index: Path) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        proc = subprocess.run(
            [
                sys.executable,
                str(verifier),
                "--analysis",
                str(analysis),
                "--index-root",
                str(index),
                "--dataset",
                str(dataset),
                "--raw-dir",
                str(raw),
                "--output",
                out.name,
            ],
            capture_output=True,
            text=True,
        )
        detail = Path(out.name).read_text() if Path(out.name).exists() else proc.stderr
        return proc.returncode, detail


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--verifier", required=True, type=Path)
    ap.add_argument("--analysis", required=True, type=Path)
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--index-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    work = args.out.parent / "negative-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    base = json.loads(args.analysis.read_text())
    raw_files = {name: work / name for name in ("fts-r1.json", "vector-r1.json", "hybrid-r1.json")}
    for name, target in raw_files.items():
        shutil.copy2(args.raw_dir / name, target)
    cases = []
    # 1 missing anchor
    data = json.loads(json.dumps(base))
    qid = next(iter(data["attribution"]["per_query"]))
    data["attribution"]["per_query"][qid]["anchors"].pop()
    p = work / "missing-anchor.json"; p.write_text(json.dumps(data))
    cases.append(("missing anchor", p, work, "anchor count"))
    # 2 structure count
    data = json.loads(json.dumps(base)); data["structure"]["fragments"] += 1
    p = work / "bad-fragment-count.json"; p.write_text(json.dumps(data))
    cases.append(("fragment count", p, work, "structure mismatch"))
    # 3 dataset hash
    data = json.loads(json.dumps(base)); data["dataset_sha256"] = "0" * 64
    p = work / "bad-dataset.json"; p.write_text(json.dumps(data))
    cases.append(("dataset hash", p, work, "dataset hash"))
    # 4 contribution
    data = json.loads(json.dumps(base)); data["attribution"]["contribution"]["both"] += 1
    p = work / "bad-contribution.json"; p.write_text(json.dumps(data))
    cases.append(("contribution", p, work, "contribution mismatch"))
    # 5 incomplete candidate depth
    raw_bad = work / "raw-truncated"; raw_bad.mkdir()
    for name, target in raw_files.items():
        d = json.loads(target.read_text()); d["results"][0]["items"] = d["results"][0]["items"][:49]
        (raw_bad / name).write_text(json.dumps(d))
    cases.append(("depth", args.analysis, raw_bad, "candidate depth"))
    # 6 wrong model identity
    raw_model = work / "raw-wrong-model"; raw_model.mkdir()
    for name, target in raw_files.items():
        d = json.loads(target.read_text())
        d["run"]["measurement_contract"]["model"]["status"] = "FAIL"
        (raw_model / name).write_text(json.dumps(d))
    cases.append(("model identity", args.analysis, raw_model, "model identity"))
    # 7 screening loss set
    data = json.loads(json.dumps(base))
    data["screening"]["P2_skip_value_module"]["routes"]["vector"]["lost_anchors_known_top10"].append("fake")
    p = work / "bad-loss.json"; p.write_text(json.dumps(data))
    cases.append(("screening loss", p, work, "lost-anchor"))

    failures = []
    report = []
    for label, analysis, raw, needle in cases:
        code, text = run(args.verifier, analysis, raw, args.dataset, args.index_root)
        ok = code != 0 and needle in text
        report.append({"case": label, "rejected": ok, "exit": code, "detail": text[:500]})
        if not ok:
            failures.append(label)
    args.out.write_text(json.dumps({"status": "PASS" if not failures else "FAIL", "cases": report}, indent=2) + "\n")
    print(json.dumps({"status": "PASS" if not failures else "FAIL", "cases": [r["case"] for r in report]}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
