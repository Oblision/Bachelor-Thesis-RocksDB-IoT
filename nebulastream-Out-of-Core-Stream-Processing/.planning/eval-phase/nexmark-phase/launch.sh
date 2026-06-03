#!/usr/bin/env bash
# Nexmark-phase parallel + RESUMABLE launcher with slot-aware CPU pinning.
#   ./launch.sh [parallelism]      (default 4 — matches the host's 8 physical cores ÷ 2 physical cores per cell)
#
# Differs from ../e3big/launch.sh: uses a FIFO slot semaphore so each concurrent cell receives a stable
# slot ID (0..N-1) appended as the 17th arg to run-cell.sh. run-cell.sh maps that to --cpuset-cpus,
# pinning the cell to a dedicated pair of physical cores. Result: -P 4 with NO inter-cell CPU sharing
# on the i7-10700K (8 physical × 2 SMT = 16 logical CPUs; 4 cells × 2 physical cores each = exact fit).
#
# RESUMABLE: run-cell.sh skips any cell whose results/<label>.tsv already exists, so re-running
# this same launch command after a crash/Ctrl-C continues where it stopped.
set -u
N="${1:-4}"   # -P 4 default = no oversubscription, no inter-cell physical-core sharing on 8-physical-core host
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-cell.sh"
JOBS="$SCRIPT_DIR/jobs.txt"
[ -s "$JOBS" ] || { echo "no jobs.txt — run: bash gen-matrix.sh"; exit 1; }

if [ "$N" -gt 4 ]; then
  echo "[warn] -P $N requested but host has 8 physical cores ÷ 2 phys/cell = 4 non-overlapping slots." >&2
  echo "[warn] Slots beyond 0..3 would share physical cores. Capping to 4 to preserve measurement purity." >&2
  N=4
fi

TOTAL=$(wc -l < "$JOBS")
DONE=$(ls "$SCRIPT_DIR/results"/*.tsv 2>/dev/null | wc -l | tr -d ' ')
echo "[launch] $TOTAL jobs total, ~$DONE already done, parallelism=$N (pinned), started $(date)"
echo "[launch] each cell ~150s avg; safe to Ctrl-C and re-run."

# ---- FIFO slot semaphore: pre-fill with N slot IDs; each cell reads one before running, returns it on exit ----
FIFO=$(mktemp -u "/tmp/nexmark-launch-fifo.XXXXXX")
mkfifo "$FIFO"
exec 3<>"$FIFO"
rm -f "$FIFO"
for i in $(seq 0 $((N - 1))); do echo "$i" >&3; done

while IFS= read -r line; do
  [ -z "$line" ] && continue
  read -u 3 SLOT  # block until a slot frees up
  (
    bash "$RUN" $line "$SLOT"
    echo "$SLOT" >&3  # release slot back to the pool
  ) &
done < "$JOBS"

wait
exec 3<&-

DONE2=$(ls "$SCRIPT_DIR/results"/*.tsv 2>/dev/null | wc -l | tr -d ' ')
echo "[launch] finished $(date) — $DONE2/$TOTAL cells have results. Next: bash collect.sh"
