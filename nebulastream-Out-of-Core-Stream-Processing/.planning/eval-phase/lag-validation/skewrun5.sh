#!/usr/bin/env bash
# Bursty positive-result attempt: N_w=60 (emit_lag=3M, pool=20k => smaller floor => OOC headroom)
# + dense windows (~89k normal / 176k burst distinct, band ~200MB so in-mem still OOMs)
# + small D=200k + WIDE bursts (period24/width6/amp3). Aim: OOC SURVIVES bursts, in-mem OOMs.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export TEMPLATE=genfile.test
export FILECSV="$(pwd)/work/data/skew_burst3_Nw60.csv"
T=25000000; W=50000; L=3000000; CAP=512; P=20000; CPUSET=0,1,8,9
./run-cell.sh ooc512_burst3_Nw60 $T $W 200000 $L $CAP on off 40 80 $P $CPUSET 4 "" "" 16 8 4
./run-cell.sh inm512_burst3_Nw60 $T $W 200000 $L $CAP on off 100000 100000 $P $CPUSET 4 "" "" 16 8 4
echo "==== skewrun5 (bursty N_w=60 dense) summary ===="
for A in ooc512_burst3_Nw60 inm512_burst3_Nw60; do
  f="results/${A}.tsv"; [ -s "$f" ] && awk -F'\t' '{printf "%-22s rc=%-4s %-4s peak=%sMB spill_dir=%sMB store_peak_b=%s slices=%s\n",$1,$9,$10,$12,$13,$14,$15}' "$f"
  l="logs/${A}.log"; [ -f "$l" ] && grep -h 'CELL_SPILLM=\|CELL_RDBM=' "$l" | sed 's/^CELL_/  /'
done
