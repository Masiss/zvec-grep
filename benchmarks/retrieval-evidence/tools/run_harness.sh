#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TARGET=${CARGO_TARGET_DIR:-/home/masis/.cache/zvec-grep/e3-01/target}
LIB=$(find "$TARGET" -path '*/release/build/zvec-rust-sys-*/out/zvec-prebuilt/libzvec_c_api.so' -print -quit)
if [[ -z "$LIB" ]]; then
  echo "native zvec library not found under $TARGET; build the harness first" >&2
  exit 1
fi
export LD_LIBRARY_PATH="$(dirname "$LIB")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$TARGET/release/e3-retrieval-harness" "$@"
