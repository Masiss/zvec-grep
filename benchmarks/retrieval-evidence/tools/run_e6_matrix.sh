#!/bin/bash
# Run the E6 measurement contract over pure FTS, pure vector, and intended
# hybrid.  No result is emitted unless the harness contract says PASS and the
# independent verifier accepts it.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=${1:?root}
CACHE=${2:?cache}
OUT=${3:?out}
MODEL=${4:?model}
REPEATS=${5:-1}
DATASET=${6:-$HERE/dataset/queries.json}
HARNESS=${HARNESS:-$HERE/harness/target/release/e3-retrieval-harness}

mkdir -p "$OUT"
for route in fts vector hybrid; do
  for run in $(seq 1 "$REPEATS"); do
    name="$(basename "$OUT")-$route-r$run"
    echo "=== $name"
    /usr/bin/time -v "$HARNESS" query \
      --root="$ROOT" --cache="$CACHE" --dataset="$DATASET" \
      --route="$route" --limit=50 --model="$MODEL" \
      --output="$OUT/$route-r$run.json" 2>"$OUT/$route-r$run.time"
    python3 "$HERE/tools/evaluate.py" \
      --dataset "$DATASET" --results "$OUT/$route-r$run.json" \
      --output "$OUT/$route-r$run.report.json"
    python3 "$HERE/tools/verify_e6.py" \
      --dataset "$DATASET" --results "$OUT/$route-r$run.json" \
      --report "$OUT/$route-r$run.report.json" \
      --output "$OUT/$route-r$run.verify.json"
  done
done
python3 "$HERE/tools/aggregate_e6.py" --report-dir "$OUT" --output "$OUT/summary.json"
