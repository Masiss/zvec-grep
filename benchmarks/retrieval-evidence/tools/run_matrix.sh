#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=${1:?root}
CACHE=${2:?cache}
OUT=${3:?out}
MODEL=${4:-local/embeddinggemma-300m}
REPEATS=${5:-3}
DATASET=${6:-$HERE/dataset/queries.json}
mkdir -p "$OUT"
for route in fts vector hybrid; do
  for run in $(seq 1 "$REPEATS"); do
    name="gemma-$route-r$run"
    echo "=== $name ==="
    /usr/bin/time -v "$HERE/tools/run_harness.sh" query \
      --root="$ROOT" --cache="$CACHE" --dataset="$DATASET" \
      --route="$route" --limit=50 --model="$MODEL" \
      --output="$OUT/$name.json" 2>"$OUT/$name.time"
    grep -E 'Elapsed|User time|System time|Maximum resident' "$OUT/$name.time"
  done
done
