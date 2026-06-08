#!/usr/bin/env bash
# CORRECTED proposal-aligned RQ1 headline: Zipfian skew + SMALL disorder (D=200k, ~4 hot windows)
# + LARGE emit_lag (L=4M, 80 retained) => ~76 cold-but-retained windows = SPILLABLE backlog.
# Expect: OOC spills the cold skewed backlog and SURVIVES; lag-honored in-mem retains all => OOMs.
# cap=512, known-good ladder params (W=50k, pool=26k), SD card, sequential.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export TEMPLATE=genfile.test
export FILECSV="$(pwd)/work/data/skew_Nw80_smallD.csv"
T=19800000; W=50000; L=4000000; CAP=512; P=26000; CPUSET=0,1,8,9
./run-cell.sh ooc512_skewD_Nw80 $T $W 200000 $L $CAP on off 45 90 $P $CPUSET 4 "" "" 16 8 4
./run-cell.sh inm512_skewD_Nw80 $T $W 200000 $L $CAP on off 100000 100000 $P $CPUSET 4 "" "" 16 8 4
echo "==== skewrun2 summary ===="
for A in ooc512_skewD_Nw80 inm512_skewD_Nw80; do
  f="results/${A}.tsv"; [ -s "$f" ] && awk -F'\t' '{printf "%-22s rc=%-4s %-4s peak=%sMB spill_dir=%sMB store_peak_b=%s slices=%s\n",$1,$9,$10,$12,$13,$14,$15}' "$f"
  l="logs/${A}.log"; [ -f "$l" ] && grep -h 'CELL_SPILLM=\|CELL_RDBM=' "$l" | sed 's/^CELL_/  /'
done
