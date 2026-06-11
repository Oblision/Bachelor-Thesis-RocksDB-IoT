#!/usr/bin/env bash
# FINAL MATRIX (EXPERIMENT-final-matrix.md) — RQ1/RQ2 headline grid under the B2 ship config.
# B2 = OPBUFSIZE=8192 RESERVEPCT=15 PACED=on, soft=pool_MB/3 hard=2*pool_MB/3. Labels V_*.
# Usage: ./final_matrix.sh         run everything (resumable; run-cell skips done cells)
#        ./final_matrix.sh check   binary sanity only
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192

BIN="../../../$SYSTEST"
strings "$BIN" 2>/dev/null | grep -q "pool_reserve_pct" || { echo "FATAL: binary lacks lever config"; exit 1; }
echo "OK: binary is the lever branch build."
[ "${1:-}" = check ] && exit 0

# cap pool rho csv rows reps  (B2 config baked in; SOFT/HARD derived from POOL)
cell() {
  local CAP=$1 POOL=$2 RHO=$3 CSV=$4 ROWS=$5 REPS=$6
  local POOL_MB=$((POOL * 8192 / 1048576))
  local SOFT=$((POOL_MB / 3)) HARD=$((2 * POOL_MB / 3))
  for r in $(seq 1 "$REPS"); do
    local LBL="V_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL (soft=$SOFT hard=$HARD)"
    OPBUFSIZE=$OPB RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}

D=/dev/shm/nes-input
declare -A ROWS=( [r075]=8000000 [r100]=15000000 [r150]=31000000 [r200]=50000000 [r300]=88000000 [r500]=140000000 [r700]=195000000 [r1000]=280000000 )
rows() { echo "${ROWS[$1]}"; }

echo "==== FINAL MATRIX START $(date +%F_%T) ===="

echo "== cap 512 =="
for P in 8000 16000; do for R in r075 r100 r150 r200 r300; do
  cell 512 "$P" "$R" "$D/rho_$R.csv" "$(rows $R)" 8
done; done

echo "== cap 1024 =="
for P in 16000 32000; do for R in r100 r150 r200 r300 r500; do
  cell 1024 "$P" "$R" "$D/rho_$R.csv" "$(rows $R)" 8
done; done

echo "== cap 2048 =="
for P in 16000 32000 64000; do for R in r200 r300 r500 r700 r1000; do
  cell 2048 "$P" "$R" "$D/rho_$R.csv" "$(rows $R)" 8
done; done

echo "== checksum correctness under active spilling (V-H5) =="
for r in 1 2; do
  LBL="V_chk_c512_p16k_r150_r$r"
  echo "[$(date +%H:%M:%S)] $LBL"
  OPBUFSIZE=$OPB RESERVEPCT=15 PACED=on TEMPLATE=genfile_checksum.test FILECSV="$D/rho_r150.csv" DCPUS=2 \
    ./run-cell.sh "$LBL" 31000000 $W 0 $L 512 on on 41 83 16000 $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
done

echo "==== FINAL MATRIX COMPLETE $(date +%F_%T) ===="
