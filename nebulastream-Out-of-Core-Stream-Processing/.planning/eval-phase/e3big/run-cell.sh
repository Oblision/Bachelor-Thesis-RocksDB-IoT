#!/usr/bin/env bash
# Scaled-sweep per-cell runner. ONE capped systest run -> ONE result line written to results/<label>.tsv
# (per-cell file, NOT a shared append) so it is safe under `xargs -P` parallelism. Idempotent/RESUMABLE:
# if results/<label>.tsv already exists and is non-empty, the cell is SKIPPED (so a killed sweep resumes).
#
# Captures the full metric set incl. compression (H3), spill latency (H5), RocksDB write-stall (H2),
# write-amp + footprint (RQ3), per-task p50/95/99 (RQ2), peak RSS, throughput (external).
#
# Args (positional):
#  1 label  2 test_rel  3 gen_tuples  4 cap_mb|none  5 spill(on|off)  6 pool  7 soft_mb  8 hard_mb
#  9 emit_lag 10 threads 11 wbuf_mb 12 cache_mb 13 num_partitions 14 variant 15 compression(lz4|zstd|none)
set -u
LABEL="${1:?}"; TEST_REL="${2:?}"; GEN="${3:?}"; CAP="${4:?}"; SPILL="${5:?}"; POOL="${6:?}"
SOFT="${7:?}"; HARD="${8:?}"; EMIT_LAG="${9:?}"; THREADS="${10:?}"; WBUF="${11:-0}"; CACHE="${12:-0}"
NPART="${13:-1}"; VARIANT="${14:-ooc}"; COMP="${15:-lz4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMG="nebulastream/nes-development:local-rocksdb"
SYSTEST="cmake-build-debug/nes-systests/systest/systest"
RES_DIR="$SCRIPT_DIR/results"; LOG_DIR="$SCRIPT_DIR/logs"; mkdir -p "$RES_DIR" "$LOG_DIR"
OUT="$RES_DIR/${LABEL}.tsv"; RUN_LOG="$LOG_DIR/${LABEL}.log"
SPILLDIR="/tmp/nes-spill-${LABEL}"

# ---- RESUME: skip if this cell already has a result ----
if [ -s "$OUT" ]; then echo "[skip] $LABEL (already done)"; exit 0; fi

if [ "$SPILL" = on ]; then
  SPILL_ARGS="--worker.default_query_execution.spill.enabled=true \
--worker.default_query_execution.spill.rocksdb_path=${SPILLDIR} \
--worker.default_query_execution.spill.soft_threshold_mb=${SOFT} \
--worker.default_query_execution.spill.hard_threshold_mb=${HARD} \
--worker.default_query_execution.spill.write_buffer_size_mb=${WBUF} \
--worker.default_query_execution.spill.block_cache_size_mb=${CACHE} \
--worker.default_query_execution.spill.number_of_partitions=${NPART} \
--worker.default_query_execution.spill.compression=${COMP} \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}"
else
  SPILL_ARGS="--worker.default_query_execution.spill.enabled=false \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}"
fi
WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${THREADS} ${SPILL_ARGS}"
MEM_ARGS=""; [ "$CAP" != none ] && MEM_ARGS="--memory=${CAP}m --memory-swap=${CAP}m"

docker run --rm $MEM_ARGS --cpus="${THREADS}" --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
  rm -rf ${SPILLDIR} 2>/dev/null; mkdir -p ${SPILLDIR}
  set +e
  T0=\$(date +%s%N); SOUT=\$(./$SYSTEST -t $TEST_REL -- $WORKER_ARGS 2>&1); RC=\$?; T1=\$(date +%s%N)
  echo \"\$SOUT\"
  PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
  SB=\$(du -sb ${SPILLDIR} 2>/dev/null | cut -f1 || echo 0)
  # parse THIS run's own log path from systest stdout (race-free under -P; ls -t would grab a concurrent run's log)
  NESLOG=\$(echo \"\$SOUT\" | grep -oE 'Find the log at: file://[^ ]+\.log' | head -1 | sed 's#.*file://##')
  echo \"CELL_RC=\$RC\"; echo \"CELL_NS=\$((T1-T0))\"; echo \"CELL_PEAK=\$PEAK\"; echo \"CELL_SB=\$SB\"
  echo \"CELL_SPILLM=\$(grep -h 'Spill metrics:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)\"
  echo \"CELL_RDBM=\$(grep -h 'RocksDB stats:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*RocksDB stats:/RocksDB stats:/' | tail -1)\"
  echo \"CELL_LATM=\$(grep -h 'Task latency us:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Task latency us:/Task latency us:/' | tail -1)\"
  rm -rf ${SPILLDIR} 2>/dev/null
" > "$RUN_LOG" 2>&1
DRC=$?

gi(){ grep -oE "$1=[0-9]+" "$RUN_LOG" | tail -1 | cut -d= -f2; }
RC="$(grep -oE 'CELL_RC=[0-9]+' "$RUN_LOG"|tail -1|cut -d= -f2)"; [ -z "$RC" ] && RC="${DRC}c"
NS="$(gi CELL_NS)"; PEAK="$(gi CELL_PEAK)"; SB="$(gi CELL_SB)"
[ -z "$NS" ] && NS=0; [ -z "$PEAK" ] && PEAK=NA; [ -z "$SB" ] && SB=0
PEAK_MB=NA; [[ "$PEAK" =~ ^[0-9]+$ ]] && PEAK_MB=$((PEAK/1024/1024))
SB_MB=0; [[ "$SB" =~ ^[0-9]+$ ]] && SB_MB=$((SB/1024/1024))
SPILLM="$(grep -E 'CELL_SPILLM=' "$RUN_LOG"|tail -1|sed 's/^CELL_SPILLM=//')"
RDBM="$(grep -E 'CELL_RDBM=' "$RUN_LOG"|tail -1|sed 's/^CELL_RDBM=//')"
LATM="$(grep -E 'CELL_LATM=' "$RUN_LOG"|tail -1|sed 's/^CELL_LATM=//')"
field(){ echo "$2" | grep -oE "$1=[0-9.]+" | tail -1 | cut -d= -f2; }
STORE_PEAK="$(echo "$SPILLM"|grep -oE 'peak resident [0-9]+'|grep -oE '[0-9]+'|tail -1)"; [ -z "$STORE_PEAK" ] && STORE_PEAK=NA
SLICES="$(echo "$SPILLM"|grep -oE 'spilled [0-9]+ slices'|grep -oE '[0-9]+'|tail -1)"; [ -z "$SLICES" ] && SLICES=0
RESTORED_B="$(echo "$SPILLM"|grep -oE 'restored [0-9]+ slices / [0-9]+ bytes'|grep -oE '[0-9]+'|tail -1)"; [ -z "$RESTORED_B" ] && RESTORED_B=0
# spill latency (H5): "spill latency us p50=.. p95=.. p99=.."
SPILL_P50="$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=\([0-9]*\).*/\1/p')"; [ -z "$SPILL_P50" ] && SPILL_P50=NA
SPILL_P99="$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=[0-9]* p95=[0-9]* p99=\([0-9]*\).*/\1/p')"; [ -z "$SPILL_P99" ] && SPILL_P99=NA
WAMP="$(field write_amp "$RDBM")"; [ -z "$WAMP" ] && WAMP=NA
SST="$(field sst_footprint_bytes "$RDBM")"; [ -z "$SST" ] && SST=NA
STALL="$(field write_stall_micros "$RDBM")"; [ -z "$STALL" ] && STALL=NA      # H2 (after stall-metric lands)
P50="$(field p50 "$LATM")"; P95="$(field p95 "$LATM")"; P99="$(field p99 "$LATM")"
[ -z "$P50" ] && P50=NA; [ -z "$P95" ] && P95=NA; [ -z "$P99" ] && P99=NA
THR=NA; [ "$RC" = 0 ] && [[ "$NS" =~ ^[0-9]+$ ]] && [ "$NS" -gt 0 ] && THR="$(awk "BEGIN{printf \"%d\", $GEN*1000000000/$NS}")"
ELAPSED="$(awk "BEGIN{printf \"%.3f\", $NS/1000000000}")"
STATUS=ok; [ "$RC" = 137 ] && STATUS=oom; { [ "$RC" != 0 ] && [ "$RC" != 137 ]; } && STATUS="err"

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$VARIANT" "$COMP" "$GEN" "$CAP" "$SPILL" "$POOL" "$SOFT" "$HARD" "$EMIT_LAG" "$THREADS" "$WBUF" "$CACHE" "$NPART" \
  "$RC" "$STATUS" "$ELAPSED" "$THR" "$PEAK_MB" "$STORE_PEAK" "$SLICES" "$RESTORED_B" "$SB_MB" \
  "$WAMP" "$SST" "$STALL" "$P50" "$P95" "$P99" "$SPILL_P50" "$SPILL_P99" > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "[done] $LABEL status=$STATUS rc=$RC thr=${THR} store_peak=${STORE_PEAK} stall=${STALL} p99=${P99} spill_p99=${SPILL_P99}"
