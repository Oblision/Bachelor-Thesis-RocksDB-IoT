#!/usr/bin/env bash
# Attribution: does per-window resident overhead scale with worker-thread count?
# threads in {1,2,4} x L in {2M,4M}. cap=1024, W=50k, soft=4/hard=8 (crush keyed->measure scaffold),
# POOL=64000 (generous, no rc=141), spill on, sampled. Slope of resident-vs-N_w per thread count.
echo "ATTR_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CPUSETS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/attr-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
for TH in 1 2 4; do
  for L in 2000000 4000000; do
    T=$((L*5/4))
    read -u 9 SLOT
    ( bash "$SD/run-cell.sh" "a_t${TH}_L$((L/1000000))M" "$T" 50000 200000 "$L" 1024 on on 4 8 64000 "${CPUSETS[$SLOT]}" "$TH"
      echo "$SLOT" >&9 ) &
  done
done
wait; exec 9<&-
echo "ATTR_DONE $(date)"
