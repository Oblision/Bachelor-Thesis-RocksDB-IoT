#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run-cell-rb32.sh — RocksDB-config CONTRAST shim (32/32 vs the 16/8 baseline).
#
# Re-runs the existing spill grids with RocksDB write_buffer=32 MB and
# block_cache=32 MB (baseline is 16/8) to test whether the larger,
# compaction-friendly config improves the spill drain at the EXPANDED memory
# caps (1024/2048 MB), where the +40 MB tax is affordable. See DESIGN.md.
#
# It does NOT modify run-cell.sh or any driver. For each cell it:
#   1. forces positional args 16/17/18 (WBUF/CACHE/NPART) to 32 / 32 / 4,
#   2. tags the label with "b32" so results never collide with the 16/8 run and
#      pair 1:1 in analysis (strip "_b32" to recover the baseline cell id),
#   3. skips spill-OFF cells (pure in-memory baseline; no RocksDB → identical),
#   4. execs the unmodified run-cell.sh.
#
# Deployed (copied) into the lag-validation dir by run_rb32_contrast.sh as an
# UNTRACKED working file. DO NOT commit it into the NebulaStream fork.
# ─────────────────────────────────────────────────────────────────────────────
set -u
args=("$@")
LABEL="${args[0]:?run-cell-rb32: missing label (arg 1)}"
SPILL="${args[6]:-}"   # positional $7 = spill (on|off)

# (3) spill-OFF cells never initialise RocksDB → 32/32 is byte-identical. Skip.
if [ "$SPILL" = off ]; then
  echo "[rb32-skip spill-off / no-RocksDB] $LABEL"
  exit 0
fi

# (2) tag: insert "b32" right after the campaign token, keeping the
#     _c<cap>_p<pool>_<rho>_r<rep> structure intact so data.py parses cap/pool/
#     rho/rep unchanged and W_… pairs with W_b32_… .
case "$LABEL" in
  *_*) args[0]="${LABEL%%_*}_b32_${LABEL#*_}" ;;
  *)   args[0]="b32_${LABEL}" ;;
esac

# (1) force the RocksDB config at positional 16/17/18 (0-based indices 15/16/17),
#     overriding whatever the driver passed (the baseline passes 16 8 4).
while [ "${#args[@]}" -lt 18 ]; do args+=(""); done
args[15]=32   # write_buffer_size_mb  (baseline 16)
args[16]=32   # block_cache_size_mb   (baseline 8)
args[17]=4    # number_of_partitions  (unchanged)

echo "[rb32] ${LABEL} -> ${args[0]}  (wbuf=32 cache=32 npart=4)"
exec ./run-cell.sh "${args[@]}"
