#!/usr/bin/env python3
"""Generate the deterministic Index/Finalize V1 fixture and manifest."""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import shutil
SEED = 20261101
FILES = 2000
SIZE = 8192
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--manifest", required=True, type=pathlib.Path)
    ap.add_argument("--copies", type=int, default=1)
    a = ap.parse_args()
    root = a.out.resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = root / "ws_1"
    for chunk in range(20):
        d = base / "src" / f"chunk_{chunk:02d}"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(100):
            ordinal = chunk * 100 + i
            body = (f"// seed={SEED} ordinal={ordinal:04d}\n"
                    f"pub fn term_{ordinal:04d}() -> usize {{ {ordinal} }}\n"
                    f"pub const UNIQUE_{ordinal:04d}: &str = \"finalize_{ordinal:04d}\";\n")
            body = (body * ((SIZE + len(body) - 1) // len(body)))[:SIZE]
            (d / f"file_{ordinal:04d}.rs").write_text(body, encoding="utf-8")
    entries = []
    for p in sorted(base.rglob("*.rs")):
        b = p.read_bytes()
        entries.append({"path": p.relative_to(root).as_posix(), "size": len(b), "sha256": hashlib.sha256(b).hexdigest()})
    manifest = {
        "protocol_id": "index-finalize-qualification-v1",
        "seed": SEED,
        "files_per_workspace": FILES,
        "file_size_bytes": SIZE,
        "copies": a.copies,
        "entries": entries,
        "entries_sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(),
        "query_terms": [f"finalize_{i:04d}" for i in range(0, FILES, 20)],
        "delete_ordinals": list(range(0, FILES, 10)),
    }
    for n in range(2, a.copies + 1):
        dst = root / f"ws_{n}"
        if dst.exists():
            try:
                shutil.rmtree(dst)
            except OSError as error:
                raise SystemExit(f"failed to remove {dst}: {error}") from error
        shutil.copytree(base, dst)
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    a.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"root": str(root), "entries": len(entries), "entries_sha256": manifest["entries_sha256"]}))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
