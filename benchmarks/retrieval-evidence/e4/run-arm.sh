#!/bin/bash
# E4 arm runner: fixed-order repeated route runs against one frozen index.
# Usage: run-arm.sh <root> <cache> <out> <model> <prefix> [repeats] [dataset]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=${1:?root}
CACHE=${2:?cache}
OUT=${3:?out}
MODEL=${4:?model}
PREFIX=${5:?prefix}
REPEATS=${6:-3}
DATASET=${7:-$HERE/dataset/queries.json}
mkdir -p "$OUT"
for route in fts vector hybrid; do
  for run in $(seq 1 "$REPEATS"); do
    name="$PREFIX-$route-r$run"
    echo "=== $name"
    /usr/bin/time -v "$HERE/tools/run_harness.sh" query \
      --root="$ROOT" --cache="$CACHE" --dataset="$DATASET" \
      --route="$route" --limit=50 --model="$MODEL" \
      --output="$OUT/$name.json" 2>"$OUT/$name.time"
    "${PYTHON:-python3}" "$HERE/tools/evaluate.py" \
      --dataset="$DATASET" --results="$OUT/$name.json" \
      --output="$OUT/$name.score.json"
    grep -E 'Elapsed|User time|System time|Maximum resident' "$OUT/$name.time"
  done
done
