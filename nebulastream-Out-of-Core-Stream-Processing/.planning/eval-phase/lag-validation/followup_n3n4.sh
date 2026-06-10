#!/usr/bin/env bash
# FOLLOW-UP to the N1 matrix (EXPERIMENT doc §8) — three open items, pooling stays ENGAGED (OPBUFSIZE=8192):
#   (1) N3 soft-from-pool: SOFT = pool_MB/3, HARD = 2*SOFT (the N1 2048 cells had SOFT=CAP/10 > pool — mis-spec)
#   (2) cost cell re-sited to a pool-feasible point: 1024/32k/r200 (in-mem comparator 10/10, 3.25M tps)
#   (3) N4 max_inflight sweep at the 512/16k/r150 regression (fast-ingest race; default 64 vs 32 vs 16)
# Labels: F_<armtag>_c<cap>_p<pool>k_<rho>_r<rep>  (armtag B2i32 etc. for the N4 group)
# Usage: ./followup_n3n4.sh        run everything (resumable)
#        ./followup_n3n4.sh check  binary sanity only
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192

BIN="../../../$SYSTEST"
if ! strings "$BIN" 2>/dev/null | grep -q "pool_reserve_pct"; then
  echo "FATAL: $BIN lacks lever config — rebuild from branch ooc-stability-levers first."; exit 1
fi
if ! strings "$BIN" 2>/dev/null | grep -q "default_max_inflight_buffers"; then
  echo "FATAL: $BIN lacks default_max_inflight_buffers."; exit 1
fi
echo "OK: binary has lever config + max_inflight."
[ "${1:-}" = check ] && exit 0

# armtag cap pool rho csv rows reservepct paced maxinflight reps   (SOFT/HARD derived from POOL)
cell_group() {
  local ARMTAG=$1 CAP=$2 POOL=$3 RHO=$4 CSV=$5 ROWS=$6 RESERVE=$7 PACED_=$8 INFLIGHT=$9 REPS=${10}
  local POOL_MB=$((POOL * 8192 / 1048576))
  local SOFT=$((POOL_MB / 3)) HARD=$((2 * POOL_MB / 3))
  for r in $(seq 1 "$REPS"); do
    local LBL="F_${ARMTAG}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    echo "[$(date +%H:%M:%S)] $LBL (soft=$SOFT hard=$HARD inflight=${INFLIGHT:-64})"
    OPBUFSIZE=$OPB RESERVEPCT="$RESERVE" PACED="$PACED_" MAXINFLIGHT="$INFLIGHT" \
      TEMPLATE=genfile.test FILECSV="$CSV" DCPUS=2 \
      ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}

R150=/dev/shm/nes-input/rho_r150.csv;  N150=31000000
R200=/dev/shm/nes-input/rho_r200.csv;  N200=50000000
R300=/dev/shm/nes-input/rho_r300.csv;  N300=88000000

echo "==== FOLLOW-UP MATRIX START $(date +%F_%T) ===="

echo "== (1) N3 soft-from-pool at the 2048 corridor cells (B2; B0 x4 at one pivot for attribution) =="
cell_group B2 2048  16000 r200 "$R200" $N200 15 on "" 8
cell_group B2 2048  32000 r200 "$R200" $N200 15 on "" 8
cell_group B0 2048  32000 r200 "$R200" $N200 ""  "" "" 4
cell_group B2 2048  64000 r300 "$R300" $N300 15 on "" 8
cell_group B2 2048 128000 r300 "$R300" $N300 15 on "" 8

echo "== (2) cost cell re-sited: 1024/32k/r200 (vs in-mem 10/10, 3.25M tps, 111us) =="
cell_group B2 1024  32000 r200 "$R200" $N200 15 on "" 8

echo "== (3) N4 max_inflight sweep at the 512/16k/r150 regression (i64 = control) =="
cell_group B2i64 512 16000 r150 "$R150" $N150 15 on 64 8
cell_group B2i32 512 16000 r150 "$R150" $N150 15 on 32 8
cell_group B2i16 512 16000 r150 "$R150" $N150 15 on 16 8

echo "==== FOLLOW-UP MATRIX COMPLETE $(date +%F_%T) ===="
