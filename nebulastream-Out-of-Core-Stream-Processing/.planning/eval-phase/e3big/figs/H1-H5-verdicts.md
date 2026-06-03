# H1-H5 verdicts (from data)

H1 throughput @lz4/wbuf64/cache32: median 19,930 tuples/s (end-to-end). Predicted >=30k db_bench ops/s — DIFFERENT metric; report integrated overhead vs in-mem.
H2 stall fraction @wbuf>=64: median 0.00% (threshold 5%) -> CONFIRMED
H3 compression: lz4 vs none throughput 19,944 vs 20,004 t/s; footprint differs -> assess mandatory-ness
H4 watermark: see fig (Sweep F). Note: architecturally the terminal-flush burst (not the watermark) was the binding constraint pre-partitioning; report in that context.
H5 spill latency: per-spill p50/p99 us median 41181/81401; spill_dir median 43 MB. Estimate s-per-100MB and compare to the ~2s prediction.