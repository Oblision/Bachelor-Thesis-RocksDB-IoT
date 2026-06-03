#!/usr/bin/env bash
# Concatenate per-cell result files into runs.tsv (with header) for analysis. Idempotent.
# 44-column schema covers RQ1 (status), RQ2 (throughput + task latency + spill+restore latency), and
# RQ3 (write-amp + footprint + bytes_{flushed,compacted,written} + block_cache_{hit,miss} + write_stall).
# Columns 32-44 are new vs e3big/collect.sh — added when run-cell.sh started parsing the metric fields
# NES already emits but the e3big runner dropped on the floor.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SCRIPT_DIR/runs.tsv"
HDR='label\tvariant\tcompression\tstate_tuples\tcap_mb\tspill\tpool\tsoft_mb\thard_mb\temit_lag_ms\tthreads\twbuf_mb\tcache_mb\tnum_partitions\trc\tstatus\telapsed_s\tthroughput_tps\tpeak_mb\tstore_peak_mb\tslices_spilled\tbytes_restored\tspill_dir_mb\twrite_amp\tsst_footprint_b\twrite_stall_micros\tp50_us\tp95_us\tp99_us\tspill_p50_us\tspill_p99_us\tspill_p95_us\tspill_max_us\trestore_p50_us\trestore_p95_us\trestore_p99_us\trestore_max_us\trestored_slices\tbytes_flushed\tbytes_compacted\tbytes_written\tblock_cache_hit\tblock_cache_miss\twrite_stall_count'
printf "$HDR\n" > "$OUT"
cat "$SCRIPT_DIR"/results/*.tsv >> "$OUT" 2>/dev/null
echo "collected $(( $(wc -l < "$OUT") - 1 )) cells -> $OUT  (44 columns)"
echo "next: python3 analyze_heatmaps.py $OUT figs   (to be drafted post-data; analyzer reuse from ../e3big possible for columns 1-31)"
