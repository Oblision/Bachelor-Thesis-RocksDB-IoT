#!/usr/bin/env bash
# FREE CONFIG LEVERS (no engine change): measure how pool right-sizing + larger windows cut the
# footprint and affect 512 feasibility. Same data (el CSV), same event-time retention (emit_lag=4M).
#   A base    : W=50k,  pool=26000 (203MB, over-provisioned)        -> the starting point
#   B pool     : W=50k,  pool=16000 (125MB, sweet-spot from sweep)  -> pool lever
#   C pool+win : W=200k, pool=16000 (fewer/bigger windows -> fewer maps -> less bucket-dir/slack)
# Arms: uncapped (true footprint, no OOM masking) for in-mem & OOC; capped@512 OOC x3 (robustness).
# Full mem_trace + gauges auto-on. --cpus=2.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest TEMPLATE=genfile.test EMITBPS=0
export FILECSV=/dev/shm/nes-input/skew_Nw80_smallD.csv
export CELL_TIMEOUT=240
T=19800000; L=4000000; CPUSET=0,1,8,9; WTH=2
run(){ DCPUS=2 ./run-cell.sh "$@" "$CPUSET" $WTH "" "" 16 8 4; }

# cfg: label W pool
for cfg in "A_base 50000 26000" "B_pool 50000 16000" "C_poolwin 200000 16000"; do
  set -- $cfg; NAME=$1; W=$2; P=$3
  echo "==== $NAME  (W=$W pool=$P) ===="
  run lev_${NAME}_uncap_inmem $T $W 0 $L none on on 100000 100000 $P   # true in-mem footprint
  run lev_${NAME}_uncap_ooc   $T $W 0 $L none on on 50     100    $P   # true OOC footprint
  for r in 1 2 3; do run lev_${NAME}_cap_ooc_r$r $T $W 0 $L 512 on on 50 100 $P; done  # 512 robustness
  run lev_${NAME}_cap_inmem   $T $W 0 $L 512 on on 100000 100000 $P   # 512 in-mem (expect OOM)
done

echo "==== LEVER SUMMARY (anon peak, pool_used, tps, status) ===="
for NAME in A_base B_pool C_poolwin; do
  for f in results/lev_${NAME}_*.tsv; do
    [ -s "$f" ] && awk -F'\t' '{printf "%-26s rc=%-4s %-4s | anon=%sMB pool_used_peak=%sMB | tps=%s p99=%sus | flush=%sMB@%s%%\n",$1,$9,$10,$23,$32,$22,$21,$25,$26}' "$f"
  done
done
