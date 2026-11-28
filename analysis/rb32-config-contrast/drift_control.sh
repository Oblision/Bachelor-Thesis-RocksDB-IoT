#!/usr/bin/env bash
# drift_control.sh — same-SESSION, same-(freshly-formatted)-card control for the RB32 contrast.
# Purpose:
#   (1) DRIFT: re-run a few 16/8 W_ cells NOW (label D16_) so they pair with the 2026-06-12
#       baseline W_c… cells — quantifies how much of the 32/32-vs-16/8 gap is session drift.
#   (2) IN-SESSION A/B: run the SAME points at 32/32 (label D32_), interleaved per rep, so the
#       16/8-vs-32/32 contrast is free of the cross-session confound.
#   (3) TRACES: SAMPLE=on captures per-cell memory-over-time (work/rendered/<lbl>.test.trace)
#       for the overhead-climb graphics (the RB32 run had SAMPLE off).
# Outcome-blind point set (not selected by result). Mirrors final_matrix.sh's cell() exactly
# except: distinct label prefix, SAMPLE=on, and the WBUF/CACHE pair (16 8 vs 32 32).
set -u
LAGVAL="${LAGVAL_DIR:-/home/oblision/Desktop/Bachelor-Thesis-RocksDB-IoT/nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation}"
cd "$LAGVAL" || { echo "no lag-validation dir"; exit 1; }
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600 SAMPLE=on
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; OPB=8192
strings "../../../$SYSTEST" 2>/dev/null | grep -q pool_reserve_pct || { echo "FATAL: not the lever build"; exit 1; }

run_one() {  # PREFIX CAP POOL RHO ROWS REP WBUF CACHE
  local PFX=$1 CAP=$2 POOL=$3 RHO=$4 ROWS=$5 REP=$6 WBUF=$7 CACHE=$8
  local SOFT=$((CAP/10)) HARD=$((CAP/5))
  local LBL="${PFX}_c${CAP}_p$((POOL/1000))k_${RHO}_r${REP}"
  echo "[$(date +%H:%M:%S)] $LBL  (wbuf=$WBUF cache=$CACHE soft=$SOFT hard=$HARD)"
  OPBUFSIZE=$OPB RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="/dev/shm/nes-input/rho_${RHO}.csv" DCPUS=2 \
    ./run-cell.sh "$LBL" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" "$WBUF" "$CACHE" 4 >/dev/null 2>&1
}

# point set: CAP POOL RHO ROWS  (spans low/mid/high load at 512 and a 1024 reference)
POINTS=(
  "512 8000 r075 8000000"
  "512 8000 r100 15000000"
  "512 8000 r150 31000000"
  "512 16000 r150 31000000"
  "1024 16000 r150 31000000"
  "1024 16000 r300 88000000"
)
REPS=3

echo "==== DRIFT CONTROL START $(date +%F_%T) ===="
for p in "${POINTS[@]}"; do
  set -- $p; CAP=$1 POOL=$2 RHO=$3 ROWS=$4
  for r in $(seq 1 $REPS); do
    run_one D16 "$CAP" "$POOL" "$RHO" "$ROWS" "$r" 16 8     # fresh 16/8
    run_one D32 "$CAP" "$POOL" "$RHO" "$ROWS" "$r" 32 32    # fresh 32/32 (interleaved)
  done
done
echo "==== DRIFT CONTROL COMPLETE $(date +%F_%T) ===="
echo "Results: $LAGVAL/results/D16_*.tsv $LAGVAL/results/D32_*.tsv ; traces: work/rendered/D*.trace"
