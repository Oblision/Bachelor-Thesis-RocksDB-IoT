#!/usr/bin/env bash
# E3 matrix driver — Axis A core sweep answering §3.3 (OOM survival), RQ2 (overhead, tail latency), RQ3 (scaling,
# write-amp, footprint). Reuses the FIXED phase0c Void workload (2,000,001 tuples, 100k-key tumbling windows); the
# state ladder is the retained completed-window band, controlled by emit_lag (ts step=1 => emit_lag ms ~= retained keys).
#
# Variants per (state, cap):
#   ooc   = spill ON, P=8, wbuf32/cache32 lz4, memory-capped     (the contribution)
#   inmem = spill OFF, memory-capped                              (the §3.3.1 baseline that OOMs)
# Plus an UNCAPPED inmem reference per state (cap=none) for the §3.3.2 throughput-overhead comparison where it fits.
#
# Run in background:  nohup bash .planning/eval-phase/e3/run-matrix.sh > .planning/eval-phase/e3/matrix.out 2>&1 &
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-eval.sh"
TEST="phase0c/SpillSmokeVoid.test"      # systest -t path is relative to /workspace; but run-eval passes it through
TEST_REL=".planning/eval-phase/phase0c/SpillSmokeVoid.test"
GEN=2000001

POOL=16000; SOFT=4; HARD=8; THREADS=4
EMIT_LAGS="400000 800000 1200000 1600000 2000000"
CAPS="320 512"
REPS=3

echo "=== E3 matrix start $(date) ==="
for EL in $EMIT_LAGS; do
  SK=$(( EL/1000 ))k   # short state tag
  for CAP in $CAPS; do
    for r in $(seq 1 $REPS); do
      # OOC: spill on, P=8, research config, capped
      bash "$RUN" "ooc_${SK}_c${CAP}_r${r}"   "$TEST_REL" "$GEN" "$CAP"  on  "$POOL" "$SOFT" "$HARD" "$EL" "$THREADS" 32 32 8 ooc
      # in-mem baseline: spill off, capped (expected OOM at large state)
      bash "$RUN" "inmem_${SK}_c${CAP}_r${r}" "$TEST_REL" "$GEN" "$CAP"  off "$POOL" "$SOFT" "$HARD" "$EL" "$THREADS" 0  0  1 inmem
    done
  done
  # uncapped in-mem throughput reference (one rep set, no cap) — the §3.3.2 overhead denominator
  for r in $(seq 1 $REPS); do
    bash "$RUN" "ref_${SK}_uncapped_r${r}" "$TEST_REL" "$GEN" none off "$POOL" "$SOFT" "$HARD" "$EL" "$THREADS" 0 0 1 inmem_uncapped
  done
done
echo "=== E3 matrix done $(date) ==="
echo "results: $SCRIPT_DIR/runs.tsv"
