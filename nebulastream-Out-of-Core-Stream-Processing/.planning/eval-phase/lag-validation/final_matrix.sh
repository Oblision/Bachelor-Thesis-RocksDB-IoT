#!/usr/bin/env bash
# FLAGSHIP COMPARISON: well-configured OOC vs in-memory @ 512MB, across rho (state/cap), with reps for
# significance + checksum correctness. Config B (from lever findings): pool=16000, W=50k, --cpus=2.
# Both arms identical except spill. rho measured from the uncapped footprint.
# Resumable (run-cell skips done cells); self-healing (pass 2 re-runs OOC spill-path crashes rc134/11/139).
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=300
W=50000; L=4000000; POOL=16000; CPUSET=0,1,8,9; WTH=2
PREFIX="${PREFIX:-}"; PAGE="${PAGESIZE:-}"   # PREFIX labels the run (e.g. b_ for bounded); PAGE sets page_size
# rho point: label:csv:rows
PTS="r075:/dev/shm/nes-input/rho_r075.csv:8000000 \
r100:/dev/shm/nes-input/rho_r100.csv:15000000 \
r150:/dev/shm/nes-input/rho_r150.csv:31000000 \
r200:/dev/shm/nes-input/rho_r200.csv:50000000 \
r300:/dev/shm/nes-input/rho_r300.csv:88000000"

cell(){ # template label csv rows cap soft hard
  TEMPLATE=$1 FILECSV=$3 DCPUS=2 ./run-cell.sh "$2" "$4" $W 0 $L "$5" on on "$6" "$7" $POOL $CPUSET $WTH "" "$PAGE" 16 8 4 >/dev/null 2>&1
}
run_point(){ LBL=$1; CSV=$2; ROWS=$3
  [ -s "$CSV" ] || { echo "skip $LBL (missing $CSV)"; return; }
  echo "  [$LBL] ($CSV)"
  for r in 1 2 3 4 5 6 7 8; do cell genfile.test ${PREFIX}ooc_${LBL}_r$r $CSV $ROWS 512  50     100    ; done   # OOC @512
  for r in 1 2 3 4 5 6;       do cell genfile.test ${PREFIX}inm_${LBL}_r$r $CSV $ROWS 512  100000 100000 ; done   # in-mem @512
  for r in 1 2 3 4 5 6 7 8; do cell genfile.test ${PREFIX}unc_${LBL}_r$r $CSV $ROWS none 100000 100000 ; done   # uncapped baseline
  cell genfile_checksum.test ${PREFIX}ooccs_${LBL} $CSV $ROWS 512  50     100      # OOC correctness
  cell genfile_checksum.test ${PREFIX}unccs_${LBL} $CSV $ROWS none 100000 100000   # uncapped correctness oracle
}

for pass in 1 2; do
  echo "==== PASS $pass ===="
  if [ "$pass" = 2 ]; then  # self-heal: remove crashed OOC results so they re-run
    for f in results/${PREFIX}ooc_*.tsv results/${PREFIX}ooccs_*.tsv; do [ -s "$f" ] || continue
      case "$(cut -f9 "$f" 2>/dev/null)" in 134|11|139) echo "  retry $(basename "$f")"; rm -f "$f";; esac; done
  fi
  for pt in $PTS; do LBL=${pt%%:*}; rest=${pt#*:}; CSV=${rest%:*}; ROWS=${rest##*:}; run_point "$LBL" "$CSV" "$ROWS"; done
done
echo "==== FINAL MATRIX COMPLETE ===="
