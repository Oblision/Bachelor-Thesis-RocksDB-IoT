#!/usr/bin/env bash
# STABILITY-LEVER EXPERIMENT (branch ooc-stability-levers) — see EXPERIMENT-stability-levers.md
# Arms: A0reg (levers off, regression gate), A1 (RESERVEPCT=15), A2 (RESERVEPCT=15 PACED=on),
#       A3 dose (RESERVEPCT=5/30 PACED=on at pivots).
# Usage: ./lever_matrix.sh          run everything (resumable; run-cell skips done cells)
#        ./lever_matrix.sh check    binary + config sanity only
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192

BIN="../../../$SYSTEST"
check() {
  if ! strings "$BIN" 2>/dev/null | grep -q "pool_reserve_pct"; then
    echo "FATAL: $BIN does not know pool_reserve_pct — rebuild from branch ooc-stability-levers first:"
    echo "  docker run --rm --workdir /workspace -v \$(pwd)/../../..:/workspace nebulastream/nes-development:local-rocksdb \\"
    echo "    bash -c 'cmake --build cmake-build-release --target systest -j 14'"
    exit 1
  fi
  echo "OK: binary has the lever config."
}
check
[ "${1:-}" = check ] && exit 0

# label cap soft hard pool rho csv rows reservepct paced reps
cell_group() {
  local ARM=$1 CAP=$2 POOL=$3 RHO=$4 CSV=$5 ROWS=$6 RESERVE=$7 PACED_=$8 REPS=$9
  local SOFT=$((CAP / 10)) HARD=$((CAP / 5))
  for r in $(seq 1 "$REPS"); do
    local LBL="S_${ARM}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL"
    RESERVEPCT="$RESERVE" PACED="$PACED_" TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}

R150=/dev/shm/nes-input/rho_r150.csv;  N150=31000000
R200=/dev/shm/nes-input/rho_r200.csv;  N200=50000000
R300=/dev/shm/nes-input/rho_r300.csv;  N300=88000000
R075=/dev/shm/nes-input/rho_r075.csv;  N075=8000000

echo "==== LEVER MATRIX START $(date +%F_%T) ===="

echo "== A0-regression (levers OFF on the new binary; gate against historic L_ data) =="
cell_group A0reg  512 16000 r075 "$R075" $N075 "" ""  4
cell_group A0reg  512 16000 r150 "$R150" $N150 "" ""  4
cell_group A0reg 1024 64000 r150 "$R150" $N150 "" ""  4

for ARMSPEC in "A1:15:" "A2:15:on"; do
  ARM=${ARMSPEC%%:*}; rest=${ARMSPEC#*:}; RES=${rest%%:*}; PCD=${rest#*:}
  echo "== $ARM (RESERVEPCT=$RES PACED=${PCD:-off}) =="
  # G1 headline: the empty corridor
  cell_group "$ARM" 2048  64000 r300 "$R300" $N300 "$RES" "$PCD" 8
  cell_group "$ARM" 2048 128000 r300 "$R300" $N300 "$RES" "$PCD" 8
  # G2 pure rc=141
  cell_group "$ARM" 2048  16000 r200 "$R200" $N200 "$RES" "$PCD" 8
  cell_group "$ARM" 2048  32000 r200 "$R200" $N200 "$RES" "$PCD" 8
  # G3 flaky column
  cell_group "$ARM" 1024  16000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM" 1024  32000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM" 1024  64000 r150 "$R150" $N150 "$RES" "$PCD" 8
  # G4 flush-OOM at the tight cap
  cell_group "$ARM"  512  16000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM"  512  16000 r200 "$R200" $N200 "$RES" "$PCD" 8
  # G5 cost (A0 already 10/10 here)
  cell_group "$ARM" 1024  64000 r200 "$R200" $N200 "$RES" "$PCD" 8
done

echo "== A3 dose-response at the pivots =="
for RES in 5 30; do
  cell_group "A3r${RES}" 1024 32000 r150 "$R150" $N150 "$RES" "on" 4
  cell_group "A3r${RES}" 2048 64000 r300 "$R300" $N300 "$RES" "on" 4
done

echo "==== LEVER MATRIX COMPLETE $(date +%F_%T) ===="
