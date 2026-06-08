#!/usr/bin/env bash
# Proposal-aligned RQ1 + BURSTY: Zipfian skew + small disorder (D=200k) + LARGE emit_lag (L=4M)
# + periodic WIDE bursts (every 24 windows, 6 contiguous windows at 4x density => ~2.7x cardinality
# spikes that survive the jitter). Tests OOC robustness to load spikes on top of the skew workload.
# Expect: OOC spills the cold (incl. burst) backlog and SURVIVES; lag-honored in-mem OOMs.
# cap=512, pool=26k, aggressive spill (soft35/hard70) for burst headroom. SD card, sequential.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export TEMPLATE=genfile.test
export FILECSV="$(pwd)/work/data/skew_burst2_Nw80.csv"
T=18000000; W=50000; L=4000000; CAP=512; P=26000; CPUSET=0,1,8,9
./run-cell.sh ooc512_burst2_Nw80 $T $W 200000 $L $CAP on off 40 80 $P $CPUSET 4 "" "" 16 8 4
./run-cell.sh inm512_burst2_Nw80 $T $W 200000 $L $CAP on off 100000 100000 $P $CPUSET 4 "" "" 16 8 4
echo "==== skewrun3 (bursty) summary ===="
for A in ooc512_burst2_Nw80 inm512_burst2_Nw80; do
  f="results/${A}.tsv"; [ -s "$f" ] && awk -F'\t' '{printf "%-22s rc=%-4s %-4s peak=%sMB spill_dir=%sMB store_peak_b=%s slices=%s\n",$1,$9,$10,$12,$13,$14,$15}' "$f"
  l="logs/${A}.log"; [ -f "$l" ] && grep -h 'CELL_SPILLM=\|CELL_RDBM=' "$l" | sed 's/^CELL_/  /'
done
