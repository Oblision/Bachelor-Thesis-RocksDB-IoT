#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROCKSDB_DIR="$(cd "$SCRIPT_DIR/../rocksdb" && pwd)"
BUILD_DIR="$ROCKSDB_DIR/build"

echo "=== RocksDB db_bench Build Script ==="
echo "RocksDB source: $ROCKSDB_DIR"
echo "Build directory: $BUILD_DIR"

# --- Dependency check ---
echo ""
echo "--- Checking dependencies ---"
MISSING=()

command -v cmake >/dev/null 2>&1 || MISSING+=("cmake")
command -v make  >/dev/null 2>&1 || MISSING+=("make")

# Check for libraries via pkg-config or Homebrew (brew list, not brew --prefix)
check_lib() {
    local name="$1"
    if pkg-config --exists "$name" 2>/dev/null; then
        return 0
    elif brew list "$name" >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

for lib in gflags snappy lz4 zstd zlib; do
    if ! check_lib "$lib"; then
        MISSING+=("$lib")
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "ERROR: Missing dependencies: ${MISSING[*]}"
    echo ""
    echo "Install on macOS with Homebrew:"
    echo "  brew install cmake gflags snappy lz4 zstd zlib"
    echo ""
    echo "Install on Ubuntu/Debian:"
    echo "  sudo apt-get install -y cmake libgflags-dev libsnappy-dev liblz4-dev libzstd-dev zlib1g-dev"
    exit 1
fi
echo "All dependencies found."

# --- Build ---
echo ""
echo "--- Building db_bench (Release) ---"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Detect CPU count
if [[ "$(uname)" == "Darwin" ]]; then
    NPROC=$(sysctl -n hw.ncpu)
else
    NPROC=$(nproc)
fi

# On macOS, tell CMake where Homebrew installs libraries
CMAKE_EXTRA_ARGS=()
if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    HOMEBREW_PREFIX="$(brew --prefix)"
    CMAKE_EXTRA_ARGS+=(
        "-DCMAKE_PREFIX_PATH=$HOMEBREW_PREFIX"
        "-DGFLAGS_INCLUDE_DIR=$HOMEBREW_PREFIX/include"
        "-DGFLAGS_LIBRARIES=$HOMEBREW_PREFIX/lib/libgflags.dylib"
    )
fi

cmake -DCMAKE_BUILD_TYPE=Release \
      -DWITH_GFLAGS=1 \
      -DWITH_SNAPPY=1 \
      -DWITH_LZ4=1 \
      -DWITH_ZSTD=1 \
      -DWITH_ZLIB=1 \
      -DWITH_BENCHMARK_TOOLS=ON \
      -DWITH_ALL_TESTS=OFF \
      -DROCKSDB_BUILD_SHARED=0 \
      "${CMAKE_EXTRA_ARGS[@]}" \
      ..

make -j"$NPROC" db_bench

# --- Verify ---
if [ -f "$BUILD_DIR/db_bench" ]; then
    echo ""
    echo "=== Build successful ==="
    echo "Binary: $BUILD_DIR/db_bench"
    "$BUILD_DIR/db_bench" --version 2>/dev/null || true
else
    echo "ERROR: db_bench binary not found after build."
    exit 1
fi
