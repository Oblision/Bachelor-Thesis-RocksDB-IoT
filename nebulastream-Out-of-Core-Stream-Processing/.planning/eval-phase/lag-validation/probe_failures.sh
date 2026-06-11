#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192
D=/dev/shm/nes-input
run8() { # LBLBASE ROWS CAP SOFT HARD POOL MAXB EMIT OPB RESERVE PACED CSV
  local LBL=$1 ROWS=$2 CAP=$3 SOFT=$4 HARD=$5 POOL=$6 MAXB=$7 EMIT=$8 OPB=$9 RES=${10} PCD=${11} CSV=${12}
  for r in 1 2 3 4 5 6 7 8; do
    echo "[$(date +%H:%M:%S)] ${LBL}_r$r"
    EMITBPS=$EMIT OPBUFSIZE=$OPB RESERVEPCT=$RES PACED=$PCD TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "${LBL}_r$r" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "$MAXB" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== PROBES START $(date +%F_%T) ===="
# P1 valley rate-limit (W/B2 + EMITBPS 30MB/s)
run8 P_W_emit_c512_p16k_r100  15000000  512 51 102 16000 "" 30000000 8192 15 on "$D/rho_r100.csv"
run8 P_W_emit_c512_p16k_r150  31000000  512 51 102 16000 "" 30000000 8192 15 on "$D/rho_r150.csv"
run8 P_W_emit_c1024_p48k_r200 50000000 1024 102 204 48000 "" 30000000 8192 15 on "$D/rho_r200.csv"
run8 P_W_emit_c2048_p96k_r300 88000000 2048 204 409 96000 "" 30000000 8192 15 on "$D/rho_r300.csv"
# P2 U8 floor fix
run8 P_U8_c512_p8k_r200       50000000  512 51 102  8000 "" 0 8128 "" "" "$D/rho_r200.csv"
# P3 U8 flood absorption
run8 P_U8_c2048_p64k_r300     88000000 2048 204 409 64000 "" 0 8128 "" "" "$D/rho_r300.csv"
# P4 right-wall shrink (maxbuckets)
run8 P_W_mb4k_c512_p24k_r150  31000000  512 51 102 24000 4096 0 8192 15 on "$D/rho_r150.csv"
# P5 r1000 sliver
run8 P_W_c2048_p112k_r1000   280000000 2048 204 409 112000 "" 0 8192 15 on "$D/rho_r1000.csv"
# P6 U8 pool-throttle
run8 P_U8_c1024_p8k_r150      31000000 1024 102 204  8000 "" 0 8128 "" "" "$D/rho_r150.csv"
echo "==== PROBES COMPLETE $(date +%F_%T) ===="
