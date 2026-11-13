#!/bin/bash
# E4 index runner with resource receipts. Run one arm at a time.
# Usage: index-arm.sh <root> <cache> <model> <name> <out-dir>
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=${1:?root}; CACHE=${2:?cache}; MODEL=${3:?model}; NAME=${4:?name}; OUT=${5:?out}
mkdir -p "$OUT"
{ date -u +%FT%TZ; uptime; free -h; df -h "$ROOT" | tail -1; pgrep -a -f 'e3-retrieval-harness|zg-engine' || true; } > "$OUT/$NAME.pre.txt"
/usr/bin/time -v "$HERE/tools/run_harness.sh" index \
  --root="$ROOT" --cache="$CACHE" --model="$MODEL" --rebuild=true \
  > "$OUT/$NAME.log" 2> "$OUT/$NAME.time"
{ date -u +%FT%TZ; uptime; free -h; df -h "$ROOT" | tail -1; } > "$OUT/$NAME.post.txt"
cat "$OUT/$NAME.log"
grep -E 'Elapsed|User time|System time|Maximum resident' "$OUT/$NAME.time"
