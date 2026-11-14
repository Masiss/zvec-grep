#!/usr/bin/env python3
"""Independent R0 verifier: recomputes the cardinality, attribution and
counterfactual loss sets from source/index/raw data without importing
r0_analyze.py. Any mismatch is a hard failure."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

import pyarrow.ipc as ipc


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_index(root: Path):
    base = root / ".zvec-grep" / "index"
    fpath = next(base.glob("fragments_*/0/scalar.0.ipc"))
    epath = base / "entities" / "0" / "scalar.0.ipc"
    paths = max(base.glob("files/*/scalar.0.ipc"), key=lambda p: p.stat().st_size)
    fragments = ipc.open_file(fpath).read_all().to_pylist()
    entities = ipc.open_file(epath).read_all().to_pylist()
    files = ipc.open_file(paths).read_all().to_pylist()
    file_path = {row["file_id"]: row["relative_path"] for row in files}
    by_id = {}
    for row in entities:
        payload = json.loads(row["payload"])
        meta = json.loads(row["metadata"]) if row.get("metadata") else {}
        by_id[row["entity_id"]] = {
            "entity_id": row["entity_id"],
            "path": file_path.get(row["file_id"]),
            "start_line": payload["source_range"]["start_line"],
            "end_line": payload["source_range"]["end_line"],
            "symbol_type": meta.get("symbol_type"),
            "fragment_ids": [f["id"] for f in payload.get("fragments", [])],
            "fragment_ranges": payload.get("fragments", []),
            "content_value": payload.get("content", {}).get("value", ""),
            "fragment_count": 0,
            "chars": 0,
        }
    for frag in fragments:
        ent = by_id.get(frag["entity_id"])
        if ent:
            ent["fragment_count"] += 1
            ent["chars"] += len(frag["text"])
    by_path = collections.defaultdict(list)
    for ent in by_id.values():
        if ent["path"]:
            by_path[ent["path"]].append(ent)
    return fragments, by_id, by_path


def specific(by_path, path, start, end):
    best = None
    best_key = (0, 0)
    for ent in by_path.get(path, []):
        overlap = min(end, ent["end_line"]) - max(start, ent["start_line"]) + 1
        if overlap <= 0:
            continue
        key = (overlap, -(ent["end_line"] - ent["start_line"] + 1))
        if key > best_key:
            best, best_key = ent, key
    return best


def route_hits(items, anchors):
    out = {}
    for anchor in anchors:
        for item in items:
            if item.get("path") != anchor["path"]:
                continue
            if min(item.get("end_line", 0), anchor["end_line"]) < max(
                item.get("start_line", 0), anchor["start_line"]
            ):
                continue
            if anchor["id"] not in out or item.get("rank", 999) < out[anchor["id"]].get(
                "rank", 999
            ):
                out[anchor["id"]] = item
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True, type=Path)
    ap.add_argument("--index-root", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    failures = []
    analysis = load_json(args.analysis)
    dataset = load_json(args.dataset)
    fragments, entities, by_path = load_index(args.index_root)
    queries = {q["id"]: q for q in dataset["queries"]}
    anchors_gold = {a["id"]: a for q in queries.values() for a in q.get("evidence", [])}

    dataset_sha = sha(args.dataset)
    if analysis["dataset_sha256"] != dataset_sha:
        failures.append("analysis dataset hash does not match dataset")
    if len(anchors_gold) != analysis["attribution"]["anchor_count"]:
        failures.append("anchor count mismatch")

    # Gold anchor source hashes.
    corpus = args.index_root.parent / "corpus"
    # index_root is the corpus path in this experiment; accept either.
    corpus = args.index_root if (args.index_root / "dapper").exists() else corpus
    for aid, anchor in anchors_gold.items():
        path = corpus / anchor["path"]
        if not path.exists():
            failures.append(f"missing anchor source {aid}")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        content = "\n".join(lines[anchor["start_line"] - 1 : anchor["end_line"]]) + "\n"
        if hashlib.sha256(content.encode()).hexdigest() != anchor["content_sha256"]:
            failures.append(f"anchor hash mismatch {aid}")

    expected = analysis["structure"]
    actual_counts = {
        "fragments": len(fragments),
        "entities": len(entities),
        "fragment_chars": sum(len(f["text"]) for f in fragments),
        "by_type_fragments": dict(
            collections.Counter(f.get("symbol_type") or "unknown" for f in fragments)
        ),
        "by_type_entities": dict(
            collections.Counter(e.get("symbol_type") or "unknown" for e in entities.values())
        ),
    }
    for key, value in actual_counts.items():
        if expected.get(key) != value:
            failures.append(f"structure mismatch {key}: {expected.get(key)} != {value}")

    raw = {
        route: load_json(args.raw_dir / f"{route}-r1.json")
        for route in ("fts", "vector", "hybrid")
    }
    for route, run in raw.items():
        contract = run.get("run", {}).get("measurement_contract", {})
        if contract.get("status") != "PASS":
            failures.append(f"{route} contract not PASS")
        if contract.get("dataset", {}).get("sha256") != dataset_sha:
            failures.append(f"{route} dataset hash mismatch")
        if contract.get("model", {}).get("status") != "PASS":
            failures.append(f"{route} model identity not PASS")
        for row in run["results"]:
            if len(row.get("items", [])) != 50:
                failures.append(f"{route} {row['query_id']} candidate depth <50")

    # Attribution: compare each recorded route rank with raw recomputation.
    contrib = collections.Counter()
    for qid, query in queries.items():
        rec = analysis["attribution"]["per_query"].get(qid)
        if not rec:
            failures.append(f"missing query {qid}")
            continue
        if len(rec["anchors"]) != len(query.get("evidence", [])):
            failures.append(f"{qid} anchor count")
        for a in rec["anchors"]:
            gold = anchors_gold.get(a["anchor_id"])
            if not gold or (gold["path"], gold["start_line"], gold["end_line"]) != (
                a["path"],
                a["start_line"],
                a["end_line"],
            ):
                failures.append(f"anchor identity mismatch {a['anchor_id']}")
            ent = specific(by_path, a["path"], a["start_line"], a["end_line"])
            if (a["structure"]["symbol_type"] or None) != (ent["symbol_type"] if ent else None):
                failures.append(f"structure mismatch {a['anchor_id']}")
        for route in ("fts", "vector", "hybrid"):
            hits = route_hits(raw[route]["results"][0]["items"] if False else [
                r for r in raw[route]["results"] if r["query_id"] == qid
            ][0]["items"], query.get("evidence", []))
            f = {
                a["anchor_id"]
                for a in rec["anchors"]
                if (a["routes"]["fts"] or {}).get("in_top10")
            }
            v = {
                a["anchor_id"]
                for a in rec["anchors"]
                if (a["routes"]["vector"] or {}).get("in_top10")
            }
            for a in rec["anchors"]:
                item = hits.get(a["anchor_id"])
                got = item is not None and (item.get("rank") or 99) <= 10
                rec_got = (a["routes"][route] or {}).get("in_top10", False)
                if got != rec_got:
                    failures.append(f"route mismatch {qid} {a['anchor_id']} {route}")
            if route == "fts":
                pass
        # Recompute contribution once per query.
        f = {a["anchor_id"] for a in rec["anchors"] if (a["routes"]["fts"] or {}).get("in_top10")}
        v = {a["anchor_id"] for a in rec["anchors"] if (a["routes"]["vector"] or {}).get("in_top10")}
        for aid in f & v:
            contrib["both"] += 1
        for aid in f - v:
            contrib["fts_only"] += 1
        for aid in v - f:
            contrib["vector_only"] += 1
    if dict(contrib) != analysis["attribution"]["contribution"]:
        failures.append(f"contribution mismatch {dict(contrib)} != {analysis['attribution']['contribution']}")

    # Policy counts and excluded fragment identities.
    def policy_defs():
        return {
            "P1_keep_container_types": lambda e: e["symbol_type"]
            in ("function", "value", "module"),
            "P2_skip_value_module": lambda e: e["symbol_type"] in ("value", "module"),
            "P3_keep_first_container_fragment": lambda e: False,
        }
    for name, pred in policy_defs().items():
        pol = analysis["policies"][name]
        if name == "P3_keep_first_container_fragment":
            excluded = [
                fid
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
                for fid in (e["fragment_ids"][1:] if e["fragment_ids"] else [])
            ]
        else:
            excluded = [
                f["_zvec_g_doc_id_"]
                for f in fragments
                if pred(entities[f["entity_id"]])
            ]
        if len(excluded) != pol["excluded_fragments"]:
            failures.append(f"{name} excluded fragment count mismatch")
        if set(excluded) != set(pol.get("excluded_fragment_ids", [])):
            failures.append(f"{name} excluded fragment identity mismatch")

    # Recompute offline loss sets from raw top-50 and policy exclusions.
    def excluded_item(name, item):
        typ = item.get("symbol_type") or "unknown"
        if name == "P1_keep_container_types":
            return typ in ("function", "value", "module")
        if name == "P2_skip_value_module":
            return typ in ("value", "module")
        ent = specific(by_path, item.get("path"), item.get("start_line", 0), item.get("end_line", 0))
        if name == "P3_keep_first_container_fragment" and ent:
            if ent["symbol_type"] in ("class", "interface", "enum") and ent["fragment_ranges"]:
                first = ent["fragment_ranges"][0]["range"]
                if first.get("kind") == "byte" and "start_offset" in first:
                    content = ent["content_value"]
                    start = ent["start_line"] + content[: first["start_offset"]].count("\n")
                    end = ent["start_line"] + content[: first["end_offset"]].count("\n")
                    return not (
                        min(item.get("end_line", 0), end)
                        >= max(item.get("start_line", 0), start)
                    )
        return False

    for name in analysis["policies"]:
        pol = analysis["policies"][name]
        if name == "P3_keep_first_container_fragment":
            excluded_chars = sum(
                sum(len(f["text"]) for f in fragments if f["entity_id"] == e["entity_id"])
                - len(next(f["text"] for f in fragments if f["entity_id"] == e["entity_id"]))
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
                and e["fragment_count"] > 0
            )
        else:
            pred = (
                (lambda e: e["symbol_type"] in ("function", "value", "module"))
                if name == "P1_keep_container_types"
                else (lambda e: e["symbol_type"] in ("value", "module"))
            )
            excluded_chars = sum(
                len(f["text"])
                for f in fragments
                if pred(entities[f["entity_id"]])
            )
        if excluded_chars != pol["excluded_chars"]:
            failures.append(f"{name} excluded chars mismatch")
        for route in ("vector", "hybrid"):
            lost = set()
            per_query = {}
            for qid, query in queries.items():
                if not query.get("answerable"):
                    continue
                rec = analysis["attribution"]["per_query"][qid]
                before = {
                    a["anchor_id"]
                    for a in rec["anchors"]
                    if (a["routes"][route] or {}).get("in_top10")
                }
                raw_items = [
                    r for r in raw[route]["results"] if r["query_id"] == qid
                ][0]["items"]
                kept = [item for item in raw_items if not excluded_item(name, item)][:10]
                after = set(route_hits(kept, [
                    {
                        "id": a["anchor_id"],
                        "path": a["path"],
                        "start_line": a["start_line"],
                        "end_line": a["end_line"],
                    }
                    for a in rec["anchors"]
                ]))
                lost |= before - after
                per_query[qid] = sorted(before - after)
            recorded = set(
                analysis["screening"][name]["routes"][route]["lost_anchors_known_top10"]
            )
            if lost != recorded:
                failures.append(
                    f"{name} {route} lost-anchor set mismatch {sorted(lost)} != {sorted(recorded)}"
                )
            for qid, expected_lost in per_query.items():
                recorded_q = analysis["screening"][name]["routes"][route].get(
                    "per_query", {}
                ).get(qid, {}).get("lost_anchors_known_top10", [])
                if sorted(recorded_q) != expected_lost:
                    failures.append(f"{name} {route} {qid} per-query loss mismatch")

    # Cost arithmetic must be internally reproducible from the recorded
    # structural counts and the declared Gemma constants.
    gemma_fragments = 9223
    gemma_mean_chars = 942.95
    gemma_chars = gemma_fragments * gemma_mean_chars
    chars_per_token = 204800 / 47315
    cost = analysis["cost"]
    checks = {
        "fragment_chars": gemma_chars,
        "chars_per_token": chars_per_token,
        "embedding_tokens": gemma_chars / chars_per_token,
        "fp16_vector_mib": gemma_fragments * 768 * 2 / 1024 / 1024,
        "fp32_vector_mib": gemma_fragments * 768 * 4 / 1024 / 1024,
    }
    for key, value in checks.items():
        if abs(cost["derived_gemma"][key] - value) > 1e-6:
            failures.append(f"cost arithmetic mismatch {key}")
    total_chars = sum(len(f["text"]) for f in fragments)
    for name, pol in analysis["policies"].items():
        frac = pol["excluded_fragments"] / len(fragments)
        char_frac = pol["excluded_chars"] / total_chars
        recorded = cost["policies"][name]
        expected = {
            "structural_fragment_fraction": frac,
            "structural_char_fraction": char_frac,
            "derived_gemma_fragments_excluded": gemma_fragments * frac,
            "derived_gemma_chars_excluded": gemma_chars * char_frac,
            "derived_gemma_tokens_excluded": (gemma_chars / chars_per_token) * char_frac,
            "derived_gemma_fp16_mib_excluded": (
                gemma_fragments * 768 * 2 / 1024 / 1024
            )
            * frac,
            "derived_inference_seconds_batch1": (
                (gemma_chars / chars_per_token) * char_frac
            )
            / 484,
        }
        for key, value in expected.items():
            if abs(recorded[key] - value) > 1e-6:
                failures.append(f"{name} cost mismatch {key}")

    out = {
        "status": "PASS" if not failures else "FAIL",
        "dataset_sha256": dataset_sha,
        "fragments": len(fragments),
        "entities": len(entities),
        "anchors": len(anchors_gold),
        "contribution": dict(contrib),
        "failures": failures,
    }
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text)
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
