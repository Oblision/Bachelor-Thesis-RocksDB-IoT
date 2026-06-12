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

**U8/I8 DISAMBIGUATION DONE (2026-06-11): THREE-FACTOR attribution.** The one-flag fix confounds allocation
type + I/O granularity (4096→8192). U8 arm = OPBUFSIZE=8128 (<page 8192 → maps stay heap, 99.2% matched
granularity); I8 = in-mem@8128. Verdicts: (1) GRANULARITY is engine-wide and hidden: in-mem +10-13% tps
(3.25→3.62M), per-task p99 SCALES with buffer size (96→219µs in-mem); heavy OOC r700 +36% tps, p99 −64%;
money cell 0/10→3/8 (p=0.069); 4096 default DOMINATED everywhere → upstream rec. (2) POOLING causal:
512/r200 W 8/8 vs U8 0/8 (p=7.8e-5); 512/r150 regression pooling-caused (U8 7/8 vs W 1/8, p=5.1e-3);
high-ρ r700 heap-owned (U8 8/8 vs W 0/8, p=7.8e-5); footprint −33% vs matched in-mem (924 vs 1387MB).
(3) LEVERS stand within-granularity (B0 0/8→B2 7/8, p=7e-4). Money-cell pooled-vs-U8 NOT separable (11/16
vs 3/8, p=0.15) — report 5-point ladder 0/10→3/8→0/8→5/8→7/8 honestly. **RETRACTED: "pooled tail-latency
tax 2-5×"** — was granularity; at matched ~8K, OOC p99 = 1.1-1.6× in-mem, cost band −11..−15% tps.
Corrected RQ2 table in EXPERIMENT-final-matrix.md (U8+I8 RESULTS). U8 (heap+8128) = best config for big
state; B2 = best for bounded footprint.
[[project-ooc-mechanics]] [[feedback-match-baseline-config]]

**GEOMETRY SAGA COMPLETE (2026-06-11, sliver+boundary+512-closure runs):** the W all-zero 2048 column was
POOL GEOMETRY. W@96k(39% cap): r200 8/8, r300 5/8 (bounded-ONLY — heap arms 0/8 rc141), r500 8/8, **r700 8/8**
(0.85M vs U8 1.55M = 45% tps cost), r1000 0/8 rc141 (absolute edge). 1024/48k: r150 8/8, r200 3/8 valley,
r300 8/8. 512/24k: ALL rc137 — TWO-SIDED LAW: band ≤ pool AND pool + dir_overhead(pool) + base ≤ cap
(resident slices carry unpooled dirs → anon superlinear in pool size; base=24% of cap at 512 → r150 permanently
locked at 512 under bounded). Valley recurs at every cap (~9 sightings): survivors <2.5M tps, sprinters >2.7M die.
Footprint advantage inverts at big pools (r700: bounded 1437 vs heap 1072MB). FINAL deployment rule: cap≥1024 +
completion/footprint-guarantee → bounded@40%cap; cap≤512 or throughput → heap+8K granularity. Master table
complete, all three arms, all coordinates. WRITING-POINTS 7.2.4/7.2.5/7.2.5b/7.6r-t hold the final form.

**FULL CIRCLE CLOSED (2026-06-11 night, branch ooc-ingest-governor, 8aa7131):** PROBES: P1 race causally
proven (2048/96k/r300 5/8→8/8 paced) BUT pacing exposes restore bug at tight caps (rc134 — bug is now
LOAD-BEARING, blocks throttle fixes; fix = exception-safe restore, future work); P2 U8@512 = pool-floor error
(lean 8k pool → 7/8); P4 maxbuckets=4096 MOVES the right wall (512/24k/r150 0/8→8/8 — "permanent 512 lock"
RETRACTED); P5 r1000 edge real; P6 pool-throttle refuted. GOVERNOR v1 (source_admission_gate_pct, pool-%-free
gate in SourceThread) IMPLEMENTED then REFUTED with mechanism: (A) never fires at valleys — depletion is a
fast transient between rent-time samples, driven by downstream amplification; (B) SELF-DEADLOCKS heavy cells —
30% free unsatisfiable vs legitimate steady occupancy (r700 8/8→0/8 rc124, source frozen in gate, trace flat).
Derived design constraint (the thesis's closing insight): admission credit must be MONOTONE IN SOURCE PROGRESS
(window-lag credit) or role-aware at the allocator; global resource gauges cannot distinguish working-full from
dying-full. Both naive coupling points (N2 alloc-time, v1 source-time) now refuted with mechanism — doubly
motivating the specified future work. GF-H5: B2+maxbuckets4096 = 8/8 at formerly-locked cell (everything-on
config validated). Eval is COMPLETE — no open cells, all arms attributed, ~3000 cells total.

**PHASE-1 BUG HUNT COMPLETE (2026-06-12 ~00:15, cfc3702+): TWIN-DEFECT verdict.** Corruption root-caused via
ASan + manual arena poisoning (pool slab + unpooled chunks — both invisible to ASan without poisoning; hooks
retained, no-ops in normal builds): double-free = two concurrent insertEntry on one ChainedHashMap. Cause:
per-worker SliceCache (default ON, "unless weird behavior happens") hands JIT raw map pointers that bypass ALL
spill fences (pin/claim/spillInProgress guard only the cache-MISS path). Falsified both ways: cache-on 3/3 crash,
cache-off 4/4 clean. BUT cache-off gauntlet: 0/8 rc141 at ALL 10 groups incl. winners — every late write then
pins its slice till emit, and with jitter D = retention L the WHOLE band pins → spilling impossible. The defects
interlock: cache masks pins, pins exist for the cache. Headline data unaffected (corruption confined to already-
failing cells, ~20/3000). Upstream fix precisely specified: epoch-stamped cache invalidation on spill (JIT-
boundary = out of thesis scope). Architectural lesson for thesis: unmediated raw pointers into evictable state
need an invalidation protocol — Flink avoids it by mediating every access. SLICECACHE=off hook exists in
run-cell.sh. Paced-valley conversion at tight caps blocked behind the upstream fix.

**NEXMARK V2 DONE (2026-06-12 overnight, 210 cells, NX_*): realism transfer CONFIRMED at the beyond-memory
coordinate.** C@1024 (in-mem 0/8 rc137 both queries): B2 8/8 (q3, p=7.8e-5) / 7/8 (q7, p=7e-4), genuine OOC
(610MB spilled, 234MB DEVICE restores — first heavy SD-read exercise); U8 6/8 / 3/8 (complementarity mirrors
synthetic 1024). Twin-defect OOD prediction EXACT: 0 corruption in 210 in-order cells; every B2 rc137 at
fits-cells = pool-floor (two-sided law placement correct OOD). HONEST: NX-H3 refuted — in-order pays full
spill+restore round trip (jitter masked restore costs: hot slices never spilled in synthetic): fits-cells
over-spill at soft=CAP/10 (−42..−54% tps, CPU 59% I/O-bound, 682MB writes for a FITS workload); win cell
−46..−59% vs uncapped. State-aware threshold = documented future work (no tuning done). Ref-inflation finding:
uncapped refs' band grows at full speed (in-order race form) — capped-inm anon is fits-truth. Generator source
crawls on current engine (~3k rows/s) → file-source route (gen_nexmark.py, 70% Zipf head + 30% tail). MAX≈SUM
broadly (one edge discrepancy). WRITING-POINTS 7.2.8-10 added. Eval FINAL state: ~3300 cells.
