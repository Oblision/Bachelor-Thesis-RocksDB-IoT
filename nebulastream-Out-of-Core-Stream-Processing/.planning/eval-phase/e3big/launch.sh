#!/usr/bin/env bash
# Parallel + RESUMABLE launcher for the scaled sweep.
#   ./launch.sh [parallelism]      (default 4)
# Reads jobs.txt (run gen-matrix.sh first) and runs each cell via run-cell.sh, P at a time, with xargs.
# RESUMABLE: run-cell.sh skips any cell whose results/<label>.tsv already exists, so re-running ./launch.sh
# after a crash/reboot/Ctrl-C continues where it stopped. Each run uses --cpus=<threads>(=4); pick parallelism
# so parallelism*4 <= cores and parallelism*cap_mb fits RAM.
set -u
N="${1:-4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-cell.sh"
JOBS="$SCRIPT_DIR/jobs.txt"
[ -s "$JOBS" ] || { echo "no jobs.txt — run: bash gen-matrix.sh"; exit 1; }

TOTAL=$(wc -l < "$JOBS")
DONE=$(ls "$SCRIPT_DIR/results"/*.tsv 2>/dev/null | wc -l | tr -d ' ')
echo "[launch] $TOTAL jobs total, ~$DONE already done, parallelism=$N, started $(date)"
echo "[launch] each cell ~2 min; tail logs/ or results/ for progress; safe to Ctrl-C and re-run."

# -L 1: one job's 15 fields per exec. Feed via stdin (portable: BSD/macOS xargs has no -a). xargs preserves
# the (space-containing) $RUN path as one argv element.
xargs -P "$N" -L 1 bash "$RUN" < "$JOBS"

DONE2=$(ls "$SCRIPT_DIR/results"/*.tsv 2>/dev/null | wc -l | tr -d ' ')
echo "[launch] finished $(date) — $DONE2/$TOTAL cells have results. Next: bash collect.sh"
