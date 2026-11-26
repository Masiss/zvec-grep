#!/usr/bin/env python3
"""Run frozen Rust RAG arms against the isolated zvec-grep baseline.

Runtime arms see only question text (and, for H2, corpus-derived terms).
Gold cases are loaded solely by the grader in this file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HIT = re.compile(r"^#(\d+)\s+(.+?):(\d+)-(\d+)\s*$")
STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "if", "in", "into", "is", "it", "its", "of", "on", "or", "that",
    "the", "their", "there", "this", "to", "was", "what", "when", "where", "which",
    "who", "with", "without",
}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in STOP and len(w) > 2}


def split_name(name: str) -> set[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return tokens(parts) | {name.lower()}


def load_cases(path: Path) -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(path.read_text())["cases"]}


def symbol_inventory(root: Path) -> dict[str, list[tuple[str, int, int]]]:
    """Corpus-only declaration inventory. No gold file is read."""
    inv: dict[str, list[tuple[str, int, int]]] = {}
    pat = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+|async\s+|unsafe\s+|const\s+)*"
        r"(?:fn|struct|enum|trait|mod|type|union)\s+([A-Za-z_][A-Za-z0-9_]*)"
    )
    for p in root.rglob("*.rs"):
        if ".git" in p.parts or ".zvec-grep" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            m = pat.match(line)
            if m:
                inv.setdefault(m.group(1), []).append((rel, i, i))
    return inv


def expand_query(question: str, inv: dict[str, list[tuple[str, int, int]]]) -> str:
    q = tokens(question)
    scored: list[tuple[int, str]] = []
    for name in inv:
        overlap = len(q & split_name(name))
        if overlap:
            scored.append((overlap, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    extra = [name for _, name in scored[:12]]
    return question + (" " + " ".join(extra) if extra else "")


def parse_output(text: str) -> list[dict]:
    hits: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = HIT.match(line)
        if m:
            current = {
                "rank": int(m.group(1)),
                "path": m.group(2),
                "start": int(m.group(3)),
                "end": int(m.group(4)),
                "score": None,
                "trace": None,
                "symbol": None,
            }
            hits.append(current)
            continue
        if current is None:
            continue
        if line.startswith("symbol:"):
            current["symbol"] = line[len("symbol:"):].strip()
        elif line.startswith("score:"):
            current["score"] = float(line[len("score:"):].strip())
        elif line.startswith("trace:"):
            try:
                current["trace"] = json.loads(line[len("trace:"):].strip())
            except json.JSONDecodeError:
                pass
    return hits


def narrow_methods(root: Path, hits: list[dict]) -> list[dict]:
    """Split large entity hits into method ranges with declaration headers."""
    out: list[dict] = []
    for h in hits:
        p = root / h["path"]
        if not p.exists():
            out.append(h)
            continue
        lines = p.read_text(errors="replace").splitlines()
        lo, hi = max(1, h["start"]), min(len(lines), h["end"])
        methods: list[tuple[int, int]] = []
        i = lo - 1
        while i < hi:
            if re.search(r"\bfn\s+[A-Za-z_]", lines[i]):
                start = i
                while start > 0 and (
                    lines[start - 1].lstrip().startswith("#[")
                    or lines[start - 1].lstrip().startswith("///")
                    or lines[start - 1].lstrip().startswith("//")
                ):
                    start -= 1
                depth = 0
                opened = False
                j = i
                end = min(hi - 1, i + 40)
                while j < hi:
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        opened = True
                    j += 1
                    if opened and depth <= 0:
                        end = j - 1
                        break
                if end > start:
                    methods.append((start + 1, end + 1))
                i = max(j, i + 1)
            else:
                i += 1
        if methods and hi - lo + 1 > 80:
            for s, e in methods[:80]:
                new = dict(h)
                new.update(start=s, end=e, unit="method", parent=(h["start"], h["end"]))
                out.append(new)
        else:
            out.append(h)
    # Preserve rank order, dedupe identical ranges.
    seen: set[tuple[str, int, int]] = set()
    uniq = []
    for h in sorted(out, key=lambda x: (x["rank"], x["start"])):
        key = (h["path"], h["start"], h["end"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq


def rerank_by_question(root: Path, hits: list[dict], question: str) -> list[dict]:
    """H5: rank method units by question/source lexical overlap. Question text is runtime-visible."""
    q = tokens(question)
    out = []
    for h in hits:
        p = root / h["path"]
        text = ""
        if p.exists():
            lines = p.read_text(errors="replace").splitlines()
            text = "\n".join(lines[max(0, h["start"] - 1):min(len(lines), h["end"])])
        overlap = len(q & tokens(text))
        new = dict(h)
        new["score"] = (h["score"] or 0) + min(overlap, 12) * 0.02
        new["question_overlap"] = overlap
        out.append(new)
    return sorted(out, key=lambda x: (-(x["score"] or 0), x["rank"]))


def graph_expand(root: Path, hits: list[dict], inv: dict[str, list[tuple[str, int, int]]]) -> list[dict]:
    """Bounded one-hop local call resolution from seed hits, no gold input."""
    out = list(hits)
    seen = {(h["path"], h["start"], h["end"]) for h in hits}
    call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for h in hits[:10]:
        p = root / h["path"]
        if not p.exists():
            continue
        lines = p.read_text(errors="replace").splitlines()
        body = "\n".join(lines[max(0, h["start"] - 1):min(len(lines), h["end"])])
        for name in dict.fromkeys(call_re.findall(body)):
            if name not in inv or len(out) >= 60:
                continue
            for path, s, e in inv[name][:2]:
                key = (path, s, e)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "rank": 100 + len(out),
                    "path": path,
                    "start": s,
                    "end": e,
                    "score": (h["score"] or 0) * 0.9,
                    "trace": {"graph": {"seed": h["path"], "symbol": name, "hops": 1}},
                    "symbol": name,
                    "unit": "graph",
                })
    return out


def overlap_covered(gold: dict, hits: list[dict], limit: int | None = None) -> tuple[int, int, list[str]]:
    total = 0
    covered = 0
    names = []
    sel = hits if limit is None else hits[:limit]
    for ref in gold["source_refs"]:
        for span in ref["spans"]:
            total += 1
            hit = False
            for h in sel:
                if h["path"] == ref["path"] and h["start"] <= span["end_line"] and h["end"] >= span["start_line"]:
                    hit = True
                    break
            if hit:
                covered += 1
                names.append(f"{ref['path']}:{span['start_line']}-{span['end_line']}")
    return covered, total, names


def build_pack(root: Path, hits: list[dict], max_items: int = 10, max_bytes: int = 24 * 1024) -> tuple[str, list[dict]]:
    chunks: list[str] = []
    selected: list[dict] = []
    for h in hits:
        p = root / h["path"]
        if not p.exists() or len(selected) >= max_items:
            continue
        lines = p.read_text(errors="replace").splitlines()
        body = "\n".join(lines[max(0, h["start"] - 1):min(len(lines), h["end"])])
        header = f"[{h['path']}:{h['start']}-{h['end']}]\n"
        piece = header + body + "\n"
        if sum(len(c.encode()) for c in chunks) + len(piece.encode()) > max_bytes:
            continue
        chunks.append(piece)
        selected.append(h)
    return "".join(chunks), selected


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_search(binary: Path, root: Path, query: str, out_dir: Path, arm: str, cid: str) -> tuple[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    txt = out_dir / f"{cid}.{arm}.out"
    err = out_dir / f"{cid}.{arm}.err"
    tm = out_dir / f"{cid}.{arm}.time"
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("ZVEC_GREP_OBSERVABILITY"):
            env.pop(k, None)
    cmd = [
        "/usr/bin/time", "-f", "%e %M", "-o", str(tm),
        "env", *[arg for k in ("ZVEC_GREP_OBSERVABILITY_CAPTURE", "ZVEC_GREP_OBSERVABILITY_HOME", "ZVEC_GREP_OBSERVABILITY_MODE") for arg in ("-u", k)],
        str(binary), query, "--fuse", "--limit", "50", "--preview", "short",
        "--trace", "--mode", "direct", "--refresh", "off", "--color", "never",
    ]
    t0 = time.monotonic()
    cp = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
    wall_py = time.monotonic() - t0
    txt.write_text(cp.stdout)
    err.write_text(cp.stderr)
    wall, rss = 0.0, 0
    if tm.exists():
        parts = tm.read_text().split()
        if len(parts) >= 2:
            wall, rss = float(parts[0]), int(parts[1])
    if cp.returncode != 0:
        raise RuntimeError(f"search failed {cid} {arm}: {cp.stderr[:500]}")
    return cp.stdout, {"wall": wall, "wall_py": wall_py, "rss_kb": rss, "command": " ".join(cmd[4:])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", type=Path, required=True)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--arms", default="baseline,h2,h3,h4")
    args = ap.parse_args()

    wt = args.worktree.resolve()
    root = args.corpus.resolve()
    art = args.artifacts.resolve()
    cases = load_cases(wt / "benchmarks/rust-rag/cases.json")
    binary = art / "build/target/debug/zg"
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    inv = symbol_inventory(root)
    raw_dir = art / "results/raw"
    pack_dir = art / "results/packs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    receipt = {
        "schema_version": 1,
        "binary_sha256": sha256_file(binary),
        "corpus": "clap-rs/clap@4201754a400f676c9744da66383c89eb9f41af88",
        "cases_sha256": sha256_file(wt / "benchmarks/rust-rag/cases.json"),
        "arms": arms,
        "cases": {},
    }

    for cid, gold in cases.items():
        row: dict = {"question": gold["question"], "arms": {}}
        base_hits: list[dict] | None = None
        for arm in arms:
            query = gold["question"]
            hits: list[dict] = []
            if arm == "baseline":
                text, stats = run_search(binary, root, query, raw_dir, arm, cid)
                hits = parse_output(text)
            elif arm == "h2":
                query = expand_query(query, inv)
                text, stats = run_search(binary, root, query, raw_dir, arm, cid)
                hits = parse_output(text)
            elif arm == "h3":
                if base_hits is None:
                    text, base_stats = run_search(binary, root, query, raw_dir, "baseline", cid)
                    base_hits = parse_output(text)
                stats = {"wall": base_stats["wall"], "rss_kb": base_stats["rss_kb"], "derived_from": "baseline"}
                hits = narrow_methods(root, base_hits)
            elif arm == "h5":
                if base_hits is None:
                    text, base_stats = run_search(binary, root, query, raw_dir, "baseline", cid)
                    base_hits = parse_output(text)
                stats = {"wall": base_stats["wall"], "rss_kb": base_stats["rss_kb"], "derived_from": "baseline"}
                hits = rerank_by_question(root, narrow_methods(root, base_hits), gold["question"])
            elif arm == "h4":
                if base_hits is None:
                    text, base_stats = run_search(binary, root, query, raw_dir, "baseline", cid)
                    base_hits = parse_output(text)
                stats = {"wall": base_stats["wall"], "rss_kb": base_stats["rss_kb"], "derived_from": "baseline"}
                hits = graph_expand(root, base_hits, inv)
                hits = sorted(hits, key=lambda h: (-(h["score"] or 0), h["rank"]))
            else:
                raise SystemExit(f"unknown arm {arm}")

            pool_cov, pool_total, pool_names = overlap_covered(gold, hits)
            pack, selected = build_pack(root, hits)
            sel_cov, sel_total, sel_names = overlap_covered(gold, selected)
            pack_path = pack_dir / f"{cid}.{arm}.pack.txt"
            pack_path.write_text(pack)
            row["arms"][arm] = {
                "query": query,
                "stats": stats,
                "candidates": len(hits),
                "pool_recall": pool_cov / pool_total if pool_total else 0,
                "selected_recall": sel_cov / sel_total if sel_total else 0,
                "pool_covered": pool_names,
                "selected_covered": sel_names,
                "selected_count": len(selected),
                "pack_bytes": len(pack.encode()),
                "pack_path": str(pack_path.relative_to(art)),
                "hits": hits[:60],
            }
        receipt["cases"][cid] = row
        print(cid, " ".join(f"{a}={row['arms'][a]['selected_recall']:.2f}/{row['arms'][a]['pool_recall']:.2f}" for a in arms))

    out = art / "results/arm-receipt.json"
    out.write_text(json.dumps(receipt, indent=2) + "\n")
    print("receipt", out)


if __name__ == "__main__":
    main()
