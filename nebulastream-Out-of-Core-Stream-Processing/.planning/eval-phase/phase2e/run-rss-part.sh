#!/usr/bin/env bash
# Phase 2e trigger measurement — peak-RSS of the OOC tumbling-aggregation workload WITH partition-at-spill (P>1).
#
# Identical to phase0c/run-rss.sh but adds the 2d `number_of_partitions` knob (arg 11, on-mode only). Decides whether
# 2e (partition-granularity backpressure) is needed: per DESIGN-BRIEF §3/§7, build 2e IFF the 320 cell at threads>1,
# P>1 still returns rc=137 (OOM-kill) OR store_peak ~= the whole retained band. If rc=0 and store_peak << the P=1 peak,
# partition-at-spill alone bounded the process and 2e is NOT built for Phase 2.
#
# Usage:
#   run-rss-part.sh <label> <cap_mb> <spill:on|off> <pool> <soft_mb> <hard_mb> <emit_lag_ms> <threads> <wbuf_mb> <cache_mb> <num_partitions>
# 2e-trigger example (320 MB cap, threads=4, P=8):
#   ./run-rss-part.sh F20_p8_320 320 on 16000 4 8 2000000 4 0 0 8
#   ./run-rss-part.sh F20_p1_320 320 on 16000 4 8 2000000 4 0 0 1   # P=1 baseline (expected: the Phase-1 OOM)
set -u

LABEL="${1:?label}"; CAP_MB="${2:?cap_mb}"; SPILL="${3:?on|off}"; POOL="${4:?pool}"
SOFT="${5:?soft_mb}"; HARD="${6:?hard_mb}"; EMIT_LAG="${7:?emit_lag_ms}"; THREADS="${8:?threads}"
WBUF="${9:-0}"; CACHE="${10:-0}"; NPART="${11:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMG="nebulastream/nes-development:local-rocksdb"
BUILD_DIR="cmake-build-debug"
SYSTEST="$BUILD_DIR/nes-systests/systest/systest"
TEST_REL=".planning/eval-phase/phase0c/SpillSmokeVoid.test"   # reuse the Void-sink workload (no result-diff pollution)
RESULTS_TSV="$SCRIPT_DIR/results.tsv"
LOG_DIR="$SCRIPT_DIR/logs"; mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/${LABEL}.log"

case "$SPILL" in
  on)
    SPILL_ARGS="--worker.default_query_execution.spill.enabled=true \
--worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill-${LABEL} \
--worker.default_query_execution.spill.soft_threshold_mb=${SOFT} \
--worker.default_query_execution.spill.hard_threshold_mb=${HARD} \
--worker.default_query_execution.spill.write_buffer_size_mb=${WBUF} \
--worker.default_query_execution.spill.block_cache_size_mb=${CACHE} \
--worker.default_query_execution.spill.number_of_partitions=${NPART} \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}" ;;
  off)
    SPILL_ARGS="--worker.default_query_execution.spill.enabled=false \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}" ;;
  *) echo "[run-rss-part] unknown spill mode '$SPILL' (want on|off)"; exit 2 ;;
esac

WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${THREADS} \
${SPILL_ARGS}"

echo "[run-rss-part] label=$LABEL cap=${CAP_MB}MB spill=$SPILL pool=$POOL soft=$SOFT hard=$HARD emit_lag=$EMIT_LAG threads=$THREADS wbuf=$WBUF cache=$CACHE P=$NPART"

docker run --rm --memory="${CAP_MB}m" --memory-swap="${CAP_MB}m" --cpus="${THREADS}" \
  --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
    rm -rf /tmp/nes-spill-${LABEL} 2>/dev/null
    mkdir -p /tmp/nes-spill-${LABEL}
    set +e
    ./$SYSTEST -t $TEST_REL -- $WORKER_ARGS
    RC=\$?
    PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
    SPILL_BYTES=\$(du -sb /tmp/nes-spill-${LABEL} 2>/dev/null | cut -f1 || echo 0)
    NESLOG=\$(ls -t /workspace/cmake-build-debug/nes-systests/SystemTest_*.log 2>/dev/null | head -1)
    METRICS=\$(grep -h 'logMetrics' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)
    echo \"PHASE2E_RC=\$RC\"
    echo \"PHASE2E_PEAK_BYTES=\$PEAK\"
    echo \"PHASE2E_SPILL_DIR_BYTES=\$SPILL_BYTES\"
    echo \"PHASE2E_METRICS=\$METRICS\"
  " > "$RUN_LOG" 2>&1
DOCKER_RC=$?

RC="$(grep -oE 'PHASE2E_RC=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
PEAK="$(grep -oE 'PHASE2E_PEAK_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
SPILL_DIR="$(grep -oE 'PHASE2E_SPILL_DIR_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
[ -z "${RC:-}" ] && RC="$DOCKER_RC(container)"
[ -z "${PEAK:-}" ] && PEAK="NA(container-killed)"
[ -z "${SPILL_DIR:-}" ] && SPILL_DIR="0"
PEAK_MB="NA"; [[ "$PEAK" =~ ^[0-9]+$ ]] && PEAK_MB=$(( PEAK / 1024 / 1024 ))
SPILL_MB="0"; [[ "$SPILL_DIR" =~ ^[0-9]+$ ]] && SPILL_MB=$(( SPILL_DIR / 1024 / 1024 ))
METRICS="$(grep -E 'PHASE2E_METRICS=' "$RUN_LOG" | tail -1 | sed 's/^PHASE2E_METRICS=//')"
[ -z "$METRICS" ] && METRICS="(no SpillMetrics line — query failed before stopSpiller, or OOM-killed)"
STORE_PEAK_B="$(echo "$METRICS" | grep -oE 'peak resident [0-9]+' | grep -oE '[0-9]+' | tail -1)"
SLICES_SPILLED="$(echo "$METRICS" | grep -oE 'spilled [0-9]+ slices' | grep -oE '[0-9]+' | tail -1)"
STORE_PEAK_MB="NA"; [[ "$STORE_PEAK_B" =~ ^[0-9]+$ ]] && STORE_PEAK_MB=$(( STORE_PEAK_B / 1024 / 1024 ))
[ -z "$SLICES_SPILLED" ] && SLICES_SPILLED="0"

echo "[run-rss-part] RESULT label=$LABEL P=$NPART rc=$RC peak=${PEAK_MB}MB store_peak=${STORE_PEAK_MB}MB spilled=${SLICES_SPILLED} spill_dir=${SPILL_MB}MB"
echo "[run-rss-part] metrics: $METRICS"

if [ ! -f "$RESULTS_TSV" ]; then
  printf 'label\tcap_mb\tspill\tpool\tsoft_mb\thard_mb\temit_lag_ms\tthreads\twbuf_mb\tcache_mb\tnum_partitions\trc\tpeak_mb\tstore_peak_mb\tslices_spilled\tspill_dir_mb\n' > "$RESULTS_TSV"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$CAP_MB" "$SPILL" "$POOL" "$SOFT" "$HARD" "$EMIT_LAG" "$THREADS" "$WBUF" "$CACHE" "$NPART" "$RC" "$PEAK_MB" "$STORE_PEAK_MB" "$SLICES_SPILLED" "$SPILL_MB" >> "$RESULTS_TSV"
