---
name: project-stability-levers
description: "OOC stability root cause (spill controller defends wrong resource) + Tier-1 levers implemented on branch ooc-stability-levers, experiment designed, build pending"
metadata: 
  node_type: memory
  type: project
  originSessionId: c56aafd1-c2ba-4076-a294-e96674067178
---

Root cause of bounded-OOC instability (analyzed 2026-06-10, full doc: `.planning/eval-phase/lag-validation/OPTIONS-ooc-stability.md`):
the spill controller regulates `residentBytes()` (slice-store data pages only) while the contended resource is the fixed
buffer pool shared by data pages + unspill floods + probe accumulators + I/O. `getBufferBlocking()` (TupleBufferImpl.hpp:
GET_BUFFER_TIMEOUT=1000ms) throws rc=141 with no spill hook. Steady-state `unspillAndMarkEmitted` re-materializes the whole
triggerable batch at once (the anon_flush flood). Pooled-vs-unpooled switch: `pageSize <= bufferSize` in ChainedHashMap::insertEntry;
bucket dirs always unpooled. Pool = permanent resident floor AND hard ceiling → feasibility corridor empty at cap=2048/ρ=0.77.

Branch `ooc-stability-levers` (wrapper repo, commits 3c7cdff+): Tier-1 implemented, config-gated via
`--worker.default_query_execution.spill.pool_reserve_pct` (0=baseline byte-identical) + `paced_trigger_flush`:
1a spiller drains on pool availability < reserve; 1b BufferManager pool-pressure listener → store spills one cold slice per
fire instead of rc=141; 1c paced steady flush gated on pool headroom; 1d bucket dirs counted in budget.
New gauges: SPILLGAUGE (per spiller wake), POOLRESCUE, "Lever metrics:" line; POOLGAUGE densified to 1/4096.
run-cell.sh: env-gated `RESERVEPCT=` / `PACED=on`.

**Why:** the experiment (EXPERIMENT-stability-levers.md, runner lever_matrix.sh, 6 pre-registered hypotheses H1-H6,
~200 cells at corridor-edge cells from the 630-cell L_ matrix) must run AFTER the L_ rerun completes and AFTER rebuilding
systest from the branch (NOT yet built — `./lever_matrix.sh check` guards this).

**RESULTS + CRITICAL CORRECTION (2026-06-10):** lever machinery works mechanically (476-506 pressure spills, 145 rescues)
but the design review found `operator_buffer_size` defaults to **4096** < page_size 8192 → `insertEntry`'s pooled branch
(`pageSize <= bufferSize`) NEVER ran → **HashMap pages were UNPOOLED in every experiment of the eval**; the pool serves
ONLY the I/O pipeline (source/queue/emit). Proof: dying cells show avail_buf≈15933/16000 during build while maps hold
110-364MB unpooled. Errata: all pool-MB labels were 2× too high (16k=62.5MB, 64k=256MB, 128k=512MB); the
"bounded pooled-pages recycling" narrative is wrong (benefits came from 8KB-unpooled pages + code diffs); the lever
experiment's "un-spillable hot band floor" conclusion RETRACTED — rc=141 = I/O-pipeline flood (emit/trigger bursts),
rc=137 = state overflow (anon): independent axes. Completion windows/Fisher/cost/anon-contrast results all stand.
**N1 RE-RUN DONE (2026-06-10, 264 cells, lever_matrix_n1.sh, OPBUFSIZE=8192 env hook in run-cell.sh):
pooling + levers IS the unlock.** Money cell 1024/32k/r150: 0/10 historic → 0/16 unpooled-levers → 0/8
pooled-no-levers (B0) → 5/8 (B1) → 7/8 (B2, p=8.1e-6 vs unpooled levers); tps 3.38M = in-mem parity (3.44M),
steady anon 517 vs in-mem 685MB (−25%), p99 2.2×. Pooled alloc = intrinsic backpressure (512/16k/r200 flips
3/10→23/24 even at B0, p=1.6e-4). New rules under pooled arch: pool MB = 2× old labels AND is the upfront anon
floor (64k=528MB pools now rc=137 at cap 1024, incl. old G5 10/10 cell); lean pools win footprint AND survival;
SOFT must derive from POOL (~pool/3) not CAP — all 2048 cells were mis-specified (soft 204 > pool 132), corridor
at 2048 still open. Regression: 512/16k/r150 0/24 vs 8/10 historic (fast-ingest race, burst kill between spiller
wakes — N4 max_inflight is the designed answer). **FOLLOW-UP RUN DONE (2026-06-11, 68 cells, followup_n3n4.sh, §8): three negatives that isolate N2.**
F-H1 REFUTED (soft=pool/3 does not convert 2048 cells), F-H3 REFUTED (max_inflight 16/32 does not bound the
race; POOLG still 15884/16000 at i16), F-H2 corridor stays closed, F-H4 cost cell infeasible (1024/32k/r200
0/8 rc141). Unified mechanism — three-regime feasibility law of the pooled arch: (1) FITS: pipeline+band ≤ pool
→ full speed ok; (2) THROTTLED: demand > pool but tight cgroup throttles ingest → ok (512/16k/r200 23/24 @2.5M
tps; same workload 0/8 @1024 dying at 3.2M tps with double pool); (3) FLOOD: demand > pool, ingest unconstrained
→ rc141 always (store fully spilled, resident=0, avail=0 — rescue has nothing left to free, 1000ms timeout aborts).
**N2 DONE (2026-06-11, c965c5d, §9, 60+4 cells): backpressure REFUTED as a fix, CONFIRMED as the diagnosis.**
allocation_backpressure_ms implemented (1s rounds, recycle-epoch gridlock guard, BPGAUGE/Backpressure-metrics);
all five regime-3 cells stay 0/8: 138 stalls → 118 gridlock aborts (ZERO pool-wide recycles in 2s), 10 resolved,
0 budget. Regime 3 = role-blind allocation DEADLOCK: emitted-slice state pins the pool (~17k bufs ≫ 1000-task
queue), draining needs the workers that are blocked allocating. Invariant to: threshold spec, max_inflight,
admission_queue_size (probe 64/8 negative), waiting, lever dose. Escapes are architectural: role-aware allocation
(drain-side reserve via AbstractBufferProvider role tag) or closed-loop ingest admission on pool headroom — future
work, precisely specified. SHIP CONFIG = B2 (pooled+reserve15+paced, BP off): money cell B3 6/8 ≈ B2 7/8 but
tps −10%; N2's gridlock guard stays as instrumentation. Lever ledger CLOSED; remaining unexplored: N5 vtable
−20% (entry-inheritance refactor), N6 W-sweep.

**FINAL MATRIX DONE (2026-06-11, V 282 cells + W 282 cells + arbitration): COMPLEMENTARY-WINDOWS verdict.**
V (soft=pool/3) DEMOTED — understates arch (V2 arbitration: CAP/10 8/8 vs pool/3 3/8 at 512/16k/r200, p=0.026);
headline = W (soft=CAP/10). W windows (pooled B2): 512→r200 8/8 (ρ≈2, p=2.3e-5 vs in-mem, beats default arch's
3/10 at p=3.8e-3); 1024→r150 11/16 pooled; 2048→NOTHING above ρ≈0.5 (default arch owns r300-r700 there).
Non-monotone in ρ at 512/16k (r075 8/8, r100 2/8, r150 1/8, r200 8/8) = race-model proof (fast light maps outrun
drain; heavy maps self-throttle). Cost: pooled −3..−12% tps but p99 2-5× in-mem everywhere (tail-latency tax) vs
~1.2× default arch at low ρ; 2048/64k/r200 = 528MB pool does what default needed 1GB-class pool for, +5% tps,
−11% anon. Checksum MATCH 4/4 under pooled spilling (6620446084; rc141 teardown wart belongs to checksum
TEMPLATE — in-mem ref also exits 141). NEW BUG: pooled restore path not exception-safe (rc134 bad_function_call
/ rc11, ~20 cells, churn-dependent, only in already-failing cells). Lean-pool rule REFUTED (bigger feasible pool
better; interior optimum). Thesis headline = two-architecture complementarity; role-aware allocation = the one
mechanism that would merge the windows. Data: results/V_*, W_*, V2_*; EXPERIMENT-final-matrix.md.
[[project-ooc-mechanics]] [[feedback-match-baseline-config]]
