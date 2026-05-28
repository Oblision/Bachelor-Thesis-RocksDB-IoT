#!/usr/bin/env bash
# E3 addendum — naive-OOC (P=1) sweep. Same OOC config as the P=8 runs (spill on, wbuf32/cache32, lz4), ONLY
# numberOfPartitions differs (P=1). This is the survival contrast: P=1 must re-materialize the WHOLE window in one
# burst at terminal emit and OOMs at tight caps / large bands, whereas P=8 keeps 1/P resident and survives.
# Appends to the SAME runs.tsv with variant=ooc_p1.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-eval.sh"
TEST_REL=".planning/eval-phase/phase0c/SpillSmokeVoid.test"
GEN=2000001
POOL=16000; SOFT=4; HARD=8; THREADS=4
EMIT_LAGS="400000 800000 1200000 1600000 2000000"
CAPS="320 512"
REPS=3
echo "=== E3 P=1 sweep start $(date) ==="
for EL in $EMIT_LAGS; do
  SK=$(( EL/1000 ))k
  for CAP in $CAPS; do
    for r in $(seq 1 $REPS); do
      bash "$RUN" "oocp1_${SK}_c${CAP}_r${r}" "$TEST_REL" "$GEN" "$CAP" on "$POOL" "$SOFT" "$HARD" "$EL" "$THREADS" 32 32 1 ooc_p1
    done
  done
done
echo "=== E3 P=1 sweep done $(date) ==="
