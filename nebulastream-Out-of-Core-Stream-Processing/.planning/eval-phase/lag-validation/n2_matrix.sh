#!/usr/bin/env bash
# N2 EXPERIMENT (EXPERIMENT doc §9) — allocation backpressure on top of the pooled+lever stack.
# Arm B3 = OPBUFSIZE=8192 RESERVEPCT=15 PACED=on BACKPRESSMS=30000, soft=pool/3 (money cell: soft=CAP/10 as §7).
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192; BPMS=30000

BIN="../../../$SYSTEST"
strings "$BIN" 2>/dev/null | grep -q "allocation_backpressure_ms" || { echo "FATAL: binary lacks N2"; exit 1; }
echo "OK: binary has N2."
[ "${1:-}" = check ] && exit 0

# armtag cap pool rho csv rows soft hard reps
cell_group() {
  local ARMTAG=$1 CAP=$2 POOL=$3 RHO=$4 CSV=$5 ROWS=$6 SOFT=$7 HARD=$8 REPS=$9
  for r in $(seq 1 "$REPS"); do
    local LBL="B3_${ARMTAG}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL (soft=$SOFT hard=$HARD bp=${BPMS}ms)"
    OPBUFSIZE=$OPB RESERVEPCT=15 PACED=on BACKPRESSMS=$BPMS TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}

R075=/dev/shm/nes-input/rho_r075.csv;  N075=8000000
R150=/dev/shm/nes-input/rho_r150.csv;  N150=31000000
R200=/dev/shm/nes-input/rho_r200.csv;  N200=50000000
R300=/dev/shm/nes-input/rho_r300.csv;  N300=88000000

echo "==== N2 MATRIX START $(date +%F_%T) ===="
echo "== G0 gate =="
cell_group g0   512  16000 r075 "$R075" $N075 41  83 8
echo "== regime-3 conversion cells (controls: §8 B2 = 0/8 each) =="
cell_group bp  1024  32000 r200 "$R200" $N200 83 166 8
cell_group bp  2048  16000 r200 "$R200" $N200 41  83 8
cell_group bp  2048  32000 r200 "$R200" $N200 83 166 8
cell_group bp   512  16000 r150 "$R150" $N150 41  83 8
echo "== corridor =="
cell_group bp  2048  64000 r300 "$R300" $N300 166 333 8
echo "== regime-1 regression check (money cell, §7 config) =="
cell_group reg 1024  32000 r150 "$R150" $N150 102 204 8
echo "==== N2 MATRIX COMPLETE $(date +%F_%T) ===="

echo "== exploratory: bounded admission queue at the smoke cell (2 reps per size; rc=124 risk accepted) =="
for Q in 64 8; do
  for r in 1 2; do
    LBL="B3_q${Q}_c1024_p32k_r200_r$r"
    echo "[$(date +%H:%M:%S)] $LBL (admq=$Q)"
    OPBUFSIZE=8192 RESERVEPCT=15 PACED=on BACKPRESSMS=30000 ADMQ=$Q TEMPLATE=genfile.test \
      FILECSV=/dev/shm/nes-input/rho_r200.csv DCPUS=2 \
      ./run-cell.sh "$LBL" 50000000 50000 0 4000000 1024 on on 83 166 32000 0,1,8,9 2 "" 8192 16 8 4 >/dev/null 2>&1
  done
done
echo "==== N2 MATRIX + PROBE COMPLETE $(date +%F_%T) ===="
