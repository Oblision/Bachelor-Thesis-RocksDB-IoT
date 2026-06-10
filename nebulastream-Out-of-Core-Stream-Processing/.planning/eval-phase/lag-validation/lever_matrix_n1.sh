#!/usr/bin/env bash
# N1 EXPERIMENT (design-review follow-up, OPTIONS doc §7) — lever matrix WITH POOLING ENGAGED.
# operator_buffer_size=8192 >= page_size 8192 -> ChainedHashMap's POOLED branch is active for the
# FIRST TIME in the entire eval (default 4096 left all map pages unpooled; pool was I/O-only).
# Arms (one binary, config-gated):
#   B0 = pooling only, levers OFF   -> architecture A/B vs historic A0 (heap maps)
#   B1 = pooling + RESERVEPCT=15    -> 1a/1b/1d now defend a pool that actually holds the maps
#   B2 = pooling + RESERVEPCT=15 PACED=on
# NOTE pool MB at 8192 B/buf is 2x the unpooled-era labels: 16k=132, 32k=264, 64k=528, 128k=1056 MB.
# Usage: ./lever_matrix_n1.sh          run everything (resumable; run-cell skips done cells)
#        ./lever_matrix_n1.sh check    binary + config sanity only
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192

BIN="../../../$SYSTEST"
if ! strings "$BIN" 2>/dev/null | grep -q "pool_reserve_pct"; then
  echo "FATAL: $BIN does not know pool_reserve_pct — rebuild from branch ooc-stability-levers first."
  exit 1
fi
echo "OK: binary has the lever config."
[ "${1:-}" = check ] && exit 0

# label cap pool rho csv rows reservepct paced reps
cell_group() {
  local ARM=$1 CAP=$2 POOL=$3 RHO=$4 CSV=$5 ROWS=$6 RESERVE=$7 PACED_=$8 REPS=$9
  local SOFT=$((CAP / 10)) HARD=$((CAP / 5))
  for r in $(seq 1 "$REPS"); do
    local LBL="N_${ARM}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL"
    OPBUFSIZE=$OPB RESERVEPCT="$RESERVE" PACED="$PACED_" TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}

R150=/dev/shm/nes-input/rho_r150.csv;  N150=31000000
R200=/dev/shm/nes-input/rho_r200.csv;  N200=50000000
R300=/dev/shm/nes-input/rho_r300.csv;  N300=88000000
R075=/dev/shm/nes-input/rho_r075.csv;  N075=8000000

echo "==== N1 MATRIX START $(date +%F_%T) ===="

for ARMSPEC in "B0::" "B1:15:" "B2:15:on"; do
  ARM=${ARMSPEC%%:*}; rest=${ARMSPEC#*:}; RES=${rest%%:*}; PCD=${rest#*:}
  echo "== $ARM (OPBUFSIZE=$OPB RESERVEPCT=${RES:-off} PACED=${PCD:-off}) =="
  # G0 regression/correctness gate: easy cell, checksum must MATCH on the pooled path
  cell_group "$ARM"  512  16000 r075 "$R075" $N075 "$RES" "$PCD" 8
  # G1 headline: the empty corridor
  cell_group "$ARM" 2048  64000 r300 "$R300" $N300 "$RES" "$PCD" 8
  cell_group "$ARM" 2048 128000 r300 "$R300" $N300 "$RES" "$PCD" 8
  # G2 pure rc=141 (I/O-flood cells under the old architecture)
  cell_group "$ARM" 2048  16000 r200 "$R200" $N200 "$RES" "$PCD" 8
  cell_group "$ARM" 2048  32000 r200 "$R200" $N200 "$RES" "$PCD" 8
  # G3 flaky column
  cell_group "$ARM" 1024  16000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM" 1024  32000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM" 1024  64000 r150 "$R150" $N150 "$RES" "$PCD" 8
  # G4 tight cap
  cell_group "$ARM"  512  16000 r150 "$R150" $N150 "$RES" "$PCD" 8
  cell_group "$ARM"  512  16000 r200 "$R200" $N200 "$RES" "$PCD" 8
  # G5 cost (historic A0 10/10 here)
  cell_group "$ARM" 1024  64000 r200 "$R200" $N200 "$RES" "$PCD" 8
done

echo "==== N1 MATRIX COMPLETE $(date +%F_%T) ===="
