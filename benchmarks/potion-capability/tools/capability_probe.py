#!/usr/bin/env python3
"""Prototype Potion-only capability probes on frozen Rust/C# dev evidence.

Gold is loaded only for grading; no gold term enters a scorer or encoder.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import time
from pathlib import Path

import numpy as np


class Potion:
    def __init__(self, model_dir: Path, encoder: Path):
        p = model_dir / "model.safetensors"
        with p.open("rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            header = json.loads(f.read(n))
            start = 8 + n
            f.seek(start)
            raw = f.read()
        meta = header["embeddings"]
        a, b = meta["data_offsets"]
        w = np.frombuffer(raw[a:b], dtype=np.float16).astype(np.float32)
        self.rows, self.dim = meta["shape"]
        self.w = w.reshape(self.rows, self.dim)
        norms = np.linalg.norm(self.w, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self.wn = self.w / norms
        self.tokenizer = model_dir / "tokenizer" / "tokenizer.json"
        self.encoder = encoder
        self.unknown_id = 1
        self.encode_seconds = 0.0

    def encode(self, texts: list[str]) -> list[list[int]]:
        if not texts:
            return []
        payload = "\n".join(t.replace("\n", " ") for t in texts) + "\n"
        t0 = time.monotonic()
        cp = subprocess.run(
            [str(self.encoder), str(self.tokenizer)], input=payload,
            capture_output=True, text=True, check=True,
        )
        self.encode_seconds += time.monotonic() - t0
        return [json.loads(line) for line in cp.stdout.splitlines()]

    def pool(self, ids: list[int]) -> np.ndarray:
        keep = [i for i in ids if i not in (0, self.unknown_id) and 0 <= i < self.rows]
        if not keep:
            return np.zeros(self.dim, dtype=np.float32)
        v = self.wn[keep].mean(axis=0)
        n = np.linalg.norm(v)
        return v / n if n else v

    def token_matrix(self, ids: list[int], cap: int = 256) -> np.ndarray:
        keep = [i for i in ids if i not in (0, self.unknown_id) and 0 <= i < self.rows][:cap]
        return self.wn[keep] if keep else np.zeros((0, self.dim), dtype=np.float32)


def cos(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0


def source_text(root: Path, path: str, start: int, end: int) -> str:
    p = root / path
    if not p.exists():
        return ""
    lines = p.read_text(errors="replace").splitlines()
    return "\n".join(lines[max(0, start - 1):min(len(lines), end)])


def covered(gold: dict, selected: list[dict]) -> tuple[int, int]:
    total = 0
    hit = 0
    for ref in gold["source_refs"]:
        for span in ref["spans"]:
            total += 1
            if any(h["path"] == ref["path"] and h["start"] <= span["end_line"] and h["end"] >= span["start_line"] for h in selected):
                hit += 1
    return hit, total


def pack_bytes(root: Path, hits: list[dict]) -> int:
    total = 0
    for h in hits[:10]:
        piece = f"[{h['path']}:{h['start']}-{h['end']}]\n" + source_text(root, h["path"], h["start"], h["end"])
        total += min(len(piece.encode()), 2560)
    return total


def train_projection(model: Potion, rust_root: Path, limit: int = 3000) -> tuple[np.ndarray, dict]:
    pairs: list[tuple[str, str]] = []
    pat = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+|async\s+|unsafe\s+)*fn\s+([A-Za-z_][A-Za-z0-9_]*)")
    for p in rust_root.rglob("*.rs"):
        if ".git" in p.parts or ".zvec-grep" in p.parts:
            continue
        lines = p.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            m = pat.match(line)
            if not m:
                continue
            name = m.group(1)
            q = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
            doc = []
            j = i - 1
            while j >= 0 and lines[j].lstrip().startswith("///"):
                doc.append(lines[j].lstrip()[3:].strip())
                j -= 1
            q = " ".join([q] + doc[:3])
            body = "\n".join(lines[i:min(len(lines), i + 24)])
            pairs.append((q, body))
            if len(pairs) >= limit:
                break
        if len(pairs) >= limit:
            break
    ids = model.encode([x for pair in pairs for x in pair])
    half = len(pairs)
    qv = np.stack([model.pool(ids[i]) for i in range(half)])
    dv = np.stack([model.pool(ids[half + i]) for i in range(half)])
    t0 = time.monotonic()
    gram = dv.T @ dv + np.eye(model.dim, dtype=np.float32)
    proj = np.linalg.solve(gram, dv.T @ qv).astype(np.float32)
    return proj, {"pairs": len(pairs), "train_seconds": round(time.monotonic() - t0, 3), "params": int(proj.size), "bytes": int(proj.nbytes)}


def semantic_chunks(model: Potion, root: Path, hits: list[dict]) -> list[dict]:
    groups = []
    texts = []
    for h in hits:
        if h["end"] - h["start"] <= 200:
            continue
        lines = (root / h["path"]).read_text(errors="replace").splitlines()
        lo, hi = h["start"] - 1, min(len(lines), h["end"])
        windows = []
        for s in range(lo, hi, 8):
            e = min(hi, s + 12)
            windows.append((s, e))
            texts.append("\n".join(lines[s:e]))
        groups.append((h, lo, hi, windows))
    encoded = model.encode(texts) if texts else []
    offset = 0
    out = []
    for h in hits:
        if h["end"] - h["start"] <= 200:
            out.append(h)
            continue
        group = next(g for g in groups if g[0] is h)
        _, lo, hi, windows = group
        vectors = [model.pool(encoded[offset + i]) for i in range(len(windows))]
        offset += len(windows)
        boundaries = [lo]
        for i in range(1, len(vectors)):
            if cos(vectors[i - 1], vectors[i]) < 0.75:
                boundaries.append(windows[i][0])
        boundaries.append(hi)
        for a, b in zip(boundaries, boundaries[1:]):
            if b - a < 10:
                continue
            new = dict(h)
            new.update(start=a + 1, end=b, unit="semantic")
            out.append(new)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--encoder", required=True, type=Path)
    ap.add_argument("--rust-root", required=True, type=Path)
    ap.add_argument("--rust-receipt", required=True, type=Path)
    ap.add_argument("--rust-cases", required=True, type=Path)
    ap.add_argument("--csharp-root", required=True, type=Path)
    ap.add_argument("--csharp-cases", required=True, type=Path)
    ap.add_argument("--b0-receipt", required=True, type=Path)
    ap.add_argument("--artifacts", required=True, type=Path)
    args = ap.parse_args()

    art = args.artifacts.resolve()
    model = Potion(args.model_dir.resolve(), args.encoder.resolve())
    rust_root = args.rust_root.resolve()
    cs_root = args.csharp_root.resolve()
    rust_receipt = json.loads(args.rust_receipt.read_text())
    rust_cases = {c["id"]: c for c in json.loads(args.rust_cases.read_text())["cases"]}
    b0 = json.loads(args.b0_receipt.read_text())
    cs_cases = {c["id"]: c for c in json.loads(args.csharp_cases.read_text())["cases"] if c["id"].startswith("D")}

    proj, proj_meta = train_projection(model, rust_root)
    result = {
        "schema_version": 1,
        "model_rows": model.rows,
        "model_dim": model.dim,
        "projection": proj_meta,
        "rust": {},
        "csharp": {},
    }

    for cid, gold in rust_cases.items():
        hits = rust_receipt["cases"][cid]["arms"]["baseline"]["hits"]
        bodies = [source_text(rust_root, h["path"], h["start"], h["end"]) for h in hits]
        metas = [f"symbol: {h.get('symbol') or ''}\n{bodies[i].splitlines()[0] if bodies[i] else ''}" for i, h in enumerate(hits)]
        ids = model.encode([gold["question"]] + bodies + metas)
        q = model.pool(ids[0])
        body_v = [model.pool(x) for x in ids[1:1 + len(hits)]]
        meta_v = [model.pool(x) for x in ids[1 + len(hits):]]
        q_tokens = model.token_matrix(ids[0], cap=64)

        scores = {}
        scores["p1"] = [cos(q, v) for v in body_v]
        scores["p2"] = [0.6 * cos(q, meta_v[i]) + 0.4 * cos(q, body_v[i]) for i in range(len(hits))]
        late = []
        for ids_b in ids[1:1 + len(hits)]:
            tm = model.token_matrix(ids_b)
            if len(tm) == 0 or len(q_tokens) == 0:
                late.append(0.0)
            else:
                sim = q_tokens @ tm.T
                late.append(float(sim.max(axis=1).mean()))
        scores["p3"] = late
        scores["p5"] = [cos(q, v @ proj) for v in body_v]

        def top(scores_: list[float]) -> list[dict]:
            order = sorted(range(len(hits)), key=lambda i: -scores_[i])
            return [hits[i] for i in order[:10]]

        sel_p1 = top(scores["p1"])
        sel_p2 = top(scores["p2"])
        sel_p3 = top(scores["p3"])
        sel_p5 = top(scores["p5"])
        # MMR from P1 ordering.
        chosen: list[int] = []
        remaining = list(range(len(hits)))
        while remaining and len(chosen) < 10:
            best_i, best_s = None, -1e9
            for i in remaining:
                div = max([cos(body_v[i], body_v[j]) for j in chosen], default=0.0)
                s = 0.7 * scores["p1"][i] - 0.3 * div
                if s > best_s:
                    best_i, best_s = i, s
            chosen.append(best_i)
            remaining.remove(best_i)
        sel_p4 = [hits[i] for i in chosen]
        sem_hits = semantic_chunks(model, rust_root, hits)
        sem_ids = model.encode([source_text(rust_root, h["path"], h["start"], h["end"]) for h in sem_hits])
        sel_p6 = [h for _, h in sorted(zip([cos(q, model.pool(x)) for x in sem_ids], sem_hits), key=lambda t: -t[0])[:10]]

        row = {}
        for name, sel, pool_hits in [
            ("p1", sel_p1, hits), ("p2", sel_p2, hits), ("p3", sel_p3, hits),
            ("p4", sel_p4, hits), ("p5", sel_p5, hits), ("p6", sel_p6, sem_hits),
        ]:
            hit, total = covered(gold, sel)
            pool_hit, _ = covered(gold, pool_hits)
            row[name] = {
                "selected_recall": round(hit / total, 3) if total else 0,
                "pool_recall": round(pool_hit / total, 3) if total else 0,
                "pack_bytes": pack_bytes(rust_root, sel),
                "selected": [{"path": h["path"], "start": h["start"], "end": h["end"]} for h in sel],
            }
        result["rust"][cid] = row
        print(cid, " ".join(f"{k}={v['selected_recall']:.2f}" for k, v in row.items()))

    # C# cross-language check on frozen B0 first-call units; no held-out.
    for case in b0["quality"]["dev_real_cases"]:
        cid = case["id"]
        gold = cs_cases[cid]
        units: list[dict] = []
        seen_units = set()
        for call in case["calls"]:
            for u in call["units"]:
                key = (u["path"], u["start_line"], u["end_line"])
                if key not in seen_units:
                    seen_units.add(key)
                    units.append(u)
        units = units[:50]
        hits = [{"path": u["path"], "start": u["start_line"], "end": u["end_line"]} for u in units]
        bodies = [source_text(cs_root, h["path"], h["start"], h["end"]) for h in hits]
        ids = model.encode([gold["question"]] + bodies)
        q = model.pool(ids[0])
        body_v = [model.pool(x) for x in ids[1:]]
        scores_p1 = [cos(q, v) for v in body_v]
        scores_p5 = [cos(q, v @ proj) for v in body_v]
        row = {}
        for name, scores_ in (("p1", scores_p1), ("p5", scores_p5)):
            order = sorted(range(len(hits)), key=lambda i: -scores_[i])[:10]
            sel = [hits[i] for i in order]
            hit, total = covered(gold, sel)
            row[name] = {"selected_recall": round(hit / total, 3) if total else 0, "pool_recall": round(len(hits) > 0, 3), "candidates": len(hits)}
        result["csharp"][cid] = row
        print("C#", cid, " ".join(f"{k}={v['selected_recall']:.2f}" for k, v in row.items()))

    def macro(split: str, probe: str) -> float:
        vals = [v[probe]["selected_recall"] for v in result[split].values()]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    result["summary"] = {
        "rust": {p: macro("rust", p) for p in ("p1", "p2", "p3", "p4", "p5", "p6")},
        "csharp": {p: macro("csharp", p) for p in ("p1", "p5")},
        "encode_seconds": round(model.encode_seconds, 3),
    }
    dest = art / "results/capability-receipt.json"
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    print("receipt", dest)


if __name__ == "__main__":
    main()
