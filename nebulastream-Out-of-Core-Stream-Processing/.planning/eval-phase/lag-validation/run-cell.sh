#!/usr/bin/env bash
# Lag-validation per-cell runner. Focused harness for validating L* = target x M_state/(rho*e).
# Renders lagval.test with TUPLES/WINDOW/EMIT_RATE, runs one capped systest, and (optionally) samples
# cgroup memory.current + spill-dir size every 2s into traces/<label>.trace for slope fitting.
#
# Config is FIXED at the research-informed baseline (lz4/wbuf=64/cache=32, P=4, threads=4) — this phase
# validates the LAG model, it is NOT a config sweep.
#
# Args (positional):
#  1 label  2 tuples  3 window_ms  4 emit_rate  5 emit_lag_ms  6 cap_mb|none  7 spill(on|off)  8 sample(on|off)
#  9 soft_mb(opt,def 182)  10 hard_mb(opt,def 234)  11 pool(opt,def 16000)
set -u
# SAMPLE defaults ON: consistent memory time-series for every run (cheap 0.2s cgroup+/proc reads).
LABEL="${1:?}"; TUPLES="${2:?}"; WINDOW="${3:?}"; EMIT_RATE="${4:?}"; EMIT_LAG="${5:?}"; CAP="${6:?}"; SPILL="${7:?}"; SAMPLE="${8:-on}"
SOFT="${9:-182}"; HARD="${10:-234}"; POOL="${11:-16000}"; CPUSET="${12:-0,1,8,9}"; WTHREADS="${13:-4}"; MAXBUCKETS="${14:-}"; PAGESIZE="${15:-}"; WBUF="${16:-64}"; CACHE="${17:-32}"; NPART="${18:-4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
IMG="nebulastream/nes-development:local-rocksdb"
# SYSTEST binary: default debug; set SYSTEST=cmake-build-release/nes-systests/systest/systest for the release build.
SYSTEST="${SYSTEST:-cmake-build-debug/nes-systests/systest/systest}"
RES_DIR="$SCRIPT_DIR/results"; LOG_DIR="$SCRIPT_DIR/logs"; REND_DIR="$SCRIPT_DIR/work/rendered"; TRACE_DIR="$SCRIPT_DIR/traces"
mkdir -p "$RES_DIR" "$LOG_DIR" "$REND_DIR" "$TRACE_DIR"
OUT="$RES_DIR/${LABEL}.tsv"; RUN_LOG="$LOG_DIR/${LABEL}.log"
# Storage tier for the spill dir: SPILL_HOST_DIR (host path) is bind-mounted to /spill in the container.
# Default = SD card (/mnt/sd-spill, ext4). Set SPILL_HOST_DIR=tmp to use the container overlay (HDD) instead.
# NOTE: a single physical device (the SD card) is shared, so SD-card cells MUST run sequentially (no parallel
# cells) or they contend on the one device and contaminate the spill/restore-latency + throughput numbers.
SPILL_HOST_DIR="${SPILL_HOST_DIR:-/mnt/sd-spill}"
if [ -n "$SPILL_HOST_DIR" ] && [ "$SPILL_HOST_DIR" != tmp ]; then
  SPILL_MOUNT="-v ${SPILL_HOST_DIR}:/spill"; SPILLDIR="/spill/nes-spill-${LABEL}"
else
  SPILL_MOUNT=""; SPILLDIR="/tmp/nes-spill-${LABEL}"
fi

if [ -s "$OUT" ]; then echo "[skip] $LABEL (already done)"; exit 0; fi

# ---- render workload ----
# TEMPLATE selects the source workload under work/ (default = lockstep lagval.test).
TEMPLATE="${TEMPLATE:-lagval.test}"
RENDERED_REL=".planning/eval-phase/lag-validation/work/rendered/${LABEL}.test"
# Input CSV: bind-mount its dir read-only at /input. Keep the CSV on a tmpfs (RAM, e.g. /dev/shm)
# so the runtime data path is RAM-in -> SD-spill-out ONLY (no HDD, no input/spill contention on the SD).
FILECSV_CONTAINER=""; INPUT_MOUNT=""
if [ -n "${FILECSV:-}" ]; then
  INDIR="$(cd "$(dirname "$FILECSV")" && pwd)"; INBASE="$(basename "$FILECSV")"
  INPUT_MOUNT="-v ${INDIR}:/input:ro"; FILECSV_CONTAINER="/input/${INBASE}"
fi
# EMITBPS = File-source ingest pacing in bytes/sec (env, default 0 = unlimited / engine max speed).
sed -e "s/\${TUPLES}/${TUPLES}/g" -e "s/\${WINDOW}/${WINDOW}/g" -e "s/\${EMIT_RATE}/${EMIT_RATE}/g" \
    -e "s#\${FILECSV}#${FILECSV_CONTAINER}#g" -e "s/\${EMITBPS}/${EMITBPS:-0}/g" \
    "$SCRIPT_DIR/work/${TEMPLATE}" > "$REND_DIR/${LABEL}.test"

# ---- fixed research-informed config (SOFT/HARD/POOL now come from args 9-11) ----
# DCPUS = Docker --cpus cap (the edge compute budget; fractional allowed). Models a weak edge SoC and
# bounds ingest below the SD spill-drain ceiling. Env-overridable (default 4 = Pi-class 4-core).
DCPUS="${DCPUS:-4}"; COMP=lz4

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
  # Stability levers (branch ooc-stability-levers), env-gated so existing callers are unchanged:
  #   RESERVEPCT=15  -> pool_reserve_pct (1a pool-pressure spiller + 1b allocation rescue + 1d dir accounting)
  #   PACED=on       -> paced_trigger_flush (1c paced steady-state flush; needs RESERVEPCT>0)
  [ -n "${RESERVEPCT:-}" ] && SPILL_ARGS="$SPILL_ARGS --worker.default_query_execution.spill.pool_reserve_pct=${RESERVEPCT}"
  [ "${PACED:-}" = on ] && SPILL_ARGS="$SPILL_ARGS --worker.default_query_execution.spill.paced_trigger_flush=true"
else
  SPILL_ARGS="--worker.default_query_execution.spill.enabled=false \
--worker.default_query_execution.spill.emit_lag=${EMIT_LAG}"
fi
EXTRA_ARGS=""
[ -n "$MAXBUCKETS" ] && EXTRA_ARGS="$EXTRA_ARGS --worker.default_query_execution.max_number_of_buckets=${MAXBUCKETS}"
[ -n "$PAGESIZE" ] && EXTRA_ARGS="$EXTRA_ARGS --worker.default_query_execution.page_size=${PAGESIZE}"
WORKER_ARGS="--worker.number_of_buffers_in_global_buffer_manager=${POOL} \
--worker.query_engine.number_of_worker_threads=${WTHREADS} ${EXTRA_ARGS} ${SPILL_ARGS}"
MEM_ARGS=""; [ "$CAP" != none ] && MEM_ARGS="--memory=${CAP}m --memory-swap=${CAP}m"

# ---- mem_trace sampler: every 0.2s append one row to the trace, giving a consistent, COMPLETE
# time profile for every run. Columns: t_s current_mb anon_mb file_mb shmem_mb rss_anon_mb
#   anon  = cgroup non-reclaimable working set (pool + HashMaps + RocksDB anon) = the OOM driver
#   file  = reclaimable page cache (spilled/restored SST + input)              = NOT real pressure
#   shmem = tmpfs/shared pages charged to the cgroup (input-file sanity check)
#   rss_anon = the systest PROCESS anon RSS (/proc/<pid>/status), cross-check vs cgroup anon
SAMPLE_SNIPPET=":"
[ "$SAMPLE" = on ] && SAMPLE_SNIPPET='( while true; do SPID=$(pidof systest 2>/dev/null|tr " " "\n"|head -1); C=$(cat /sys/fs/cgroup/memory.current 2>/dev/null||echo 0); A=$(grep "^anon " /sys/fs/cgroup/memory.stat 2>/dev/null|cut -d" " -f2); F=$(grep "^file " /sys/fs/cgroup/memory.stat 2>/dev/null|cut -d" " -f2); SH=$(grep "^shmem " /sys/fs/cgroup/memory.stat 2>/dev/null|cut -d" " -f2); RA=0; [ -n "$SPID" ] && RA=$(grep "^RssAnon:" /proc/$SPID/status 2>/dev/null|grep -oE "[0-9]+"|head -1); printf "%s %s %s %s %s %s\n" "$(date +%s.%N)" "$((C/1048576))" "$(( ${A:-0}/1048576 ))" "$(( ${F:-0}/1048576 ))" "$(( ${SH:-0}/1048576 ))" "$(( ${RA:-0}/1024 ))"; sleep 0.2; done ) > /workspace/'"$RENDERED_REL"'.trace 2>/dev/null & SPLR=$!'

docker run --rm $MEM_ARGS --cpus="${DCPUS}" --cpuset-cpus=${CPUSET} --workdir /workspace -v "$REPO_ROOT:/workspace" ${SPILL_MOUNT} ${INPUT_MOUNT} "$IMG" bash -c "
  rm -rf ${SPILLDIR} 2>/dev/null; mkdir -p ${SPILLDIR}
  set +e
  ${SAMPLE_SNIPPET}
  C0=\$(awk '/usage_usec/{print \$2}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0)
  T0=\$(date +%s%N); SOUT=\$(timeout ${CELL_TIMEOUT:-900} ./$SYSTEST -t $RENDERED_REL -- $WORKER_ARGS 2>&1); RC=\$?; T1=\$(date +%s%N)
  C1=\$(awk '/usage_usec/{print \$2}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0)
  IOSTAT=\$(awk '{for(i=1;i<=NF;i++){if(\$i~/^rbytes=/){split(\$i,a,\"=\");r+=a[2]} if(\$i~/^wbytes=/){split(\$i,b,\"=\");w+=b[2]}}} END{printf \"%d %d\", r, w}' /sys/fs/cgroup/io.stat 2>/dev/null || echo \"0 0\")
  ${SAMPLE:+kill \$SPLR 2>/dev/null}
  echo \"\$SOUT\"
  PEAK=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo NA)
  SB=\$(du -sb ${SPILLDIR} 2>/dev/null | cut -f1 || echo 0)
  NESLOG=\$(echo \"\$SOUT\" | grep -oE 'Find the log at: file://[^ ]+\.log' | head -1 | sed 's#.*file://##')
  echo \"CELL_RC=\$RC\"; echo \"CELL_NS=\$((T1-T0))\"; echo \"CELL_PEAK=\$PEAK\"; echo \"CELL_SB=\$SB\"
  echo \"CELL_CPU_USEC=\$((C1-C0))\"; echo \"CELL_IO=\$IOSTAT\"
  echo \"CELL_TASKLAT=\$(grep -h 'Task latency us:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Task latency us:/Task latency us:/' | tail -1)\"
  echo \"CELL_CHECKSUM=\$(grep -h 'Checksum:' \"\$NESLOG\" 2>/dev/null | tail -1)\$(echo \"\$SOUT\" | grep -oE 'checksum[^ ]*' | tail -1)\"
  echo \"CELL_SPILLM=\$(grep -h 'Spill metrics:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Spill metrics:/Spill metrics:/' | tail -1)\"
  echo \"CELL_RDBM=\$(grep -h 'RocksDB stats:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*RocksDB stats:/RocksDB stats:/' | tail -1)\"
  echo \"CELL_POOLG_MAX=\$(grep -h 'POOLGAUGE' \"\$NESLOG\" 2>/dev/null | grep -oE 'used_buf=[0-9]+' | cut -d= -f2 | sort -n | tail -1)\"
  echo \"CELL_POOLG_LAST=\$(grep -h 'POOLGAUGE' \"\$NESLOG\" 2>/dev/null | grep -oE 'used_buf=[0-9]+' | tail -1 | cut -d= -f2)\"
  echo \"CELL_UPOOLG_MAX=\$(grep -h 'UPOOLGAUGE' \"\$NESLOG\" 2>/dev/null | grep -oE 'live_chunk_bytes=[0-9]+' | cut -d= -f2 | sort -n | tail -1)\"
  echo \"CELL_LEVERM=\$(grep -h 'Lever metrics:' \"\$NESLOG\" 2>/dev/null | sed 's/^.*Lever metrics:/Lever metrics:/' | tail -1)\"
  echo \"CELL_RESCUES=\$(grep -hc 'POOLRESCUE' \"\$NESLOG\" 2>/dev/null || echo 0)\"
  echo CELL_SPILLG_START; grep -h 'SPILLGAUGE' \"\$NESLOG\" 2>/dev/null | sed 's/^.*SPILLGAUGE //' | head -5000; echo CELL_SPILLG_END
  echo CELL_HMG_START; grep -h 'HMGAUGE' \"\$NESLOG\" 2>/dev/null | sed 's/^.*HMGAUGE //' | head -3000; echo CELL_HMG_END
  rm -rf ${SPILLDIR} 2>/dev/null
" > "$RUN_LOG" 2>&1
DRC=$?

# move the in-workspace trace to traces/
[ -f "$REND_DIR/${LABEL}.test.trace" ] && mv "$REND_DIR/${LABEL}.test.trace" "$TRACE_DIR/${LABEL}.trace" 2>/dev/null

# ---- mem_trace ANALYSIS: split steady-state vs terminal-flush, keep full series for plotting ----
# anon peak     = max non-reclaimable working set (the OOM driver) over the whole run
# steady_anon   = mean anon over the middle 20-80% of runtime = the "can it RUN" footprint
# flush_anon    = max anon in the last 15% of runtime = the emit-lag terminal-flush flood at CLOSE
# peak_frac     = where in [0,100]% of runtime the peak occurred (near 100 => the flood is the peak)
# file/shmem    = reclaimable page cache + tmpfs (NOT pressure); rss_anon = process-level cross-check
PEAK_ANON=0; STEADY_ANON=0; FLUSH_ANON=0; PEAK_FRAC=0; PEAK_FILE=0; PEAK_SHMEM=0; PEAK_RSSANON=0
if [ -s "$TRACE_DIR/${LABEL}.trace" ]; then
  read PEAK_ANON STEADY_ANON FLUSH_ANON PEAK_FRAC PEAK_FILE PEAK_SHMEM PEAK_RSSANON <<EOF
$(awk '{n++; t[n]=$1; an[n]=$3; fi[n]=$4; sh[n]=$5; ra[n]=$6}
  END{ if(n==0){print "0 0 0 0 0 0 0"; exit}
    t0=t[1]; span=t[n]-t0; if(span<=0)span=1; pa=0; pfrac=0; ssum=0; scnt=0; fa=0;
    for(i=1;i<=n;i++){ fr=(t[i]-t0)/span;
      if(an[i]>pa){pa=an[i]; pfrac=fr}
      if(fi[i]>pkf)pkf=fi[i]; if(sh[i]>pks)pks=sh[i]; if(ra[i]>pkr)pkr=ra[i];
      if(fr>=0.2&&fr<=0.8){ssum+=an[i]; scnt++}
      if(fr>0.85&&an[i]>fa)fa=an[i]; }
    printf "%d %d %d %d %d %d %d", pa, (scnt>0?ssum/scnt:pa), fa, pfrac*100, pkf+0, pks+0, pkr+0 }' "$TRACE_DIR/${LABEL}.trace")
EOF
fi
POOL_MB=$(( POOL * 8192 / 1048576 ))

gi(){ grep -oE "$1=[0-9]+" "$RUN_LOG" | tail -1 | cut -d= -f2; }
RC="$(grep -oE 'CELL_RC=[0-9]+' "$RUN_LOG"|tail -1|cut -d= -f2)"; [ -z "$RC" ] && RC="${DRC}c"
NS="$(gi CELL_NS)"; PEAK="$(gi CELL_PEAK)"; SB="$(gi CELL_SB)"
[ -z "$NS" ] && NS=0; [ -z "$PEAK" ] && PEAK=0; [ -z "$SB" ] && SB=0
PEAK_MB=$((PEAK/1048576)); SB_MB=$((SB/1048576))
ELAPSED="$(awk "BEGIN{printf \"%.3f\", $NS/1000000000}")"
STATUS=ok; [ "$RC" = 137 ] && STATUS=oom; { [ "$RC" != 0 ] && [ "$RC" != 137 ]; } && STATUS="err"
SPILLM="$(grep -E 'CELL_SPILLM=' "$RUN_LOG"|tail -1|sed 's/^CELL_SPILLM=//')"
STORE_PEAK="$(echo "$SPILLM"|grep -oE 'peak resident [0-9]+'|grep -oE '[0-9]+'|tail -1)"; [ -z "$STORE_PEAK" ] && STORE_PEAK=0
SLICES="$(echo "$SPILLM"|grep -oE 'spilled [0-9]+ slices'|grep -oE '[0-9]+'|head -1)"; [ -z "$SLICES" ] && SLICES=0
N_w=$(awk "BEGIN{printf \"%d\", $EMIT_LAG/$WINDOW}")
# RQ metrics: CPU (cgroup usage_usec delta), disk I/O (cgroup io.stat r/w bytes), per-task latency percentiles, throughput
CPU_USEC="$(gi CELL_CPU_USEC)"; [ -z "$CPU_USEC" ] && CPU_USEC=0
IO_LINE="$(grep -E 'CELL_IO=' "$RUN_LOG"|tail -1|sed 's/^CELL_IO=//')"
IO_R="$(echo "$IO_LINE"|awk '{print $1+0}')"; IO_W="$(echo "$IO_LINE"|awk '{print $2+0}')"
TASKLAT="$(grep -E 'CELL_TASKLAT=' "$RUN_LOG"|tail -1|sed 's/^CELL_TASKLAT=//')"
LAT_P50="$(echo "$TASKLAT"|grep -oE 'p50=[0-9]+'|cut -d= -f2)"; [ -z "$LAT_P50" ] && LAT_P50=0
LAT_P95="$(echo "$TASKLAT"|grep -oE 'p95=[0-9]+'|cut -d= -f2)"; [ -z "$LAT_P95" ] && LAT_P95=0
LAT_P99="$(echo "$TASKLAT"|grep -oE 'p99=[0-9]+'|cut -d= -f2)"; [ -z "$LAT_P99" ] && LAT_P99=0
TPS="$(awk "BEGIN{e=$ELAPSED; printf \"%.0f\", (e>0)?$TUPLES/e:0}")"
# RocksDB resident (measured properties): memtable + block cache + table readers
RDBM_LINE="$(grep -E 'CELL_RDBM=' "$RUN_LOG"|tail -1)"
RDB_MEMT="$(echo "$RDBM_LINE"|grep -oE 'rss_memtable_bytes=[0-9]+'|cut -d= -f2)"
RDB_BC="$(echo "$RDBM_LINE"|grep -oE 'rss_block_cache_bytes=[0-9]+'|cut -d= -f2)"
RDB_TR="$(echo "$RDBM_LINE"|grep -oE 'rss_table_readers_bytes=[0-9]+'|cut -d= -f2)"
ROCKSDB_RSS_MB=$(( ( ${RDB_MEMT:-0} + ${RDB_BC:-0} + ${RDB_TR:-0} ) / 1048576 ))
# component gauges (direct, over time): pool buffers in use (peak + final=at-close => flood attribution),
# and resident unpooled HashMap bytes (peak). 8KB/buffer.
POOLG_MAX="$(gi CELL_POOLG_MAX)"; [ -z "$POOLG_MAX" ] && POOLG_MAX=0
POOLG_LAST="$(gi CELL_POOLG_LAST)"; [ -z "$POOLG_LAST" ] && POOLG_LAST=0
UPOOLG_MAX="$(gi CELL_UPOOLG_MAX)"; [ -z "$UPOOLG_MAX" ] && UPOOLG_MAX=0
POOL_USED_PEAK_MB=$(( POOLG_MAX * 8192 / 1048576 )); POOL_USED_FINAL_MB=$(( POOLG_LAST * 8192 / 1048576 )); UNPOOLED_PEAK_MB=$(( UPOOLG_MAX / 1048576 ))

# cols: ...(1-31 as before)... pool_used_peak_mb pool_used_final_mb unpooled_peak_mb
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$LABEL" "$TUPLES" "$WINDOW" "$EMIT_RATE" "$EMIT_LAG" "$N_w" "$CAP" "$SPILL" "$RC" "$STATUS" "$ELAPSED" "$PEAK_MB" "$SB_MB" "$STORE_PEAK" "$SLICES" \
  "$CPU_USEC" "$IO_R" "$IO_W" "$LAT_P50" "$LAT_P95" "$LAT_P99" "$TPS" \
  "$PEAK_ANON" "$STEADY_ANON" "$FLUSH_ANON" "$PEAK_FRAC" "$PEAK_FILE" "$PEAK_SHMEM" "$PEAK_RSSANON" "$POOL_MB" "$ROCKSDB_RSS_MB" \
  "$POOL_USED_PEAK_MB" "$POOL_USED_FINAL_MB" "$UNPOOLED_PEAK_MB" \
  > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "[done] $LABEL rc=$RC $STATUS | total_peak=${PEAK_MB}MB | anon: peak=${PEAK_ANON} steady=${STEADY_ANON} flush=${FLUSH_ANON}MB (peak@${PEAK_FRAC}%) | file=${PEAK_FILE} shmem=${PEAK_SHMEM} rss_anon=${PEAK_RSSANON}MB"
echo "        COMPONENTS | pool_alloc=${POOL_MB}MB pool_used: peak=${POOL_USED_PEAK_MB} final=${POOL_USED_FINAL_MB}MB | hashmap_unpooled_peak=${UNPOOLED_PEAK_MB}MB | rocksdb=${ROCKSDB_RSS_MB}MB store=$((STORE_PEAK/1048576))MB | slices=$SLICES"
# HMGAUGE: measured HashMap composition (header vs key+value vs bucket-dir vs page-slack) from per-map samples
HMG="$(sed -n '/^CELL_HMG_START/,/^CELL_HMG_END/p' "$RUN_LOG" | grep '^entries=')"
if [ -n "$HMG" ]; then
  echo "$HMG" | awk '{for(i=1;i<=NF;i++){split($i,a,"=");v[a[1]]=a[2]}
      e=v["entries"]; es=v["entrySize"]; h=v["hdr"]; b=v["buckets"]; epp=v["entriesPerPage"]; ps=v["pageSize"];
      if(epp+0<=0||es+0<=0)next; pages=int((e+epp-1)/epp); dp=pages*ps; bk=(b+1)*8;
      HDR+=e*h; PAY+=e*(es-h); BK+=bk; SL+=dp-e*es; TOT+=dp+bk; ne+=e; n++}
    END{if(TOT>0) printf "        HASHMAP COMPOSITION (n=%d maps, %d entries) | header=%.0f%% keyval=%.0f%% bucket_dir=%.0f%% page_slack=%.0f%% | entrySize=%dB hdr=%dB\n", n, ne, 100*HDR/TOT,100*PAY/TOT,100*BK/TOT,100*SL/TOT, es, h}'
fi
