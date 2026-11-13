#!/usr/bin/env python3
"""Paired per-query deltas between two E4 arms on one route."""
from __future__ import annotations
import argparse, json, statistics, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from evaluate import query_metrics  # noqa: E402

def load_mean(matrix: Path, prefix: str, route: str, dataset):
    files = [p for p in sorted(matrix.glob(f"{prefix}-{route}-r*.json")) if ".score." not in p.name]
    if not files:
        raise SystemExit(f"no runs for {prefix} {route}")
    per = {}
    for path in files:
        run = json.loads(path.read_text())
        rows = {row["query_id"]: row for row in run["results"]}
        for q in dataset:
            m = query_metrics(q, rows[q["id"]]["items"], rows[q["id"]].get("latency_ms"))
            per.setdefault(m["id"], []).append(m)
    return {qid: {k: (statistics.fmean(vals) if (vals := [m[k] for m in ms if isinstance(m.get(k),(int,float))]) else None) for k in ("recall_at_10","solved_at_10","mrr_at_10","ndcg_at_10")} for qid, ms in per.items()}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--matrix-dir", type=Path, required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--cand", required=True)
    p.add_argument("--route", default="hybrid")
    p.add_argument("--dataset", type=Path, default=HERE.parent/"dataset/queries.json")
    p.add_argument("--output", type=Path)
    a=p.parse_args()
    qs=json.loads(a.dataset.read_text())["queries"]
    base=load_mean(a.matrix_dir,a.base,a.route,qs)
    cand=load_mean(a.matrix_dir,a.cand,a.route,qs)
    rows=[]
    for q in qs:
        if not q["answerable"]:
            continue
        b,c=base[q["id"]],cand[q["id"]]
        rows.append({"id":q["id"],"category":q["category"],"corpus":q["corpus"],
                     "recall_delta":c["recall_at_10"]-b["recall_at_10"],
                     "solved_delta":c["solved_at_10"]-b["solved_at_10"],
                     "mrr_delta":c["mrr_at_10"]-b["mrr_at_10"],
                     "ndcg_delta":c["ndcg_at_10"]-b["ndcg_at_10"]})
    def sm(key):
        vals=[r[key] for r in rows]
        return {"mean":statistics.fmean(vals),"wins":sum(v>1e-12 for v in vals),"losses":sum(v<-1e-12 for v in vals),"ties":sum(abs(v)<=1e-12 for v in vals)}
    out={"route":a.route,"base":a.base,"cand":a.cand,"n":len(rows),"summary":{k:sm(k) for k in ("recall_delta","solved_delta","mrr_delta","ndcg_delta")},"per_query":rows}
    text=json.dumps(out,indent=2)+"\n"
    (a.output.write_text(text) if a.output else print(text))
if __name__=="__main__": main()
