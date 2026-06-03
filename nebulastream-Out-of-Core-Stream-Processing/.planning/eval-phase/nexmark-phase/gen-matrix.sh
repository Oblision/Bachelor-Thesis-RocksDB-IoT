#!/usr/bin/env bash
# Emit jobs.txt — one `run-cell.sh ...` invocation per cell for the Nexmark-phase matrix.
# Implements EXPERIMENT-PLAN-NEXMARK.md §3: query × scale × variant + inmem_uncapped references.
# All windows are tumbling 10 s (set in the .test files). No knob sweeps — config is FROZEN from the 10k phase.
# Edit the FROZEN_* constants below after eyeballing ../e3big/figs/H1-H5-verdict.md.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$SCRIPT_DIR/run-cell.sh"
JOBS="$SCRIPT_DIR/jobs.txt"; : > "$JOBS"

# ---- Configuration — DEFENDED by independent prior work, NOT tuned on the eval workloads (see EXPERIMENT-PLAN-NEXMARK.md §2) ----
FROZEN_COMP=lz4          # Microbenchmark-BA: lz4 = 4.4x over none on readwhilewriting; community default.
FROZEN_WBUF=64           # RocksDB tuning guide default for write-heavy LSM; Microbenchmark-BA diminishing-returns knee.
FROZEN_CACHE=32          # cache:wbuf=0.5 rule of thumb; fits within smallest cap (256) at <6%.
FROZEN_SOFT=182          # H4 theoretical OOM ceiling = 2*wbuf+cache+100 = 260 MB; 70% watermark = 182.
FROZEN_HARD=234          # Same formula; 90% watermark = 234.
FROZEN_POOL=16000        # NES baseline default (~64 MB of 4 KB buffers ~ one write-buffer's worth of pipeline buffering).
FROZEN_THREADS=4         # Pinned to the per-cell --cpus=4 docker budget.

# ---- Axes (heatmap-shaped) ----
QUERIES=(q3_bridge q7_max_skewed q8_variant)
SCALES="1000000 2000000 5000000 10000000 20000000 40000000"            # 6 log-spaced (80M dropped — 40M is already 4x past inmem ceiling at any reasonable cap; 80M was the highest-cost / lowest-marginal-info point)
CAPS="256 512 1024"                                                      # 3 caps for cap × scale heatmaps
P_VALUES="1 2 4 8"                                                       # P SWEPT — partition-at-spill IS the thesis contribution, not a tuning knob.
                                                                          #          P=1 = the failed naive baseline this work improves on; P=2/4/8 trace the knee on realistic workloads.
                                                                          #          Choice is the data, not a heuristic — no contamination from Sweep A's empirical P-knee.
REPS="${REPS:-40}"

# Map (query,scale) -> (test_rel, gen_tuples, emit_lag) for run-cell.sh.
# Generator-driven workloads need the ${STATE} substitution to be applied to the .test file
# before run-cell.sh consumes it; run-cell.sh handles that via the SCALE field (see run-cell.sh).
resolve(){ local q="$1" st="$2"
  # gen_tuples = scale+1 (matches SpillSmokeVoid's GEN convention: SEQUENCE 0..STATE produces STATE+1 tuples)
  local gen=$((st + 1))
  # emit_lag = same band as 10k phase: lag == state
  local lag="$st"
  case "$q" in
    q3_bridge|q7_max_skewed) echo "work/${q}.test ${gen} ${lag}" ;;
    q8_variant)              echo "work/${q}.test ${gen} ${lag}" ;;
    *) echo "BAD_QUERY_${q}" >&2; return 1 ;;
  esac
}

# jobs.txt holds plain run-cell.sh arg-tuples consumed by `xargs -L1 bash run-cell.sh`.
# run-cell.sh arg order matches e3big's run-cell.sh:
#   label test gen cap spill pool soft hard emit_lag threads wbuf cache npart variant comp scale_state
job(){
  printf '%s %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s\n' \
    "$1" "$2" "$3" "$5" "$6" "$7" "$8" "$9" "$4" "${10}" "${11}" "${12}" "${13}" "${14}" "${15}" "${16}" >> "$JOBS"
}

for r in $(seq 1 "$REPS"); do
  for Q in "${QUERIES[@]}"; do
    for ST in $SCALES; do
      read -r T G EL <<<"$(resolve "$Q" "$ST")"
      # ---- inmem_uncapped reference (no cap, no P — overhead denominator) ----
      job "${Q}_s$((ST/1000000))M_unc_inmem_r${r}" \
          "$T" "$G" "$EL" none off "$FROZEN_POOL" "$FROZEN_SOFT" "$FROZEN_HARD" \
          "$FROZEN_THREADS" 0 0 1 "inmem_uncapped" none "$ST"
      # ---- per-cap cells: 1 inmem (no P) + |P_VALUES| ooc variants ----
      for CAP in $CAPS; do
        # inmem at the cap (no P — spill is off, P is not a meaningful axis for inmem)
        job "${Q}_s$((ST/1000000))M_c${CAP}_inmem_r${r}" \
            "$T" "$G" "$EL" "$CAP" off "$FROZEN_POOL" "$FROZEN_SOFT" "$FROZEN_HARD" \
            "$FROZEN_THREADS" 0 0 1 "inmem" none "$ST"
        # ooc, swept over P
        for P in $P_VALUES; do
          job "${Q}_s$((ST/1000000))M_c${CAP}_p${P}_ooc_r${r}" \
              "$T" "$G" "$EL" "$CAP" on "$FROZEN_POOL" "$FROZEN_SOFT" "$FROZEN_HARD" \
              "$FROZEN_THREADS" "$FROZEN_WBUF" "$FROZEN_CACHE" "$P" "ooc" "$FROZEN_COMP" "$ST"
        done
      done
    done
  done
done

N=$(wc -l < "$JOBS" | tr -d ' ')
NQ=$(echo "${QUERIES[@]}" | wc -w); NS=$(echo $SCALES | wc -w); NC=$(echo $CAPS | wc -w); NP=$(echo $P_VALUES | wc -w)
echo "wrote $JOBS  ($N jobs = $NQ queries × $NS scales × ($NC caps × ($NP P-values ooc + 1 inmem) + 1 unc) × REPS=$REPS)"
# Mean cell ~250s averaged across the matrix (scales with state size + boundary timeouts)
echo "est. serial: $(awk "BEGIN{printf \"%.0f\", $N*250/3600}") h  |  4-wide: $(awk "BEGIN{printf \"%.1f\", $N*250/3600/4}") h"
echo "FROZEN config: comp=$FROZEN_COMP wbuf=$FROZEN_WBUF cache=$FROZEN_CACHE soft=$FROZEN_SOFT hard=$FROZEN_HARD pool=$FROZEN_POOL"
echo "P_VALUES swept: $P_VALUES (partition-at-spill is the thesis contribution; P-knee on realistic workloads is part of the deliverable)"
