#!/usr/bin/env bash
# LARGE 2D MATRIX: cap × pool × ρ × arm
# Bounded binary, pageSize=8192, proportional soft/hard per cap, 10 reps per arm.
# Skips cells when CSV is missing; idempotent (run-cell.sh skips done cells).
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
export SYSTEST=cmake-build-release/nes-systests/systest/systest EMITBPS=0 CELL_TIMEOUT=600

W=50000; L=4000000; CPUSET=0,1,8,9; WTH=2; PAGESIZE=8192

cell() { # template label csv rows cap soft hard pool
  TEMPLATE=$1 FILECSV=$3 DCPUS=2 ./run-cell.sh "$2" "$4" $W 0 $L "$5" on on "$6" "$7" "$8" $CPUSET $WTH "" "$PAGESIZE" 16 8 4 >/dev/null 2>&1
}

run_cap() {
  local cap=$1; shift
  local soft=$1; shift
  local hard=$1; shift
  local pool_prop=$1; shift
  local rho_list="$@"
  for rho_entry in $rho_list; do
    local LBL=${rho_entry%%:*}
    local rest=${rho_entry#*:}
    local CSV=${rest%:*}
    local ROWS=${rest##*:}
    if [ ! -s "$CSV" ]; then
      echo "  [$(date +%H:%M:%S)] skip $LBL @ cap=$cap (missing $CSV)"
      continue
    fi
    echo "  [$(date +%H:%M:%S)] $LBL @ cap=$cap"
    # OOC at 3 pool sizes × 10 reps
    for POOL in 16000 32000 64000; do
      for r in 1 2 3 4 5 6 7 8 9 10; do
        cell genfile.test "L_c${cap}_p${POOL}_ooc_${LBL}_r$r" $CSV $ROWS $cap $soft $hard $POOL
      done
    done
    # In-mem at proportional pool × 10 reps
    for r in 1 2 3 4 5 6 7 8 9 10; do
      cell genfile.test "L_c${cap}_inm_${LBL}_r$r" $CSV $ROWS $cap 100000 100000 $pool_prop
    done
  done
}

echo "==== LARGE MATRIX START $(date +%Y-%m-%d_%H:%M:%S) ===="

echo "==== CAP=512 (soft=50 hard=100 pool_prop=16000) ===="
run_cap 512 50 100 16000 \
  "r075:/dev/shm/nes-input/rho_r075.csv:8000000" \
  "r100:/dev/shm/nes-input/rho_r100.csv:15000000" \
  "r150:/dev/shm/nes-input/rho_r150.csv:31000000" \
  "r200:/dev/shm/nes-input/rho_r200.csv:50000000" \
  "r300:/dev/shm/nes-input/rho_r300.csv:88000000"

echo "==== CAP=1024 (soft=100 hard=200 pool_prop=32000) ===="
run_cap 1024 100 200 32000 \
  "r100:/dev/shm/nes-input/rho_r100.csv:15000000" \
  "r150:/dev/shm/nes-input/rho_r150.csv:31000000" \
  "r200:/dev/shm/nes-input/rho_r200.csv:50000000" \
  "r300:/dev/shm/nes-input/rho_r300.csv:88000000" \
  "r500:/dev/shm/nes-input/rho_r500.csv:140000000"

echo "==== CAP=2048 (soft=200 hard=400 pool_prop=64000) ===="
run_cap 2048 200 400 64000 \
  "r200:/dev/shm/nes-input/rho_r200.csv:50000000" \
  "r300:/dev/shm/nes-input/rho_r300.csv:88000000" \
  "r500:/dev/shm/nes-input/rho_r500.csv:140000000" \
  "r700:/dev/shm/nes-input/rho_r700.csv:195000000" \
  "r1000:/dev/shm/nes-input/rho_r1000.csv:280000000"

# Uncapped baselines for new ρ-files (existing have prior unpooled-unc data)
echo "==== UNCAPPED BASELINES (new ρ only, pool=64000 for bounded HashMap) ===="
for rho_entry in \
  "r500:/dev/shm/nes-input/rho_r500.csv:140000000" \
  "r700:/dev/shm/nes-input/rho_r700.csv:195000000" \
  "r1000:/dev/shm/nes-input/rho_r1000.csv:280000000"; do
  LBL=${rho_entry%%:*}; rest=${rho_entry#*:}; CSV=${rest%:*}; ROWS=${rest##*:}
  if [ ! -s "$CSV" ]; then
    echo "  [$(date +%H:%M:%S)] skip unc $LBL (missing $CSV)"
    continue
  fi
  echo "  [$(date +%H:%M:%S)] unc $LBL"
  for r in 1 2 3 4 5 6 7 8 9 10; do
    cell genfile.test "L_unc_${LBL}_r$r" $CSV $ROWS none 100000 100000 64000
  done
done

echo "==== LARGE MATRIX COMPLETE $(date +%Y-%m-%d_%H:%M:%S) ===="
