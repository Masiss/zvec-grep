#!/usr/bin/env python3
"""Apply frozen bounded-neighbor policy C to development baseline packs."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from pathlib import Path
from typing import Any

MAX_ITEMS = 10
MAX_SOURCE_BYTES = 24_576


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def byte_range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, dict) and isinstance(value.get("byte"), dict):
        item = value["byte"]
        start, end = item.get("start_offset"), item.get("end_offset")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            return start, end
    return None


def entity_source_range(value: Any, decoded_size: int) -> tuple[int, int] | None:
    if value == "full":
        return 0, decoded_size
    if isinstance(value, dict) and isinstance(value.get("text"), dict):
        item = value["text"]
        start = (item.get("start") or {}).get("byte_offset")
        end = (item.get("end") or {}).get("byte_offset")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= decoded_size:
            return start, end
    if isinstance(value, dict) and isinstance(value.get("byte"), dict):
        item = value["byte"]
        start, end = item.get("start_offset"), item.get("end_offset")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= decoded_size:
            return start, end
    return None


def aligned_interval(interval: tuple[int, int], line_starts: list[int], text_size: int) -> tuple[int, int]:
    start, end = interval
    start_line = max(0, bisect.bisect_right(line_starts, start) - 1)
    end_index = bisect.bisect_left(line_starts, end)
    aligned_start = line_starts[start_line]
    aligned_end = line_starts[end_index] if end_index < len(line_starts) else text_size
    return aligned_start, max(aligned_end, end)


def output_coordinates(start: int, end: int, line_starts: list[int]) -> tuple[list[int], list[int]]:
    start_index = max(0, bisect.bisect_right(line_starts, start) - 1)
    end_index = max(0, bisect.bisect_right(line_starts, end) - 1)
    start_line = start_index + 1
    end_line = end_index + 1
    start_column = start - line_starts[start_index]
    end_column = end - line_starts[end_index]
    inclusive_end_line = end_line if end_column else max(start_line, end_line - 1)
    return [start_line, inclusive_end_line], [start_column, end_column]


def neighbors(entity: dict[str, Any], fragment_id: str) -> list[tuple[str, tuple[int, int]]]:
    try:
        ordinal = int(fragment_id[-8:], 16)
    except (ValueError, TypeError):
        return []
    owned = entity.get("owned_fragments", [])
    by_ordinal: dict[int, tuple[str, tuple[int, int]]] = {}
    for fragment in owned:
        fid = fragment.get("fragment_id")
        if not isinstance(fid, str) or not fid.startswith(entity["entity_id"]):
            continue
        try:
            number = int(fid[-8:], 16)
        except ValueError:
            continue
        interval = byte_range(fragment.get("fragment_range"))
        if interval is not None:
            by_ordinal[number] = (fid, interval)
    return [by_ordinal[n] for n in (ordinal - 1, ordinal + 1) if n in by_ordinal]


def hybrid_trace_by_query(trace_path: Path) -> dict[str, dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for line in trace_path.read_text().splitlines():
        record = json.loads(line)
        if record.get("phase") == "search_start":
            groups.append([record])
        elif groups:
            groups[-1].append(record)
    out: dict[str, dict[str, Any]] = {}
    for group in groups:
        start = group[0]["payload"]
        routes = start.get("routes", [])
        modes = {route.get("mode") for route in routes}
        if len(routes) != 2 or modes != {"Fts", "Vector"}:
            continue
        queries = {route.get("query") for route in routes}
        if len(queries) != 1:
            raise ValueError("hybrid routes do not share one query")
        final = next((record["payload"] for record in group if record.get("phase") == "final_ranked_delivery"), None)
        if final is None:
            raise ValueError("hybrid diagnostic group is missing final delivery")
        query = next(iter(queries))
        if query in out:
            raise ValueError(f"duplicate hybrid diagnostic query: {query}")
        out[query] = final
    return out


def expand_task(
    task: dict[str, Any],
    baseline_path: Path,
    final_trace: dict[str, Any],
    corpus: Path,
    locked_files: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline = json.loads(baseline_path.read_text())
    if len(baseline.get("items", [])) > MAX_ITEMS:
        raise ValueError(f"baseline exceeds item budget: {task['task_id']}")
    if int(baseline.get("source_content_bytes", -1)) > MAX_SOURCE_BYTES:
        raise ValueError(f"baseline exceeds byte budget: {task['task_id']}")
    entities = final_trace.get("entities", [])
    by_rank_path = {(item.get("rank"), item.get("path")): item for item in baseline["items"]}
    used = sum(int(item["raw_file_byte_offsets"][1]) - int(item["raw_file_byte_offsets"][0]) for item in baseline["items"])
    if used != int(baseline["source_content_bytes"]):
        raise ValueError(f"baseline byte accounting mismatch: {task['task_id']}")

    verified_sources: dict[tuple[int, str], tuple[bytes, bool, list[int]]] = {}
    for item in baseline["items"]:
        relative_path = Path(str(item["path"]))
        source_path = (corpus / relative_path).resolve()
        if not source_path.is_relative_to(corpus.resolve()):
            raise ValueError(f"source path escapes corpus: {item['path']}")
        raw = source_path.read_bytes()
        lock_entry = locked_files.get(str(relative_path))
        if lock_entry is None or lock_entry.get("sha256") != sha256(raw) or lock_entry.get("bytes") != len(raw):
            raise ValueError(f"source differs from frozen lock: {item['path']}")
        if item.get("possibly_stale") or item.get("sha256") != sha256(raw):
            raise ValueError(f"baseline source snapshot is not fresh: {item['path']}")
        if item.get("file_encoding") not in {"utf-8", "utf-8-bom"}:
            raise ValueError(f"unsupported encoding for neighbor experiment: {item['path']}")
        bom = raw.startswith(b"\xef\xbb\xbf")
        if (item["file_encoding"] == "utf-8-bom") != bom:
            raise ValueError(f"encoding label does not match locked source: {item['path']}")
        decoded = raw.decode("utf-8-sig" if bom else "utf-8").encode("utf-8")
        line_starts = [0] + [index + 1 for index, value in enumerate(decoded) if value == 10]
        offsets = item.get("content_byte_offsets")
        raw_offsets = item.get("raw_file_byte_offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != 2
            or not all(isinstance(value, int) for value in offsets)
            or not 0 <= offsets[0] <= offsets[1] <= len(decoded)
        ):
            raise ValueError(f"invalid baseline decoded offsets: {item['path']}")
        expected_raw = [offsets[0] + (3 if bom else 0), offsets[1] + (3 if bom else 0)]
        if raw_offsets != expected_raw:
            raise ValueError(f"invalid baseline raw offsets: {item['path']}")
        content = decoded[offsets[0] : offsets[1]]
        content_range, columns = output_coordinates(offsets[0], offsets[1], line_starts)
        if item.get("content") != content.decode("utf-8") or item.get("content_sha256") != sha256(content):
            raise ValueError(f"baseline content or content hash fails source verification: {item['path']}")
        if item.get("content_range") != content_range or item.get("content_columns") != columns:
            raise ValueError(f"baseline line coordinates fail source verification: {item['path']}")
        verified_sources[(int(item["rank"]), str(item["path"]))] = (decoded, bom, line_starts)

    changes: list[dict[str, Any]] = []
    output_items = [json.loads(json.dumps(item)) for item in baseline["items"]]
    for entity in entities:
        key = (entity.get("rank"), entity.get("path"))
        item = by_rank_path.get(key)
        if item is None:
            continue
        rank = int(item["rank"])
        candidate = next((copy for copy in output_items if copy["rank"] == rank and copy["path"] == item["path"]), None)
        if candidate is None:
            raise ValueError("baseline item mapping failed")

        decoded, bom, line_starts = verified_sources[(rank, str(item["path"]))]

        evidence = entity.get("evidence", [])
        if not evidence or not isinstance(evidence[0].get("fragment_id"), str):
            continue
        base_id = evidence[0]["fragment_id"]
        local = byte_range(evidence[0].get("fragment_range"))
        source_entity = entity_source_range(entity.get("entity_source_range"), len(decoded))
        if local is None or source_entity is None:
            continue
        entity_start, entity_end = source_entity
        entity_content = decoded[entity_start:entity_end]
        entity_line_starts = [0] + [index + 1 for index, value in enumerate(entity_content) if value == 10]
        aligned_base = aligned_interval(local, entity_line_starts, len(entity_content))
        current = (entity_start + aligned_base[0], entity_start + aligned_base[1])
        original_decoded = item.get("content_byte_offsets")
        if not isinstance(original_decoded, list) or tuple(original_decoded) != current:
            raise ValueError(f"selected fragment does not match baseline range for {item['path']} rank {rank}")
        raw_offsets = [current[0] + (3 if bom else 0), current[1] + (3 if bom else 0)]
        if item.get("raw_file_byte_offsets") != raw_offsets:
            raise ValueError(f"raw source offsets do not match baseline range for {item['path']} rank {rank}")
        if candidate["content"].encode("utf-8") != decoded[current[0] : current[1]]:
            raise ValueError(f"baseline content differs from verified source slice: {item['path']} rank {rank}")

        source_interval = local
        before = current
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for neighbor_id, neighbor_local in neighbors(entity, base_id):
            proposed_source = neighbor_local
            if proposed_source[0] > source_interval[1] or proposed_source[1] < source_interval[0]:
                rejected.append({"fragment_id": neighbor_id, "reason": "noncontiguous_source"})
                continue
            proposed_source = (min(source_interval[0], proposed_source[0]), max(source_interval[1], proposed_source[1]))
            aligned_local = aligned_interval(proposed_source, entity_line_starts, len(entity_content))
            proposed = (entity_start + aligned_local[0], entity_start + aligned_local[1])
            if proposed[0] < entity_start or proposed[1] > entity_end:
                rejected.append({"fragment_id": neighbor_id, "reason": "outside_entity_source_range"})
                continue
            increment = (proposed[1] - proposed[0]) - (current[1] - current[0])
            if increment < 0:
                raise ValueError("neighbor expansion unexpectedly shrank the range")
            if used + increment > MAX_SOURCE_BYTES:
                rejected.append({"fragment_id": neighbor_id, "reason": "byte_budget"})
                continue
            current = proposed
            source_interval = proposed_source
            used += increment
            accepted.append({"fragment_id": neighbor_id, "incremental_source_bytes": increment})

        if current != before:
            content = decoded[current[0] : current[1]]
            content_range, columns = output_coordinates(current[0], current[1], line_starts)
            candidate["content"] = content.decode("utf-8")
            candidate["content_range"] = content_range
            candidate["content_columns"] = columns
            candidate["content_byte_offsets"] = [current[0], current[1]]
            candidate["raw_file_byte_offsets"] = [current[0] + (3 if bom else 0), current[1] + (3 if bom else 0)]
            candidate["content_sha256"] = sha256(content)
        changes.append({
            "rank": rank,
            "path": item["path"],
            "entity_id": entity["entity_id"],
            "base_fragment_id": base_id,
            "baseline_decoded_byte_offsets": list(before),
            "candidate_decoded_byte_offsets": list(current),
            "accepted_neighbors": accepted,
            "rejected_neighbors": rejected,
        })

    candidate_pack = {
        **baseline,
        "items": sorted(output_items, key=lambda item: int(item["rank"])),
        "source_content_bytes": used,
    }
    if len(candidate_pack["items"]) > MAX_ITEMS or used > MAX_SOURCE_BYTES:
        raise ValueError(f"candidate pack exceeds frozen budget: {task['task_id']}")
    for item in candidate_pack["items"]:
        source_path = (corpus / item["path"]).resolve()
        raw = source_path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig" if bom else "utf-8").encode("utf-8")
        start, end = item["content_byte_offsets"]
        if item["content"].encode("utf-8") != decoded[start:end]:
            raise ValueError(f"candidate item fails source-slice integrity: {item['path']}")
        if item["content_sha256"] != sha256(decoded[start:end]):
            raise ValueError(f"candidate content hash mismatch: {item['path']}")
        if item["sha256"] != sha256(raw):
            raise ValueError(f"candidate full-file hash mismatch: {item['path']}")
    actual_bytes = sum(
        int(item["raw_file_byte_offsets"][1]) - int(item["raw_file_byte_offsets"][0])
        for item in candidate_pack["items"]
    )
    if actual_bytes != used:
        raise ValueError(f"candidate byte accounting mismatch: {task['task_id']}")
    baseline_by_key = {(item["rank"], item["path"]): item for item in baseline["items"]}
    for item in candidate_pack["items"]:
        original = baseline_by_key.get((item["rank"], item["path"]))
        if original is None:
            raise ValueError(f"candidate displaced or reordered baseline evidence: {task['task_id']}")
        old_start, old_end = original["content_byte_offsets"]
        new_start, new_end = item["content_byte_offsets"]
        if new_start > old_start or new_end < old_end:
            raise ValueError(f"candidate range does not preserve baseline interval: {task['task_id']}")
    if len(candidate_pack["items"]) != len(baseline["items"]):
        raise ValueError(f"candidate changed baseline item count: {task['task_id']}")
    return candidate_pack, {"task_id": task["task_id"], "accepted_or_considered_items": changes, "source_content_bytes": used}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True, help="development task split only")
    parser.add_argument("--baseline-root", type=Path, required=True, help="directory of hybrid-delivered.json task packs")
    parser.add_argument("--trace", type=Path, required=True, help="diagnostic sidecar from the same queries")
    parser.add_argument("--corpus", type=Path, required=True, help="verified materialized source corpus")
    parser.add_argument("--output", type=Path, required=True, help="new candidate output directory")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"output directory must be absent or empty: {args.output}")
    if not args.corpus.is_dir():
        parser.error(f"corpus does not exist: {args.corpus}")
    args.output.mkdir(parents=True, exist_ok=True)
    task_doc = json.loads(args.tasks.read_text())
    source_lock_path = Path(__file__).with_name("source.lock.json")
    source_lock = json.loads(source_lock_path.read_text())
    locked_files = {
        str(file["path"]): file
        for repository in source_lock["repositories"]
        for file in repository["files"]
    }
    trace_map = hybrid_trace_by_query(args.trace)
    receipts = []
    for task in task_doc["tasks"]:
        query = task["question"]
        if query not in trace_map:
            raise ValueError(f"missing diagnostic hybrid group for {task['task_id']}")
        baseline_path = args.baseline_root / task["task_id"] / "hybrid-delivered.json"
        if not baseline_path.is_file():
            raise ValueError(f"missing baseline pack for {task['task_id']}")
        pack, receipt = expand_task(task, baseline_path, trace_map[query], args.corpus, locked_files)
        task_dir = args.output / task["task_id"]
        task_dir.mkdir()
        (task_dir / "candidate-delivered.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n")
        (task_dir / "candidate-expansion.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        receipts.append(receipt)
    manifest = {
        "schema_version": 1,
        "policy": "C bounded source-neighbor expansion",
        "task_input_sha256": sha256(args.tasks.read_bytes()),
        "source_lock_sha256": sha256(source_lock_path.read_bytes()),
        "trace_sha256": sha256(args.trace.read_bytes()),
        "budgets": {"items": MAX_ITEMS, "source_content_bytes": MAX_SOURCE_BYTES},
        "ordering": "baseline pack fixed; items in rank order; previous ordinal before next ordinal",
        "expansion": "one hop to immediate indexed neighbor ordinals; overlap/touch required; merged source range only",
        "budget_behavior": "never displace baseline items; skip widening when exact incremental bytes exceed remaining budget",
        "integrity": "fresh full-file SHA checked; source slices read without normalization; content SHA and byte/line coordinates recomputed",
        "tasks": receipts,
    }
    manifest_path = args.output / "candidate-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"tasks": len(receipts), "manifest_sha256": sha256(manifest_path.read_bytes())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
