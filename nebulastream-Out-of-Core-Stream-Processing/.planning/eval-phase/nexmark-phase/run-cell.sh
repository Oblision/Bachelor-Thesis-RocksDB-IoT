#!/usr/bin/env bash
# Nexmark-phase per-cell runner. Same docker harness as ../e3big/run-cell.sh — PLUS:
#   1. ${STATE} / ${AUCTION_STATE} template substitution into the .test file at launch.
#   2. RQ-coverage closure: parses every metric NES already emits in its Spill / RocksDB / Task NES_INFO
#      lines (the e3big runner dropped about half of them). 44-column TSV; see collect.sh for the header.
#   3. Variant-aware NA-vs-0 semantics: ooc + ok cells with absent RocksDB metric -> 0 (measured zero);
#      inmem* cells -> NA (not applicable, spill disabled); crashed cells -> NA (no measurement).
#
# Args (positional, 16 + optional 17):
#  1 label  2 test_rel  3 gen_tuples  4 cap_mb|none  5 spill(on|off)  6 pool  7 soft_mb  8 hard_mb
#  9 emit_lag 10 threads 11 wbuf_mb 12 cache_mb 13 num_partitions 14 variant 15 compression(lz4|zstd|none)
#  16 scale_state  (substituted for ${STATE} in the .test; also used for ${AUCTION_STATE}=scale/10 in q8)
#  17 slot_id      (OPTIONAL: 0..3 — if set, pins the cell to a dedicated pair of physical cores via
#                              docker --cpuset-cpus, guaranteeing no inter-cell CPU sharing. launch.sh
#                              assigns slots from a 4-slot FIFO semaphore. Omitted by smoke runs
#                              (xargs -P 2 -L 1) — those run without pinning.)
set -u
LABEL="${1:?}"; TEST_REL="${2:?}"; GEN="${3:?}"; CAP="${4:?}"; SPILL="${5:?}"; POOL="${6:?}"
SOFT="${7:?}"; HARD="${8:?}"; EMIT_LAG="${9:?}"; THREADS="${10:?}"; WBUF="${11:-0}"; CACHE="${12:-0}"
NPART="${13:-1}"; VARIANT="${14:-ooc}"; COMP="${15:-lz4}"; SCALE_STATE="${16:?}"; SLOT="${17:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMG="nebulastream/nes-development:local-rocksdb"
SYSTEST="cmake-build-debug/nes-systests/systest/systest"
RES_DIR="$SCRIPT_DIR/results"; LOG_DIR="$SCRIPT_DIR/logs"; REND_DIR="$SCRIPT_DIR/work/rendered"
mkdir -p "$RES_DIR" "$LOG_DIR" "$REND_DIR"
OUT="$RES_DIR/${LABEL}.tsv"; RUN_LOG="$LOG_DIR/${LABEL}.log"
SPILLDIR="/tmp/nes-spill-${LABEL}"

# ---- RESUME: skip if this cell already has a result ----
if [ -s "$OUT" ]; then echo "[skip] $LABEL (already done)"; exit 0; fi

# ---- Render the .test file with STATE / AUCTION_STATE substituted ----
AUCTION_STATE=$((SCALE_STATE / 10))
RENDERED_REL=".planning/eval-phase/nexmark-phase/work/rendered/${LABEL}.test"
RENDERED_ABS="$REND_DIR/${LABEL}.test"
sed -e "s/\${STATE}/${SCALE_STATE}/g" -e "s/\${AUCTION_STATE}/${AUCTION_STATE}/g" \
    "$SCRIPT_DIR/$TEST_REL" > "$RENDERED_ABS"

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

# ---- CPU pinning per slot (i7-10700K topology: SMT siblings are (n, n+8); 8 physical cores → 4 slots × 2 phys cores each) ----
CPUSET_ARGS=""
if [ -n "$SLOT" ]; then
  case "$SLOT" in
    0) CPUSET_ARGS="--cpuset-cpus=0,1,8,9"   ;;  # physical cores 0,1
    1) CPUSET_ARGS="--cpuset-cpus=2,3,10,11" ;;  # physical cores 2,3
    2) CPUSET_ARGS="--cpuset-cpus=4,5,12,13" ;;  # physical cores 4,5
    3) CPUSET_ARGS="--cpuset-cpus=6,7,14,15" ;;  # physical cores 6,7
    *) echo "[warn] $LABEL: SLOT=$SLOT outside 0..3, running without pinning" >&2 ;;
  esac
fi

docker run --rm $MEM_ARGS --cpus="${THREADS}" $CPUSET_ARGS --workdir /workspace -v "$REPO_ROOT:/workspace" "$IMG" bash -c "
  rm -rf ${SPILLDIR} 2>/dev/null; mkdir -p ${SPILLDIR}
  set +e
  T0=\$(date +%s%N); SOUT=\$(./$SYSTEST -t $RENDERED_REL -- $WORKER_ARGS 2>&1); RC=\$?; T1=\$(date +%s%N)
  echo \"\$SOUT\"
  PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
  SB=\$(du -sb ${SPILLDIR} 2>/dev/null | cut -f1 || echo 0)
  NESLOG=\$(echo \"\$SOUT\" | grep -oE 'Find the log at: file://[^ ]+\.log' | head -1 | sed 's#.*file://##')
  echo \"CELL_RC=\$RC\"; echo \"CELL_NS=\$((T1-T0))\"; echo \"CELL_PEAK=\$PEAK\"; echo \"CELL_SB=\$SB\"
  echo \"CELL_SPILLM=\$(grep -h 'Spill metrics:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)\"
  echo \"CELL_RDBM=\$(grep -h 'RocksDB stats:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*RocksDB stats:/RocksDB stats:/' | tail -1)\"
  echo \"CELL_LATM=\$(grep -h 'Task latency us:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Task latency us:/Task latency us:/' | tail -1)\"
  rm -rf ${SPILLDIR} 2>/dev/null
" > "$RUN_LOG" 2>&1
DRC=$?

# ----- Core run-state -----
gi(){ grep -oE "$1=[0-9]+" "$RUN_LOG" | tail -1 | cut -d= -f2; }
RC="$(grep -oE 'CELL_RC=[0-9]+' "$RUN_LOG"|tail -1|cut -d= -f2)"; [ -z "$RC" ] && RC="${DRC}c"
NS="$(gi CELL_NS)"; PEAK="$(gi CELL_PEAK)"; SB="$(gi CELL_SB)"
[ -z "$NS" ] && NS=0; [ -z "$PEAK" ] && PEAK=NA; [ -z "$SB" ] && SB=0
PEAK_MB=NA; [[ "$PEAK" =~ ^[0-9]+$ ]] && PEAK_MB=$((PEAK/1024/1024))
SB_MB=0; [[ "$SB" =~ ^[0-9]+$ ]] && SB_MB=$((SB/1024/1024))
ELAPSED="$(awk "BEGIN{printf \"%.3f\", $NS/1000000000}")"
STATUS=ok; [ "$RC" = 137 ] && STATUS=oom; { [ "$RC" != 0 ] && [ "$RC" != 137 ]; } && STATUS="err"
THR=NA; [ "$RC" = 0 ] && [[ "$NS" =~ ^[0-9]+$ ]] && [ "$NS" -gt 0 ] && THR="$(awk "BEGIN{printf \"%d\", $GEN*1000000000/$NS}")"

# ----- Variant-aware default helper -----
# default_metric "<parsed_value>" <kind>   where kind ∈ { rocksdb_only, both }
#   non-empty value          → keep as-is
#   empty + STATUS != ok     → NA   (cell crashed before metric was emitted; no data)
#   empty + STATUS == ok + variant==inmem* + kind=rocksdb_only → NA   (not applicable; spill was off)
#   empty otherwise          → 0    (measured zero — ooc cell ran but metric didn't appear in log)
default_metric() {
  local v="$1" kind="$2"
  if [ -n "$v" ]; then echo "$v"; return; fi
  if [ "$STATUS" != "ok" ]; then echo "NA"; return; fi
  if [ "$kind" = "rocksdb_only" ] && [[ "$VARIANT" == inmem* ]]; then echo "NA"; return; fi
  echo "0"
}

# ----- NES_INFO line captures -----
SPILLM="$(grep -E 'CELL_SPILLM=' "$RUN_LOG"|tail -1|sed 's/^CELL_SPILLM=//')"
RDBM="$(grep -E 'CELL_RDBM=' "$RUN_LOG"|tail -1|sed 's/^CELL_RDBM=//')"
LATM="$(grep -E 'CELL_LATM=' "$RUN_LOG"|tail -1|sed 's/^CELL_LATM=//')"
field(){ echo "$2" | grep -oE "$1=[0-9.]+" | tail -1 | cut -d= -f2; }

# ----- Spill metrics (RQ2 spill+restore cost; RQ3 spill volume) -----
STORE_PEAK=$(default_metric "$(echo "$SPILLM"|grep -oE 'peak resident [0-9]+'|grep -oE '[0-9]+'|tail -1)" rocksdb_only)
SLICES_SPILLED=$(default_metric "$(echo "$SPILLM"|grep -oE 'spilled [0-9]+ slices'|grep -oE '[0-9]+'|head -1)" rocksdb_only)
RESTORED_SLICES=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*restored \([0-9]*\) slices.*/\1/p')" rocksdb_only)
RESTORED_B=$(default_metric "$(echo "$SPILLM"|grep -oE 'restored [0-9]+ slices / [0-9]+ bytes'|grep -oE '[0-9]+'|tail -1)" rocksdb_only)
SPILL_P50=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=\([0-9]*\).*/\1/p')" rocksdb_only)
SPILL_P95=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=[0-9]* p95=\([0-9]*\).*/\1/p')" rocksdb_only)
SPILL_P99=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=[0-9]* p95=[0-9]* p99=\([0-9]*\).*/\1/p')" rocksdb_only)
SPILL_MAX=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*spill latency us p50=[0-9]* p95=[0-9]* p99=[0-9]* max=\([0-9]*\).*/\1/p')" rocksdb_only)
RESTORE_P50=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*restore latency us p50=\([0-9]*\).*/\1/p')" rocksdb_only)
RESTORE_P95=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*restore latency us p50=[0-9]* p95=\([0-9]*\).*/\1/p')" rocksdb_only)
RESTORE_P99=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*restore latency us p50=[0-9]* p95=[0-9]* p99=\([0-9]*\).*/\1/p')" rocksdb_only)
RESTORE_MAX=$(default_metric "$(echo "$SPILLM"|sed -n 's/.*restore latency us p50=[0-9]* p95=[0-9]* p99=[0-9]* max=\([0-9]*\).*/\1/p')" rocksdb_only)

# ----- RocksDB stats (RQ3 disk I/O + write-amp + footprint + cache effectiveness; H2 stall) -----
WAMP=$(default_metric "$(field write_amp "$RDBM")" rocksdb_only)
SST=$(default_metric "$(field sst_footprint_bytes "$RDBM")" rocksdb_only)
BYTES_FLUSHED=$(default_metric "$(field bytes_flushed "$RDBM")" rocksdb_only)
BYTES_COMPACTED=$(default_metric "$(field bytes_compacted "$RDBM")" rocksdb_only)
BYTES_WRITTEN=$(default_metric "$(field bytes_written "$RDBM")" rocksdb_only)
BLOCK_CACHE_HIT=$(default_metric "$(field block_cache_hit "$RDBM")" rocksdb_only)
BLOCK_CACHE_MISS=$(default_metric "$(field block_cache_miss "$RDBM")" rocksdb_only)
STALL=$(default_metric "$(field write_stall_micros "$RDBM")" rocksdb_only)
STALL_COUNT=$(default_metric "$(field write_stall_count "$RDBM")" rocksdb_only)

# ----- Task latency (RQ2; applies to both variants) -----
P50=$(default_metric "$(field p50 "$LATM")" both)
P95=$(default_metric "$(field p95 "$LATM")" both)
P99=$(default_metric "$(field p99 "$LATM")" both)

# ----- 44-column TSV (must match collect.sh's header) -----
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$VARIANT" "$COMP" "$GEN" "$CAP" "$SPILL" "$POOL" "$SOFT" "$HARD" "$EMIT_LAG" "$THREADS" "$WBUF" "$CACHE" "$NPART" \
  "$RC" "$STATUS" "$ELAPSED" "$THR" "$PEAK_MB" "$STORE_PEAK" "$SLICES_SPILLED" "$RESTORED_B" "$SB_MB" \
  "$WAMP" "$SST" "$STALL" "$P50" "$P95" "$P99" "$SPILL_P50" "$SPILL_P99" \
  "$SPILL_P95" "$SPILL_MAX" "$RESTORE_P50" "$RESTORE_P95" "$RESTORE_P99" "$RESTORE_MAX" "$RESTORED_SLICES" \
  "$BYTES_FLUSHED" "$BYTES_COMPACTED" "$BYTES_WRITTEN" "$BLOCK_CACHE_HIT" "$BLOCK_CACHE_MISS" "$STALL_COUNT" \
  > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "[done] $LABEL status=$STATUS rc=$RC thr=${THR} store_peak=${STORE_PEAK} stall=${STALL} p99=${P99} spill_p99=${SPILL_P99} restore_p99=${RESTORE_P99} bw=${BYTES_WRITTEN} wa=${WAMP}"
