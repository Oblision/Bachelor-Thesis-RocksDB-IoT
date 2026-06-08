#!/usr/bin/env bash
echo "GROUP1_PID=$$"
SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for L in 1000000 2000000 3000000 4000000; do
  T=$(( L * 3 / 2 ))
  echo "=== Group1 L=$L TUPLES=$T $(date) ==="
  bash "$SD/run-cell.sh" "g1_L$((L/1000000))M_w50k_r200k_c1024" "$T" 50000 200000 "$L" 1024 on on
done
echo "=== Group1 done $(date) ==="
