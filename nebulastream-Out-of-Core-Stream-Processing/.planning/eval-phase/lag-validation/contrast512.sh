#!/usr/bin/env bash
# cap=512 OOM-vs-survive contrast ladder (the RQ1 headline: in-mem OOMs, OOC survives).
# Runs SEQUENTIALLY on the single SD card (parallel would contend on one device).
# For each N_w: OOC arm (spill fires, soft60/hard120) + lag-honored in-mem arm
# (spill.enabled stays true so emit_lag still retains the band, but soft/hard are set
#  huge so the spiller NEVER fires -> band accrues resident -> OOM). Same workload both arms.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
TUPLES=5000000; W=50000; RATE=200000; CAP=512
CPUSET=0,1,8,9; WTH=4; WBUF=16; CACHE=8; NPART=4

# N_w -> pool (~330 pooled buffers/window per the FINDINGS pool rule, + margin)
declare -A POOL=( [48]=16000 [64]=21000 [80]=26000 )

for NW in 48 64 80; do
  L=$(( NW * W ))
  P=${POOL[$NW]}
  echo "==== N_w=$NW  emit_lag=$L  pool=$P  cap=${CAP}MB ===="
  # OOC arm: spiller active
  ./run-cell.sh ooc512_Nw${NW} $TUPLES $W $RATE $L $CAP on off 60 120 $P $CPUSET $WTH "" "" $WBUF $CACHE $NPART
  # lag-honored in-mem arm: spiller never fires (soft/hard huge), band still retained
  ./run-cell.sh inm512_Nw${NW} $TUPLES $W $RATE $L $CAP on off 100000 100000 $P $CPUSET $WTH "" "" $WBUF $CACHE $NPART
done

echo "==== contrast512 summary (label rc status peak_mb) ===="
for NW in 48 64 80; do
  for A in ooc512 inm512; do
    f="results/${A}_Nw${NW}.tsv"
    [ -s "$f" ] && awk -F'\t' '{printf "%-16s rc=%-4s %-4s peak=%sMB store_peak_b=%s slices=%s\n",$1,$9,$10,$12,$14,$15}' "$f"
  done
done
