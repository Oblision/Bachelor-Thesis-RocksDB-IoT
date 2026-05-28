#!/usr/bin/env bash
# E3 single-run driver — one capped (or uncapped) systest run of the tumbling OOC aggregation, capturing the full
# E1 metric set: spill/restore counts+bytes+latency, RocksDB write-amp+footprint, per-task p50/95/99, peak RSS,
# and externally-computed throughput (known generated tuples / measured elapsed).
#
# Extends phase2e/run-rss-part.sh with: variant (ooc|inmem), cap on/off, the new E1 NES_INFO lines, throughput.
#
# Usage:
#   run-eval.sh <label> <test_rel> <gen_tuples> <cap_mb|none> <spill:on|off> <pool> <soft_mb> <hard_mb> \
#               <emit_lag_ms> <threads> <wbuf_mb> <cache_mb> <num_partitions> <variant>
# Example (OOC, 320MB cap, P=8):
#   run-eval.sh A_s2M_c320_ooc e3/work/state_2000000.test 2000001 320 on 16000 4 8 2000000 4 32 32 8 ooc
# Example (in-mem baseline, same cap, spill off):
#   run-eval.sh A_s2M_c320_inmem e3/work/state_2000000.test 2000001 320 off 16000 4 8 2000000 4 0 0 1 inmem
set -u

LABEL="${1:?label}"; TEST_REL="${2:?test_rel}"; GEN_TUPLES="${3:?gen_tuples}"
CAP_MB="${4:?cap_mb|none}"; SPILL="${5:?on|off}"; POOL="${6:?pool}"
SOFT="${7:?soft_mb}"; HARD="${8:?hard_mb}"; EMIT_LAG="${9:?emit_lag_ms}"; THREADS="${10:?threads}"
WBUF="${11:-0}"; CACHE="${12:-0}"; NPART="${13:-1}"; VARIANT="${14:-ooc}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMG="nebulastream/nes-development:local-rocksdb"
BUILD_DIR="cmake-build-debug"
SYSTEST="$BUILD_DIR/nes-systests/systest/systest"
RESULTS_TSV="$SCRIPT_DIR/runs.tsv"
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
  *) echo "[run-eval] unknown spill mode '$SPILL'"; exit 2 ;;
esac

WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${THREADS} \
${SPILL_ARGS}"

# memory cap is optional (none = no cap, for the uncapped baseline throughput reference)
MEM_ARGS=""
if [ "$CAP_MB" != "none" ]; then MEM_ARGS="--memory=${CAP_MB}m --memory-swap=${CAP_MB}m"; fi

echo "[run-eval] label=$LABEL variant=$VARIANT cap=${CAP_MB} spill=$SPILL P=$NPART emit_lag=$EMIT_LAG pool=$POOL threads=$THREADS wbuf=$WBUF cache=$CACHE"

docker run --rm $MEM_ARGS --cpus="${THREADS}" \
  --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
    rm -rf /tmp/nes-spill-${LABEL} 2>/dev/null; mkdir -p /tmp/nes-spill-${LABEL}
    set +e
    T0=\$(date +%s%N)
    ./$SYSTEST -t $TEST_REL -- $WORKER_ARGS
    RC=\$?
    T1=\$(date +%s%N)
    PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
    SPILL_BYTES=\$(du -sb /tmp/nes-spill-${LABEL} 2>/dev/null | cut -f1 || echo 0)
    NESLOG=\$(ls -t /workspace/cmake-build-debug/nes-systests/SystemTest_*.log 2>/dev/null | head -1)
    SPILLM=\$(grep -h 'Spill metrics:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)
    RDBM=\$(grep -h 'RocksDB stats:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*RocksDB stats:/RocksDB stats:/' | tail -1)
    LATM=\$(grep -h 'Task latency us:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Task latency us:/Task latency us:/' | tail -1)
    echo \"E3_RC=\$RC\"
    echo \"E3_ELAPSED_NS=\$((T1 - T0))\"
    echo \"E3_PEAK_BYTES=\$PEAK\"
    echo \"E3_SPILL_DIR_BYTES=\$SPILL_BYTES\"
    echo \"E3_SPILLM=\$SPILLM\"
    echo \"E3_RDBM=\$RDBM\"
    echo \"E3_LATM=\$LATM\"
  " > "$RUN_LOG" 2>&1
DOCKER_RC=$?

g() { grep -oE "$1=[-.0-9]+" "$RUN_LOG" | tail -1 | cut -d= -f2; }
RC="$(g E3_RC)"; [ -z "$RC" ] && RC="${DOCKER_RC}(container)"
ELAPSED_NS="$(grep -oE 'E3_ELAPSED_NS=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"
ELAPSED="NA"; [[ "$ELAPSED_NS" =~ ^[0-9]+$ ]] && ELAPSED="$(awk "BEGIN{printf \"%.3f\", $ELAPSED_NS/1000000000}")"
PEAK="$(grep -oE 'E3_PEAK_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"; [ -z "$PEAK" ] && PEAK="NA"
SPILL_DIR="$(grep -oE 'E3_SPILL_DIR_BYTES=[0-9]+' "$RUN_LOG" | tail -1 | cut -d= -f2)"; [ -z "$SPILL_DIR" ] && SPILL_DIR=0
PEAK_MB="NA"; [[ "$PEAK" =~ ^[0-9]+$ ]] && PEAK_MB=$(( PEAK/1024/1024 ))
SPILL_MB=0; [[ "$SPILL_DIR" =~ ^[0-9]+$ ]] && SPILL_MB=$(( SPILL_DIR/1024/1024 ))

SPILLM="$(grep -E 'E3_SPILLM=' "$RUN_LOG" | tail -1 | sed 's/^E3_SPILLM=//')"
RDBM="$(grep -E 'E3_RDBM=' "$RUN_LOG" | tail -1 | sed 's/^E3_RDBM=//')"
LATM="$(grep -E 'E3_LATM=' "$RUN_LOG" | tail -1 | sed 's/^E3_LATM=//')"

# Spill metrics fields
gm() { echo "$SPILLM" | grep -oE "$1" | grep -oE '[0-9]+' | tail -1; }
STORE_PEAK_B="$(echo "$SPILLM" | grep -oE 'peak resident [0-9]+' | grep -oE '[0-9]+' | tail -1)"
SLICES_SPILLED="$(echo "$SPILLM" | grep -oE 'spilled [0-9]+ slices' | grep -oE '[0-9]+' | tail -1)"
BYTES_RESTORED="$(echo "$SPILLM" | grep -oE 'restored [0-9]+ slices / [0-9]+ bytes' | grep -oE '[0-9]+' | tail -1)"
STORE_PEAK_MB="NA"; [[ "$STORE_PEAK_B" =~ ^[0-9]+$ ]] && STORE_PEAK_MB=$(( STORE_PEAK_B/1024/1024 ))
[ -z "$SLICES_SPILLED" ] && SLICES_SPILLED=0; [ -z "$BYTES_RESTORED" ] && BYTES_RESTORED=0

# RocksDB stats fields
rf() { echo "$RDBM" | grep -oE "$1=[0-9.]+" | tail -1 | cut -d= -f2; }
SST_FOOTPRINT="$(rf sst_footprint_bytes)"; WRITE_AMP="$(rf write_amp)"; BYTES_WRITTEN="$(rf bytes_written)"
[ -z "$SST_FOOTPRINT" ] && SST_FOOTPRINT=NA; [ -z "$WRITE_AMP" ] && WRITE_AMP=NA; [ -z "$BYTES_WRITTEN" ] && BYTES_WRITTEN=NA

# Task latency fields
lf() { echo "$LATM" | grep -oE "$1=[0-9]+" | tail -1 | cut -d= -f2; }
P50="$(lf p50)"; P95="$(lf p95)"; P99="$(lf p99)"
[ -z "$P50" ] && P50=NA; [ -z "$P95" ] && P95=NA; [ -z "$P99" ] && P99=NA

# Throughput (external): known generated tuples / elapsed; only meaningful on success (rc=0)
THROUGHPUT=NA
if [ "$RC" = "0" ] && [[ "$ELAPSED_NS" =~ ^[0-9]+$ ]] && [ "$ELAPSED_NS" -gt 0 ]; then
  THROUGHPUT="$(awk "BEGIN{printf \"%d\", $GEN_TUPLES*1000000000/$ELAPSED_NS}")"
fi
STATUS=ok; [ "$RC" = "137" ] && STATUS=oom; [ "$RC" != "0" ] && [ "$RC" != "137" ] && STATUS="err($RC)"

echo "[run-eval] RESULT $LABEL status=$STATUS rc=$RC elapsed=${ELAPSED}s thr=${THROUGHPUT}t/s peak=${PEAK_MB}MB store_peak=${STORE_PEAK_MB}MB spilled=${SLICES_SPILLED} restored_b=${BYTES_RESTORED} writeamp=${WRITE_AMP} sst=${SST_FOOTPRINT} p50=${P50} p95=${P95} p99=${P99}"

if [ ! -f "$RESULTS_TSV" ]; then
  printf 'label\tvariant\tstate_tuples\tcap_mb\tspill\tpool\tsoft_mb\thard_mb\temit_lag_ms\tthreads\twbuf_mb\tcache_mb\tnum_partitions\trc\tstatus\telapsed_s\tthroughput_tps\tpeak_mb\tstore_peak_mb\tslices_spilled\tbytes_restored\tspill_dir_mb\twrite_amp\tsst_footprint_b\tbytes_written\tp50_us\tp95_us\tp99_us\n' > "$RESULTS_TSV"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$VARIANT" "$GEN_TUPLES" "$CAP_MB" "$SPILL" "$POOL" "$SOFT" "$HARD" "$EMIT_LAG" "$THREADS" "$WBUF" "$CACHE" "$NPART" \
  "$RC" "$STATUS" "$ELAPSED" "$THROUGHPUT" "$PEAK_MB" "$STORE_PEAK_MB" "$SLICES_SPILLED" "$BYTES_RESTORED" "$SPILL_MB" \
  "$WRITE_AMP" "$SST_FOOTPRINT" "$BYTES_WRITTEN" "$P50" "$P95" "$P99" >> "$RESULTS_TSV"
