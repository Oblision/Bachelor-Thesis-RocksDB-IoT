#!/usr/bin/env bash
# insession_512.sh — POWERED same-session 16/8-vs-32/32 A/B at cap=512 (the decisive cap).
# Full W_ 512 grid (pools 8k/16k x rho{r075..r300} x 8 reps) run NOW at both configs, interleaved
# per rep, SAMPLE on. Reuses D16_/D32_ labels so the drift-control pilot cells (reps 1-3 of 4 points)
# are skipped (resumable) and merge into one coherent in-session dataset. ~160 cells total, ~136 new.
set -u
LAGVAL="${LAGVAL_DIR:-/home/oblision/Desktop/Bachelor-Thesis-RocksDB-IoT/nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation}"
cd "$LAGVAL" || { echo "no lag-validation dir"; exit 1; }
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192
strings "../../../$SYSTEST" 2>/dev/null | grep -q pool_reserve_pct || { echo "FATAL: not the lever build"; exit 1; }

declare -A ROWS=( [r075]=8000000 [r100]=15000000 [r150]=31000000 [r200]=50000000 [r300]=88000000 )

run_one() {  # PREFIX POOL RHO REP WBUF CACHE   (cap fixed 512)
  local PFX=$1 POOL=$2 RHO=$3 REP=$4 WBUF=$5 CACHE=$6
  local LBL="${PFX}_c512_p$((POOL/1000))k_${RHO}_r${REP}"
  echo "[$(date +%H:%M:%S)] $LBL  (wbuf=$WBUF cache=$CACHE)"
  OPBUFSIZE=$OPB RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="/dev/shm/nes-input/rho_${RHO}.csv" DCPUS=2 \
    ./run-cell.sh "$LBL" "${ROWS[$RHO]}" $W 0 $L 512 on on 51 102 "$POOL" $CPUSET $WTH "" "$PAGESIZE" "$WBUF" "$CACHE" 4 >/dev/null 2>&1
}

echo "==== IN-SESSION 512 A/B START $(date +%F_%T) ===="
for POOL in 8000 16000; do
  for RHO in r075 r100 r150 r200 r300; do
    for r in $(seq 1 8); do
      run_one D16 "$POOL" "$RHO" "$r" 16 8     # fresh 16/8
      run_one D32 "$POOL" "$RHO" "$r" 32 32    # fresh 32/32 (interleaved)
    done
  done
done
echo "==== IN-SESSION 512 A/B COMPLETE $(date +%F_%T) ===="
echo "Results: results/D16_c512_*.tsv results/D32_c512_*.tsv ; traces: traces/D*_c512_*.trace"
