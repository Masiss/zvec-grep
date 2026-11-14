#!/usr/bin/env python3
"""Independent E8-P2 integration verifier.

Reads Git history, source text, raw harness receipts and frozen E6/E8 reports.
It does not import the engine or trust implementation test assertions.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        fail(f"cannot read {path}: {error}")


def run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def is_ancestor(repo: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        check=False,
    )
    return result.returncode == 0


def check_provenance(repo: Path) -> dict:
    required = [
        "986ab51", "e9a9824", "928f49f", "894b71e",
        "63e6b2d", "6536b00", "6aa7385", "ca04bb0", "24af3e6",
        "0b1c667", "e93b5db", "d1e98b7", "5744704",
        "7013a47", "9c1306d", "b565472", "b8a33b5", "7b0aab8",
    ]
    missing = [commit for commit in required if not is_ancestor(repo, commit)]
    if missing:
        fail(f"missing required commits: {missing}")
    source = "\n".join(
        path.read_text(errors="replace")
        for path in (repo / "rust/crates/zg-engine/src").rglob("*.rs")
    )
    if "ZVEC_GREP_E7_" in source:
        fail("E7 experiment environment hooks leak into production source")
    if "F2_" in source or "R0_SELECTIVE" in source:
        fail("research-only policy hook leaks into production source")
    return {"required_commits": len(required), "e7_production_hooks": "absent"}


def check_manifest(path: Path, device: str | None) -> dict:
    data = load(path)
    descriptor = data.get("index", {}).get("descriptor", {})
    configs = descriptor.get("embeddingConfigs", {})
    devices = sorted(
        {config.get("device") for config in configs.values() if config.get("device")}
    )
    if device is None:
        if devices:
            fail(f"{path.name}: expected no concrete device, got {devices}")
    elif devices != [device]:
        fail(f"{path.name}: expected device {device}, got {devices}")
    return {"device": devices, "index_version": data.get("indexVersion")}


def check_query_contract(payload: dict) -> bool:
    run = payload.get("run", {})
    contract = run.get("measurement_contract", {})
    intended = contract.get("intended_route")
    actual = contract.get("actual", {}).get("route_modes")
    expected = contract.get("expected", {})
    return (
        contract.get("status") == "PASS"
        and intended in {"fts", "vector", "hybrid", "hybrid-symbol"}
        and isinstance(actual, list)
        and intended == "hybrid"
        and actual == ["fts", "vector"]
        and expected.get("group_count") == 1
        and contract.get("actual", {}).get("group_count") == 1
        and contract.get("actual", {}).get("duplicate_routes") == 0
    )


def check_query_receipts(receipts: Path) -> dict:
    names = [
        "p2-query-cs40-cpu",
        "p2-query-cs40-vulkan",
        "p2-query-vk40-cpu",
        "p2-query-vk40-vulkan",
    ]
    contracts = {}
    for name in names:
        payload = load(receipts / f"{name}.json")
        if not check_query_contract(payload):
            fail(f"{name}: E6 effective-route contract failed")
        contracts[name] = payload["run"]["measurement_contract"]["actual"]["route_modes"]
    routes = {}
    for route in ["fts", "vector", "hybrid", "hybrid-symbol"]:
        payload = load(receipts / f"p2-route-{route}.json")
        contract = payload["run"]["measurement_contract"]
        if contract.get("status") != "PASS" or contract.get("intended_route") != route:
            fail(f"route {route}: contract mismatch")
        routes[route] = contract["actual"]["route_modes"]
    return {"cross_device_contracts": contracts, "route_contracts": routes}


def timing_counts(payload: dict) -> dict:
    return {entry["name"]: entry["count"] for entry in payload.get("timings", [])}


def check_workload(receipts: Path, manifests: Path) -> dict:
    cpu = load(receipts / "p2-cpu-cold.json")
    vk = load(receipts / "p2-vk-cold.json")
    noop_cpu = load(receipts / "p2-cpu-noop.json")
    noop_vk = load(receipts / "p2-vk-noop.json")
    inc = load(receipts / "p2-cpu-inc.json")
    shift = load(receipts / "p2-cpu-shift.json")
    if cpu.get("files_added") != 40 or vk.get("files_added") != 40:
        fail("cold corpus cardinality is not 40 files in both arms")
    if noop_cpu.get("files_added") or noop_cpu.get("files_modified"):
        fail("CPU no-op performed work")
    if noop_vk.get("files_added") or noop_vk.get("files_modified"):
        fail("Vulkan no-op performed work")
    if inc.get("files_modified") != 1 or shift.get("files_modified") != 1:
        fail("incremental edit was not observed exactly once")
    cold = timing_counts(cpu)
    inc_counts = timing_counts(inc)
    shift_counts = timing_counts(shift)
    if cold.get("index_reconcile_embeddings_computed", 0) <= 0:
        fail("cold CPU pass did not compute embeddings")
    if cold.get("index_embedding_requests", 0) < cold.get("index_embedding_unique_inputs", 0):
        fail("same-pass dedup counters are internally inconsistent")
    if inc_counts.get("index_reconcile_embeddings_computed", 99) > 1:
        fail("CPU incremental pass recomputed more than one fragment")
    if inc_counts.get("index_reconcile_vector_fetch", 0) < 1:
        fail("CPU incremental pass did not fetch reusable vectors")
    if shift_counts.get("index_reconcile_vector_fetch", 0) < 1:
        fail("line-shift pass did not fetch reusable vectors")
    recovered = load(receipts / "p2-crash2-recovered.json")
    if recovered.get("files_added") != 3 or recovered.get("files_failed") != 0:
        fail("crash recovery did not publish the three-file workload")
    if not (receipts / "p2-crash2-killed.log").exists():
        fail("killed-process receipt is missing")
    cpu_noop_log = (receipts / "p2-cpu-noop.log").read_text()
    vk_noop_log = (receipts / "p2-vk-noop.log").read_text()
    cpu_log = (receipts / "p2-cpu-cold.log").read_text()
    vk_log = (receipts / "p2-vk-cold.log").read_text()
    if "indexed files=40 added=0 modified=0 deleted=0" not in cpu_noop_log:
        fail("CPU no-op log does not prove zero work")
    if "indexed files=40 added=0 modified=0 deleted=0" not in vk_noop_log:
        fail("Vulkan no-op log does not prove zero work")
    if "wall=" not in cpu_log or "wall=" not in vk_log:
        fail("cold timing receipts missing")
    cpu_device = check_manifest(manifests / "cs40.json", "cpu")
    vk_device = check_manifest(manifests / "vk40.json", "vulkan")
    reloc_device = check_manifest(manifests / "reloc-cpu.json", "cpu")
    legacy_device = check_manifest(manifests / "legacy-auto2.json", "auto")
    return {
        "cold_files": {"cpu": cpu["files_added"], "vulkan": vk["files_added"]},
        "noop_files": {"cpu": 0, "vulkan": 0},
        "incremental_computed": inc_counts.get("index_reconcile_embeddings_computed"),
        "incremental_reused": inc_counts.get("index_reconcile_vector_fetch"),
        "shift_reused": shift_counts.get("index_reconcile_vector_fetch"),
        "same_pass_requests": cold.get("index_embedding_requests"),
        "same_pass_unique": cold.get("index_embedding_unique_inputs"),
        "devices": {"cpu": cpu_device, "vulkan": vk_device, "relocated": reloc_device, "legacy": legacy_device},
    }


def check_consistency(receipts: Path) -> dict:
    def shape(payload: dict) -> list:
        return [
            (
                row.get("query_id"),
                [
                    (item.get("relative_path"), item.get("start_line"), item.get("end_line"))
                    for item in row.get("items", [])
                ],
            )
            for row in payload.get("results", [])
        ]

    fresh = load(receipts / "p2-cons-fresh-query.json")
    incremental = load(receipts / "p2-cons-inc-query.json")
    if shape(fresh) != shape(incremental):
        fail("fresh and incremental query result shapes differ")
    fresh_index = load(receipts / "p2-cons-fresh-index.json")
    inc_index = load(receipts / "p2-cons-inc-index.json")
    if fresh_index.get("files_added") != 3 or inc_index.get("files_modified") != 1:
        fail("fresh/incremental index cardinality mismatch")
    return {"result_shape_equal": True, "fresh_files": 3, "incremental_modified": 1}


def check_guards(receipts: Path) -> dict:
    checks = {
        "vk_incremental_rejected": "accelerator-backed indexes are not supported",
        "cross_noop_rejected": "different embedding device",
        "relocation_rejected": "different embedding device",
        "unavailable_rejected": "refusing CPU fallback",
        "auto_new_rejected": "Auto device cannot create",
    }
    for name, needle in checks.items():
        path = receipts / (
            {
                "vk_incremental_rejected": "p2-vk-inc.log",
                "cross_noop_rejected": "p2-cross-noop.log",
                "legacy_incremental_rejected": "p2-legacy-cli-inc.log",
                "relocation_rejected": "p2-reloc-cross.log",
                "unavailable_rejected": "p2-unavailable.log",
                "auto_new_rejected": "p2-auto-new.log",
            }[name]
        )
        if not path.exists():
            fail(f"negative guard receipt missing: {name}")
        text = path.read_text(errors="replace")
        if needle not in text:
            fail(f"negative guard receipt missing expected rejection: {name}")
    legacy_text = (receipts / "p2-legacy-cli-inc.log").read_text(errors="replace")
    legacy_fail_closed = any(
        marker in legacy_text
        for marker in (
            "legacy auto-device indexes",
            "different embedding device",
            "Auto device cannot create",
        )
    )
    if not legacy_fail_closed:
        fail("legacy auto incremental write did not fail closed")
    legacy_query = (receipts / "p2-legacy-cli-query.log").read_text()
    if "Error" in legacy_query:
        fail("legacy auto read-only query failed")
    rebuilt = load(receipts / "manifests" / "legacy-auto2.json")
    return {"guards": checks, "legacy_query_read_only": "ok", "legacy_snapshot": rebuilt.get("indexVersion")}


def check_quality(receipts: Path, e8: Path) -> dict:
    reports = Path(e8) / "receipts"
    cpu = load(reports / "full-cpu-hybrid.report.json")["overall"]
    vk = load(reports / "full-vk-hybrid.report.json")["overall"]
    keys = ["recall_at_10", "recall_at_50", "mrr_at_10", "ndcg_at_10", "solved_at_10", "no_answer_fp_at_10"]
    for key in keys:
        if not math.isclose(cpu[key], vk[key], rel_tol=0.0, abs_tol=0.0):
            fail(f"hybrid quality changed between CPU and Vulkan: {key}")
    verify_log = (receipts / "p2-e6-verify-full-hybrid.log").read_text()
    if "E6 verification PASS" not in verify_log:
        fail("E6 independent verifier did not pass full hybrid reports")
    negative = (receipts / "p2-e6-negative.log").read_text()
    if "OK:" not in negative:
        fail("E6 negative contract/provenance tests did not pass")
    return {"hybrid": cpu, "cpu_vk_equal": True}


def check_performance(receipts: Path) -> dict:
    def wall(name: str) -> float:
        text = (receipts / name).read_text()
        match = re.search(r"wall=([0-9.]+)", text)
        if not match:
            fail(f"missing wall time in {name}")
        return float(match.group(1))

    cpu = wall("p2-cpu-cold.log")
    vk = wall("p2-vk-cold.log")
    if not (vk < cpu):
        fail("Vulkan cold index was not faster than CPU")
    ratio = cpu / vk
    if not 1.0 < ratio < 20.0:
        fail(f"implausible CPU/Vulkan ratio: {ratio}")
    return {"cpu_cold_s": cpu, "vk_cold_s": vk, "ratio": ratio}


def negative_checks(receipts: Path) -> dict:
    payload = load(receipts / "p2-query-cs40-cpu.json")
    good = check_query_contract(payload)
    bad = json.loads(json.dumps(payload))
    bad["run"]["measurement_contract"]["status"] = "FAIL"
    if good and not check_query_contract(bad):
        contract_rejected = True
    else:
        fail("negative E6 contract mutation was not rejected")
    bad_route = json.loads(json.dumps(payload))
    bad_route["run"]["measurement_contract"]["actual"]["route_modes"] = ["fts"]
    if not check_query_contract(bad_route):
        route_rejected = True
    else:
        fail("negative E6 route mutation was not rejected")
    if "different embedding device" in (receipts / "p2-cross-noop.log").read_text():
        device_rejected = True
    else:
        fail("negative device guard receipt not observed")
    return {"contract_mutation": contract_rejected, "route_mutation": route_rejected, "device_mutation": device_rejected}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--e8-receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--negative", action="store_true")
    args = parser.parse_args()
    if args.negative:
        result = negative_checks(args.receipts)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    output = {
        "provenance": check_provenance(args.repo),
        "query_contracts": check_query_receipts(args.receipts),
        "workload_e2": check_workload(args.receipts, args.receipts / "manifests"),
        "fresh_incremental_consistency": check_consistency(args.receipts),
        "guards": check_guards(args.receipts),
        "quality": check_quality(args.receipts, args.e8_receipts),
        "performance": check_performance(args.receipts),
        "negative": negative_checks(args.receipts),
    }
    text = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
