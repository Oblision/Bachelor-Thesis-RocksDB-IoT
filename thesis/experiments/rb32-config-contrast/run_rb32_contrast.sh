#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_rb32_contrast.sh — drive the 32/32 RocksDB-config mirror of the existing
# 16/8 spill campaigns (full spill-ON A/B). See DESIGN.md.
#
# Canonical scripts live HERE in the thesis wrapper repo. This runner deploys
# only UNTRACKED, removable files into the NebulaStream fork's lag-validation
# dir: it copies the run-cell-rb32.sh shim there and generates *_rb32.sh mirror
# drivers from the originals by redirecting their run-cell.sh calls to the shim
# (identical grids; only the RocksDB config + label tag differ). Nothing tracked
# in the fork is modified. `clean` removes the generated files again.
#
# Usage (from anywhere):
#   ./run_rb32_contrast.sh list    # show coverage + resolved paths
#   ./run_rb32_contrast.sh gen     # deploy shim + generate *_rb32.sh mirrors
#   ./run_rb32_contrast.sh run     # gen, then run all mirrors  (LONG: ~2.5k cells)
#   ./run_rb32_contrast.sh clean   # remove deployed shim + *_rb32.sh (keeps TSVs)
#
# Override the fork location with: LAGVAL_DIR=/path/to/lag-validation ./run_rb32_contrast.sh ...
#
# Resumable: run-cell.sh skips a cell whose (tagged) result TSV already exists,
# so re-running `run` after an interruption continues where it left off.
# ─────────────────────────────────────────────────────────────────────────────
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAGVAL="${LAGVAL_DIR:-$HERE/../../../nebulastream-Out-of-Core-Stream-Processing/.planning/eval-phase/lag-validation}"
SHIM="$HERE/run-cell-rb32.sh"

# Spill-ON headline + grid drivers to mirror (the shim auto-skips any spill-OFF
# cells, e.g. the uncapped footprint runs). Add/remove names to change coverage.
DRIVERS=(
  # ── headline RQ grids (the core A/B) ─────────────────────────────────────
  final_matrix     # W_   RQ1/RQ2 bounded headline grid (512/1024/2048)
  large_matrix     # L_   heap OOC grid (inm-threshold cells init RocksDB -> included)
  lever_matrix     # S_   stability levers (A1/A2/A3)
  lever_matrix_n1  # N_   pooling arms (B0/B1/B2)
  nx_matrix        # NX_  Nexmark
  u8_arm           # U_   matched-granularity heap control (disambiguation)
  followup_n3n4    # F_   follow-up cells
  n2_matrix        # B3_  bounded-admission exploration
  pool_sweep       # psweep_  capped pool sweep
  # ── auxiliary capped spill-on grids (for the FULL ~2,524-cell data set) ───
  gaps_run         # W_/U_   feasibility-grid gap fills
  boundary_run     # W_      boundary cells
  contrast512      # ooc512_ 512-cap contrast (inm512_ cells are spill-on too)
  skewrun          # ooc512_ key-skew sweeps
  skewrun2
  skewrun3
  skewrun4
  skewrun5
  # pool_sweep_uncapped   # psweepU_ — UNCAPPED, outside the cap-contrast scope; enable if wanted
)

die(){ echo "FATAL: $*" >&2; exit 1; }
prereq(){
  [ -x "$SHIM" ] || die "shim not found/executable: $SHIM"
  [ -d "$LAGVAL" ] || die "lag-validation dir not found: $LAGVAL (set LAGVAL_DIR)"
}

gen(){
  prereq
  cp "$SHIM" "$LAGVAL/run-cell-rb32.sh"; chmod +x "$LAGVAL/run-cell-rb32.sh"
  local n=0
  for d in "${DRIVERS[@]}"; do
    if [ ! -f "$LAGVAL/$d.sh" ]; then echo "  [skip-missing] $d.sh"; continue; fi
    sed 's#\./run-cell\.sh#./run-cell-rb32.sh#g' "$LAGVAL/$d.sh" > "$LAGVAL/${d}_rb32.sh"
    chmod +x "$LAGVAL/${d}_rb32.sh"; echo "  [gen] ${d}_rb32.sh"; n=$((n+1))
  done
  echo "Deployed shim + $n mirror driver(s) into:"
  echo "  $LAGVAL"
  echo "These are UNTRACKED working files — do NOT commit them into the fork."
}

run(){
  gen
  ( cd "$LAGVAL" || die "cannot cd $LAGVAL"
    for d in "${DRIVERS[@]}"; do
      [ -f "${d}_rb32.sh" ] || continue
      echo "==== RB32 MIRROR: ${d}_rb32.sh  $(date +%F_%T) ===="
      "./${d}_rb32.sh" || echo "  [warn] ${d}_rb32.sh exited non-zero (continuing)"
    done )
  echo "==== RB32 CONTRAST COMPLETE $(date +%F_%T) ===="
  echo "Results: $LAGVAL/results/*_b32_*.tsv"
}

clean(){
  prereq
  rm -f "$LAGVAL/run-cell-rb32.sh"
  for d in "${DRIVERS[@]}"; do rm -f "$LAGVAL/${d}_rb32.sh"; done
  echo "Removed deployed shim + *_rb32.sh from $LAGVAL (result TSVs kept)."
}

case "${1:-list}" in
  gen)   gen ;;
  run)   run ;;
  clean) clean ;;
  list)
    echo "RB32 RocksDB-config contrast (32/32 vs 16/8). Mirrors (spill-off auto-skipped):"
    printf '  %s\n' "${DRIVERS[@]}"
    echo "shim:           $SHIM"
    echo "lag-validation: $LAGVAL"
    [ -d "$LAGVAL" ] && echo "  (found)" || echo "  (NOT FOUND — set LAGVAL_DIR)"
    ;;
  *) echo "usage: $0 {list|gen|run|clean}"; exit 1 ;;
esac
