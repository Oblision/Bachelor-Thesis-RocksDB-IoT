# E3/E4 — results (Axis-A sweep, 2026-05-25)

Dataset: `e3/runs.tsv` (105 runs). Figures: `e3/figs/fig{1..6}_*.png`. Workload: phase0c Void tumbling
GROUP BY SUM, 2,000,001 tuples, 100k-key windows; state ladder = retained band via `emit_lag`. Config:
lz4 / write_buffer 32 MB / block_cache 32 MB, pool 16000, soft 4 / hard 8 MB, 4 threads. P=8 for partitioned OOC.
Data quality: throughput CV 0.005–0.079 across all OOC cells (3 reps sufficient).

## §3.3 / RQ1 — OOM survival (fig1)
At a **320 MB cap**, naive OOC (P=1, no partitioning) is OOM-killed once retained state ≥ 800k keys
(800k = 1/3 survived = boundary; ≥1.2M = always OOM). **Partition-at-spill (P=8) completes the entire ladder to
2M keys.** At 512 MB all three variants survive (more headroom). The in-memory baseline survives at all tested
sizes — honest nuance: at ≤2M keys total state fits under these caps, so the OOM that partitioning solves is the
**OOC terminal re-materialization burst**, which P=1 suffers and P=8 avoids. (phase2e earlier showed the same
P=1→rc137 vs P=8→rc0 at 2M/320.)

## Residency — the 1/P bound (fig2, @512 MB where both P survive)
- P=8 store working set: **19 → 4 MB** as state grows (bounded ~1/P).
- P=1 store working set: **19 → 66 MB** (climbs with the whole band).
- P=1 process RSS climbs toward the cap (445 → 504 MB of 512); P=8 process RSS stays ~410–462 MB.
  This is why P=1 OOMs at the tighter 320 cap and P=8 does not.

## RQ2 — overhead (fig3, fig4)
- Throughput: OOC mean **15,132 t/s** vs uncapped in-mem **16,433 t/s** → **~8% overhead**. Modest cost for
  surviving a cap that kills naive OOC.
- Per-task tail latency p99 **~1.4–1.5 ms** (one 2.3 ms outlier), comparable to baseline — no tail blow-up.
  Caveat: per-task, not per-window (engine exposes per-task events only).

## RQ3 — storage efficiency (fig5, fig6)
- Write amplification **1.00** (lz4, flush-dominated; no compaction triggered at this scale).
- On-disk SST footprint **~16 MB** (16.15–16.42 MB), stable across the ladder for the spilled band.
- Spill volume 19 slices / ~45–78 MB depending on band; partitioned restore bytes now correctly recorded
  (E1-PR1) — non-zero on the P>1 path.

## Caveats
See `E1-MEASUREMENT-CAVEATS.md` (per-task latency, small-n percentiles, RocksDB-Statistics overhead, restore
proxy, throughput-includes-startup, process-RSS-pool-dominated).

## Bottom line
Partition-at-spill turns naive OOC (which OOMs on the terminal flush) into a system that completes tumbling
windowed aggregation with state spilled to RocksDB, at ~8% throughput overhead, write-amp 1.0, and a store
working set bounded to ~1/P — satisfying the §3.3 gate and answering RQ1/RQ2/RQ3.
