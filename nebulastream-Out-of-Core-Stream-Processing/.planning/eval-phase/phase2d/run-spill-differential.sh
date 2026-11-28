#!/usr/bin/env bash
# 2d differential-correctness gate (the key-disjoint dup-row guard).
#
# Runs the SAME spill query (SpillPartitionDifferential.test) twice inside the local-rocksdb Docker image:
#   pass A: spill.number_of_partitions=1  (baseline; byte-identical to the non-partitioned terminal emit)
#   pass B: spill.number_of_partitions=8  (partitioned terminal emit — K contiguous chunks per window)
# then DIFFS the two File-sink outputs. They MUST be identical (same rows, no duplicates, no drops): identical output
# proves the partitioned chunks are key-disjoint and complete vs the single-chunk P=1 emit.
#
# Usage:  ./run-spill-differential.sh [cap_mb] [pool_buffers] [soft_mb] [hard_mb] [emit_lag_ms] [threads]
# Example (512 MB cap, big pool, hard=8/soft=4, 2-window band via emit_lag, 2 threads — partitioning matters at P>1):
#   ./run-spill-differential.sh 512 16000 4 8 200000 2
#
# This script documents the exact invocation; run it manually on the evaluation host.
set -euo pipefail

CAP_MB="${1:-512}"; POOL="${2:-16000}"; SOFT="${3:-4}"; HARD="${4:-8}"; EMIT_LAG="${5:-200000}"; THREADS="${6:-2}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"      # .planning/eval-phase/phase2d -> repo root
IMG="nebulastream/nes-development:local-rocksdb"
BUILD_DIR="cmake-build-debug"
SYSTEST="$BUILD_DIR/nes-systests/systest/systest"
TEST_REL=".planning/eval-phase/phase2d/SpillPartitionDifferential.test"
OUT_DIR="$SCRIPT_DIR/diff-out"; mkdir -p "$OUT_DIR"

# Run one pass: $1 = label, $2 = number_of_partitions. Captures the File-sink result into $OUT_DIR/<label>.csv (sorted),
# so the diff is order-insensitive (the chunk-count difference between P=1 and P>1 only changes emit ORDER, not rows).
run_pass() {
  local LABEL="$1" NPART="$2"
  local SPILL_ARGS="--worker.default_query_execution.spill.enabled=true \
--worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill-${LABEL} \
--worker.default_query_execution.spill.soft_threshold_mb=${SOFT} \
--worker.default_query_execution.spill.hard_threshold_mb=${HARD} \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG} \
--worker.default_query_execution.spill.number_of_partitions=${NPART}"
  local WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${THREADS} \
${SPILL_ARGS}"

  echo "[diff] pass=$LABEL number_of_partitions=$NPART cap=${CAP_MB}MB threads=$THREADS"
  # systest writes each query's produced rows to <workingDir>/results/<testName>_<queryId>.csv (SystestState.cpp:167).
  # We point --workingDir at a per-pass temp dir, then cat every results/*.csv. Sorting makes the diff order-insensitive
  # (the chunk-count difference between P=1 and P>1 changes emit ORDER only, not the row set).
  docker run --rm --memory="${CAP_MB}m" --memory-swap="${CAP_MB}m" --cpus="${THREADS}" \
    --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
      rm -rf /tmp/nes-spill-${LABEL} /tmp/diff-wd-${LABEL} 2>/dev/null
      mkdir -p /tmp/nes-spill-${LABEL} /tmp/diff-wd-${LABEL}
      set +e
      ./$SYSTEST -t $TEST_REL --workingDir=/tmp/diff-wd-${LABEL} -- $WORKER_ARGS
      echo \"DIFF_RC=\$?\"
      # Emit every produced result row so the host can diff (markers are filtered out below).
      find /tmp/diff-wd-${LABEL}/results -type f -name '*.csv' 2>/dev/null -exec cat {} +
    " > "$OUT_DIR/${LABEL}.raw" 2>&1
  # Strip runner markers, keep only the CSV result rows (start,end,key,sumVal => 4 unsigned ints), sorted.
  grep -E '^[0-9]+,[0-9]+,[0-9]+,[0-9]+$' "$OUT_DIR/${LABEL}.raw" | LC_ALL=C sort > "$OUT_DIR/${LABEL}.csv" || true
  echo "[diff] $LABEL rows: $(wc -l < "$OUT_DIR/${LABEL}.csv")  rc: $(grep -oE 'DIFF_RC=[0-9]+' "$OUT_DIR/${LABEL}.raw" | tail -1)"
}

run_pass "p1" 1
run_pass "p8" 8

echo "[diff] comparing p1 vs p8 ..."
if diff -u "$OUT_DIR/p1.csv" "$OUT_DIR/p8.csv"; then
  echo "[diff] PASS: P=1 and P=8 produced byte-identical (sorted) output — key-disjoint + complete."
  exit 0
else
  echo "[diff] FAIL: P=1 and P=8 outputs differ — see the diff above (rows added/dropped/duplicated)."
  exit 1
fi
