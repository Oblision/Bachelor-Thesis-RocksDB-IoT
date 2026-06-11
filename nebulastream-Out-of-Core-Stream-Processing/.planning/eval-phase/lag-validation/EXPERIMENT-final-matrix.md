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

## U8 DISAMBIGUATION ARM (pre-registered 2026-06-11 before the run; runner u8_arm.sh, labels U_*)

The one-flag correction changes TWO things at once: allocation type (maps heap→pool) AND I/O buffer
granularity (4096→8192). Every cross-flag contrast is therefore confounded. The U8 arm isolates it:
`operator_buffer_size=8128` (64-aligned, < page 8192) keeps maps UNBOUNDED (heap) at 99.2%-matched
I/O granularity. Smoke confirmed the signature: pool_used peak 1 MB (empty), unpooled maps 136 MB.
Three-point design: default(4096,heap) / U8(8128,heap) / W,B2(8192,pool). U8 runs leverless,
soft=CAP/10, 8 reps.

| cell | default(4096) | bounded(8192) | U8 prediction if POOLING is causal | if BUFFER SIZE is causal |
|---|---|---|---|---|
| 512/16k/r200 | 3/10 | 8/8 | ≈3/8 | ≈8/8 |
| 1024/32k/r150 (money) | 0/10 | 11/16 | 0/8 | high |
| 512/16k/r150 (regression) | 8/10 | 1/8 | ≈8/8 (regression = pooling) | low |
| 2048/16k/r700 | 10/10 | 0/8 | ≈8/8 | — |
| cost cells r075/512, r100/1024, r200/2048-64k | −12%/−9%/−11% tps | −12%/−3%/−6% | Δtps ≈ default | Δtps ≈ bounded |

### U8 + I8 RESULTS (2026-06-11, 56+24 cells, 12:22–13:15) — the three-factor attribution

**One-line: the disambiguation splits the credit three ways — I/O granularity is a real, engine-wide,
previously hidden factor (it speeds up EVERYTHING, including in-mem by 10–13%); allocation type
(pooling) is cleanly causal for the 512/r200 win, the bounded footprint, the 512/r150 regression and
the loss of the high-ρ territory; the levers remain causal within pooling. One prior claim is
RETRACTED: the "pooled tail-latency tax" was the granularity, not the architecture.**

Five-point money-cell ladder (1024/32k/r150): default(4096,heap) 0/10 → U8(8128,heap) 3/8 →
B0(8192,pool,no levers) 0/8 → B1 5/8 → B2 7/8 (+W 4/8).

| attribution claim | evidence | p |
|---|---|---|
| pooling causal at 512/r200 | W 8/8 vs U8 0/8 (granularity alone: 0/8, ≤ default 3/10) | **7.8×10⁻⁵** |
| 512/r150 regression is pooling-caused | U8 7/8 vs W 1/8 (granularity does NOT cause it) | 5.1×10⁻³ |
| high-ρ territory belongs to heap allocation | 2048/r700: U8 8/8 vs W 0/8 | **7.8×10⁻⁵** |
| granularity helps the money cell | U8 3/8 vs default 0/10 (removes I/O-flood deaths; rc141→rc137 shift) | 0.069 (marginal) |
| pooling+levers > granularity at money cell | 11/16 vs 3/8 — direction positive, NOT separable at this n | 0.15 (n.s.) |
| levers causal within pooling (unchanged) | B0 0/8 → B2 7/8, same binary, same granularity | 7.0×10⁻⁴ |

**Granularity as an engine-wide factor (I8 = in-mem at 8128):** in-mem tps 3.15→3.22 (r075@512),
3.14→3.56 (r100@1024), 3.25→3.62 M (r200@2048) = +2…+13%; in-mem p99 95→427, 96→219, 109→203 µs
(per-TASK latency scales with buffer size — affects every variant equally). Heavy default-arch OOC:
r700 tps 1.14→1.55 M (+36%), p99 2509→899 µs (−64%). The engine's 4096 default is dominated at every
tested cell — an actionable upstream finding.

**Corrected cost table (all variants at ~8 K granularity — the honest RQ2 numbers):**
| cell | in-mem (I8) | heap OOC (U8) | bounded OOC (W) |
|---|---|---|---|
| 512/r075 | 3.22M / 427 µs / 340 MB | 2.31M (−28%*) / 429 µs / 381 | 2.77M (−14%) / 485 µs / 308 |
| 1024/r100 | 3.56M / 219 µs / 601 | 3.04M (−15%) / 339 µs / 621 | 3.04M (−15%) / 358 µs / 502 |
| 2048/r200 | 3.62M / 203 µs / 1387 | 3.22M (−11%) / 231 µs / 1264 | 3.06M (−15%) / 238 µs / **924** |
(*r075 U8 outlier on 3–4 s cells; report as measured.) OOC cost at matched granularity: **−11…−15%
tps, p99 1.1–1.6× in-mem** (the earlier 2–5× tail-latency claim is retracted — it compared across
granularities). Bounded footprint advantage CONFIRMED and larger than reported: **−33% vs matched
in-mem** (924 vs 1387 MB) and −27% vs heap OOC at 2048/r200.

**RQ1 in-mem baselines stand:** capped in-mem failures are state>cap OOM kills; granularity cannot
save them (U8's own rc137×8 at 512/r200 demonstrates heap state>cap dies regardless of buffers).

**Practical recommendations (final):** big state / high ρ → heap allocation + the granularity flag
(U8 config: one flag, dominates the shipped default everywhere tested); tight caps with footprint
guarantees → bounded B2; the shipped 4096 default → recommend upstream change.

## GAP-CLOSURE RUN (pre-registered 2026-06-11; runner gaps_run.sh)
1. **U8 arm completion** (heap @8128, leverless, soft=CAP/10, 8 reps): 512/16k×{r100,r300},
   1024/32k×{r200,r300}, 2048/16k×{r300,r500,r1000} — fills every "—" in the three-arm master table.
   Predictions: r100@512 ≈ valley (fast ingest); r300@512, r200/r300@1024, r1000@2048 = rc137
   (state>cap); r500@2048 ≈ 8/8 (mirrors default arch, +granularity).
2. **W 2048 sliver probe** (B2, soft=CAP/10, 8 reps): pool 96k (792 MB — untested between 64k ✓ and
   128k ✗floor) × {r200 control, r300, r500}. Predictions: r200 8/8 (control); r300 = THE open cell —
   if its band ≤ 792 MB it completes and the 2048 bounded window extends; r500 likely rc141 (band too
   big) or rc137 (unpooled+floor). Any r300 completions ⇒ the 2048 wall is pool-geometry, not deadlock.

## GAP-RUN RESULTS + BOUNDARY RUN (2026-06-11)
Gap run (14:59–16:03): **the 2048 bounded wall was pool geometry.** W@96k (792 MB ≈ 39% of cap):
r200 8/8, **r300 5/8 (bounded-ONLY: U8 0/8 rc141, default 0/10)**, **r500 8/8**. U8 gaps: 512/r100
7/8 @1.86M tps (cgroup throttles the heap arm too — regime 2 generalizes; explains the 512 U8 wins),
512/r300 0/8 rc137, 1024/r200 6/8, 1024/r300 8/8, 2048/r300 0/8 rc141 (the heap valley at 2048),
2048/r500 8/8 (anon 1072 < bounded@96k 1336 — big pools erase the footprint advantage), r1000 0/8 rc137.

**Boundary run (pre-registered): pool ≈ 40% of cap geometry.** W@2048/96k × {r700, r1000};
W@1024/48k (396 MB) × {r150, r200, r300}. Predictions: r700@96k = open decisive cell; r1000 rc137;
1024/48k: r150 high, r200 marginal, r300 rc141.

## BOUNDARY RESULTS + 512 GEOMETRY CLOSURE (pre-registered 2026-06-11)
Boundary run (16:05–16:57): **W@2048/96k/r700 = 8/8** (0.85M tps — bounded pays 45% tps vs U8's
1.55M but COMPLETES the former heap-flagship cell); r1000 0/8 rc141 (band > 792 MB = the true 2048
edge); **1024/48k (39% of cap): r150 8/8, r200 3/8 (the valley, survivors at 2.73M), r300 8/8** —
the geometry rule transplants. The complementarity largely DISSOLVES at geometry-correct pools:
bounded covers everything heap covers at 1024/2048 plus the bounded-only 2048/r300; heap keeps the
valley cells (fast-ingest, band≈pool) and the throughput edge at the heavy end. Footprint advantage
inverts at big pools (r700: bounded 1437 vs heap 1072 MB).
**512 closure (predictions):** 24k pool (198 MB ≈ 39%): r100 → unlock or valley; r150 → unlock
(band ~150-200 MB ≤ 198); r200 → keeps 8/8 (throttled regime survives any pool ≥ demand).

### 512 closure result + THE COMPLETED FEASIBILITY LAW (final form)
512/24k (39% of cap): r100/r150/r200 ALL 0/8 **rc137** — not pool exhaustion: the pool floor plus its
own induced overhead breaks the cap. Mechanism: a bigger pool retains more resident slices; every
resident slice carries UNPOOLED bucket-directory bytes; so anon grows superlinearly with pool size at
fixed cap. The bounded feasibility window is two-sided:
  **band ≤ pool   AND   pool + dir_overhead(pool) + base + RocksDB ≤ cap**
with two modifiers: the throttled regime rescues band>pool cells when the cap (or heap pressure)
slows ingest, and fast-ingest valleys kill marginal cells where survivors would need >2.7 M tps drain.
Per cap: 2048 → wide window (r200–r700 ✓ at 96k; r1000 = band > max pool); 1024 → 48k works
(r150 ✓, r200 valley 3/8, r300 ✓); 512 → upper bound sits below r150's band (base = 24% of cap;
only r075 (fits) and r200 (throttled) complete — an explained, permanent limit at the smallest cap.

**Final deployment rule:** cap ≥ 1024 and completion/footprint-guarantee priority → bounded with
pool ≈ 40% of cap (covers the heap window plus bounded-only cells like 2048/r300, at a throughput
cost at the heavy end: r700 0.85M vs heap 1.55M); cap ≤ 512 or throughput priority → heap + 8K
granularity. r1000@2048 fails under every configuration tested = the absolute edge of this engine.

## FAILURE-ATTRIBUTION PROBES (pre-registered 2026-06-11; runner probe_failures.sh, labels P_*)
Investigating every failure class of the two best configs (U8, W/B2) for config-attribution errors.
| probe | cell (prior result) | fix hypothesis | prediction if attribution error |
|---|---|---|---|
| P1a–d valley = race | W 512/16k/r100 (2/8), 512/16k/r150 (1/8), 1024/48k/r200 (3/8), 2048/96k/r300 (5/8) + EMITBPS=30MB/s (≈2.0M tps) | valley deaths are pure ingest-sprint; pacing to survivor speed converts them | ≥6/8 each ⇒ race CAUSALLY proven + rate-limit = practical fix |
| P2 U8 floor | U8 512/8k/r200 (0/8 rc137 @16k) | U8's 127MB pool floor pushed heap arm over the cap (default@62MB got 3/10) | ≥3/8 ⇒ U8's 512 loss was pool-floor attribution error |
| P3 U8 flood | U8 2048/64k/r300 (0/8 rc141 @16k) | heap-arm rc141 is an I/O-pool flood; 508MB pool absorbs it | ≥6/8 ⇒ heap valley at 2048 is pool-sizing, not fundamental |
| P4 right wall | W 512/24k/r150 + MAXBUCKETS=4096 (0/8 rc137) | dir overhead is the right-wall term; ÷4 dirs moves the wall right | any completion ⇒ overhead term causally confirmed + r150@512 unlockable |
| P5 r1000 sliver | W 2048/112k/r1000 (rc141 @96k) | band ∈ (792, 917]? | any completion ⇒ absolute edge moves |
| P6 U8 pool-throttle | U8 1024/8k/r150 (3/8 @32k) | tiny I/O pool throttles the source = heap-arm governor | ≥6/8 ⇒ heap valley fixable by pool sizing |

## FULL-CIRCLE RUN (pre-registered 2026-06-11; branch ooc-ingest-governor, runner fullcircle_run.sh)
Probe results folded in: P1 proved the race causally where headroom exists (2048/96k/r300 5/8→8/8
paced) and exposed the restore bug as the binding obstacle at tight caps (rc134 under pacing);
P2 fixed U8@512 (lean pool, 7/8); P4 moved the right wall (maxbuckets 4096 → r150@512 8/8).
DECISION: the restore-bug fix is NOT attempted tonight (invariant-dense concurrency code); instead
GF-H2 tests whether the governor's headroom guarantee makes the bug unreachable — closed-loop
maintains the free-buffer floor the restore path implicitly needs, which static pacing does not.

Arm GA = B2 + SRCGATE=30 (source waits while global pool free < 30%). Control = same binary, gate off.
| id | hypothesis | decision rule |
|---|---|---|
| GF-H1 | the governor converts the valleys adaptively | GA ≥6/8 at the four valley cells; tps ≥ paced oracle (1.61M at 2048/r300) |
| GF-H2 | headroom guarantee makes the restore bug unreachable | zero rc134/rc11 in GA valley cells (vs rc134×6/4/3 under pacing) |
| GF-H3 | free when not needed | gate_waits ≈ 0 AND tps within noise at r075, money cell, 2048/r500+r700 |
| GF-H4 | binary regression gate | control (gate off) reproduces W-era results at 2 valley cells (~2/8, ~3/8) |
| GF-H5 | the everything-on config | 512/24k/r150 + maxbuckets 4096 + gate: ≥6/8 (P4's unlock survives the governor) |
