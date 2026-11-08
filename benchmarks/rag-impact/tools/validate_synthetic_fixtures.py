#!/usr/bin/env python3
"""Validate the D13-D16 synthetic fixture shapes.

This validates fixture structure and expected-outcome metadata, not engine
behavior. R2/R4 must exercise the engine against these fixtures; until then the
engine-behavior gate stays PENDING.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parent.parent
SYNTH = RAG_DIR / "cases" / "synthetic"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fail(message: str) -> None:
    raise SystemExit(f"FIXTURE FAIL: {message}")


def main() -> int:
    checks: dict[str, list[str]] = {}

    # D13: same FQN/method in two assemblies, different bodies.
    d13 = SYNTH / "D13"
    a = read(d13 / "ProjectA/Orders/OrderService.cs")
    b = read(d13 / "ProjectB/Orders/OrderService.cs")
    for text, marker in ((a, "AuditLog.Write"), (b, "OrderHistory.Write")):
        if "namespace Shop.Orders" not in text or "class OrderService" not in text or "void Update()" not in text:
            fail("D13 missing FQN/method")
        if marker not in text:
            fail(f"D13 missing body marker {marker}")
    if "AuditLog.Write" in b or "OrderHistory.Write" in a:
        fail("D13 bodies are not distinct")
    checks["D13"] = ["same FQN/method", "distinct bodies", "distinct assembly names"]

    # D14: reflection plus dynamic SQL target.
    d14 = read(SYNTH / "D14/DynamicTargets.cs")
    for marker in ("Type.GetType", "GetMethod", "Invoke", "ExecuteSqlCommand"):
        if marker not in d14:
            fail(f"D14 missing {marker}")
    if '"UPDATE " + tableName' not in d14:
        fail("D14 dynamic SQL concatenation not found")
    checks["D14"] = ["reflection dispatch", "dynamic SQL target", "candidate/gap expected"]

    # D15: two versions with a shifted coordinate and different hashes.
    v1 = SYNTH / "D15/ProfileService.v1.cs"
    v2 = SYNTH / "D15/ProfileService.v2.cs"
    t1, t2 = read(v1), read(v2)
    if sha256(v1) == sha256(v2):
        fail("D15 versions have equal hashes")
    line1 = next(i for i, line in enumerate(t1.splitlines(), 1) if "current.FullName = name" in line)
    line2 = next(i for i, line in enumerate(t2.splitlines(), 1) if "current.FullName = name" in line)
    if line1 == line2:
        fail("D15 assignment line did not shift")
    checks["D15"] = [f"assignment line {line1} -> {line2}", "distinct hashes", "stale revalidation expected"]

    # D16: cycle, over-cap chain, embedded instruction data.
    ca = read(SYNTH / "D16/CycleA.cs")
    cb = read(SYNTH / "D16/CycleB.cs")
    if "new CycleB().B()" not in ca or "new CycleA().A()" not in cb:
        fail("D16 cycle not present")
    long_chain = read(SYNTH / "D16/LongChain.cs")
    methods = len(re.findall(r"\bM\d+\s*\(", long_chain))
    if methods <= 6:
        fail("D16 long chain does not exceed the hop cap example")
    instructions = read(SYNTH / "D16/Instructions.cs")
    if "IGNORE PREVIOUS INSTRUCTIONS" not in instructions:
        fail("D16 instruction canary missing")
    checks["D16"] = [f"cycle edges present", f"{methods} chain methods", "instruction canary is text data"]

    receipt = {
        "schema_version": 1,
        "status": "FIXTURE_SHAPE_VALID_ENGINE_BEHAVIOR_PENDING_R2",
        "fixtures": {
            case: {
                "checks": values,
                "files": {
                    str(path.relative_to(SYNTH)): {
                        "sha256": sha256(path),
                        "bytes": path.stat().st_size,
                    }
                    for path in sorted((SYNTH / case).rglob("*"))
                    if path.is_file()
                },
            }
            for case in ("D13", "D14", "D15", "D16")
            for values in [checks[case]]
        },
        "engine_behavior": "PENDING_R2_R4",
    }
    output = RAG_DIR / "results" / "synthetic-fixture-validation.json"
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"fixtures OK; receipt: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
