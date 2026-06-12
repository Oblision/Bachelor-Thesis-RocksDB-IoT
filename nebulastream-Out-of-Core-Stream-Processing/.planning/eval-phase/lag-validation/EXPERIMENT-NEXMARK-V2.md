---
title: "Nexmark V2 — realistic-workload validation on the corrected stack (overnight run)"
date: 2026-06-12
tags: [thesis, nexmark, realism, RQ1, RQ2, pre-registration]
status: pre-registered before the run; runner nx_matrix.sh, labels NX_*
---

# Nexmark V2: the prior phase's tumbling-aggregation workloads on the validated configuration

## Why and what
The prior nexmark-phase ran Q3-bridge (SUM per auction) and Q7 (MAX per auction) on the OLD stack
(pre-discovery config: wbuf64/cache32/soft182, 4096 buffers, unpooled-everything, old binary).
This re-run puts the SAME two tumbling-window aggregation workloads (Q8 join excluded — out of
tumbling-agg scope) on the corrected stack to provide realism support: Nexmark bid schema,
hot-skewed auction keys (Generator NORMAL distribution), in-order event time — i.e., milder than
the adversarial synthetic jitter, closer to deployment reality. Honest caveat carried over from the
prior phase: Generator-based Nexmark is internally comparable (variant vs variant), not comparable
to published Flink Nexmark numbers.

## Workload sizing (state via window breadth; ρ-truth measured by uncapped refs FIRST)
In-order 1 ms/row event time; retention N_w = L/W = 80 windows in all cells.
| workload | WINDOW | EMIT_LAG | rows | est. keys/window | est. state |
|---|---|---|---|---|---|
| NX-A | 25000 ms | 2,000,000 | 31M | ~25k | ~550 MB (ρ≈1.1 @512) |
| NX-B | 50000 ms | 4,000,000 | 31M | ~50k | ~1.05 GB (ρ≈1.05 @1024, ρ≈2.1 @512) |
Estimates are sized from the synthetic calibration (~270 B/key total); the uncapped in-mem refs
(3 reps per query × workload) run FIRST and their measured anon REPLACES the estimates in analysis.

## Arms (validated configs only — no tuning on these workloads; cache ON = headline-data condition)
| arm | config | pools |
|---|---|---|
| inm-unc | in-mem reference, cap=none | 32k |
| inm | in-mem capped (the victim) | 32k |
| B2 | bounded: OPBUFSIZE=8192, reserve 15, paced, soft=CAP/10 | 512→16k, 1024→48k |
| U8 | heap: OPBUFSIZE=8128, leverless, soft=CAP/10 | 512→8k, 1024→32k |

Grid: {q3, q7} × {NX-A@512, NX-A@1024, NX-B@512, NX-B@1024} × {inm, B2, U8} × 8 reps + refs
= 192 + 12 = 204 cells, sequential (SD discipline). Est. 2–4 h; CELL_TIMEOUT 900.

## Pre-registered hypotheses
| id | hypothesis | decision rule |
|---|---|---|
| NX-H1 | the beyond-memory windows transfer to realistic workloads | where measured ρ>1: in-mem 0/8 and at least one OOC arm ≥6/8 |
| NX-H2 | in-order data is EASIER than the synthetic jitter (no late writes → no pins, no cache hazard, clean cold-spilling) | OOC completion ≥ synthetic at comparable ρ; zero rc134/rc11 |
| NX-H3 | the cost band holds (−11…−22% tps vs matched in-mem) | both-arms cells |
| NX-H4 | fits-cells complete at parity for all arms | NX-A@1024 across arms |
| NX-H5 | MAX ≈ SUM (aggregate function is not a factor) | q7 ≈ q3 within noise at every coordinate |

## RQ mapping (how this run feeds the thesis chapters)
| RQ | what Nexmark V2 contributes | where it lands in WRITING-POINTS |
|---|---|---|
| RQ1 effectiveness | external validity: the beyond-memory windows demonstrated on synthetic skew re-tested on canonical streaming schema/distribution (NX-H1); widens 7.2 from "synthetic edge extremes" to "realistic scenarios" | new 7.2.8 |
| RQ2 cost | cost band on realistic workloads at matched granularity (NX-H3); MAX-vs-SUM operator factor (NX-H5) | 7.3 addendum |
| RQ3 memory behavior | the regime model's first out-of-distribution test: in-order data removes the late-write band → predicts cleaner spilling, no pins, no cache hazard (NX-H2) — a falsifiable prediction DERIVED from the twin-defect mechanism | 7.6 addendum |
| RQ4 CPU/IO | same column pass on NX cells (CPU%, io_r/io_w, restore-from-cache vs device) | 7.4 |
| RQ5 storage | device-level WA on realistic bid streams vs synthetic (lz4 on Nexmark payloads) | 7.5 |
Scope note: Nexmark was the proposal's stretch goal; it enters the thesis as SUPPORTING evidence
(external validity), never replacing the pre-registered synthetic grid as the primary basis.

## AMENDMENT (pre-launch): Generator source abandoned, file source adopted; sizing re-measured
The prior phase's Generator source crawls (~3 k rows/s) on the current engine — smoke rc=124. Adopted
the file-source route the prior phase itself proposed: gen_nexmark.py bid CSV (31M rows, in-order
1 ms, auctionId = 70% Zipf(1.1) hot head + 30% uniform tail over 20M auctions). File smoke: rc=0,
16 s, 1.9M tps, 465 MB steady @W=50k (Zipf head saturates distinct/window ≈ 17k — below estimate).
Revised workloads by window breadth: A=W50k (~465 MB, fits/edge@512), B=W100k (~0.9 GB est,
beyond@512 + edge@1024), C=W200k (~1.5 GB est, beyond@1024). Refs measure the truth per query.
Grid: 2q × {A@512, B@512, B@1024, C@1024} × {inm,B2,U8} × 8 + 18 refs = 210 cells.

## RESULTS (2026-06-12, 210 cells, 02:35–03:49)

**One-line: the beyond-memory claim TRANSFERS to realistic workloads — at the one coordinate where
the in-memory engine genuinely cannot fit (C@1024: 0/8 OOM), bounded OOC completes 8/8 (q3,
p=7.8×10⁻⁵) and 7/8 (q7, p=7.0×10⁻⁴) doing real out-of-core work (153 slices, 610 MB written,
234 MB device restores); the twin-defect model's out-of-distribution prediction holds exactly
(zero rc134/rc11 in 210 in-order cells); and the honest news is the cost — in-order data pays the
full spill+restore round trip, and the pre-committed thresholds over-spill fits-cells.**

Footprint truth: uncapped refs inflate (688/784/956 MB — at full speed the open-window band grows
beyond nominal retention; the in-order form of the race); capped in-mem anon is the fits-truth
(325/433/785 MB). By that measure A@512, B@512, B@1024 are fits/edge cells; C@1024 is the one true
beyond-memory coordinate (in-mem 0/8 rc137, both queries).

| hypothesis | verdict | evidence |
|---|---|---|
| NX-H1 windows transfer | **MET at the beyond-memory coordinate** | C@1024: in-mem 0/8; B2 8/8 (q3) / 7/8 (q7); U8 6/8 / 3/8 — bounded wins decisively, heap partially: mirrors the synthetic 1024 complementarity |
| NX-H2 in-order is safe (model prediction) | **CONFIRMED** | zero rc134/rc11 across 210 cells; AND the two-sided law correctly explains every bounded rc137 at fits-cells (the 396 MB pool floor at 1024 / 132 MB at 512 eats the cap: B2 5/8, 6/8, 4/8, 2/8 at cells in-mem completes) |
| NX-H3 cost band | **REFUTED — costs are steeper on in-order data** | at over-spilled fits-cells −42…−54% tps (A@512: U8 1.07M vs in-mem 2.31M; 580 slices / 682 MB written for a workload that FITS — the soft=CAP/10 policy spills without benefit; CPU 59% = I/O-bound vs in-mem 97%); at the win cell B2 runs 1.05–1.38M vs uncapped 2.55M (−46…−59%) doing genuine OOC |
| NX-H4 fits-parity | REFUTED for B2 (pool-floor flakiness, law-consistent), held for U8 on completion (8/8) but not cost | table above |
| NX-H5 MAX ≈ SUM | broadly held | same pattern both queries; one edge-cell discrepancy (B@512 B2: q7 0/8 rc141 vs q3 5/8) in knife-edge territory |

## RQ contributions (final)
- **RQ1 (7.2.8):** external validity DELIVERED: on a canonical bid-stream schema with realistic
  auction popularity, bounded OOC completes a workload the in-memory engine cannot, p=7.8×10⁻⁵,
  with device-verified out-of-core operation. Boundary honestly stated: demonstrated at one
  coordinate (ρ_inm≈0.93+flush at 1024); fits-cells show the pool-floor cost instead.
- **RQ2:** in-order realistic streams pay the FULL spill+restore round trip (the synthetic jitter
  masked restore costs by keeping slices hot/unspillable): −42…−59% tps at spilling cells. A
  state-aware threshold (don't spill below fit) is the obvious, documented improvement — NOT
  applied here (no tuning on evaluation workloads).
- **RQ3:** the model survives out of distribution: safety prediction exact (0 corruption), failure
  placement exact (every bounded rc137 = right-wall floor; the lone in-mem rc141 = I/O flood at the
  63 MB pool), and the ref-inflation effect is the race mechanism in its in-order form.
- **RQ4:** fits-cell over-spilling visible as CPU 59% vs in-mem 97%; win-cell restores read from
  DEVICE (234 MB — first heavy exercise of the SD read path at 1024).
- **RQ5:** device WA at the win cell ≈ 610/309 ≈ 2.0 (restore-heavy churn); fits-cell 682/317 ≈ 2.2.
