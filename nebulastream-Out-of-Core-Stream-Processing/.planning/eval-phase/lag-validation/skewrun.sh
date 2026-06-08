#!/usr/bin/env bash
# RQ1 headline on the proposal-aligned SKEW workload: in-mem OOMs, OOC survives, at cap=512.
# Zipfian (s=1.0, ~48.6k distinct/window, top-10=20% of rows) + out-of-order (D=4M, N_w=80) CSV.
# Same known-good params as the working lockstep ladder (W=50k, pool=26k); OOC gains margin via a
# lower soft spill threshold (45/90) rather than starving the pool (which made the W-lever OOM).
# Sequential on the single SD card.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export TEMPLATE=genfile.test
export FILECSV="$(pwd)/work/data/skew_Nw80.csv"
T=19800000; W=50000; D=4000000; CAP=512; P=26000; CPUSET=0,1,8,9
# OOC arm: spiller active, aggressive (soft45/hard90)
./run-cell.sh ooc512_skew_Nw80 $T $W 200000 $D $CAP on off 45 90 $P $CPUSET 4 "" "" 16 8 4
# lag-honored in-mem arm: spiller never fires (soft/hard huge); band still retained -> OOM
./run-cell.sh inm512_skew_Nw80 $T $W 200000 $D $CAP on off 100000 100000 $P $CPUSET 4 "" "" 16 8 4
echo "==== skewrun summary ===="
for A in ooc512_skew_Nw80 inm512_skew_Nw80; do
  f="results/${A}.tsv"; [ -s "$f" ] && awk -F'\t' '{printf "%-22s rc=%-4s %-4s peak=%sMB spill_dir=%sMB band_b=%s slices=%s\n",$1,$9,$10,$12,$13,$14,$15}' "$f"
  l="logs/${A}.log"; [ -f "$l" ] && grep -h 'CELL_SPILLM=\|CELL_RDBM=' "$l" | sed 's/^CELL_/  /'
done
