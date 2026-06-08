#!/usr/bin/env bash
# Phase 2: L* boundary + pool-overhead ceiling. cap=1024, W=50k, soft=182/hard=234, POOL=400*N_w.
echo "PHASE2_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CPUSETS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/phase2-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
launch(){ # L spill
  local L="$1" SP="$2"; local Nw=$((L/50000)); local POOL=$((Nw*400)); local T=$((L*5/4))
  read -u 9 SLOT
  ( bash "$SD/run-cell.sh" "p2_L$((L/1000000))M_${SP}" "$T" 50000 200000 "$L" 1024 "$SP" on 182 234 "$POOL" "${CPUSETS[$SLOT]}"
    echo "$SLOT" >&9 ) &
}
for L in 2000000 4000000 8000000 12000000; do launch "$L" on; launch "$L" off; done
launch 16000000 on
wait; exec 9<&-
echo "PHASE2_DONE $(date)"
