#!/usr/bin/env bash
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=900
CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192; ROWS=31000000
nx() { # Q WLD WINDOW LAG CAP ARM POOL SOFT HARD OPB RES PCD REPS
  local Q=$1 WLD=$2 WIN=$3 LAG=$4 CAP=$5 ARM=$6 POOL=$7 SOFT=$8 HARD=$9 OPB=${10} RES=${11} PCD=${12} REPS=${13}
  for r in $(seq 1 "$REPS"); do
    echo "[$(date +%H:%M:%S)] NX_${Q}_${WLD}_c${CAP}_${ARM}_r$r"
    OPBUFSIZE=$OPB RESERVEPCT=$RES PACED=$PCD TEMPLATE=nx_${Q}.test DCPUS=2 \
      ./run-cell.sh "NX_${Q}_${WLD}_c${CAP}_${ARM}_r$r" $ROWS "$WIN" 0 "$LAG" "$CAP" on on "$SOFT" "$HARD" "$POOL" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
  done
}
echo "==== NEXMARK V2 START $(date +%F_%T) ===="
for Q in q7 q3; do
  echo "== $Q uncapped refs =="
  nx $Q A 25000 2000000 none unc 32000 100000 100000 8128 "" "" 3
  nx $Q B 50000 4000000 none unc 32000 100000 100000 8128 "" "" 3
  for SPEC in "A:25000:2000000:512" "A:25000:2000000:1024" "B:50000:4000000:512" "B:50000:4000000:1024"; do
    IFS=: read WLD WIN LAG CAP <<< "$SPEC"
    SOFT=$((CAP/10)); HARD=$((CAP/5))
    if [ "$CAP" = 512 ]; then BP=16000; UP=8000; else BP=48000; UP=32000; fi
    echo "== $Q $WLD cap=$CAP =="
    nx $Q $WLD $WIN $LAG $CAP inm $UP 100000 100000 8128 "" "" 8
    nx $Q $WLD $WIN $LAG $CAP B2  $BP "$SOFT" "$HARD" 8192 15 on 8
    nx $Q $WLD $WIN $LAG $CAP U8  $UP "$SOFT" "$HARD" 8128 "" "" 8
  done
done
echo "==== NEXMARK V2 COMPLETE $(date +%F_%T) ===="
