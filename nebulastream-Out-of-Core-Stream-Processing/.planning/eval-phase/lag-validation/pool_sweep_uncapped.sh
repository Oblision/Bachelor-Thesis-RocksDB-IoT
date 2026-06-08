#!/usr/bin/env bash
# UNCAPPED pool sweep -> clean throughput + memory vs pool size (no OOM masking) for plotting/linear fit.
# Tiny pools still rc141 (fixed-pool exhaustion is independent of the mem cap) => the lower floor still shows.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest TEMPLATE=genfile.test EMITBPS=0
export FILECSV=/dev/shm/nes-input/skew_Nw80_smallD.csv
export CELL_TIMEOUT=180
T=19800000; W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2
POOLS="250 500 1000 2000 4000 8000 16000 26000 40000 64000"
for arm in inmem ooc; do
  if [ "$arm" = ooc ]; then SOFT=50; HARD=100; else SOFT=100000; HARD=100000; fi
  for P in $POOLS; do for r in 1 2 3; do
    DCPUS=2 ./run-cell.sh psweepU_${arm}_p${P}_r$r $T $W 0 $L none on on $SOFT $HARD $P $CPUSET $WTH "" "" 16 8 4 >/dev/null 2>&1
  done; done
done
echo "uncapped pool sweep done"
