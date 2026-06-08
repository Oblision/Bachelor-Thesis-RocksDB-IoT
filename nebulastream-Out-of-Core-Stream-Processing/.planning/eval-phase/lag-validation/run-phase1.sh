#!/usr/bin/env bash
# Phase 1: pin POOL = k*N_w + c. N_w in {40,80,160} (L=2/4/8M at W=50k) x POOL in {16,24,32,48,64}k.
# cap=1024 (isolate pool effect), spill on, soft=4/hard=8 (aggressive -> keyed state crushed, so pool
# pressure is per-window overhead = clean k). 4-wide via FIFO slot pool on 4 distinct physical-core pairs.
echo "PHASE1_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CPUSETS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/phase1-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
for L in 2000000 4000000 8000000; do
  Nw=$((L/50000)); T=$((L*5/4))
  for POOL in 16000 24000 32000 48000 64000; do
    read -u 9 SLOT
    ( bash "$SD/run-cell.sh" "p1_Nw${Nw}_pool$((POOL/1000))k" "$T" 50000 200000 "$L" 1024 on off 4 8 "$POOL" "${CPUSETS[$SLOT]}"
      echo "$SLOT" >&9 ) &
  done
done
wait
exec 9<&-
echo "PHASE1_DONE $(date)"
