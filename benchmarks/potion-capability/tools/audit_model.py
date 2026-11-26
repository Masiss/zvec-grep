#!/usr/bin/env python3
"""Audit Potion Code 16M v2 static embedding weights and tokenizer."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

import numpy as np


def load_safetensors(path: Path, tensor: str) -> tuple[np.ndarray, dict]:
    with path.open("rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
        data_start = 8 + n
        f.seek(data_start)
        raw = f.read()
    meta = header[tensor]
    dtype = meta["dtype"]
    rows, dim = meta["shape"]
    start, end = meta["data_offsets"]
    chunk = raw[start:end]
    if dtype == "F16":
        arr = np.frombuffer(chunk, dtype=np.float16).astype(np.float32)
    elif dtype == "F32":
        arr = np.frombuffer(chunk, dtype=np.float32).copy()
    else:
        raise SystemExit(f"unsupported dtype {dtype}")
    return arr.reshape(rows, dim), header


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    model = args.model_dir.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    w, header = load_safetensors(model / "model.safetensors", "embeddings")
    load_s = time.monotonic() - t0
    rows, dim = w.shape
    norms = np.linalg.norm(w, axis=1)
    col_norms = np.linalg.norm(w, axis=0)
    step = max(1, rows // 20000)
    sampled = w[::step]
    row_hashes = [hashlib.blake2b(row.tobytes(), digest_size=8).digest() for row in sampled]
    duplicated_sample = len(row_hashes) - len(set(row_hashes))
    sampled_rows = int(sampled.shape[0])

    t1 = time.monotonic()
    # Covariance in 256-d space; singular values of centered and uncentered matrix.
    s = np.linalg.svd(w, compute_uv=False, full_matrices=False)
    svd_s = time.monotonic() - t1
    energy = (s ** 2) / float(np.sum(s ** 2))
    cum = np.cumsum(energy)
    k90 = int(np.searchsorted(cum, 0.90) + 1)
    k95 = int(np.searchsorted(cum, 0.95) + 1)
    k99 = int(np.searchsorted(cum, 0.99) + 1)

    tok_path = model / "tokenizer" / "tokenizer.json"
    tok = json.loads(tok_path.read_text()) if tok_path.exists() else json.loads((model / "tokenizer.json").read_text())
    vocab = tok["model"]["vocab"]

    result = {
        "schema_version": 1,
        "tensor": header,
        "parameters": int(rows * dim),
        "dtype": header["embeddings"]["dtype"],
        "weight_bytes": int(header["embeddings"]["data_offsets"][1] - header["embeddings"]["data_offsets"][0]),
        "file_bytes": (model / "model.safetensors").stat().st_size,
        "load_seconds": round(load_s, 3),
        "rows": int(rows),
        "dimension": int(dim),
        "row_norm": {
            "min": float(norms.min()), "p01": float(np.percentile(norms, 1)),
            "p50": float(np.percentile(norms, 50)), "p99": float(np.percentile(norms, 99)),
            "max": float(norms.max()),
            "near_zero_lt_0.01": int((norms < 0.01).sum()),
            "near_zero_lt_0.05": int((norms < 0.05).sum()),
        },
        "column_norm": {
            "min": float(col_norms.min()), "p50": float(np.percentile(col_norms, 50)),
            "max": float(col_norms.max()),
            "near_zero_lt_1": int((col_norms < 1).sum()),
        },
        "sampled_rows": sampled_rows,
        "sampled_duplicate_rows": int(duplicated_sample),
        "singular_values": {
            "top10": [float(x) for x in s[:10]],
            "k_for_90pct_energy": k90,
            "k_for_95pct_energy": k95,
            "k_for_99pct_energy": k99,
            "svd_seconds": round(svd_s, 3),
        },
        "tokenizer": {
            "type": tok["model"]["type"],
            "vocab_size": len(vocab),
            "merges": len(tok["model"].get("merges", [])),
            "normalizer": tok.get("normalizer"),
            "pre_tokenizer": tok.get("pre_tokenizer"),
            "truncation": tok.get("truncation"),
            "padding": tok.get("padding"),
            "added_tokens": tok.get("added_tokens"),
        },
    }
    dest = out / "model-audit.json"
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "singular_values"}, indent=2)[:3000])
    print("singular", result["singular_values"])
    print("receipt", dest)


if __name__ == "__main__":
    main()
