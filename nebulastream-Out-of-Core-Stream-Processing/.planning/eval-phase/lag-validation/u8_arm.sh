#!/usr/bin/env bash
# U8 disambiguation arm — unbounded allocation at matched I/O granularity (see EXPERIMENT-final-matrix.md)
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192
D=/dev/shm/nes-input
cell() { # cap pool rho csv rows
  local CAP=$1 POOL=$2 RHO=$3 CSV=$4 ROWS=$5
  local SOFT=$((CAP/10)) HARD=$((CAP/5))
  for r in 1 2 3 4 5 6 7 8; do
    local LBL="U_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL"
    OPBUFSIZE=8128 TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== U8 ARM START $(date +%F_%T) ===="
cell  512 16000 r200 "$D/rho_r200.csv"  50000000
cell 1024 32000 r150 "$D/rho_r150.csv"  31000000
cell  512 16000 r150 "$D/rho_r150.csv"  31000000
cell 2048 16000 r700 "$D/rho_r700.csv" 195000000
cell  512 16000 r075 "$D/rho_r075.csv"   8000000
cell 1024 32000 r100 "$D/rho_r100.csv"  15000000
cell 2048 64000 r200 "$D/rho_r200.csv"  50000000
echo "==== U8 ARM COMPLETE $(date +%F_%T) ===="
