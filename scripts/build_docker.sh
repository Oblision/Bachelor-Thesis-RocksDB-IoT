#!/usr/bin/env bash
# Build the Docker image for memory-constrained IoT benchmarks.
#
# Usage:
#   bash scripts/build_docker.sh
#
# Requires Docker Desktop to be running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="rocksdb-bench-iot"

cd "$PROJECT_ROOT"

# Check Docker is available
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker not found. Install with: brew install --cask docker"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Docker daemon not running. Start Docker Desktop first."
    exit 1
fi

echo "=== Building Docker image: $IMAGE_NAME ==="
echo "This will compile RocksDB from source inside the container."
echo "Estimated time: 15-30 minutes on first build."
echo ""

docker build -t "$IMAGE_NAME" .

echo ""
echo "=== Build complete. Verifying... ==="

# Sanity check: run --version
docker run --rm "$IMAGE_NAME" --version

echo ""
echo "Docker image '$IMAGE_NAME' is ready."
echo "Run constrained benchmarks with:"
echo "  python3 scripts/run_benchmarks.py --constrained --quick"
