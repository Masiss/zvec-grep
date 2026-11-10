#!/usr/bin/env bash
# Paired resource benchmark for the index-v3 foundation fixes.
#
# Runs an already-built `index_bench` binary from two worktrees (baseline and
# candidate) over the same synthetic corpora, alternating the order each
# repetition so host drift hits both variants. Receipts, host state and the
# optional per-file diagnostics reports are written under $OUT.
#
# Environment:
#   OUT              output root (default: ~/.cache/zvec-grep/out-foundation-opt)
#   BASELINE_BIN     path to the baseline `index_bench` binary
#   FINAL_BIN        path to the candidate `index_bench` binary
#   ZVEC_LIB_DIR     directory holding libzvec_c_api.so
#   REPS             repetitions per corpus/variant (default 5)
#   FILES            corpus size (default 300)
#   CORPORA          corpora to run (default "dense mixed")
set -u
OUT=${OUT:-$HOME/.cache/zvec-grep/out-foundation-opt}
BASELINE_BIN=${BASELINE_BIN:?set BASELINE_BIN}
FINAL_BIN=${FINAL_BIN:?set FINAL_BIN}
REPS=${REPS:-5}
FILES=${FILES:-300}
CORPORA=${CORPORA:-"dense mixed"}
REC=$OUT/receipts
WORK=$OUT/work
mkdir -p "$REC" "$WORK"
if [ -n "${ZVEC_LIB_DIR:-}" ]; then export LD_LIBRARY_PATH="$ZVEC_LIB_DIR"; fi

bin_for() { case "$1" in baseline) echo "$BASELINE_BIN" ;; *) echo "$FINAL_BIN" ;; esac; }

host_state() {
  {
    echo "== $1 $(date -Is)"
    echo "loadavg: $(cut -d' ' -f1-3 /proc/loadavg)"
    grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
    ps -eo pid,comm,%cpu,rss --sort=-%cpu | head -6
  } >> "$OUT/host-state.log"
}

run_variant() {
  local variant="$1" corpus="$2" rep="$3"
  local bin root diag
  bin=$(bin_for "$variant")
  root="$WORK/$corpus/$variant/r$rep"
  diag="$REC/$corpus-$variant-r$rep-diagnostics.json"
  rm -rf "$root"; mkdir -p "$root"
  host_state "before $corpus $variant r$rep"
  ZVEC_GREP_INDEX_DIAGNOSTICS="$diag" \
    /usr/bin/time -v "$bin" --root "$root" --out "$REC/$corpus-$variant-r$rep-full.json" \
      --phase full --corpus "$corpus" --files "$FILES" --index-version 3 \
      --diagnostics "$diag" \
      2> "$REC/$corpus-$variant-r$rep-full.time" >/dev/null
  ZVEC_GREP_INDEX_DIAGNOSTICS="$REC/$corpus-$variant-r$rep-incremental-diag.json" \
    /usr/bin/time -v "$bin" --root "$root" --out "$REC/$corpus-$variant-r$rep-incremental.json" \
      --phase incremental --change body --corpus "$corpus" --files "$FILES" --index-version 3 \
      --diagnostics "$REC/$corpus-$variant-r$rep-incremental-diag.json" \
      2> "$REC/$corpus-$variant-r$rep-incremental.time" >/dev/null
  host_state "after $corpus $variant r$rep"
}

for corpus in $CORPORA; do
  for rep in $(seq 1 "$REPS"); do
    if [ $((rep % 2)) -eq 1 ]; then ORDER="baseline final"; else ORDER="final baseline"; fi
    for variant in $ORDER; do run_variant "$variant" "$corpus" "$rep"; done
  done
  for variant in baseline final; do
    "$(bin_for "$variant")" --root "$WORK/$corpus/$variant/r1" \
      --out "$REC/$corpus-$variant-query.json" --phase query --corpus "$corpus" \
      --files "$FILES" --index-version 3 >/dev/null 2>&1 || true
  done
done
echo PAIRED_DONE
