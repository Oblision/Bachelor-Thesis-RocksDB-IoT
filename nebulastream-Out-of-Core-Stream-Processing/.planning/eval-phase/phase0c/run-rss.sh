#!/usr/bin/env bash
# Phase 0c — clean peak-RSS measurement harness for the OOC tumbling-aggregation workload.
#
# Runs the Void-sink workload (SpillSmokeVoid.test) inside a memory-capped Docker container and reports the
# QUERY's peak RSS via the container cgroup-v2 `memory.peak`, with the result check eliminated (Void sink) so the
# number is NOT polluted by the systest 2M-row diff. Also captures the Phase-0b SpillMetrics NES_INFO line, the
# RocksDB spill-dir size, and the systest exit code (0 ok / 137 OOM-kill / 141 buffer-starvation).
#
# Usage:
#   run-rss.sh <label> <cap_mb> <spill:on|off> <pool_buffers> <soft_mb> <hard_mb> <emit_lag_ms> <threads> <wbuf_mb> <cache_mb>
# Example (spill on, 512 MB cap, 16000-buffer pool, hard=8/soft=4, 2-window band, 1 thread, RocksDB defaults):
#   ./run-rss.sh G_on512 512 on 16000 4 8 200000 1 0 0
# Example (spill OFF baseline at the same cap):
#   ./run-rss.sh B_off512 512 off 16000 0 0 200000 1 0 0
#
# All knobs map to NES config dotted paths passed via systest's `-- --worker.<path>=<val>` passthrough.
set -u

# ---- args ----------------------------------------------------------------------------------------------------
LABEL="${1:?label}"; CAP_MB="${2:?cap_mb}"; SPILL="${3:?on|off}"; POOL="${4:?pool_buffers}"
SOFT="${5:?soft_mb}"; HARD="${6:?hard_mb}"; EMIT_LAG="${7:?emit_lag_ms}"; THREADS="${8:?threads}"
WBUF="${9:-0}"; CACHE="${10:-0}"

# ---- paths (host) --------------------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"      # .planning/eval-phase/phase0c -> repo root
IMG="nebulastream/nes-development:local-rocksdb"
BUILD_DIR="cmake-build-debug"
SYSTEST="$BUILD_DIR/nes-systests/systest/systest"
TEST_REL=".planning/eval-phase/phase0c/SpillSmokeVoid.test"
RESULTS_TSV="$SCRIPT_DIR/results.tsv"
LOG_DIR="$SCRIPT_DIR/logs"; mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/${LABEL}.log"

# ---- spill mode -> config override set -----------------------------------------------------------------------
# Three modes (the 2nd is the FAIR no-OOC baseline that OOC must improve on):
#   on       = retain band (emit_lag) + spill to RocksDB at soft/hard thresholds (the OOC system under test).
#   inmem    = retain band (emit_lag) but NEVER spill (enabled=true so the spillable store + emit_lag are active,
#              but soft/hard set to ~infinity so the spiller never evicts) => the whole band stays resident in the
#              pool. This is "what OOC replaces": it OOMs once the resident band exceeds RAM. (RocksDB is opened but
#              unused, so it carries a small fixed RocksDB-open overhead vs a pure in-mem store — noted in results.)
#   off      = enabled=false => DefaultTimeBasedSliceStore, which IGNORES emit_lag (the lag logic only exists in the
#              spillable store) => continuous emit, NO band retained. This is a plain query, NOT the OOC baseline.
case "$SPILL" in
  on)
    SPILL_ARGS="--worker.default_query_execution.spill.enabled=true \
--worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill-${LABEL} \
--worker.default_query_execution.spill.soft_threshold_mb=${SOFT} \
--worker.default_query_execution.spill.hard_threshold_mb=${HARD} \
--worker.default_query_execution.spill.write_buffer_size_mb=${WBUF} \
--worker.default_query_execution.spill.block_cache_size_mb=${CACHE} \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}" ;;
  inmem)
    SPILL_ARGS="--worker.default_query_execution.spill.enabled=true \
--worker.default_query_execution.spill.rocksdb_path=/tmp/nes-spill-${LABEL} \
--worker.default_query_execution.spill.soft_threshold_mb=1000000 \
--worker.default_query_execution.spill.hard_threshold_mb=1000000 \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}" ;;
  off)
    SPILL_ARGS="--worker.default_query_execution.spill.enabled=false \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}" ;;
  *) echo "[run-rss] unknown spill mode '$SPILL' (want on|inmem|off)"; exit 2 ;;
esac

WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${THREADS} \
${SPILL_ARGS}"

echo "[run-rss] label=$LABEL cap=${CAP_MB}MB spill=$SPILL pool=$POOL soft=$SOFT hard=$HARD emit_lag=$EMIT_LAG threads=$THREADS wbuf=$WBUF cache=$CACHE"
echo "[run-rss] worker args: $WORKER_ARGS"

# ---- run inside a memory-capped container --------------------------------------------------------------------
# The container IS the cgroup; /sys/fs/cgroup/memory.peak is its lifetime peak RSS. We read it right after systest
# exits (still inside the same container) so it captures systest's peak even on a non-fatal failure.
docker run --rm --memory="${CAP_MB}m" --memory-swap="${CAP_MB}m" --cpus="${THREADS}" \
  --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
    rm -rf /tmp/nes-spill-${LABEL} 2>/dev/null
    mkdir -p /tmp/nes-spill-${LABEL}   # RocksDB create_if_missing only makes the leaf 'agg-<id>' dir, not its parent
    set +e
    ./$SYSTEST -t $TEST_REL -- $WORKER_ARGS
    RC=\$?
    PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
    SPILL_BYTES=\$(du -sb /tmp/nes-spill-${LABEL} 2>/dev/null | cut -f1 || echo 0)
    # Phase-0b logMetrics() logs at NES_INFO into the per-run SystemTest_*.log file, NOT stdout. Grab it from the
    # newest such log (systest writes a fresh one per invocation).
    NESLOG=\$(ls -t /workspace/cmake-build-debug/nes-systests/SystemTest_*.log 2>/dev/null | head -1)
    METRICS=\$(grep -h 'logMetrics' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)
    echo \"PHASE0C_RC=\$RC\"
    echo \"PHASE0C_PEAK_BYTES=\$PEAK\"
    echo \"PHASE0C_SPILL_DIR_BYTES=\$SPILL_BYTES\"
    echo \"PHASE0C_METRICS=\$METRICS\"
  " > "$RUN_LOG" 2>&1
DOCKER_RC=$?

# ---- parse results -------------------------------------------------------------------------------------------
RC="$(grep -oE 'PHASE0C_RC=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
PEAK="$(grep -oE 'PHASE0C_PEAK_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
SPILL_DIR="$(grep -oE 'PHASE0C_SPILL_DIR_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
# If the wrapper bash itself was OOM-killed we lose the markers; fall back to the docker exit code.
[ -z "${RC:-}" ] && RC="$DOCKER_RC(container)"
[ -z "${PEAK:-}" ] && PEAK="NA(container-killed)"
[ -z "${SPILL_DIR:-}" ] && SPILL_DIR="0"
PEAK_MB="NA"; [[ "$PEAK" =~ ^[0-9]+$ ]] && PEAK_MB=$(( PEAK / 1024 / 1024 ))
SPILL_MB="0"; [[ "$SPILL_DIR" =~ ^[0-9]+$ ]] && SPILL_MB=$(( SPILL_DIR / 1024 / 1024 ))

# Phase-0b SpillMetrics line, captured from the SystemTest_*.log inside the container.
METRICS="$(grep -E 'PHASE0C_METRICS=' "$RUN_LOG" | tail -1 | sed 's/^PHASE0C_METRICS=//')"
[ -z "$METRICS" ] && METRICS="(no SpillMetrics line — spill off, or query failed before stopSpiller)"
# Derive store internal peak-resident (MB) and spilled-slice count from the metrics line, when present.
STORE_PEAK_B="$(echo "$METRICS" | grep -oE 'peak resident [0-9]+' | grep -oE '[0-9]+' | tail -1)"
SLICES_SPILLED="$(echo "$METRICS" | grep -oE 'spilled [0-9]+ slices' | grep -oE '[0-9]+' | tail -1)"
STORE_PEAK_MB="NA"; [[ "$STORE_PEAK_B" =~ ^[0-9]+$ ]] && STORE_PEAK_MB=$(( STORE_PEAK_B / 1024 / 1024 ))
[ -z "$SLICES_SPILLED" ] && SLICES_SPILLED="0"

echo "[run-rss] RESULT label=$LABEL rc=$RC peak=${PEAK_MB}MB store_peak=${STORE_PEAK_MB}MB spilled=${SLICES_SPILLED} spill_dir=${SPILL_MB}MB"
echo "[run-rss] metrics: $METRICS"
echo "[run-rss] full log: $RUN_LOG"

# ---- append to TSV -------------------------------------------------------------------------------------------
if [ ! -f "$RESULTS_TSV" ]; then
  printf 'label\tcap_mb\tspill\tpool\tsoft_mb\thard_mb\temit_lag_ms\tthreads\twbuf_mb\tcache_mb\trc\tpeak_mb\tstore_peak_mb\tslices_spilled\tspill_dir_mb\n' > "$RESULTS_TSV"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$CAP_MB" "$SPILL" "$POOL" "$SOFT" "$HARD" "$EMIT_LAG" "$THREADS" "$WBUF" "$CACHE" "$RC" "$PEAK_MB" "$STORE_PEAK_MB" "$SLICES_SPILLED" "$SPILL_MB" >> "$RESULTS_TSV"
