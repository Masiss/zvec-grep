#!/usr/bin/env python3
"""Freeze or revalidate the RAG B0 corpus manifest.

Read-only over the pilot source. Never writes the source tree, never parses
MSBuild beyond reading Compile Include items as XML data.

Usage:
  python3 benchmarks/rag-impact/tools/freeze_corpus.py --write
  python3 benchmarks/rag-impact/tools/freeze_corpus.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAG_DIR = HERE.parent
MANIFEST_PATH = RAG_DIR / "corpus-manifest.json"
SCOPE_PATH = RAG_DIR / "scope-manifest.json"
MSBUILD_NS = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


def project_compile_set(root: Path, project: dict) -> tuple[dict, list[dict]]:
    csproj = root / project["path"]
    tree = ET.parse(csproj)
    nodes = tree.getroot().findall(".//m:Compile", MSBUILD_NS)
    files: list[dict] = []
    seen: set[str] = set()
    for node in nodes:
        include = node.get("Include", "")
        if not include:
            continue
        if "*" in include or "?" in include:
            raise SystemExit(f"wildcard Compile Include is not supported: {include}")
        rel = os.path.normpath(
            os.path.join(os.path.dirname(project["path"]), include.replace("\\", "/"))
        )
        if rel.startswith("..") or os.path.isabs(rel):
            raise SystemExit(f"Compile Include escapes source root: {include}")
        if rel in seen:
            raise SystemExit(f"duplicate Compile Include in {project['id']}: {rel}")
        seen.add(rel)
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"missing Compile Include: {rel}")
        files.append(
            {
                "path": rel,
                "kind": "cs_compile",
                "project": project["id"],
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    meta = {
        "id": project["id"],
        "path": project["path"],
        "sha256": sha256_file(csproj),
        "compile_items": len(files),
        "bytes": sum(f["bytes"] for f in files),
    }
    return meta, files


def build_manifest() -> dict:
    scope = load_scope()
    root = Path(scope["pilot"]["observed_root"])
    if not root.is_dir():
        raise SystemExit(f"pilot root missing: {root}")

    projects: list[dict] = []
    files: list[dict] = []
    for project in scope["selected_projects"]:
        meta, project_files = project_compile_set(root, project)
        projects.append(meta)
        files.extend(project_files)

    # Project files themselves are part of the frozen searchable scope.
    for project in scope["selected_projects"]:
        path = root / project["path"]
        files.append(
            {
                "path": project["path"],
                "kind": "project_file",
                "project": project["id"],
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    sql_anchors: list[dict] = []
    for anchor in scope["source_anchors"]:
        path = root / anchor["path"]
        if not path.is_file():
            raise SystemExit(f"missing source anchor: {anchor['path']}")
        digest = sha256_file(path)
        if digest != anchor["sha256"]:
            raise SystemExit(f"source anchor hash changed: {anchor['path']}")
        if not anchor["path"].endswith(".sql"):
            # C# anchors are already covered by the project compile sets.
            continue
        sql_anchors.append(
            {
                "id": anchor["id"],
                "path": anchor["path"],
                "sha256": digest,
                "authority": anchor.get("authority", "selected_source_variant"),
                "bytes": path.stat().st_size,
            }
        )
        files.append(
            {
                "path": anchor["path"],
                "kind": "sql_anchor",
                "project": None,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    paths = [f["path"] for f in files]
    if len(paths) != len(set(paths)):
        raise SystemExit("duplicate corpus paths")
    files.sort(key=lambda f: f["path"])

    return {
        "schema_version": 1,
        "status": "FROZEN_FOR_B0",
        "source_root": str(root),
        "source_git_root": scope["pilot"]["observed_git_root"],
        "source_head": scope["pilot"]["observed_git_head"],
        "projects": projects,
        "sql_anchors": sql_anchors,
        "files": files,
        "totals": {
            "files": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "projects": len(projects),
            "sql_anchors": len(sql_anchors),
        },
        "exclusions": [
            "NewSolution (different implementation; not an oracle)",
            "DB/Store/sp_UpdateOrgTotalEmployee_old.sql (unselected variant)",
            "DB/Store/sp_UpdateOrgTotalEmployee_old1.sql (unselected variant)",
            "untracked HRM2.0/Database/ snapshots and generated evidence docs",
            "bin/, obj/, packages/, vendor/generated binaries",
            "files outside the three selected projects and two SQL anchors",
        ],
        "freeze_policy": (
            "Immutable for the B0/candidate experiment. Any corpus change requires a "
            "new experiment ID and a new pre-registration; old receipts remain historical."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()

    manifest = build_manifest()
    if args.write:
        MANIFEST_PATH.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"wrote {MANIFEST_PATH}: {manifest['totals']['files']} files, "
            f"{manifest['totals']['bytes']} bytes"
        )
        return 0

    current = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if current != manifest:
        print("corpus manifest is stale or does not match the source", file=sys.stderr)
        return 1
    print(
        f"corpus manifest OK: {manifest['totals']['files']} files, "
        f"{manifest['totals']['bytes']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
