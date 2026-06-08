#!/usr/bin/env bash
# POOL-SIZE SWEEP for the 512MB model: how buffer-pool size affects survival, throughput, and memory.
# Both extremes probed: tiny (250 buf = 2MB -> expect exhaustion rc141) ... huge (64000 = 500MB -> OOM at 512).
# Both arms (different pool needs): in-mem (lag-honored, spiller off) and OOC (spill on).
# Fixed: el workload (state ~232MB), --cpus=2, emit_lag=4M, cap=512. 3 reps/point. Short timeout so
# tiny-pool deadlocks die fast. mem_trace gauges give anon + actual pool_used per run.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest TEMPLATE=genfile.test EMITBPS=0
export FILECSV=/dev/shm/nes-input/skew_Nw80_smallD.csv
export CELL_TIMEOUT=180
T=19800000; W=50000; L=4000000; CAP=512; CPUSET=0,1,8,9; WTH=2
POOLS="250 500 1000 2000 4000 8000 16000 26000 40000 64000"

for arm in inmem ooc; do
  if [ "$arm" = ooc ]; then SOFT=50; HARD=100; else SOFT=100000; HARD=100000; fi
  for P in $POOLS; do
    for r in 1 2 3; do
      DCPUS=2 ./run-cell.sh psweep_${arm}_p${P}_r$r $T $W 0 $L $CAP on on $SOFT $HARD $P $CPUSET $WTH "" "" 16 8 4 >/dev/null 2>&1
    done
  done
done

echo "==== POOL SWEEP SUMMARY  (rc, tps, anon_peakMB, pool_usedMB - each x3 reps) ===="
printf "%-6s %-16s | %-14s | %-26s | %-14s | %s\n" arm pool_buf"(MB)" "rc x3" "tps x3" "anon_peakMB x3" "pool_usedMB x3"
for arm in inmem ooc; do
  for P in $POOLS; do
    poolmb=$((P*8192/1048576)); rcs=""; tps=""; anon=""; pu=""
    for r in 1 2 3; do f="results/psweep_${arm}_p${P}_r$r.tsv"
      if [ -s "$f" ]; then
        rcs="$rcs $(cut -f9 "$f")"; tps="$tps $(cut -f22 "$f")"; anon="$anon $(cut -f23 "$f")"; pu="$pu $(cut -f32 "$f")"
      else rcs="$rcs -"; fi
    done
    printf "%-6s %-16s | %-14s | %-26s | %-14s | %s\n" "$arm" "${P}(${poolmb})" "$rcs" "$tps" "$anon" "$pu"
  done
done
