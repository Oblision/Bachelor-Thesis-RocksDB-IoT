#!/usr/bin/env bash
# Concatenate all per-cell result files into one runs.tsv (with header) for analysis. Idempotent.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/runs.tsv"
HDR='label\tvariant\tcompression\tstate_tuples\tcap_mb\tspill\tpool\tsoft_mb\thard_mb\temit_lag_ms\tthreads\twbuf_mb\tcache_mb\tnum_partitions\trc\tstatus\telapsed_s\tthroughput_tps\tpeak_mb\tstore_peak_mb\tslices_spilled\tbytes_restored\tspill_dir_mb\twrite_amp\tsst_footprint_b\twrite_stall_micros\tp50_us\tp95_us\tp99_us\tspill_p50_us\tspill_p99_us'
printf "$HDR\n" > "$OUT"
cat "$SCRIPT_DIR"/results/*.tsv >> "$OUT" 2>/dev/null
echo "collected $(( $(wc -l < "$OUT") - 1 )) cells -> $OUT"
echo "next: python3 ../e3/analyze_ooc.py $OUT figs   (or the extended analyzer)"
