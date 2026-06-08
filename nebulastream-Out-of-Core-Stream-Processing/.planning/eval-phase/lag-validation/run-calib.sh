#!/usr/bin/env bash
echo "CALIB_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
L=4000000; T=5000000   # fixed L=4M, TUPLES=1.25*L
for POOL in 16000 32000 64000; do
  for SH in "4:8" "96:128" "182:234"; do
    SOFT=${SH%:*}; HARD=${SH#*:}
    echo "=== calib soft=$SOFT pool=$POOL $(date) ==="
    bash "$SD/run-cell.sh" "cal_L4M_soft${SOFT}_pool${POOL}" "$T" 50000 200000 "$L" 1024 on off "$SOFT" "$HARD" "$POOL"
  done
done
echo "=== calib done $(date) ==="
