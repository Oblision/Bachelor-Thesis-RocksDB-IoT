#!/usr/bin/env bash
# FINAL EVALUATION MATRIX (release, CPU-capped, SD spill, tmpfs input).
# rho sweep (state/cap) x 3 arms. rho is set by per-window cardinality (the CSVs).
#   OOC          = cap512, spill on, soft50/hard100   -> survives if spill keeps up
#   in-mem capped= cap512, spiller never fires         -> OOMs when state>cap
#   uncapped     = no mem cap, spiller never fires     -> sufficient-memory baseline (RQ2 ref) + true peak state (=> rho)
# Fixed research-informed config: W=50k, emit_lag=2M (N_w=40), pool=14k, P=4, lz4 wbuf16/cache8,
#   --cpus=2 + 2 worker threads (dual-core edge SoC; spiller has its own core), no source pacing.
# Perf-bearing arms (OOC, uncapped) get 3 reps; in-mem-capped 1 (deterministic OOM/survive).
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest
export TEMPLATE=genfile.test EMITBPS=0
W=50000; L=2000000; POOL=14000; CPUSET=0,1,8,9; WTH=2
COMMON="$W 0 $L"   # args: window emit_rate emit_lag

run(){ DCPUS=2 ./run-cell.sh "$@" "$CPUSET" $WTH "" "" 16 8 4; }

for pt in rho05:/dev/shm/nes-input/rho05.csv:27500000 \
          rho1:/dev/shm/nes-input/cal_rho.csv:55000000 \
          rho2:/dev/shm/nes-input/rho2.csv:110000000; do
  LBL=${pt%%:*}; rest=${pt#*:}; CSV=${rest%:*}; ROWS=${rest##*:}
  [ -s "$CSV" ] || { echo "[skip $LBL] missing $CSV"; continue; }
  export FILECSV=$CSV
  echo "==== $LBL  ($CSV, rows=$ROWS) ===="
  for r in 1 2 3; do run ${LBL}_uncap_r$r $ROWS $COMMON none on off 100000 100000 $POOL; done
  run ${LBL}_inmcap $ROWS $COMMON 512 on off 100000 100000 $POOL
  for r in 1 2 3; do run ${LBL}_ooc_r$r $ROWS $COMMON 512 on off 50 100 $POOL; done
done

echo "==== MATRIX SUMMARY ===="
printf '%-20s %-4s %-4s %6s %12s %4s %10s %6s %12s %12s\n' label rc st peakMB store_b slc tps p99us cpu_us io_w
for f in results/rho0*_*.tsv results/rho1_*.tsv results/rho2_*.tsv; do
  [ -s "$f" ] && awk -F'\t' '{printf "%-20s %-4s %-4s %6s %12s %4s %10s %6s %12s %12s\n",$1,$9,$10,$12,$14,$15,$22,$21,$16,$18}' "$f"
done 2>/dev/null
