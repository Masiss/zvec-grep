#!/usr/bin/env python3
"""Write SHA-256 receipts and a small provenance manifest for E6."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    files = sorted(path for path in args.receipts.rglob("*") if path.is_file())
    hashes = {str(path.relative_to(args.receipts)): sha256(path) for path in files}
    manifest = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "git_branch": subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "dataset": {
            "path": str(args.dataset),
            "sha256": sha256(args.dataset),
            "manifest": json.loads(
                (args.dataset.parent / "manifest.json").read_text(encoding="utf-8")
            ),
        },
        "models": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(args.model_dir.iterdir())
            if path.is_file()
        },
        "receipts": hashes,
    }
    target = args.output or (args.receipts / "manifest.json")
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    sums = args.receipts / "SHA256SUMS"
    sums.write_text(
        "\n".join(f"{digest}  {name}" for name, digest in hashes.items()) + "\n"
    )
    print(f"wrote {target} and {sums} ({len(hashes)} receipts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
