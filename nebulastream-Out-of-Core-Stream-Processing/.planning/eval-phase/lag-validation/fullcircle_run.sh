#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600
W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192
D=/dev/shm/nes-input
ga() { # cap pool rho rows soft hard maxb gate reps
  local CAP=$1 POOL=$2 RHO=$3 ROWS=$4 SOFT=$5 HARD=$6 MAXB=$7 GATE=$8 REPS=$9
  local TAG="GA"; [ -z "$GATE" ] && TAG="GC"
  [ -n "$MAXB" ] && TAG="${TAG}mb"
  for r in $(seq 1 "$REPS"); do
    echo "[$(date +%H:%M:%S)] ${TAG}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r"
    SRCGATE=$GATE OPBUFSIZE=8192 RESERVEPCT=15 PACED=on TEMPLATE=genfile.test FILECSV="$D/rho_$RHO.csv" DCPUS=2 \
      ./run-cell.sh "${TAG}_c${CAP}_p$((POOL/1000))k_${RHO}_r$r" "$ROWS" $W 0 $L "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "$MAXB" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== FULL-CIRCLE START $(date +%F_%T) ===="
echo "== GF-H4 controls (new binary, gate OFF) =="
ga  512 16000 r100  15000000  51 102 "" "" 4
ga 1024 48000 r200  50000000 102 204 "" "" 4
echo "== GF-H1/H2 valleys (gate 30) =="
ga  512 16000 r100  15000000  51 102 "" 30 8
ga  512 16000 r150  31000000  51 102 "" 30 8
ga 1024 48000 r200  50000000 102 204 "" 30 8
ga 2048 96000 r300  88000000 204 409 "" 30 8
echo "== GF-H3 regression winners (gate 30) =="
ga  512 16000 r075   8000000  51 102 "" 30 8
ga  512 16000 r200  50000000  51 102 "" 30 8
ga 1024 32000 r150  31000000 102 204 "" 30 8
ga 2048 96000 r500 140000000 204 409 "" 30 8
ga 2048 96000 r700 195000000 204 409 "" 30 8
echo "== GF-H5 everything-on =="
ga  512 24000 r150  31000000  51 102 4096 30 8
echo "==== FULL-CIRCLE COMPLETE $(date +%F_%T) ===="
