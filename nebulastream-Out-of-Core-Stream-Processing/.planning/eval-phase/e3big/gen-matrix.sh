#!/usr/bin/env bash
# Emit jobs.txt — one `run-cell.sh ...` invocation per cell for the full 197-config matrix x REPS.
# Implements EXPERIMENT-PLAN-FULL.md: Sweep A (survival/P), B (compression/H3), C (wbuf/H1), D (cache),
# F (watermark/H4), + in-mem references. Edit REPS / the axis arrays to retune. Labels are unique; the axis
# values live in the TSV columns (run-cell.sh writes them), so analysis reads columns, not labels.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-cell.sh"
JOBS="$SCRIPT_DIR/jobs.txt"; : > "$JOBS"

UNIFORM=".planning/eval-phase/phase0c/SpillSmokeVoid.test"     # 2M keys
UNIFORM5M=".planning/eval-phase/e3big/work/uniform5m.test"     # 5M keys
SKEWED=".planning/eval-phase/e3big/work/skewed.test"           # binomial keys, 2M tuples

# ---- baseline (research-informed) ----
POOL=16000; THREADS=4; SOFT0=4; HARD0=8; WBUF0=64; CACHE0=32; COMP0=lz4; P0=8; CAP0=512
STATES="400000 800000 1200000 1600000 2000000"
REPS="${REPS:-50}"     # override: REPS=20 bash gen-matrix.sh

# resolve (workload,state) -> "TEST GEN EMITLAG"
resolve(){ local wl="$1" st="$2"
  if [ "$wl" = skewed ]; then echo "$SKEWED 2000001 $st";
  elif [ "$st" = 5000000 ]; then echo "$UNIFORM5M 5000001 5000000";
  else echo "$UNIFORM 2000001 $st"; fi; }

# jobs.txt holds plain run-cell.sh arg-tuples (15 space-free fields), consumed by `xargs -L1 bash run-cell.sh`.
# run-cell.sh arg order: label test gen cap spill pool soft hard emit_lag threads wbuf cache npart variant comp
job(){ # callsite order: label test gen emitlag cap spill pool soft hard threads wbuf cache npart variant comp
  printf '%s %s %s %s %s %s %s %s %s %s %s %s %s %s %s\n' \
    "$1" "$2" "$3" "$5" "$6" "$7" "$8" "$9" "$4" "${10}" "${11}" "${12}" "${13}" "${14}" "${15}" >> "$JOBS"; }

for r in $(seq 1 "$REPS"); do
  # ---- Sweep A: survival & residency (P x state x cap x workload) ----
  for P in 1 2 4 8 16; do for ST in $STATES 5000000; do for CAP in 320 512; do for WL in uniform skewed; do
    [ "$WL" = skewed ] && [ "$ST" = 5000000 ] && continue   # skewed has no 5M variant
    read -r T G EL <<<"$(resolve "$WL" "$ST")"
    V=ooc; [ "$WL" = skewed ] && V=ooc_skew
    job "A_p${P}_s$((ST/1000))k_c${CAP}_${WL}_r${r}" "$T" "$G" "$EL" "$CAP" on $POOL $SOFT0 $HARD0 $THREADS $WBUF0 $CACHE0 $P "$V" $COMP0
  done; done; done; done
  # ---- Sweep B: compression (H3) ----
  for C in none lz4 zstd; do for ST in $STATES; do
    read -r T G EL <<<"$(resolve uniform "$ST")"
    job "B_comp${C}_s$((ST/1000))k_r${r}" "$T" "$G" "$EL" $CAP0 on $POOL $SOFT0 $HARD0 $THREADS $WBUF0 $CACHE0 $P0 ooc "$C"
  done; done
  # ---- Sweep C: write buffer (H1) ----
  for WB in 16 32 64 128; do for ST in $STATES; do
    read -r T G EL <<<"$(resolve uniform "$ST")"
    job "C_wbuf${WB}_s$((ST/1000))k_r${r}" "$T" "$G" "$EL" $CAP0 on $POOL $SOFT0 $HARD0 $THREADS "$WB" $CACHE0 $P0 ooc $COMP0
  done; done
  # ---- Sweep D: block cache ----
  for CA in 0 32 64; do for ST in $STATES; do
    read -r T G EL <<<"$(resolve uniform "$ST")"
    job "D_cache${CA}_s$((ST/1000))k_r${r}" "$T" "$G" "$EL" $CAP0 on $POOL $SOFT0 $HARD0 $THREADS $WBUF0 "$CA" $P0 ooc $COMP0
  done; done
  # ---- Sweep F: watermark validation (H4). ceiling=2*wbuf+cache+100=260MB; 70/90% = 182/234 ----
  for SH in "32 48" "182 234" "96 128"; do set -- $SH; SOFT=$1; HARD=$2
    for ST in 1600000 2000000 5000000; do
      read -r T G EL <<<"$(resolve uniform "$ST")"
      job "F_wm${SOFT}-${HARD}_s$((ST/1000))k_r${r}" "$T" "$G" "$EL" $CAP0 on $POOL "$SOFT" "$HARD" $THREADS $WBUF0 $CACHE0 $P0 ooc $COMP0
    done
  done
  # ---- References: in-mem capped + uncapped ----
  for ST in $STATES 5000000; do for CAP in 320 512; do
    read -r T G EL <<<"$(resolve uniform "$ST")"
    job "REFcap_s$((ST/1000))k_c${CAP}_r${r}" "$T" "$G" "$EL" "$CAP" off $POOL $SOFT0 $HARD0 $THREADS 0 0 1 inmem none
  done; done
  for ST in $STATES; do
    read -r T G EL <<<"$(resolve uniform "$ST")"
    job "REFunc_s$((ST/1000))k_r${r}" "$T" "$G" "$EL" none off $POOL $SOFT0 $HARD0 $THREADS 0 0 1 inmem_uncapped none
  done
done

N=$(wc -l < "$JOBS" | tr -d ' ')
echo "wrote $JOBS  ($N jobs = 197 configs x REPS=$REPS, minus skewed-5M)"
echo "est. serial: $(awk "BEGIN{printf \"%.0f\", $N*2/60}") h  |  8-wide: $(awk "BEGIN{printf \"%.0f\", $N*2/60/8}") h"
