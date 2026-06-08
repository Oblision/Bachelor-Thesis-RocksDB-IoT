#!/usr/bin/env bash
# DECISIVE test: L=16M (held backlog ~520MB > cap-floor => spill-OFF MUST OOM = data exceeds memory).
# spill-ON at P in {4,8,16} (higher P => smaller 1/P unspill-on-emit chunk => smaller transient).
# W=400s, cap=512, soft=32/hard=64, wbuf=16/cache=8 (minimal RocksDB), POOL=16000 (=400*N_w, N_w=40), TUPLES=24M (1.5L).
# If spill-ON still OOMs at all P, config is closed -> Option B required. If a P survives, config wins.
echo "DEC_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/dec-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
#                                    label        tup      win    rate   lag      cap sp  smp sf hd pool  cpuset      wt mb pg wb ca np
read -u 9 S; ( bash "$SD/run-cell.sh" dec_off_L16M 24000000 400000 200000 16000000 512 off on 32 64 16000 "${CS[$S]}" 4 "" "" 16 8 4;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" dec_on_P4    24000000 400000 200000 16000000 512 on  on 32 64 16000 "${CS[$S]}" 4 "" "" 16 8 4;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" dec_on_P8    24000000 400000 200000 16000000 512 on  on 32 64 16000 "${CS[$S]}" 4 "" "" 16 8 8;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" dec_on_P16   24000000 400000 200000 16000000 512 on  on 32 64 16000 "${CS[$S]}" 4 "" "" 16 8 16; echo "$S">&9 ) &
wait; exec 9<&-
echo "DEC_DONE $(date)"
