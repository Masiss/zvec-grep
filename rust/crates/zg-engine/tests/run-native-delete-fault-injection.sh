#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    echo "native delete fault interposer requires Linux/LD_PRELOAD" >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../../../../" && pwd)"
artifact_dir="${ZVEC_FAULT_TEST_ARTIFACT_DIR:?set ZVEC_FAULT_TEST_ARTIFACT_DIR under the repository .tmp directory}"
case "$artifact_dir" in
    "$repo_root"/.tmp/*) ;;
    *) echo "artifact directory must be under $repo_root/.tmp" >&2; exit 2 ;;
esac
mkdir -p "$artifact_dir" "$repo_root/.tmp"

shim="$artifact_dir/libzvec-delete-fault-interposer.so"
cc -std=c11 -O2 -Wall -Wextra -Werror -fPIC -shared -pthread \
    -o "$shim" "$script_dir/support/native_delete_fault_interposer.c" -ldl

cd "$repo_root/rust"
export TMPDIR="$repo_root/.tmp"
export LD_PRELOAD="$shim"
export ZVEC_TEST_FAULT_INJECT_DELETE=1
exec cargo test --locked --offline -p zg-engine --lib \
    partial_primary_key_delete_failure_is_reported_after_prior_success -- --nocapture
