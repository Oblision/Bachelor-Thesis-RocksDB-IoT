---
title: "FINAL evaluation matrix — the corrected architecture (B2 ship config) across the full grid"
date: 2026-06-11
tags: [thesis, nebulastream, ooc, final-matrix, RQ1, RQ2, pre-registration]
status: pre-registered before the run; runner final_matrix.sh, labels V_*
---

# Final matrix: RQ1/RQ2 headline numbers under the system-as-designed

## Why this run exists
The original L_ matrix ran with `operator_buffer_size=4096` (engine default), which silently disabled
the designed maps-in-pool architecture (OPTIONS doc §7). Those runs are valid measurements of the
DEFAULT configuration, but the thesis's headline evaluation must describe the system as designed and
corrected. This matrix re-measures the RQ1 operating windows and RQ2 cost grid under the one
pre-committed ship configuration. The old data is retained as (a) the discovery narrative and
(b) the maps-on-heap ablation arm.

## Configuration (fixed a priori — NOT tuned on this run's outcomes)
B2 ship config from §7–§9: `OPBUFSIZE=8192, RESERVEPCT=15, PACED=on`, soft = pool_MB/3,
hard = 2·pool_MB/3 (the §8 pool-derivation rule). All other knobs identical to the L_ matrix
(W=50k, L=4M, pageSize 8192, lz4/wbuf16/cache8/P=4, 2 CPUs, tmpfs-in/SD-out, CELL_TIMEOUT=600).

**Selection/evaluation separation:** the config was selected on the §7–§9 pivot cells
(1024/32k/r150, 512/16k/r200, 2048 corridor cells). This matrix evaluates it on the full ρ ladder,
most of which (r075, r100, r300, r500, r1000, all 8k-pool cells) played no role in selection.
Regressions are reported as plainly as wins.

**The in-mem arm is NOT re-run.** The baseline is the status-quo system (NES default in-memory
backend, default configuration) — the thing being improved upon. Existing in-mem capped (0/10 OOM)
and uncapped (ρ-truth) data stands. ρ_meas keeps its definition (default-engine footprint ÷ cap) as
a consistent workload-size scale.

## Grid (8 reps per cell, sequential)
| cap | pools (MB at 8 KB/buf) | ρ-files |
|---|---|---|
| 512 | 8k (66), 16k (132) | r075 r100 r150 r200 r300 |
| 1024 | 16k (132), 32k (264) | r100 r150 r200 r300 r500 |
| 2048 | 16k (132), 32k (264), 64k (528) | r200 r300 r500 r700 r1000 |

35 coordinates × 8 reps = 280 cells, plus 2 checksum-correctness cells under active spilling.

## Pre-registered hypotheses
| id | hypothesis | decision rule / prediction |
|---|---|---|
| V-H1 | each cap has an operating window under B2 | window tables per cap; known priors: 512/r150 regresses (was 0/24 pooled), 512/r200 holds (23/24), 1024/r150 holds (7/8) |
| V-H2 | lean pools dominate (the §7 rule) | completion(16k) ≥ completion(32k) ≥ completion(64k) at fixed cap/ρ, up to the demand floor |
| V-H3 | the regime model predicts outcomes | cells with light maps + fast ingest (unpooled tps ≥ ~2.5M) and demand > pool → deadlock (rc141); heavy self-throttling workloads (r500+) complete where their band fits |
| V-H4 | cost band vs existing in-mem data at both-arms cells | Δtps within the 3–22% band of the default arch, or better (pooled recycling); p99 ratio reported |
| V-H5 | correctness under B2 while spilling | checksum MATCH at the two checksum cells |

## Analysis products
RQ1 window table per cap (completion + Fisher vs in-mem 0/10 where applicable); RQ2 cost table at
both-arms cells; V-H2 monotonicity check; rc-taxonomy map (which failures are floor/137 vs
deadlock/141) overlaid on the regime model; anon footprint vs in-mem and vs the unpooled arm at
matched cells.
