#!/usr/bin/env python3
"""Capture direct R4 search routes for one frozen task split."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent
DEFAULT_CORPUS = Path("/tmp/zvec-public-source-20261123")
SCRATCH_ROOT = WORKSPACE / ".tmp/indexed-semantic-foundations-20261124"
DEFAULT_CLI = SCRATCH_ROOT / "r4-target-cache/debug/zg"
DEFAULT_ADAPTER = SCRATCH_ROOT / "r4-target-cache/debug/r4-adapter"
DEFAULT_SESSION_ROOT = SCRATCH_ROOT / "sessions"
DEFAULT_TMP_ROOT = SCRATCH_ROOT / "tmp"
DEFAULT_ENGINE_HOME = SCRATCH_ROOT / "engine-home"
EXCLUSION_SENTINEL = "__zvec_eval_no_exclusion__"
SOURCE_BYTE_BUDGET = 24_576
MAX_ITEMS = 10


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_tasks(path: Path) -> tuple[str, list[dict[str, object]]]:
    document = json.loads(path.read_text())
    tasks = document["tasks"]
    return document.get("corpus_id", "public-csharp-source-20261123-v1"), tasks


def source_bytes(item: dict[str, object]) -> int:
    offsets = item.get("raw_file_byte_offsets")
    if isinstance(offsets, list) and len(offsets) == 2 and all(isinstance(value, int) for value in offsets):
        return max(0, offsets[1] - offsets[0])
    return len(str(item.get("content", "")).encode("utf-8"))


def command(
    args: list[str], env: dict[str, str], timing_file: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    timed = [
        "/usr/bin/time",
        "-f",
        "%e\t%M",
        "-o",
        str(timing_file.resolve()),
        *args,
    ]
    return subprocess.run(timed, check=True, capture_output=True, text=True, env=env, cwd=cwd)


def deliverable(result: dict[str, object]) -> dict[str, object]:
    items = result.get("items", [])
    items = sorted(items, key=lambda item: int(item.get("rank", 0)))
    selected: list[dict[str, object]] = []
    used = 0
    for item in items:
        content_bytes = source_bytes(item)
        if len(selected) >= MAX_ITEMS:
            break
        if used + content_bytes > SOURCE_BYTE_BUDGET:
            continue
        selected.append(item)
        used += content_bytes
    return {
        "items": selected,
        "item_count": len(selected),
        "source_content_bytes": used,
        "source_content_byte_budget": SOURCE_BYTE_BUDGET,
        "item_budget": MAX_ITEMS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True, help="development.json or validation-prompts.json")
    parser.add_argument("--output", type=Path, required=True, help="new durable output directory")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSION_ROOT)
    parser.add_argument("--quiet", action="store_true", help="print only completion and manifest digest")
    args = parser.parse_args()

    if args.output.exists() and any(args.output.iterdir()):
        parser.error(f"output directory must be absent or empty: {args.output}")
    if not args.corpus.is_dir() or not args.adapter.is_file() or not args.cli.is_file():
        parser.error("corpus, CLI, or adapter path does not exist")
    if EXCLUSION_SENTINEL in str(args.corpus):
        parser.error("exclusion sentinel must not occur in the corpus root")
    if any(EXCLUSION_SENTINEL in p.relative_to(args.corpus).as_posix() for p in args.corpus.rglob("*.cs")):
        parser.error("exclusion sentinel occurs in a corpus path")

    corpus_id, tasks = read_tasks(args.tasks)
    args.output.mkdir(parents=True, exist_ok=True)
    args.session_root.mkdir(parents=True, exist_ok=True)
    DEFAULT_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    DEFAULT_ENGINE_HOME.mkdir(parents=True, exist_ok=True)
    runtime_dir = args.adapter.parent
    env = os.environ.copy()
    env.update(
        {
            "TMPDIR": str(DEFAULT_TMP_ROOT),
            "LD_LIBRARY_PATH": str(runtime_dir),
            "ORT_DYLIB_PATH": str(runtime_dir / "libonnxruntime.so.1"),
            "ZVEC_GREP_HOME": str(DEFAULT_ENGINE_HOME),
            "ZVEC_GREP_MODEL_CACHE": "/home/masis/.cache/zvec-grep/e3-01/model-cache",
            "ZVEC_GREP_DEVICE": "cpu",
            "CARGO_NET_OFFLINE": "true",
        }
    )

    summary: list[dict[str, object]] = []
    for task in tasks:
        task_id = str(task["task_id"])
        query = str(task["question"])
        task_dir = args.output / task_id
        task_dir.mkdir()
        session_dir = args.session_root / f"{task_id}-hybrid"
        if session_dir.exists():
            raise FileExistsError(f"refusing to reuse an existing session: {session_dir}")
        created = subprocess.run(
            [
                str(args.adapter),
                "session",
                "new",
                "--dir",
                str(session_dir),
                "--root",
                str(args.corpus),
                "--profile",
                "arm-a",
                "--case",
                task_id,
                "--exclude",
                EXCLUSION_SENTINEL,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        session_meta = json.loads(created.stdout)
        timing_file = task_dir / "hybrid.time"
        result = command(
            [
                str(args.adapter),
                "call",
                str(session_dir),
                "search",
                "--query",
                query,
                "--mode",
                "hybrid",
                "--limit",
                str(MAX_ITEMS),
                "--round",
                "1",
            ],
            env,
            timing_file,
        )
        raw = json.loads(result.stdout)
        raw_bytes = result.stdout.encode("utf-8")
        (task_dir / "hybrid.json").write_bytes(raw_bytes)
        (task_dir / "hybrid.stderr.txt").write_text(result.stderr)
        timing_fields = timing_file.read_text().strip().split("\t")
        engine_result = raw.get("result", {})
        items = engine_result.get("items", [])
        item_bytes = sum(source_bytes(item) for item in items)
        capped = deliverable(engine_result)
        delivered_bytes = json.dumps(capped, ensure_ascii=False).encode("utf-8")
        (task_dir / "hybrid-delivered.json").write_bytes(delivered_bytes)
        summary.append(
            {
                "task_id": task_id,
                "mode": "hybrid",
                "limit": MAX_ITEMS,
                "session_id": session_meta.get("session_id"),
                "raw_sha256": digest(raw_bytes),
                "raw_serialized_bytes": len(raw_bytes),
                "result_item_count": len(items),
                "result_source_content_bytes": item_bytes,
                "result_gap_count": len(engine_result.get("gaps", [])),
                "elapsed_seconds": float(timing_fields[0]),
                "peak_rss_kb": int(timing_fields[1]),
                "delivered_sha256": digest(delivered_bytes),
                "delivered_item_count": capped["item_count"],
                "delivered_source_content_bytes": capped["source_content_bytes"],
            }
        )

        for mode in ("fts", "vector"):
            timing_file = task_dir / f"{mode}-pool.time"
            route = command(
                [
                    str(args.cli),
                    f"--{mode}",
                    query,
                    "--limit",
                    "50",
                    "--preview",
                    "detail",
                    "--trace",
                    "--mode",
                    "direct",
                    "--color",
                    "never",
                    "--glob",
                    "*.cs",
                ],
                env,
                timing_file,
                cwd=args.corpus,
            )
            route_bytes = route.stdout.encode("utf-8")
            (task_dir / f"{mode}-pool.txt").write_bytes(route_bytes)
            (task_dir / f"{mode}-pool.stderr.txt").write_text(route.stderr)
            timing_fields = timing_file.read_text().strip().split("\t")
            summary.append(
                {
                    "task_id": task_id,
                    "mode": f"{mode}_route_pool",
                    "limit": 50,
                    "raw_sha256": digest(route_bytes),
                    "raw_serialized_bytes": len(route_bytes),
                    "elapsed_seconds": float(timing_fields[0]),
                    "peak_rss_kb": int(timing_fields[1]),
                }
            )

    manifest = {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "task_input_sha256": digest(args.tasks.read_bytes()),
        "corpus_root": str(args.corpus),
        "source_lock_sha256": digest((EVAL_ROOT / "source.lock.json").read_bytes()),
        "index_manifest_sha256": digest((args.corpus / ".zvec-grep" / "manifest.json").read_bytes()),
        "index_generation": json.loads((args.corpus / ".zvec-grep" / "manifest.json").read_text()).get("storageGeneration"),
        "index_version": json.loads((args.corpus / ".zvec-grep" / "manifest.json").read_text()).get("indexVersion"),
        "cli_sha256": digest(args.cli.read_bytes()),
        "adapter_sha256": digest(args.adapter.read_bytes()),
        "excluded_path_marker": EXCLUSION_SENTINEL,
        "routes": ["hybrid adapter limit 10", "direct FTS CLI route pool limit 50", "direct vector CLI route pool limit 50"],
        "budgets": {"items": MAX_ITEMS, "source_content_bytes": SOURCE_BYTE_BUDGET},
        "source_byte_accounting": "raw_file_byte_offsets; UTF-8 content-byte fallback only when offsets are unavailable",
        "runs": summary,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_path = args.output / "run-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_digest = digest(manifest_bytes)
    if args.quiet:
        print(json.dumps({"tasks": len(tasks), "runs": len(summary), "manifest_sha256": manifest_digest}))
    else:
        print(json.dumps({"tasks": len(tasks), "runs": len(summary), "manifest_sha256": manifest_digest, "summary": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
