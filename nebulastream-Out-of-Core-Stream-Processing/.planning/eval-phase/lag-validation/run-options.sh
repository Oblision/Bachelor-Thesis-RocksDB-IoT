#!/usr/bin/env bash
# Test "everything aside from B": Option A (config), D-approx (aggressive spill), scaffold trims.
# W=200000 (large window -> few windows, keyed/window >> overhead), L=10M (N_w=50, backlog ~390MB > cap-floor),
# cap=512, POOL=20000 (=400*N_w). spill OFF should OOM; spill ON should survive. soft=182 research config.
echo "OPT_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
declare -a CS=("0,1,8,9" "2,3,10,11" "4,5,12,13" "6,7,14,15")
FIFO=$(mktemp -u /tmp/opt-fifo.XXXXXX); mkfifo "$FIFO"; exec 9<>"$FIFO"; rm -f "$FIFO"
for s in 0 1 2 3; do echo "$s" >&9; done
RC(){ read -u 9 S; ( bash "$SD/run-cell.sh" "$@" "${CS[$S]}" "${@:14}"; echo "$S" >&9 ) & }
# args: label tuples window emit_rate emit_lag cap spill sample soft hard pool [cpuset auto] [wthreads] [maxbuckets] [pagesize]
# A-off baseline (predict OOM), A-on (predict survive), A-on-long steady-state (3xL), D aggressive soft=4, trims
read -u 9 S; ( bash "$SD/run-cell.sh" optA_off_L10M    15000000 200000 200000 10000000 512 off on 182 234 20000 "${CS[$S]}" 4; echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" optA_on_L10M     15000000 200000 200000 10000000 512 on  on 182 234 20000 "${CS[$S]}" 4; echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" optA_on_long30M  30000000 200000 200000 10000000 512 on  on 182 234 20000 "${CS[$S]}" 4; echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" optD_soft4_L10M  15000000 200000 200000 10000000 512 on  on 4   8   20000 "${CS[$S]}" 4; echo "$S">&9 ) &
wait
# scaffold trims (after first wave frees slots)
read -u 9 S; ( bash "$SD/run-cell.sh" trim_buckets2k_L10M 15000000 200000 200000 10000000 512 on on 182 234 20000 "${CS[$S]}" 4 2000; echo "$S">&9 ) &
read -u 9 S; ( bash "$SD/run-cell.sh" trim_page512_L10M   15000000 200000 200000 10000000 512 on on 182 234 20000 "${CS[$S]}" 4 ""   512; echo "$S">&9 ) &
wait; exec 9<&-
echo "OPT_DONE $(date)"
