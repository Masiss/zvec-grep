#!/usr/bin/env bash
set -euo pipefail

# Linux/Ninja builder for the native zvec_c_api (research + integration).
# Mirrors rust/scripts/build-zvec-macos.sh intent but targets the Linux host and
# the relocated checkout at .local/native.  Does not install dependencies and does
# not change the on-disk C++ format/ABI.
#
# Configurables (env or -D):
#   ZVEC_SOURCE_DIR  = repo/.local/native/zvec-native-profile
#   ZVEC_BUILD_DIR    = repo/.local/native/zvec-native-build-ninja
#   ZVEC_RUNTIME_DIR  = repo/rust/target/zvec-runtime
#   ZVEC_JOBS         = outside parallel (default: min(nproc, avail-RAM/768MiB, 8))
#   ZVEC_ARROW_JOBS   = nested Arrow jobs (default: min(JOBS/2, 8), clamped [2, 8])

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
readonly SOURCE_DIR="${ZVEC_SOURCE_DIR:-$REPO_ROOT/.local/native/zvec-native-profile}"
readonly BUILD_DIR="${ZVEC_BUILD_DIR:-$REPO_ROOT/.local/native/zvec-native-build-ninja}"
readonly RUNTIME_DIR="${ZVEC_RUNTIME_DIR:-$REPO_ROOT/rust/target/zvec-runtime}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "zvec linux build requires Linux" >&2
  exit 1
fi

for tool in cmake ninja; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing required tool: $tool" >&2
    exit 1
  fi
done

if [[ ! -f "$SOURCE_DIR/CMakeLists.txt" ]]; then
  echo "zvec source not found at $SOURCE_DIR (expect .local/native/zvec-native-profile)" >&2
  exit 1
fi

# Job sizing based on available memory (keep the bill down to avoid swap).
avail_kb="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
avail_mb=$((avail_kb / 1024))
default_jobs=$((avail_mb / 768))
if (( default_jobs < 1 )); then default_jobs=1; fi
if (( default_jobs > 8 )); then default_jobs=8; fi
default_jobs=$((default_jobs < $(nproc) ? default_jobs : $(nproc)))

JOBS="${ZVEC_JOBS:-$default_jobs}"
ARROW_DEFAULT=$((JOBS / 2))
if (( ARROW_DEFAULT < 2 )); then ARROW_DEFAULT=2; fi
if (( ARROW_DEFAULT > 8 )); then ARROW_DEFAULT=8; fi
ARROW_JOBS="${ZVEC_ARROW_JOBS:-$ARROW_DEFAULT}"

echo "Linux build: source=$SOURCE_DIR build=$BUILD_DIR runtime=$RUNTIME_DIR"
echo "jobs=$JOBS arrow_jobs=$ARROW_JOBS avail_mb=$avail_mb"

# launch via ccache/sccache if present (cache is separate from the build, opt-in).
CMAKE_LAUNCHER_ARGS=()
if command -v ccache >/dev/null 2>&1; then
  echo "ccache detected; enabling C/C++ compiler launcher"
  CMAKE_LAUNCHER_ARGS=(
    -DCMAKE_C_COMPILER_LAUNCHER=ccache
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
  )
fi

cmake -S "$SOURCE_DIR" -B "$BUILD_DIR" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DBUILD_C_BINDINGS=ON \
  -DBUILD_PYTHON_BINDINGS=OFF \
  -DBUILD_TOOLS=OFF \
  -DBUILD_CPP_EXAMPLES=OFF \
  -DZVEC_ENABLE_LTO=OFF \
  -DZVEC_ARROW_JOBS="$ARROW_JOBS" \
  "${CMAKE_LAUNCHER_ARGS[@]}"

cmake --build "$BUILD_DIR" --target zvec_c_api -j "$JOBS"

mkdir -p "$RUNTIME_DIR/data/jieba_dict"
jscpp_dir="$BUILD_DIR/lib"
# copy whatever shared library variant the build produced (libzvec_c_api.so)
for lib in "$jscpp_dir"/libzvec_c_api.so; do
  if [[ -f "$lib" ]]; then cp -f "$lib" "$RUNTIME_DIR/"; fi
done
# jieba dictionaries
DICT_SRC="$SOURCE_DIR/thirdparty/cppjieba/cppjieba-5.6.7/dict"
for f in "$DICT_SRC/jieba.dict.utf8" "$DICT_SRC/hmm_model.utf8"; do
  if [[ -f "$f" ]]; then cp -f "$f" "$RUNTIME_DIR/data/jieba_dict/"; fi
done

echo "Build complete: $RUNTIME_DIR/libzvec_c_api.so"
ls -la "$RUNTIME_DIR/libzvec_c_api.so" 2>/dev/null || true