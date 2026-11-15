#!/usr/bin/env python3
"""R0 structural/cardinality attribution and offline policy screening.

This is an analysis tool, not a production policy. It reads:
  * the frozen e3-01-v2 queries (gold anchors),
  * corrected E6 Gemma raw results (top-50 per route),
  * a fast Potion structural index built from the same corpus (10,905 fragments,
    8,182 entities) for structural metadata and cardinality.

It never uses a missing contract receipt for a decision and never treats
filtered top-50 as a rebuilt-ANN estimate.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path

import pyarrow.ipc as ipc


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_index(root: Path):
    base = root / ".zvec-grep" / "index"
    fragments_path = next(base.glob("fragments_*/0/scalar.0.ipc"))
    entities_path = base / "entities" / "0" / "scalar.0.ipc"
    fragments = ipc.open_file(fragments_path).read_all().to_pylist()
    entities = ipc.open_file(entities_path).read_all().to_pylist()
    files_path = max(base.glob("files/*/scalar.0.ipc"), key=lambda p: p.stat().st_size)
    files = ipc.open_file(files_path).read_all().to_pylist()
    file_path = {row["file_id"]: row["relative_path"] for row in files}
    entity_by_id = {}
    for row in entities:
        payload = json.loads(row["payload"])
        metadata = json.loads(row["metadata"]) if row.get("metadata") else {}
        entity_by_id[row["entity_id"]] = {
            "entity_id": row["entity_id"],
            "file_id": row["file_id"],
            "path": None,  # filled from fragments by entity
            "start_line": payload["source_range"]["start_line"],
            "end_line": payload["source_range"]["end_line"],
            "kind": metadata.get("kind"),
            "symbol_name": metadata.get("symbol_name"),
            "symbol_type": metadata.get("symbol_type"),
            "scope": metadata.get("scope"),
            "visibility": metadata.get("visibility"),
            "signature": metadata.get("signature"),
            "documentation": bool(metadata.get("documentation")),
            "fragment_ids": [f["id"] for f in payload.get("fragments", [])],
            "fragment_ranges": payload.get("fragments", []),
            "content_value": payload.get("content", {}).get("value", ""),
            "chars": 0,
            "fragment_count": 0,
        }
    for frag in fragments:
        ent = entity_by_id.get(frag["entity_id"])
        if ent is None:
            continue
        if ent["path"] is None:
            ent["path"] = file_path.get(frag["file_id"]) or frag["file_name"]
        ent["chars"] += len(frag["text"])
        ent["fragment_count"] += 1
    by_path = collections.defaultdict(list)
    for ent in entity_by_id.values():
        if ent["path"]:
            by_path[ent["path"]].append(ent)
    return fragments, entity_by_id, by_path


def find_entity(by_path, path, start, end):
    # Prefer maximum line overlap; on a tie prefer the most specific (smallest)
    # entity, so a method anchor does not map to its enclosing class.
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


def item_for(entity):
    if not entity:
        return {"symbol_type": None, "entity_id": None, "scope": None, "chars": 0}
    return {
        "entity_id": entity["entity_id"],
        "symbol_type": entity["symbol_type"],
        "scope": entity["scope"],
        "chars": entity["chars"],
        "fragment_count": entity["fragment_count"],
    }


def hit_items(items, anchors):
    """Return anchor_id -> first matching item."""
    out = {}
    for anchor in anchors:
        anchor_id = anchor.get("id") or anchor["anchor_id"]
        for item in items:
            if item.get("path") != anchor["path"]:
                continue
            if item.get("start_line") is None or item.get("end_line") is None:
                continue
            if min(item["end_line"], anchor["end_line"]) < max(
                item["start_line"], anchor["start_line"]
            ):
                continue
            if anchor_id not in out or item.get("rank", 10**9) < out[anchor_id].get(
                "rank", 10**9
            ):
                out[anchor_id] = item
    return out


def structural_summary(fragments, entities):
    type_frag = collections.Counter()
    type_ent = collections.Counter()
    chars_type = collections.Counter()
    text_hashes = collections.Counter()
    for frag in fragments:
        type_frag[frag.get("symbol_type") or "unknown"] += 1
        chars_type[frag.get("symbol_type") or "unknown"] += len(frag["text"])
        text_hashes[hashlib.sha256(norm_text(frag["text"]).encode()).hexdigest()] += 1
    for ent in entities.values():
        type_ent[ent["symbol_type"] or "unknown"] += 1
    multi = [e for e in entities.values() if e["fragment_count"] > 1]
    total_chars = sum(len(f["text"]) for f in fragments)
    return {
        "fragments": len(fragments),
        "entities": len(entities),
        "fragment_chars": total_chars,
        "fragment_mean_chars": total_chars / len(fragments),
        "by_type_fragments": dict(type_frag),
        "by_type_entities": dict(type_ent),
        "by_type_chars": dict(chars_type),
        "entities_with_multiple_fragments": len(multi),
        "duplicate_normalized_texts": {
            "unique": len(text_hashes),
            "duplicate_occurrences": sum(v - 1 for v in text_hashes.values()),
            "max_group": max(text_hashes.values()) if text_hashes else 0,
        },
    }


def parent_child(entities):
    """Count entities whose source range contains at least one member entity."""
    by_path = collections.defaultdict(list)
    for ent in entities.values():
        by_path[ent["path"]].append(ent)
    out = {}
    for path, group in by_path.items():
        for ent in group:
            children = [
                other
                for other in group
                if other is not ent
                and ent["start_line"] <= other["start_line"]
                and other["end_line"] <= ent["end_line"]
                and (other["end_line"] - other["start_line"])
                < (ent["end_line"] - ent["start_line"])
            ]
            out[ent["entity_id"]] = children
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-root", required=True, type=Path)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--gemma-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    fragments, entities, by_path = load_index(args.index_root)
    summary = structural_summary(fragments, entities)
    children = parent_child(entities)
    dataset = read_json(args.dataset)
    queries = {q["id"]: q for q in dataset["queries"]}

    raw = {
        route: read_json(args.gemma_dir / f"{route}-r1.json")
        for route in ("fts", "vector", "hybrid")
    }
    # Verify E6 contract and dataset hash before using raw results.
    for route, run in raw.items():
        contract = run.get("run", {}).get("measurement_contract", {})
        assert contract.get("status") == "PASS", route
        contract_dataset = contract.get("dataset", {})
        assert contract_dataset.get("sha256") == hashlib.sha256(
            args.dataset.read_bytes()
        ).hexdigest()
    top = {
        route: {row["query_id"]: row["items"] for row in run["results"]}
        for route, run in raw.items()
    }

    anchors = {}
    per_query = {}
    for qid, query in queries.items():
        qanchors = query.get("evidence", [])
        hits = {}
        for route in ("fts", "vector", "hybrid"):
            hits[route] = hit_items(top[route].get(qid, []), qanchors)
        row = {
            "query_id": qid,
            "corpus": query["corpus"],
            "category": query["category"],
            "answerable": query["answerable"],
            "leakage": query["leakage"],
            "required_anchor_ids": query.get("solved_when", {}).get(
                "required_anchor_ids", []
            ),
            "required_groups": query.get("solved_when", {}).get("required_groups", []),
            "anchors": [],
        }
        for anchor in qanchors:
            ent = find_entity(by_path, anchor["path"], anchor["start_line"], anchor["end_line"])
            rec = {
                "anchor_id": anchor["id"],
                "query_id": qid,
                "corpus": query["corpus"],
                "group": anchor["group"],
                "role": anchor["role"],
                "path": anchor["path"],
                "start_line": anchor["start_line"],
                "end_line": anchor["end_line"],
                "symbol": anchor["symbol"],
                "required": anchor["id"] in row["required_anchor_ids"],
                "structure": item_for(ent),
                "routes": {},
            }
            for route in ("fts", "vector", "hybrid"):
                item = hits[route].get(anchor["id"])
                rec["routes"][route] = (
                    None
                    if item is None
                    else {
                        "rank": item.get("rank"),
                        "score": item.get("score"),
                        "matched_by": item.get("matched_by"),
                        "in_top10": (item.get("rank") or 99) <= 10,
                    }
                )
            row["anchors"].append(rec)
            anchors[anchor["id"]] = rec
        per_query[qid] = row

    # Unique contribution at K=10, after corrected Gemma runs.
    contribution = collections.Counter()
    query_contribution = collections.Counter()
    for qid, row in per_query.items():
        if not row["answerable"]:
            continue
        f = {a["anchor_id"] for a in row["anchors"] if (a["routes"]["fts"] or {}).get("in_top10")}
        v = {a["anchor_id"] for a in row["anchors"] if (a["routes"]["vector"] or {}).get("in_top10")}
        h = {a["anchor_id"] for a in row["anchors"] if (a["routes"]["hybrid"] or {}).get("in_top10")}
        for aid in f & v:
            contribution["both"] += 1
        for aid in f - v:
            contribution["fts_only"] += 1
        for aid in v - f:
            contribution["vector_only"] += 1
        if f and v:
            query_contribution["both"] += 1
        elif f:
            query_contribution["fts_only"] += 1
        elif v:
            query_contribution["vector_only"] += 1
        else:
            query_contribution["neither"] += 1
        row["hybrid_hits"] = sorted(h)
        row["hybrid_misses"] = sorted(a["anchor_id"] for a in row["anchors"] if a["anchor_id"] not in h)

    # Policy inclusion manifests, at fragment/entity level.
    # P2 keeps one canonical fragment per normalized text hash and excludes
    # the rest. An entity is excluded only when all its fragments are excluded,
    # which is conservative for raw-result screening.
    by_text = collections.defaultdict(list)
    for frag in fragments:
        by_text[hashlib.sha256(norm_text(frag["text"]).encode()).hexdigest()].append(frag)
    duplicate_fragment_ids = set()
    for group in by_text.values():
        group = sorted(
            group,
            key=lambda f: (f["file_id"], f["entity_id"], f["_zvec_g_doc_id_"]),
        )
        duplicate_fragment_ids.update(f["_zvec_g_doc_id_"] for f in group[1:])
    frag_ids_by_entity = collections.defaultdict(set)
    for frag in fragments:
        frag_ids_by_entity[frag["entity_id"]].add(frag["_zvec_g_doc_id_"])
    dedupe_entity_ids = {
        eid
        for eid, fids in frag_ids_by_entity.items()
        if fids and fids <= duplicate_fragment_ids
    }
    dedupe_fragments = [
        frag for frag in fragments if frag["_zvec_g_doc_id_"] in duplicate_fragment_ids
    ]
    policies = {
        "P1_keep_container_types": {
            "excluded_entities": sum(
                1
                for e in entities.values()
                if e["symbol_type"] in ("function", "value", "module")
            ),
            "excluded_fragments": sum(
                1
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("function", "value", "module")
            ),
            "excluded_chars": sum(
                len(f["text"])
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("function", "value", "module")
            ),
            "excluded_types": dict(
                collections.Counter(
                    (f.get("symbol_type") or "unknown")
                    for f in fragments
                    if (f.get("symbol_type") or "unknown")
                    in ("function", "value", "module")
                )
            ),
            "excluded_entity_ids": sorted(
                e["entity_id"]
                for e in entities.values()
                if e["symbol_type"] in ("function", "value", "module")
            ),
            "excluded_fragment_ids": sorted(
                f["_zvec_g_doc_id_"]
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("function", "value", "module")
            ),
            "kept_types": ["class", "interface", "enum", "unknown"],
        },
        "P2_skip_value_module": {
            "excluded_entities": sum(
                1
                for e in entities.values()
                if e["symbol_type"] in ("value", "module")
            ),
            "excluded_fragments": sum(
                1
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("value", "module")
            ),
            "excluded_chars": sum(
                len(f["text"])
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("value", "module")
            ),
            "excluded_types": dict(
                collections.Counter(
                    (f.get("symbol_type") or "unknown")
                    for f in fragments
                    if (f.get("symbol_type") or "unknown") in ("value", "module")
                )
            ),
            "excluded_entity_ids": sorted(
                e["entity_id"]
                for e in entities.values()
                if e["symbol_type"] in ("value", "module")
            ),
            "excluded_fragment_ids": sorted(
                f["_zvec_g_doc_id_"]
                for f in fragments
                if (f.get("symbol_type") or "unknown") in ("value", "module")
            ),
            "kept_types": ["class", "interface", "enum", "function", "unknown"],
        },
        "P3_keep_first_container_fragment": {
            "excluded_entities": 0,
            "excluded_fragments": sum(
                max(0, e["fragment_count"] - 1)
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
            ),
            "excluded_chars": sum(
                sum(
                    len(f["text"])
                    for f in fragments
                    if f["entity_id"] == e["entity_id"]
                )
                - len(
                    next(
                        f["text"]
                        for f in fragments
                        if f["entity_id"] == e["entity_id"]
                    )
                )
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
                and e["fragment_count"] > 0
            ),
            "excluded_types": dict(
                collections.Counter(
                    e["symbol_type"]
                    for e in entities.values()
                    if e["symbol_type"] in ("class", "interface", "enum")
                    for _ in range(max(0, e["fragment_count"] - 1))
                )
            ),
            "excluded_entity_ids": [],
            "affected_entities": sum(
                1
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
                and e["fragment_count"] > 1
            ),
            "excluded_fragment_ids": sorted(
                fid
                for e in entities.values()
                if e["symbol_type"] in ("class", "interface", "enum")
                for fid in (e["fragment_ids"][1:] if e["fragment_ids"] else [])
            ),
        },
    }

    # Offline screening on corrected Gemma top-50. If a removed item is in
    # top-10, the replacement is UNKNOWN; we report top-10 anchor loss only.
    screening = {}
    for name, policy in policies.items():
        if name == "P1_keep_container_types":
            ids = {
                e["entity_id"]
                for e in entities.values()
                if e["symbol_type"] in ("function", "value", "module")
            }
        elif name == "P2_skip_value_module":
            ids = {
                e["entity_id"]
                for e in entities.values()
                if e["symbol_type"] in ("value", "module")
            }
        else:
            ids = set()
        result = {"routes": {}}
        for route in ("vector", "hybrid"):
            removed_top10 = collections.Counter()
            lost_anchors = set()
            kept_top10 = {}
            per_query_impact = {}
            for qid, row in per_query.items():
                kept = []
                for item in top[route].get(qid, []):
                    ent = find_entity(by_path, item.get("path"), item.get("start_line", 0), item.get("end_line", 0))
                    excluded = bool(ent and ent["entity_id"] in ids)
                    if name == "P1_keep_container_types":
                        excluded = (item.get("symbol_type") or "unknown") in (
                            "function",
                            "value",
                            "module",
                        )
                    if name == "P2_skip_value_module":
                        excluded = (item.get("symbol_type") or "unknown") in (
                            "value",
                            "module",
                        )
                    if name == "P3_keep_first_container_fragment" and ent:
                        if ent["symbol_type"] in ("class", "interface", "enum") and ent["fragment_ranges"]:
                            first = ent["fragment_ranges"][0]["range"]
                            if first.get("kind") == "byte" and "start_offset" in first:
                                content = ent["content_value"]
                                start = ent["start_line"] + content[: first["start_offset"]].count("\n")
                                end = ent["start_line"] + content[: first["end_offset"]].count("\n")
                                excluded = not (
                                    min(item.get("end_line", 0), end)
                                    >= max(item.get("start_line", 0), start)
                                )
                            else:
                                excluded = False
                    if excluded:
                        if (item.get("rank") or 99) <= 10:
                            removed_top10[qid] += 1
                        continue
                    kept.append(item)
                kept_top10[qid] = kept[:10]
            for qid, row in per_query.items():
                if not row["answerable"]:
                    continue
                before = {
                    a["anchor_id"]
                    for a in row["anchors"]
                    if (a["routes"][route] or {}).get("in_top10")
                }
                after = {
                    a["anchor_id"]
                    for a in row["anchors"]
                    if a["anchor_id"] in hit_items(kept_top10[qid], row["anchors"])
                }
                lost_anchors |= before - after
                per_query_impact[qid] = {
                    "removed_top10": removed_top10.get(qid, 0),
                    "before_top10_anchors": len(before),
                    "after_known_top10_anchors": len(after),
                    "lost_anchors_known_top10": sorted(before - after),
                }
            result["routes"][route] = {
                "per_query": per_query_impact,
                "removed_items_in_top10": sum(removed_top10.values()),
                "queries_with_removed_top10": len(removed_top10),
                "lost_anchors_known_top10": sorted(lost_anchors),
                "lost_anchor_count": len(lost_anchors),
                "replacement_depth": "UNKNOWN (top-50 truncation)",
            }
        screening[name] = result

    # Derived Gemma-layout cost estimates. Structural fractions come from the
    # Potion scan; Gemma totals come from E6/E4 and are explicitly derived.
    gemma_fragments = 9223
    gemma_mean_chars = 942.95
    gemma_chars = gemma_fragments * gemma_mean_chars
    chars_per_token = 204800 / 47315  # E4 fixed workload: 256 * 800 chars / 47,315 tokens
    gemma_tokens = gemma_chars / chars_per_token
    cost = {
        "observed_gemma_index": {
            "fragments": gemma_fragments,
            "mean_fragment_chars": gemma_mean_chars,
            "index_wall_seconds": 4236,
            "user_cpu_seconds": 16619,
            "peak_rss_gib": 2.99,
            "index_disk_mb": 63.2,
            "doc_tokens_per_second_batch1": 484,
        },
        "derived_gemma": {
            "fragment_chars": gemma_chars,
            "chars_per_token": chars_per_token,
            "embedding_tokens": gemma_tokens,
            "fp16_vector_mib": gemma_fragments * 768 * 2 / 1024 / 1024,
            "fp32_vector_mib": gemma_fragments * 768 * 4 / 1024 / 1024,
        },
        "policies": {},
    }
    for name, pol in policies.items():
        frag_frac = pol["excluded_fragments"] / len(fragments)
        char_frac = pol["excluded_chars"] / sum(len(f["text"]) for f in fragments)
        tokens = gemma_tokens * char_frac
        cost["policies"][name] = {
            "structural_fragment_fraction": frag_frac,
            "structural_char_fraction": char_frac,
            "derived_gemma_fragments_excluded": gemma_fragments * frag_frac,
            "derived_gemma_chars_excluded": gemma_chars * char_frac,
            "derived_gemma_tokens_excluded": tokens,
            "derived_gemma_fp16_mib_excluded": (
                gemma_fragments * 768 * 2 / 1024 / 1024
            )
            * frag_frac,
            "derived_inference_seconds_batch1": tokens / 484,
        }

    out = {
        "dataset": dataset["dataset"],
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "index_root": str(args.index_root),
        "structure": summary,
        "attribution": {
            "anchor_count": len(anchors),
            "contribution": dict(contribution),
            "query_contribution": dict(query_contribution),
            "per_query": per_query,
        },
        "policies": policies,
        "cost": cost,
        "screening": screening,
        "limitations": [
            "raw results are top-50; replacements after filtering are UNKNOWN",
            "structural metadata comes from a 1024-token Potion index; fragment counts are not the 2048 Gemma layout",
            "offline filtering is not a rebuilt HNSW/ANN estimate",
            "entity/type classification is extractor-derived; property vs field is not distinguished",
        ],
    }
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "fragments": summary["fragments"],
        "entities": summary["entities"],
        "types_fragments": summary["by_type_fragments"],
        "types_entities": summary["by_type_entities"],
        "contribution": dict(contribution),
        "query_contribution": dict(query_contribution),
        "policies": {k: {x: v[x] for x in ("excluded_entities", "excluded_fragments", "excluded_chars")} for k, v in policies.items()},
        "screening": screening,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
