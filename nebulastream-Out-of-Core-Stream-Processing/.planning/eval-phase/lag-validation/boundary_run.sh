#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192
D=/dev/shm/nes-input
b2() {
  local CAP=$1 POOL=$2 RHO=$3 ROWS=$4
  local SOFT=$((CAP/10)) HARD=$((CAP/5))
  for r in 1 2 3 4 5 6 7 8; do
    echo "[$(date +%H:%M:%S)] W_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    OPBUFSIZE=8192 RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="$D/rho_$RHO.csv" DCPUS=2 \
      ./run-cell.sh "W_c${CAP}_p$((POOL/1000))k_${RHO}_r$r" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== BOUNDARY RUN START $(date +%F_%T) ===="
b2 2048 96000 r700  195000000
b2 2048 96000 r1000 280000000
b2 1024 48000 r150  31000000
b2 1024 48000 r200  50000000
b2 1024 48000 r300  88000000
echo "==== BOUNDARY RUN COMPLETE $(date +%F_%T) ===="
