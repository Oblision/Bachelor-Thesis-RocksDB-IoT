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
