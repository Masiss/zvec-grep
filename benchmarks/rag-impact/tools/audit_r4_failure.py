#!/usr/bin/env python3
"""Read-only failure attribution for the frozen R4 candidate pilot.

Inputs are the frozen B0 receipt, the frozen candidate pilot receipt, the frozen
dev cases and the read-only HRM2.0 source. It does not change code, targets,
corpus or held-out data.

The audit reconstructs per-gold-span status:
  packed            final candidate pack contains an overlapping span
  selection_loss    a B0 seed contained the span, but the final pack did not
  ranking_budget    seed existed but its estimated score was below the pack cutoff
  expansion_miss    no B0 seed hit, but the containing method was in a seed file
  discovery_loss    no B0 seed and no seed file/method reached the span

It also reports final pack token/read pressure. The expanded pre-truncation set
is not retained by the frozen pilot; ranking/budget estimates use the committed
scoring rules and the frozen final cutoff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RAG = Path(__file__).resolve().parent.parent
B0 = RAG / "results" / "b0-official.json"
CAND = RAG / "results" / "candidate-pilot.json"
DEV = RAG / "cases" / "dev.json"
CORPUS = RAG / "corpus-manifest.json"

WORD_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
METHOD_RE = re.compile(
    r"^\s*(?:public|private|protected|internal|static|virtual|override|async|partial|\s)+"
    r"[\w<>,\[\]\?\.]+\s+(\w+)\s*\([^;]*\)\s*$"
)


def tokens(text: str) -> list[str]:
    return [word.lower() for word in WORD_RE.findall(text)]


def method_for_line(lines: list[str], line: int) -> tuple[str, int, int] | None:
    """Find the C# method containing a 1-based line with a small brace scan."""
    if not (1 <= line <= len(lines)):
        return None
    starts = list(range(max(0, line - 3), min(len(lines), line + 2)))
    starts += list(range(line - 4, -1, -1))
    for start in starts:
        text = lines[start]
        match = METHOD_RE.match(text)
        if not match:
            continue
        name = match.group(1)
        brace_line = start
        while brace_line < len(lines) and "{" not in lines[brace_line]:
            brace_line += 1
            if brace_line - start > 3:
                break
        if brace_line >= len(lines) or "{" not in lines[brace_line]:
            continue
        depth = 0
        for index in range(brace_line, len(lines)):
            depth += lines[index].count("{")
            depth -= lines[index].count("}")
            if depth <= 0:
                return name, start + 1, index + 1
    return None


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def main() -> int:
    b0 = json.loads(B0.read_text())
    cand = json.loads(CAND.read_text())
    dev = json.loads(DEV.read_text())
    corpus = json.loads(CORPUS.read_text())
    root = Path(corpus["source_root"])
    b0_cases = {case["id"]: case for case in b0["quality"]["dev_real_cases"]}
    cand_cases = {case["id"]: case for case in cand["cases"]}
    dev_cases = {case["id"]: case for case in dev["cases"]}

    file_lines: dict[str, list[str]] = {}

    def lines_for(path: str) -> list[str]:
        if path not in file_lines:
            file_lines[path] = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        return file_lines[path]

    cases = []
    cause_totals = {
        "packed": 0,
        "selection_seed_set": 0,
        "selection_dedup_or_rank": 0,
        "budget_ranking": 0,
        "packing_range": 0,
        "selection_expansion_miss": 0,
        "discovery_loss": 0,
    }
    for case_id in sorted(cand_cases):
        b0_case = b0_cases[case_id]
        cand_case = cand_cases[case_id]
        dev_case = dev_cases[case_id]
        final_items = cand_case["items"]
        cutoff = min((item["score"] for item in final_items), default=0)
        seeds = []
        for call in b0_case["calls"]:
            for unit in call.get("units", []):
                seeds.append((unit["path"], unit["start_line"], unit["end_line"], call["call"]))
        seed_paths = {seed[0] for seed in seeds}
        seed_files = set(seed_paths)

        gold = []
        for ref in dev_case.get("source_refs", []):
            for span in ref.get("spans", []):
                gold.append((ref["path"], span["start_line"], span["end_line"]))

        spans = []
        for path, start, end in gold:
            final_hit = any(
                item["path"] == path and overlap(item["start_line"], item["end_line"], start, end)
                for item in final_items
            )
            seed_hit = any(
                seed_path == path and overlap(seed_start, seed_end, start, end)
                for seed_path, seed_start, seed_end, _ in seeds
            )
            best_hit = any(
                seed_path == path and overlap(seed_start, seed_end, start, end)
                for seed_path, seed_start, seed_end, _ in seeds
                if _ == b0_case.get("best_call")
            )
            method = method_for_line(lines_for(path), start)
            method_name = method[0] if method else None
            method_in_seed_file = method is not None and path in seed_files
            exact_seed = next(
                (
                    (seed_path, seed_start, seed_end, call)
                    for seed_path, seed_start, seed_end, call in seeds
                    if seed_path == path and overlap(seed_start, seed_end, start, end)
                ),
                None,
            )
            seed_score = None
            if exact_seed:
                path_hits = len(set(tokens(path)) & set(tokens(dev_case["question"])))
                method_hits = 0
                if method_name:
                    method_hits = len(set(tokens(method_name)) & set(tokens(dev_case["question"])))
                seed_score = 100 + path_hits * 30 + method_hits * 50
            final_file_but_no_overlap = any(
                item["path"] == path
                and not overlap(item["start_line"], item["end_line"], start, end)
                for item in final_items
            )
            if final_hit:
                cause = "packed"
            elif seed_hit:
                if best_hit and seed_score is not None and seed_score >= cutoff:
                    cause = "selection_seed_set"
                elif seed_score is not None and seed_score >= cutoff:
                    cause = "selection_dedup_or_rank"
                else:
                    cause = "budget_ranking"
            elif final_file_but_no_overlap:
                cause = "packing_range"
            elif method_in_seed_file:
                cause = "selection_expansion_miss"
            else:
                cause = "discovery_loss"
            cause_totals[cause] += 1
            spans.append(
                {
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "method": method_name,
                    "final_hit": final_hit,
                    "b0_union_hit": seed_hit,
                    "b0_best_call_hit": best_hit,
                    "seed_call": exact_seed[3] if exact_seed else None,
                    "seed_score_estimate": seed_score,
                    "pack_cutoff_score": cutoff,
                    "cause": cause,
                    "in_seed_file": path in seed_files,
                }
            )

        chosen_call = None
        chosen_count = -1
        for call in b0_case["calls"]:
            call_units = {(u["path"], u["start_line"], u["end_line"]) for u in call.get("units", [])}
            count = sum(
                1
                for item in final_items
                if item.get("reason") == "best-call seed"
                and (item["path"], item["start_line"], item["end_line"]) in call_units
            )
            if count > chosen_count:
                chosen_count = count
                chosen_call = call["call"]
        final_files = {item["path"] for item in final_items}
        b0_best_call = next(
            (call for call in b0_case["calls"] if call["call"] == b0_case.get("best_call")),
            None,
        )
        b0_best_files = {unit["path"] for unit in (b0_best_call or {}).get("units", [])}
        new_files = sorted(final_files - b0_best_files)
        reason_files: dict[str, set[str]] = {}
        for item in final_items:
            reason_files.setdefault(item.get("reason", ""), set()).add(item["path"])
        cases.append(
            {
                "id": case_id,
                "candidate_chosen_call": chosen_call,
                "candidate_chosen_call_matches": chosen_count,
                "b0_best_call": b0_case.get("best_call"),
                "final_files": sorted(final_files),
                "b0_best_files": sorted(b0_best_files),
                "new_files_vs_b0_best": new_files,
                "reason_files": {reason: sorted(paths) for reason, paths in reason_files.items()},
                "question": dev_case["question"],
                "gold_spans": len(gold),
                "candidate_recall": cand_case["recall"],
                "b0_recall": cand_case["b0_recall"],
                "candidate_items": final_items,
                "candidate_reads": cand_case["additional_source_reads"],
                "b0_reads": cand_case["b0_additional_source_reads"],
                "candidate_tokens": cand_case["additional_context_tokens"],
                "b0_tokens": cand_case["b0_additional_context_tokens"],
                "b0_seed_calls": [
                    {"call": call["call"], "recall": call["recall"], "units": len(call.get("units", []))}
                    for call in b0_case["calls"]
                ],
                "spans": spans,
            }
        )

    receipt = {
        "schema_version": 1,
        "status": "READ_ONLY_R4_FAILURE_ATTRIBUTION",
        "inputs": {
            "b0": str(B0.relative_to(RAG)),
            "candidate": str(CAND.relative_to(RAG)),
            "dev": str(DEV.relative_to(RAG)),
            "corpus": str(CORPUS.relative_to(RAG)),
        },
        "method": (
            "Per-gold-span final/seed overlap, best-call reference, seed-score estimate, "
            "method containment and final pack cutoff. Expanded pre-truncation set was not "
            "retained by the frozen pilot; ranking/budget is estimated from committed scoring."
        ),
        "cause_totals": cause_totals,
        "cases": cases,
    }
    out = RAG / "results" / "r4-failure-attribution.json"
    out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(cause_totals, indent=2))
    for case in cases:
        print(
            case["id"],
            "cand=%.3f" % case["candidate_recall"],
            "b0=%.3f" % case["b0_recall"],
            "losses=",
            {cause: sum(1 for span in case["spans"] if span["cause"] == cause)
             for cause in cause_totals if cause != "packed" and any(
                 span["cause"] == cause for span in case["spans"])},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
