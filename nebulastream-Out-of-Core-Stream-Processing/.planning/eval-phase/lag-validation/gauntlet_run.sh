#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192
D=/dev/shm/nes-input
g() { # LBL ROWS CAP SOFT HARD POOL EMIT CSV
  local LBL=$1 ROWS=$2 CAP=$3 SOFT=$4 HARD=$5 POOL=$6 EMIT=$7 CSV=$8
  for r in 1 2 3 4 5 6 7 8; do
    echo "[$(date +%H:%M:%S)] ${LBL}_r$r"
    SLICECACHE=off EMITBPS=$EMIT OPBUFSIZE=8192 RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "${LBL}_r$r" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== GAUNTLET START $(date +%F_%T) ===="
echo "== crash-cell extinction (historical rc134/rc11 cells, cache off) =="
g X_c512_p8k_r200    50000000  512  51 102  8000 0 "$D/rho_r200.csv"
g X_c512_p8k_r300    88000000  512  51 102  8000 0 "$D/rho_r300.csv"
g X_c512_p16k_r300   88000000  512  51 102 16000 0 "$D/rho_r300.csv"
g X_c1024_p16k_r300  88000000 1024 102 204 16000 0 "$D/rho_r300.csv"
g X_c2048_p16k_r300  88000000 2048 204 409 16000 0 "$D/rho_r300.csv"
echo "== paced valleys (the Phase-1 goal: convert now that the bug is gone?) =="
g XP_c512_p16k_r100  15000000  512  51 102 16000 30000000 "$D/rho_r100.csv"
g XP_c512_p16k_r150  31000000  512  51 102 16000 30000000 "$D/rho_r150.csv"
g XP_c1024_p48k_r200 50000000 1024 102 204 48000 30000000 "$D/rho_r200.csv"
echo "== cache-off overhead check at winners =="
g XO_c1024_p32k_r150 31000000 1024 102 204 32000 0 "$D/rho_r150.csv"
g XO_c2048_p64k_r200 50000000 2048 204 409 64000 0 "$D/rho_r200.csv"
echo "==== GAUNTLET COMPLETE $(date +%F_%T) ===="
