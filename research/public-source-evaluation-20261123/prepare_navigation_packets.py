#!/usr/bin/env python3
"""Create blinded navigator input packets from the frozen R4 and rg deliveries."""

import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs/validation-final-20261123"
OUT = ROOT / "navigation-diagnostic-20261123"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    prompts = json.loads((ROOT / "author/validation-prompts.json").read_text())['tasks']
    units = []
    key = []
    rng = random.Random(20261123)
    unit_no = 0
    for task in prompts:
        arms = [
            ("r4", RUNS / "r4" / task["task_id"] / "hybrid-delivered.json"),
            ("rg", RUNS / "rg" / task["task_id"] / "rg-delivered.json"),
        ]
        labels = ["amber", "violet"]
        rng.shuffle(labels)
        for (method, path), label in zip(arms, labels):
            unit_no += 1
            packet_path = OUT / "packets" / f"unit-{unit_no:02d}.json"
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            delivered = json.loads(path.read_text())
            packet = {
                "unit": f"unit-{unit_no:02d}",
                "blind_label": label,
                "question": task["question"],
                "starting_pack_source_bytes": delivered["source_content_bytes"],
                "items": [
                    {
                        "rank": item["rank"],
                        "path": item["path"],
                        "content_range": item.get("content_range"),
                        "content": item["content"],
                    }
                    for item in delivered["items"]
                ],
            }
            packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
            units.append({"unit": packet["unit"], "packet_sha256": sha(packet_path), "packet_bytes": packet_path.stat().st_size})
            key.append({"unit": packet["unit"], "task_id": task["task_id"], "blind_label": label, "method": method, "source_delivery": path.relative_to(ROOT).as_posix(), "source_delivery_sha256": sha(path)})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "unit-manifest.json").write_text(json.dumps({"protocol": "navigation-diagnostic-preregistration-20261123.md", "units": units}, indent=2) + "\n")
    (OUT / "sealed-unit-key.json").write_text(json.dumps({"status": "do not reveal until navigator/adjudicator labels are sealed", "units": key}, indent=2) + "\n")
    print(json.dumps({"units": len(units), "manifest_sha256": sha(OUT / "unit-manifest.json"), "key_sha256": sha(OUT / "sealed-unit-key.json")}))


if __name__ == "__main__":
    main()
