#!/usr/bin/env bash
# M3 incremental scenario matrix: none, touch, body, prepend, rename, delete,
# samesize, append. Each scenario gets a fresh index so scenarios cannot
# influence each other.
#
# Environment: OUT, BENCH_BIN, ZVEC_LIB_DIR, FILES (see foundation-paired-bench.sh)
set -u
OUT=${OUT:-$HOME/.cache/zvec-grep/out-foundation-opt}
BENCH_BIN=${BENCH_BIN:?set BENCH_BIN}
FILES=${FILES:-300}
mkdir -p "$OUT/m3"
if [ -n "${ZVEC_LIB_DIR:-}" ]; then export LD_LIBRARY_PATH="$ZVEC_LIB_DIR"; fi
for scenario in none touch body prepend rename delete samesize append; do
  root=$OUT/m3/$scenario/root
  rm -rf "$OUT/m3/$scenario"; mkdir -p "$root"
  "$BENCH_BIN" --root "$root" --out "$OUT/m3/$scenario/full.json" --phase full \
    --corpus dense --files "$FILES" --index-version 3 >/dev/null 2>&1
  ZVEC_GREP_INDEX_DIAGNOSTICS="$OUT/m3/$scenario/incremental-diag.json" \
    /usr/bin/time -f "%U %S %e %M %O" -o "$OUT/m3/$scenario/incremental.time" \
    "$BENCH_BIN" --root "$root" --out "$OUT/m3/$scenario/incremental.json" --phase incremental \
      --change "$scenario" --corpus dense --files "$FILES" --index-version 3 \
      --diagnostics "$OUT/m3/$scenario/incremental-diag.json" >/dev/null 2>&1
  echo "done $scenario"
done
