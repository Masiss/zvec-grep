#!/usr/bin/env bash
# W2 PoC build. Requires the local native zvec build at
# .local/native/zvec-native-build and source headers at
# .local/native/zvec-native-profile.
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../../.." && pwd)
out="$root/benchmarks/harness-v2/operations/deep-storage-integrity/w2/build"
inc_src="$root/.local/native/zvec-native-profile/src/include"
inc_rocks="$root/.local/native/zvec-native-profile/thirdparty/rocksdb/rocksdb-8.1.1/include"
lib_ailego="$root/.local/native/zvec-native-build/lib/libzvec_ailego.a"
lib_rocks="$root/.local/native/zvec-native-build/external/usr/local/lib/librocksdb.a"
mkdir -p "$out"
g++ -std=c++17 -O2 -fPIC -shared \
  -I "$inc_src" -I "$inc_rocks" \
  -x c++ "$(dirname "$0")/w2_verify.cc.in" -x none \
  "$lib_ailego" "$lib_rocks" \
  -lzstd -llz4 -lsnappy -lbz2 -lz -lpthread -ldl -lrt \
  -o "$out/libzvec_w2_verify.so"
g++ -std=c++17 -O2 -DW2_STANDALONE \
  -I "$inc_src" -I "$inc_rocks" \
  -x c++ "$(dirname "$0")/w2_verify.cc.in" -x none \
  "$lib_ailego" "$lib_rocks" \
  -lzstd -llz4 -lsnappy -lbz2 -lz -lpthread -ldl -lrt \
  -o "$out/w2_verify"
