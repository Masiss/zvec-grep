#!/usr/bin/env python3
"""Deterministic E3-01 dataset validator.  Never calls a retrieval engine."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - environment guard
    Draft202012Validator = None

ALLOWED_CATEGORIES = {
    "exact_symbol", "nl_implementation", "hardcoded_value", "configuration",
    "configuration_flow", "cross_file", "data_flow", "sql_database",
    "serialization", "linq", "data_structure", "docs_vs_impl", "multi_hop",
    "no_answer",
}
ALLOWED_ROLES = {"primary", "supporting", "hard_negative"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
HEX = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_root(root: Path, dataset: Path) -> list[str]:
    if Draft202012Validator is None:
        raise SystemExit(
            "jsonschema is required: python3 -m pip install -r tools/requirements.txt"
        )
    errors: list[str] = []
    queries_doc = load(dataset / "queries.json")
    corpus = load(dataset / "corpus.json")
    manifest = load(dataset / "manifest.json")

    queries_bytes = (dataset / "queries.json").read_bytes()
    corpus_bytes = (dataset / "corpus.json").read_bytes()
    if sha256_bytes(queries_bytes) != manifest.get("queries_sha256"):
        fail(errors, "queries.json checksum does not match manifest")
    if sha256_bytes(corpus_bytes) != manifest.get("corpus_sha256"):
        fail(errors, "corpus.json checksum does not match manifest")
    if manifest.get("query_count") != len(queries_doc.get("queries", [])):
        fail(errors, "manifest query_count mismatch")
    if manifest.get("corpus_files") != corpus.get("counts", {}).get("files"):
        fail(errors, "manifest corpus_files mismatch")

    for schema_name, document in (
        ("query.schema.json", queries_doc),
        ("corpus.schema.json", corpus),
    ):
        schema_path = dataset.parent / "schema" / schema_name
        if schema_path.exists():
            schema = load(schema_path)
            for error in Draft202012Validator(schema).iter_errors(document):
                fail(errors, f"{schema_name}: {error.json_path}: {error.message}")

    files = {item["path"]: item for item in corpus.get("files", [])}
    if len(files) != corpus.get("counts", {}).get("files"):
        fail(errors, "duplicate or missing corpus file entries")
    actual_source_files = sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*.cs")
    )
    if sorted(files) != actual_source_files:
        fail(errors, "corpus manifest does not match the source tree file set")
    for rel, meta in files.items():
        data = (root / rel).read_bytes()
        if sha256_bytes(data) != meta.get("sha256"):
            fail(errors, f"corpus file hash mismatch: {rel}")
        if len(data) != meta.get("bytes"):
            fail(errors, f"corpus file size mismatch: {rel}")

    seen_ids: set[str] = set()
    no_answer_count = 0
    for query in queries_doc.get("queries", []):
        qid = query.get("id", "<missing>")
        if qid in seen_ids:
            fail(errors, f"duplicate query id: {qid}")
        seen_ids.add(qid)
        if query.get("category") not in ALLOWED_CATEGORIES:
            fail(errors, f"{qid}: unknown category {query.get('category')!r}")
        if not isinstance(query.get("question"), str) or len(query["question"].strip()) < 12:
            fail(errors, f"{qid}: question is too short")
        if query.get("leakage") not in {"none", "identifier"}:
            fail(errors, f"{qid}: invalid leakage class")
        answerable = query.get("answerable")
        if not isinstance(answerable, bool):
            fail(errors, f"{qid}: answerable must be boolean")
            continue
        evidence = query.get("evidence", [])
        negatives = query.get("hard_negatives", [])
        anchors = evidence + negatives
        required_ids = query.get("solved_when", {}).get("required_anchor_ids", [])
        if answerable and not required_ids:
            fail(errors, f"{qid}: answerable query needs required_anchor_ids")
        if not answerable and required_ids:
            fail(errors, f"{qid}: no-answer query must not require anchors")
        if query.get("category") in {"exact_symbol", "hardcoded_value", "no_answer"} and not negatives:
            fail(errors, f"{qid}: category {query['category']} needs a hard negative")
        if not answerable:
            no_answer_count += 1
            if evidence:
                fail(errors, f"{qid}: no-answer query must not have evidence")
            if not query.get("absence_checks"):
                fail(errors, f"{qid}: no-answer query needs absence checks")
            if not negatives:
                fail(errors, f"{qid}: no-answer query needs hard negatives")
            if query.get("solved_when", {}).get("max_false_positives") != 0:
                fail(errors, f"{qid}: no-answer threshold must be zero")
        else:
            if not evidence:
                fail(errors, f"{qid}: answerable query needs evidence")
            groups = {item.get("group") for item in evidence}
            required = query.get("solved_when", {}).get("required_groups", [])
            if not required or not set(required).issubset(groups):
                fail(errors, f"{qid}: required groups are not covered by evidence")
            if set(required) != groups:
                fail(errors, f"{qid}: required groups must equal the evidence groups")
            evidence_ids = {item.get("id") for item in evidence}
            if set(required_ids) != evidence_ids:
                fail(errors, f"{qid}: required_anchor_ids must equal the evidence anchor ids")
            if query.get("category") == "multi_hop" and len(required) < 2:
                fail(errors, f"{qid}: multi-hop query needs at least two required groups")
        anchor_ids = set()
        for anchor in anchors:
            if anchor.get("id") in anchor_ids:
                fail(errors, f"{qid}: duplicate anchor id {anchor.get('id')}")
            anchor_ids.add(anchor.get("id"))
            if not re.fullmatch(r"[a-z0-9]+-[0-9]{2}-[a-z0-9-]+-[en][0-9]+", anchor.get("id", "")):
                fail(errors, f"{qid}: malformed anchor id {anchor.get('id')!r}")
            expected_group = "hard_negative" if anchor in negatives else None
            if anchor.get("role") not in ALLOWED_ROLES:
                fail(errors, f"{qid}: invalid anchor role")
            if expected_group and anchor.get("group") != expected_group:
                fail(errors, f"{qid}: hard negative group must be hard_negative")
            if anchor.get("confidence") not in ALLOWED_CONFIDENCE:
                fail(errors, f"{qid}: invalid confidence")
            rel = anchor.get("path", "")
            if rel not in files:
                fail(errors, f"{qid}: anchor path is outside corpus: {rel}")
                continue
            start, end = anchor.get("start_line"), anchor.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                fail(errors, f"{qid}: invalid anchor range {start}..{end}")
                continue
            lines = (root / rel).read_text(encoding="utf-8").splitlines()
            if end > len(lines):
                fail(errors, f"{qid}: anchor end exceeds file: {rel}:{end}")
                continue
            content = "\n".join(lines[start - 1 : end]) + "\n"
            if sha256_bytes(content.encode("utf-8")) != anchor.get("content_sha256"):
                fail(errors, f"{qid}: anchor content hash mismatch {rel}:{start}-{end}")
            if not anchor.get("symbol"):
                fail(errors, f"{qid}: anchor symbol is empty")
            if not anchor.get("rationale"):
                fail(errors, f"{qid}: anchor rationale is empty")
            if not HEX.match(anchor.get("content_sha256", "")):
                fail(errors, f"{qid}: malformed anchor hash")
            if not rel.startswith(query.get("corpus", "") + "/"):
                fail(errors, f"{qid}: anchor is outside the query corpus: {rel}")
        for negative_anchor in negatives:
            for positive in evidence:
                if (
                    negative_anchor.get("path") == positive.get("path")
                    and negative_anchor.get("start_line", 0) <= positive.get("end_line", 0)
                    and negative_anchor.get("end_line", 0) >= positive.get("start_line", 0)
                ):
                    fail(errors, f"{qid}: hard negative overlaps evidence")

        for check in query.get("absence_checks", []):
            pattern = check.get("pattern")
            scope = check.get("scope")
            if not pattern or not scope:
                fail(errors, f"{qid}: malformed absence check")
                continue
            try:
                regex = re.compile(pattern)
            except re.error as error:
                fail(errors, f"{qid}: invalid absence regex: {error}")
                continue
            matches = 0
            for rel, _meta in files.items():
                if not rel.startswith(scope):
                    continue
                matches += len(regex.findall((root / rel).read_text(encoding="utf-8")))
            if matches != check.get("expected_matches", 0):
                fail(errors, f"{qid}: absence check {pattern!r} found {matches} matches")

    if no_answer_count == 0:
        fail(errors, "dataset needs at least one no-answer query")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    args = parser.parse_args()
    errors = validate_root(args.source, args.dataset)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(f"{len(errors)} validation error(s)")
        return 1
    print("OK: corpus checksums, anchors, groups, absence checks and manifest are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
