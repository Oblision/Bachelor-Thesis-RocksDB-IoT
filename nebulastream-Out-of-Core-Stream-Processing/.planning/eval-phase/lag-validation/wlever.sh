#!/usr/bin/env bash
# Validate the window-size lever: same lateness D=6M (=> same in-mem band),
# bigger window W=120000 => N_w=50 => smaller unspillable pooled floor => OOC survives comfortably
# while lag-honored in-mem OOMs. cap=512, SD card, sequential.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
T=8000000; W=120000; D=6000000; RATE=200000; CAP=512; P=15000
CPUSET=0,1,8,9
./run-cell.sh ooc512_W120k_D6M  $T $W $RATE $D $CAP on off 60     120    $P $CPUSET 4 "" "" 16 8 4
./run-cell.sh inm512_W120k_D6M  $T $W $RATE $D $CAP on off 100000 100000 $P $CPUSET 4 "" "" 16 8 4
echo "==== wlever summary ===="
for A in ooc512 inm512; do f="results/${A}_W120k_D6M.tsv"; [ -s "$f" ] && awk -F'\t' '{printf "%-20s rc=%-4s %-4s peak=%sMB band_b=%s slices=%s\n",$1,$9,$10,$12,$14,$15}' "$f"; done
