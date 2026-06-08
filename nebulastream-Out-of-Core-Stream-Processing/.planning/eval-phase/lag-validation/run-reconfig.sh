#!/usr/bin/env bash
# Reconfigured demo — BUDGET the overhead. W=400s (large -> N_w=30 small pool, keyed/win>>overhead),
# L=12M (backlog ~468MB > cap-floor => spill-OFF must OOM = data exceeds memory), TUPLES=30M (2.5xL steady),
# cap=512, soft=32/hard=64 (keep resident keyed small), POOL=12000 (=400*N_w). Vary RocksDB wbuf/cache.
echo "RECON_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/recon-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
# label                  tup       win    rate   lag      cap sp  smp soft hard pool  cpuset      wt mb pg wbuf cache
read -u 9 S; ( bash "$SD/run-cell.sh" recon_off          30000000 400000 200000 12000000 512 off on 32 64 12000 "${CS[$S]}" 4 "" "" 16 8;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" recon_on_min       30000000 400000 200000 12000000 512 on  on 32 64 12000 "${CS[$S]}" 4 "" "" 16 8;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" recon_on_tiny      30000000 400000 200000 12000000 512 on  on 32 64 12000 "${CS[$S]}" 4 "" "" 8  4;  echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" recon_on_research  30000000 400000 200000 12000000 512 on  on 32 64 12000 "${CS[$S]}" 4 "" "" 64 32; echo "$S">&9 ) &
wait; exec 9<&-
echo "RECON_DONE $(date)"
