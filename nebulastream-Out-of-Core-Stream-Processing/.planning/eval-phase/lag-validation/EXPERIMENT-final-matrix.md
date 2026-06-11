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

## AMENDMENT (2026-06-11, pre-registered before the W run)
The V run (282 cells, 06:29–08:10) used soft=pool/3 — a rule validated in §8 only on cells that fail
under either rule. Arbitration at 512/16k/r200 (V2 cells): soft=CAP/10 → **8/8**; soft=pool/3 → 3/8
with a new crash mode. The V grid therefore UNDERSTATES the architecture and is demoted to a
threshold-sensitivity + robustness arm. The headline grid re-runs as W_* with the validated rule
(SOFT=CAP/10, HARD=CAP/5), identical otherwise.

**V-arm findings that stand on their own:**
1. **Restore-path robustness bug**: under tight thresholds + pooled pages, cells die rc=134
   (`std::bad_function_call`) and once rc=11 (SIGSEGV) — an allocation failure mid-restore leaves a
   half-restored slice whose creation callback is empty; next invocation crashes. The pooled restore
   path is not exception-safe. Documented as implementation finding; fix is future work.
2. **Threshold sensitivity is steep near the corridor edge**: soft 41→51 at 512/16k/r200 moves
   completion 3/8 → 8/8 (p = 0.026).
3. Checksum template adds emit-side pool demand (historic caveat confirmed under pooling) —
   checksum cells re-sited to headroom-rich coordinates (1024/32k/r100).

## W RESULTS (2026-06-11, 282 cells 08:19–10:04 + checksum refs)

**One-line: the corrected architecture and the default architecture have COMPLEMENTARY operating
windows — pooled wins quality (parity tps, lower anon, new completions) inside a narrower, lower-ρ
window governed by the pool; the default arch owns the high-ρ territory; correctness under pooled
spilling is confirmed (checksum MATCH 4/4); the regime model explains every row, including a
non-monotonicity in ρ that no state-size model can.**

### Beyond-memory wins under W (in-mem capped = 0/10 at these cells)
| cap | cell | W | Fisher vs in-mem | vs default arch |
|---|---|---|---|---|
| 512 | 16k/r200 (ρ≈2.0) | **8/8** | **p=2.3×10⁻⁵** | default 3/10 → **p=3.8×10⁻³ improvement** |
| 1024 | 32k/r150 (ρ≈1.4) | 4/8 (+N1 7/8 → 11/16) | p=5.7×10⁻⁴ pooled | default 0/10 @32k |
| 2048 | — | none (r300+ all 0/8) | — | default owns r300–r700 |

### The non-monotonicity that proves the race model (V-H3 ✓)
At 512/16k: r075 8/8, **r100 2/8, r150 1/8, r200 8/8**, r300 0/8. Completion is NOT monotone in ρ.
Survivor tps explains it: r100 ingests at 3.33 M tps (outruns the drain), r200 self-throttles to
1.98 M (pool backpressure couples ingest to drain). Same pattern at 1024 (r100 8/8 fast-and-fits,
r150 4/8 edge, r200 0/8 too big to fit + too fast to throttle).

### Cost under pooled (both-arms cells, W vs in-mem)
| cell | W tps | in-mem tps | Δ | W p99 | in-mem p99 |
|---|---|---|---|---|---|
| 512/16k/r075 | 2.77M | 3.15M | −12% | 485 µs | 95 µs |
| 1024/32k/r100 | 3.04M | 3.14M | −3% | 358 µs | 96 µs |
| 2048/64k/r200 | 3.06M | 3.25M | −6% | 238 µs | 109 µs |
Δtps matches or beats the default arch's 3–22% band; the new cost axis is TAIL LATENCY: pooled p99
runs 2–5× in-mem even at comfortable cells (allocation-path contention), vs ~1.2× for the default
arch at low ρ. 2048/64k/r200 completes with a 528 MB pool where the default arch needed a 1 GB-class
pool (128k), at +5% tps over that historic cell and −11% anon vs in-mem (924 vs ~1034 MB).

### Hypothesis scorecard
| id | verdict |
|---|---|
| V-H1 windows | ✓ per-cap windows exist but are NARROWER and shifted down vs default arch; 512/r150 regression confirmed (1/8 vs 8/10, p=7.6×10⁻³) |
| V-H2 lean pools | **REFUTED**: completion is better at the LARGER feasible pool at every cap (16k>8k @512, 32k≥16k @1024, 64k>32k>16k @2048) — the binding side is demand ≤ pool; the floor only bites when pool MB ≈ cap. Interior optimum, not monotone rule |
| V-H3 regime model | ✓ every row classified: fits → ok at parity; throttled (heavy maps at small cap) → ok at reduced tps; demand>pool + fast ingest → rc141 deadlock; pool≈cap or state≈cap → rc137 |
| V-H4 cost | ✓ −3…−12% tps; NEW finding: p99 2–5× (the pooled tail-latency tax) |
| V-H5 correctness | ✓ **MATCH**: OOC-pooled (spilling) and in-mem reference produce identical checksum 6620446084 / 5 505 091 tuples, 4/4 runs; the rc141 teardown wart is the checksum TEMPLATE's (reference exits 141 too) |

### Threshold arbitration (V vs W, same grid)
CAP/10 ≥ pool/3 at every coordinate where either completes; at the decisive cell the gap is 8/8 vs
3/8. The restore-path crash cluster (rc134/rc11, bad_function_call) appears under BOTH rules at
high-churn under-provisioned cells (512/8k heavy, */16k/r300) — it is churn-dependent, not
rule-dependent: ~20 crashed cells total, all in cells that fail anyway.

### Thesis consequence
The headline RQ1 narrative becomes two-architecture: the DEFAULT architecture (maps-on-heap) offers
wide ρ-windows (up to ρ≈2.2 @512, r700 @2048) with knife-edge pool sensitivity and heap-growth
footprint; the CORRECTED architecture (maps-in-pool, B2) offers in-mem-parity throughput, bounded
footprint (pool = floor AND ceiling), intrinsic backpressure, and NEW completions at ρ≈2 on the
smallest cap — but its window is bounded by the pool and closes entirely at 2048 high-ρ, where the
role-blind allocation deadlock (§9) is the proven, invariant cause. Neither dominates; the choice is
a deployment decision (state size vs latency/footprint requirements), and the deadlock fix
(role-aware allocation) is the precisely-specified path to merging the two windows.
