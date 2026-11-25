#!/usr/bin/env python3
"""Join the frozen final evaluation's delivered ranges to source gold and trace."""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EVAL = Path(__file__).resolve().parent
RUNS = EVAL / "runs/validation-final-20261123"
CORPUS = Path("/tmp/zvec-public-source-20261123")
OUTPUT = EVAL / "product-reality-analysis-20261123.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def byte_interval(value: Any) -> tuple[int, int] | None:
    if isinstance(value, dict) and isinstance(value.get("byte"), dict):
        start = value["byte"].get("start_offset")
        end = value["byte"].get("end_offset")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            return start, end
    return None


def source_origin(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, dict) or not isinstance(value.get("text"), dict):
        return None
    origin = value["text"]
    start, end = origin.get("start", {}), origin.get("end", {})
    vals = (
        start.get("byte_offset"),
        end.get("byte_offset"),
        start.get("line"),
        end.get("line"),
    )
    if all(isinstance(x, int) for x in vals) and vals[0] <= vals[1]:
        return vals  # type: ignore[return-value]
    return None


def lines_for_offsets(source: bytes, start: int, end: int) -> list[int]:
    line_starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
    start_idx = max(0, bisect.bisect_right(line_starts, start) - 1)
    start_line = start_idx + 1
    if end in line_starts and end > start:
        end_line = max(start_line, line_starts.index(end))
    else:
        end_line = max(start_line, bisect.bisect_right(line_starts, end))
    return [start_line, end_line]


def item_interval(item: dict[str, Any]) -> list[int] | None:
    cr = item.get("content_range")
    if isinstance(cr, list) and len(cr) == 2 and all(isinstance(x, int) for x in cr):
        return cr
    start, end = item.get("start_line"), item.get("end_line")
    if isinstance(start, int) and isinstance(end, int):
        return [start, end]
    return None


def target_overlaps(path: str, interval: list[int] | None, gold_ranges: list[dict[str, Any]]) -> list[int]:
    if interval is None:
        return []
    return [
        i + 1
        for i, target in enumerate(gold_ranges)
        if path == target["path"]
        and max(interval[0], int(target["start_line"])) <= min(interval[1], int(target["end_line"]))
    ]


def summarize_items(
    items: list[dict[str, Any]],
    gold_ranges: list[dict[str, Any]],
    locked_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_paths = sorted({target["path"] for target in gold_ranges})
    rows = []
    for item in items:
        path = str(item["path"])
        lock = locked_files.get(path)
        if lock is None:
            raise ValueError(f"delivered path not in source lock: {path}")
        source = (CORPUS / path).read_bytes()
        expected_hash = lock["sha256"]
        item_hash = item.get("sha256", item.get("source_sha256"))
        if digest(source) != expected_hash or (item_hash is not None and item_hash != expected_hash):
            raise ValueError(f"source or delivery hash mismatch: {path}")
        interval = item_interval(item)
        source_bytes = item.get("source_bytes")
        if source_bytes is None:
            offsets = item.get("raw_file_byte_offsets")
            if isinstance(offsets, list) and len(offsets) == 2:
                source_bytes = int(offsets[1]) - int(offsets[0])
            else:
                source_bytes = len(str(item.get("content", "")).encode("utf-8"))
        rows.append(
            {
                "rank": int(item.get("rank", 0)),
                "path": path,
                "content_range": interval,
                "source_bytes": int(source_bytes),
                "gold_range_overlaps": target_overlaps(path, interval, gold_ranges),
                "source_sha256": expected_hash,
                "delivered_file_hash_present": item_hash is not None,
                "content_sha256": item.get("content_sha256"),
                "declaration": item.get("declaration"),
                "outline_present": bool(item.get("outline")),
                "matched_terms": item.get("terms"),
            }
        )
    rows.sort(key=lambda row: row["rank"])
    target_items = [row for row in rows if row["path"] in target_paths]
    overlap_items = [row for row in rows if row["gold_range_overlaps"]]
    total_bytes = sum(row["source_bytes"] for row in rows)
    return {
        "item_count": len(rows),
        "source_bytes": total_bytes,
        "first_target_file_rank": min((row["rank"] for row in target_items), default=None),
        "first_accepted_range_overlap_rank": min((row["rank"] for row in overlap_items), default=None),
        "first_target_file_bytes_through_rank": sum(
            row["source_bytes"] for row in rows if row["rank"] <= min((x["rank"] for x in target_items), default=10**9)
        ) if target_items else None,
        "first_accepted_overlap_bytes_through_rank": sum(
            row["source_bytes"] for row in rows if row["rank"] <= min((x["rank"] for x in overlap_items), default=10**9)
        ) if overlap_items else None,
        "items": rows,
    }


def compare_item_ranges(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Describe C's per-rank interval changes without inferring item usefulness."""
    base_by_rank = {item["rank"]: item for item in baseline["items"]}
    candidate_by_rank = {item["rank"]: item for item in candidate["items"]}
    changes = []
    for rank in sorted(set(base_by_rank) | set(candidate_by_rank)):
        base = base_by_rank.get(rank)
        cand = candidate_by_rank.get(rank)
        if base is None or cand is None or base["path"] != cand["path"] or base["content_range"] == cand["content_range"]:
            continue
        changes.append({
            "rank": rank,
            "path": base["path"],
            "r4_range": base["content_range"],
            "candidate_c_range": cand["content_range"],
            "r4_gold_range_overlaps": base["gold_range_overlaps"],
            "candidate_c_gold_range_overlaps": cand["gold_range_overlaps"],
            "r4_source_bytes": base["source_bytes"],
            "candidate_c_source_bytes": cand["source_bytes"],
            "source_bytes_delta": cand["source_bytes"] - base["source_bytes"],
        })
    return {
        "changed_same_rank_ranges": changes,
        "changed_item_count": len(changes),
        "source_bytes_delta_total": candidate["source_bytes"] - baseline["source_bytes"],
        "interpretation_limit": "Range overlap and byte growth are structural observations; adjudication did not retain per-item labels.",
    }


def parse_route_pool(path: Path, mode: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_path: str | None = None
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^File: (.+)$", line):
            current_path = match.group(1)
        elif match := re.match(r"^\s+#(\d+)\s+", line):
            current = {"rank": int(match.group(1)), "path": current_path, "start_line": None, "end_line": None, "route_rank": None}
            items.append(current)
        elif match := re.match(r"^\s+Range: (\d+)-(\d+)", line):
            if current is not None:
                current["start_line"], current["end_line"] = int(match.group(1)), int(match.group(2))
        elif line.strip().startswith("Trace: ") and current is not None:
            if match := re.search(rf"\b{mode} #(\d+)", line):
                current["route_rank"] = int(match.group(1))
    return [item for item in items if item["path"] and item["start_line"] is not None]


def find_hybrid_groups(trace_path: Path, prompts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    task_by_question = {task["question"]: task["task_id"] for task in prompts}
    groups: list[list[dict[str, Any]]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("phase") == "search_start":
            groups.append([record])
        elif groups:
            groups[-1].append(record)
    result: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        routes = group[0]["payload"].get("routes", [])
        if len(routes) != 2 or {route.get("mode") for route in routes} != {"Fts", "Vector"}:
            continue
        question = routes[0]["query"]
        task_id = task_by_question.get(question)
        if task_id is None:
            raise ValueError("hybrid diagnostic query does not map to a frozen task")
        result[task_id] = group
    return result


def source_fragments_for_entity(entity: dict[str, Any], source: bytes) -> dict[str, Any]:
    origin = source_origin(entity.get("entity_source_range"))
    if origin is None:
        return {"source_range_lines": None, "recalled_gold_overlap_fragments": [], "owned_gold_overlap_fragments": []}
    base_offset, end_offset, start_line, end_line = origin

    def map_fragments(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mapped = []
        for frag in fragments:
            local = byte_interval(frag.get("fragment_range"))
            if local is None:
                if frag.get("fragment_range") == "full":
                    interval = [start_line, end_line]
                else:
                    continue
            else:
                interval = lines_for_offsets(source, base_offset + local[0], base_offset + local[1])
                interval[0] = max(start_line, interval[0])
                interval[1] = min(end_line, interval[1])
            mapped.append({"fragment_id": frag.get("fragment_id"), "source_lines": interval})
        return mapped

    return {
        "source_range_lines": [start_line, end_line],
        "recalled_fragments": map_fragments(entity.get("evidence", [])),
        "owned_fragments": map_fragments(entity.get("owned_fragments", [])),
    }


def main() -> None:
    lock_path = EVAL / "source.lock.json"
    lock_doc = read_json(lock_path)
    locked_files = {f["path"]: f for repo in lock_doc["repositories"] for f in repo["files"]}
    prompts = read_json(EVAL / "author/validation-prompts.json")["tasks"]
    gold_tasks = {task["task_id"]: task for task in read_json(EVAL / "adjudicator/validation-gold.json")["tasks"]}
    adjudication = read_json(EVAL / "adjudicator/final-validation-adjudication.json")
    score_tasks = {task["task_id"]: task for task in adjudication["tasks"]}
    trace_path = RUNS / "r4/diagnostic-trace.jsonl"
    hybrid_groups = find_hybrid_groups(trace_path, prompts)
    per_task = []

    for prompt in prompts:
        task_id = prompt["task_id"]
        gold = gold_tasks[task_id]
        ranges = gold["accepted_source_ranges"]
        arms: dict[str, Any] = {}
        for name, path in (
            ("r4", RUNS / f"r4/{task_id}/hybrid-delivered.json"),
            ("candidate_c", RUNS / f"candidate-c/{task_id}/candidate-delivered.json"),
            ("rg", RUNS / f"rg/{task_id}/rg-delivered.json"),
        ):
            data = read_json(path)
            arms[name] = summarize_items(data["items"], ranges, locked_files)

        route_pools = {}
        for mode in ("fts", "vector"):
            pool = parse_route_pool(RUNS / f"r4/{task_id}/{mode}-pool.txt", mode)
            route_pools[mode] = summarize_items(pool, ranges, locked_files)

        group = hybrid_groups[task_id]
        loaded = next(r["payload"]["entities"] for r in group if r["phase"] == "loaded_pre_ranking")
        final = next(r["payload"]["entities"] for r in group if r["phase"] == "final_ranked_delivery")
        target_paths = {target["path"] for target in ranges}
        loaded_by_path: dict[str, list[dict[str, Any]]] = {}
        for entity in loaded:
            if entity["path"] in target_paths:
                loaded_by_path.setdefault(entity["path"], []).append(
                    {"entity_id": entity["entity_id"], "fusion_rank": entity["fusion_rank"], "evidence": entity["evidence"]}
                )
        final_target_entities = []
        for entity in final:
            if entity["path"] not in target_paths:
                continue
            source = (CORPUS / entity["path"]).read_bytes().decode("utf-8-sig").encode("utf-8")
            source_info = source_fragments_for_entity(entity, source)
            for collection in ("recalled_fragments", "owned_fragments"):
                for fragment in source_info[collection]:
                    fragment["gold_range_overlaps"] = [
                        i + 1
                        for i, target in enumerate(ranges)
                        if target["path"] == entity["path"]
                        and max(fragment["source_lines"][0], target["start_line"]) <= min(fragment["source_lines"][1], target["end_line"])
                    ]
            final_target_entities.append(
                {
                    "rank": entity["rank"],
                    "entity_id": entity["entity_id"],
                    "path": entity["path"],
                    **source_info,
                    "selected_fragment_id": entity["evidence"][0]["fragment_id"] if entity["evidence"] else None,
                }
            )

        labels = score_tasks[task_id]
        task_analysis = {
            "task_id": task_id,
            "question": prompt["question"],
            "outcome_category": gold["outcome_category"],
            "answerable_in_pinned_scope": gold["answerable_in_pinned_cs_scope"],
            "required_ranges": ranges,
            "adjudication": {
                name: {
                    "useful_source": labels["methods"][name]["useful_source"],
                    "complete_support": labels["methods"][name]["complete_support"],
                    **labels["methods"][name]["item_counts"],
                }
                for name in ("r4", "rg", "candidate-c")
            },
            "arms": arms,
            "candidate_c_vs_r4": compare_item_ranges(arms["r4"], arms["candidate_c"]),
            "direct_route_pool_proxies": route_pools,
            "hybrid_internal": {
                "loaded_target_file_candidate_counts": {path: len(entities) for path, entities in loaded_by_path.items()},
                "loaded_target_file_best_fusion_rank": {path: min(e["fusion_rank"] for e in entities) for path, entities in loaded_by_path.items()},
                "loaded_target_file_source_ranges": "not captured for non-final candidates; do not infer fragment-level reachability from path-only joins",
                "final_target_entities": final_target_entities,
            },
        }
        per_task.append(task_analysis)

    manifest = {
        "schema_version": 1,
        "purpose": "post hoc S1/S2 product-reality joins; no new retrieval queries",
        "analysis_script_sha256": digest(Path(__file__).read_bytes()),
        "source_lock_sha256": digest(lock_path.read_bytes()),
        "validation_prompt_sha256": digest((EVAL / "author/validation-prompts.json").read_bytes()),
        "gold_sha256": digest((EVAL / "adjudicator/validation-gold.json").read_bytes()),
        "r4_manifest_sha256": digest((RUNS / "r4/run-manifest.json").read_bytes()),
        "rg_manifest_sha256": digest((RUNS / "rg/run-manifest.json").read_bytes()),
        "candidate_manifest_sha256": digest((RUNS / "candidate-c/candidate-manifest.json").read_bytes()),
        "diagnostic_trace_sha256": digest(trace_path.read_bytes()),
        "limitations": [
            "Adjudication receipt stores per-task item counts but not item IDs/labels; report per-item overlap and source content separately rather than projecting item-level labels from aggregate counts.",
            "Direct FTS/vector top-50 outputs are route-pool proxies; they are not the hybrid's adaptive candidate map.",
            "The diagnostic sidecar captures source ranges only for final ranked entities. For non-final entities it records file path and candidate evidence but no source-range identity join.",
            "Single-shot source delivery is measured; downstream agent navigation or answer correctness was not measured."
        ],
        "tasks": per_task,
    }
    OUTPUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(per_task), "output": str(OUTPUT), "sha256": digest(OUTPUT.read_bytes())}))


if __name__ == "__main__":
    main()
