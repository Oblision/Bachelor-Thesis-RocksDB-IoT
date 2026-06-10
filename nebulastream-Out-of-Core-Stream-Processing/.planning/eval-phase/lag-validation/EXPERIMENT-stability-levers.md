---
title: "Stability-lever experiment — pool-pressure defense A/B with full memory-instrumentation pipeline"
date: 2026-06-10
tags: [thesis, nebulastream, ooc, stability, experiment-design, RQ3, levers]
status: designed; runs after the L_ rerun completes (branch ooc-stability-levers, build pending)
---

# Experiment: do the Tier-1 levers give bounded-OOC in-mem-class stability?

## 0. Prerequisites (in order)
1. The L_ matrix rerun (r500/r700/r1000 corrected CSVs) has completed — no concurrent cells.
2. Rebuild release binary from branch `ooc-stability-levers`:
   `docker run --rm --workdir /workspace -v $(pwd):/workspace nebulastream/nes-development:local-rocksdb \
      bash -c "cmake --build cmake-build-release --target systest -j 14"`
3. Sanity: `./lever_matrix.sh check` greps the binary for the new config option and runs ONE smoke cell per arm.

## 1. Hypotheses (pre-registered, falsifiable)

| id | hypothesis | metric | decision rule |
|---|---|---|---|
| H1 | 1b converts pool-exhaustion aborts into survival: rc=141 cells gain completion | n_ok at G2/G3 | A1 ≥ +4/8 vs historic A0 per cell (Fisher p<0.05 pooled) |
| H2 | 1c collapses the end-of-stream flood | anon_flush − anon_steady | A2 ≤ 50% of A0's gap at G1/G2/G4 cells |
| H3 | The corridor reopens at cap=2048/ρ=0.77 | n_ok at G1 | A2 ≥ 6/10 at any pool ⇒ demand-side was binding; 0/10 everywhere ⇒ floor-binding → Tier-0 follow-up (maxBuckets) |
| H4 | Levers are ~free when not needed | tps, p99 at G5 (A0 already 10/10) | Welch t: tps drop ≤5%, p99 rise ≤15% |
| H5 | Pool-size sensitivity flattens | success vs pool curve per arm | A2 curve monotone non-decreasing up to the OOM wall (no "bigger pool worse" on the 141 side) |
| H6 | 1d makes soft honest | SPILLGAUGE resident vs HMGAUGE-derived true bytes | A1 budget error ≤5% (vs ~15% in A0) |

## 2. Arms (one binary, config-gated)

| arm | RESERVEPCT | PACED | levers active |
|---|---|---|---|
| A0 | (unset) | off | none — byte-identical baseline; mostly REUSED from the L_ matrix (10 reps/cell, same workloads) |
| A1 | 15 | off | 1a + 1b + 1d |
| A2 | 15 | on | 1a + 1b + 1c + 1d (the bundle) |
| A3 | 5 / 30 | on | dose-response at the two pivot cells |

A0-regression: 4 fresh reps at G6 + one G3 cell on the NEW binary with levers off, compared against the
historic L_ numbers — guards against an accidental baseline shift from the branch (the gating contract).

## 3. Cells (anchored to the measured corridor edges of the L_ matrix)

| group | cap | ρ-file | pools | historic A0 | what it tests |
|---|---|---|---|---|---|
| G1 headline | 2048 | r300 | 64k, 128k | 0/10 POOL, 0/10 OOM | H3: the empty corridor |
| G2 rc141 | 2048 | r200 | 16k, 32k | 0/10 POOL, 0/10 POOL | H1: pure pool-exhaustion rescue |
| G3 flaky | 1024 | r150 | 16k, 32k, 64k | 0/10 POOL, 0/10 POOL, 4/10 OOM | H1+H2 mixed-mode |
| G4 flush-OOM | 512 | r150, r200 | 16k | 8/10 POOL, 3/10 POOL | H2 at the tight cap |
| G5 cost | 1024 | r200 | 64k | 10/10 | H4 overhead-when-idle |
| G6 regression | 512 | r075 | 16k | 10/10 | A0-regression gate |

Reps: 8 (A1, A2), 4 (A3, A0-regression). ≈ 200 cells ≈ 2.5–4 h sequential (SD-card discipline: one cell at a time).
In-mem reference points come from the existing `L_c*_inm_*` data (10 reps each) — no re-run needed.

## 4. Memory-instrumentation pipeline (what makes this experiment "memory-first")

Every cell automatically produces, time-aligned:
1. **mem_trace** (0.2 s): cgroup anon / file / shmem + process RssAnon — the OOM-driver ground truth.
2. **SPILLGAUGE** (every spiller wake, ~100 ms): `resident_mb soft_mb avail_buf reserve_buf pressure
   pressure_spills rescue_spills paced_waits` — the controller's view, NEW on this branch.
3. **POOLGAUGE** (1/4096 rents, densified 16×): pool occupancy from the allocator's view.
4. **POOLRESCUE** (throttled 1/8): every allocation-pressure fire with availability at fire time.
5. **HMGAUGE** (1/4 map dtors): exact header/keyval/bucket-dir/slack split — feeds H6.
6. **UPOOLGAUGE**: unpooled-chunk live bytes (bucket dirs + arena + retention slack).
7. **Spill metrics + Lever metrics** final lines: spill/restore counts+latencies, pressure/rescue/paced counters.

### Analysis products (lever_analyze, after the run)
- **Money figure per pivot cell:** overlay anon(t) [mem_trace], avail_buf(t) [SPILLGAUGE], resident(t)
  [SPILLGAUGE] for A0 vs A1 vs A2 — shows the reserve being defended and the flood collapsing.
- **Failure-taxonomy shift table:** rc histogram per (cell, arm) — H1's evidence.
- **Flood table:** anon_flush − anon_steady per (cell, arm) — H2's evidence.
- **Backpressure-cost distribution:** rescue fire count × wait slices + paced_wait_us vs p99 delta — the
  price of stability, the honest counterweight.
- **Sensitivity curves:** success-vs-pool per arm at each cap — H5.
- **Budget-honesty check:** SPILLGAUGE resident vs HMGAUGE ground truth per arm — H6.
- Fisher (completion) + Welch (tps/p99) per cell vs historic A0.

## 5. Run

`./lever_matrix.sh` (after the build; resumable, skips done cells; labels `S_<arm>_c<cap>_p<pool>k_<rho>_r<rep>`).

## RESULTS (2026-06-10, 188 cells, build 209/209 clean, run 14:23–15:29)

**One-line: the levers work mechanically and help at the margin, but the hard failures are governed by
UN-SPILLABLE pool demand — the open-window (hot) band — which no spill policy may touch. The controller
is no longer the binding limit; the data structure is.**

| hypothesis | verdict | evidence |
|---|---|---|
| H1 rescue | **PARTIAL** | c512/16k/r200 (deficit within spillable state): 3/10 → A1 6/8 (p=0.077), A1+A2 11/16 (p=0.063), tps unchanged. ALL 16k/32k cells at 1024/2048: 0/8 — see the smoking gun below |
| H2 flood | **untestable as designed** | paced_waits = 0 everywhere: this workload's band drains through the P=4 partitioned TERMINAL path, not a steady-path watermark jump; the measured flush spikes (r500/r700, +326…+444 MB) live in cells the matrix did not include |
| H3 corridor | **structural, confirmed** | r300/2048: 0/8 at 64k+128k, dose-invariant (5/15/30%) |
| H4 cost | **no lever overhead** | within-session, levers-ON BEATS levers-OFF at starved cells (+12% tps — the reserve avoids allocation stalls); G5 −4% (A1) / −9% (A2) vs historic, within session-drift range |
| H5 sensitivity | not flattened | corridor cells stayed closed (consistent with un-spillable demand) |
| H6 budget | gauges work | SPILLGAUGE/POOLRESCUE/Lever-metrics all captured; deep budget-honesty check not pursued (moot given H1/H3) |

**The smoking gun** (`S_A1_c1024_p16k_r150_r1`): at death, SPILLGAUGE reads `resident=9MB, avail_buf=0,
reserve=2400` after 112 slices spilled and 4 rescue fires. The spiller did its job perfectly — everything
spillable WAS spilled — and the pool was still empty. The demand is the hot band + emitted maps + I/O:
irreducible by any spill policy. rc=141 cells split cleanly into *reclaimable* (deficit ≤ retained-completed
state → levers convert them, c512/r200) and *irreducible* (open-window state > pool → nothing converts them).

**A0-regression gate:** completion parity ✓ (4/4, 4/4, 3/4 vs 10/10, 8/10, 4/10). tps this session ran
4–17% below historic at the 512 cells for ALL arms including levers-off — session drift (and A1/A2 ≥ A0reg
within-session), not a gating leak; thesis numbers should prefer within-session contrasts.

**Lever counters:** pressure_spills 476 (A1) / 506 (A2), rescue_spills 145/146, paced_waits 0/0.

**Thesis-level take:** ~~this experiment proves the un-spillable hot window is the hard floor~~
**RETRACTED by the 2026-06-10 design review (OPTIONS doc §7):** `operator_buffer_size` defaults to 4096 <
page_size 8192, so HashMap pages were UNPOOLED in every run — the pool the levers defended contains no map
state, and rescue spills could not free pool buffers by construction. The smoking-gun cell reads correctly
as "state fully spilled, **I/O pool exhausted by the emit/ingest pipeline**" — rc=141 is an I/O-pipeline
flood, not a state-memory failure. The experiment's real findings: (a) the lever machinery functions
mechanically end-to-end; (b) rc=141 and rc=137 are INDEPENDENT failure axes (I/O vs anon); (c) the levers
must be re-tested with pooling actually engaged (`operator_buffer_size=8192`, one flag) before any verdict
on the maps-in-pool architecture.

## 6. Interpretation guardrails
- If A0-regression deviates from historic L_ numbers → STOP, the gating leaks; fix before interpreting A1/A2.
- H3 negative (G1 stays 0) is a FINDING, not a failure: it pins the corridor on the floor side and promotes
  the Tier-0 maxBuckets probe + Tier-2 (pool the bucket dirs) as the remaining path.
- Watch rescue_spills at G5: if >0 there, the reserve is mis-sized (it should never fire when A0 succeeds
  comfortably) — re-run G5 with RESERVEPCT=5 before concluding H4.

## 7. N1 RE-RUN (pre-registered 2026-06-10, runner `lever_matrix_n1.sh`) — pooling ENGAGED

Per the design review (OPTIONS doc §7): all runs above had `operator_buffer_size=4096` < page_size 8192,
so the pooled branch never ran. This re-run adds the one flag (`OPBUFSIZE=8192` → run-cell.sh) and re-tests
at the same pivots. Pool MB are now 2× the unpooled-era labels (16k=132, 32k=264, 64k=528, 128k=1056 MB);
the pool is allocated upfront, so it is also the anon floor.

Arms: **B0** pooling only (levers OFF — the architecture A/B vs historic heap-map A0),
**B1** pooling + RESERVEPCT=15, **B2** pooling + RESERVEPCT=15 + PACED. 8 reps each.
Cells: G0 correctness gate (512/r075/16k, historic 10/10) + G1–G5 as above. Labels `N_<arm>_…`.

| id | hypothesis | decision rule |
|---|---|---|
| N-H1 | the flag engages pooling: SPILLGAUGE avail_buf tracks map growth; UPOOLG ≈ bucket dirs only | **CONFIRMED in smoke** (`N_smoke_c512_p16k_r075_r1`): avail 16000→11353 during build, pool_used peak 118/125 MB, rc=0 |
| N-H2 | maps-in-pool alone changes the failure taxonomy | B0 vs historic A0 rc histograms at G2–G4 |
| N-H3 | the levers are real now: rescue/pressure spills free pool buffers | B1/B2 > B0 completion at rc=141-dominated cells (Fisher) |
| N-H4 | the corridor reopens at 2048/r300 | any arm ≥6/8 at G1 ⇒ the old failure was the unpooled architecture |
| N-H5 | pooled allocation is not slower than heap | G0+G5 tps within −5%, p99 within +15% of historic A0 |
| N-H6 | anon floor = pool: steady anon ≈ pool MB + unpooled dirs + base; the RQ3 anon advantage shrinks at small ρ, may grow at large ρ via recycling | mem_trace vs S_/L_ anon at identical cells |

Guardrail: G0 must complete ≈8/8 per arm — it is the correctness gate for the pooled path
(allocation, spill/unspill round-trip, emit). If G0 fails, stop and debug before interpreting G1–G5.

### §7 RESULTS (2026-06-10, 264 cells, run 19:03–20:27)

**One-line: pooling + levers IS the unlock at the right operating point — the attribution cell
1024/32k/r150 goes 0/10 (historic) → 0/16 (levers, unpooled) → 0/8 (pooled, no levers) → 5/8 (B1) →
7/8 (B2) — but the pooled architecture re-draws the whole feasibility map: the pool floor doubles,
soft thresholds derived from CAP are wrong (must derive from POOL), and one fast-ingest cell regresses.**

| contrast | result | Fisher |
|---|---|---|
| **N-H3 levers real** (1024/32k/r150): B2 vs B0 | 7/8 vs 0/8 | **p = 7.0×10⁻⁴** |
| same, B1+B2 vs unpooled levers S_A1+A2 | 12/16 vs 0/16 | **p = 8.1×10⁻⁶** |
| same, B1+B2 vs B0+historic A0 | 12/16 vs 0/18 | **p = 3.3×10⁻⁶** |
| **pooling alone** (512/16k/r200): B0 vs historic A0 | 7/8 vs 3/10 | p = 2.3×10⁻² |
| same, all pooled arms | 23/24 vs 3/10 | **p = 1.6×10⁻⁴** |
| **regression** (512/16k/r150): pooled vs historic A0 | 0/24 vs 8/10 | p = 2.5×10⁻⁶ (less) |

**The money cell** (1024 MB cap, pool 32k = 264 MB, r150, arm B2): completion 7/8 vs in-mem 10/10;
tps 3.38M vs in-mem 3.44M (**−1.7%, parity**); steady anon 517 MB vs in-mem 685 MB (**−25%**);
p99 215 µs vs 98 µs (2.2×). Working SPILLGAUGE: resident held ~26 MB ≪ soft 102, 14 pressure spills,
0 rescues — the reserve absorbs the bursts, the regular spiller does the rest.

Hypothesis verdicts:
- **N-H1 engaged** ✓ (smoke + pool_used up to 248 MB at 32k pools).
- **N-H2 taxonomy shift** ✓ both directions: G5 (1024/64k/r200, historic 10/10) and 1024/64k/r150 now
  die rc=137 — the 528 MB upfront pool floor alone breaks the cap; 512/16k/r200 flips 3/10 → 23/24
  (pooled allocation is intrinsic backpressure: blocking allocs throttle ingest to the spiller's pace).
- **N-H3 confirmed** ✓ (table above) — with maps in the pool, pressure/rescue spills actually free
  buffers; the lever architecture works as designed.
- **N-H4 corridor NOT reopened** at 2048 — but the test was mis-specified: SOFT=CAP/10=204 MB exceeds
  the 16k/32k pools (132/264 MB), so the regular spiller engaged too late everywhere at 2048; deaths
  show resident=0, avail=0 after 106 pressure spills — everything spillable spilled, pool eaten by
  non-store demand (emit/in-flight) at uncapped ingest speed. **Re-test needs soft ≈ pool/3, not cap/10.**
- **N-H5 cost** ✓ at the money cell (tps parity); G5 infeasible under pooled (floor) — no clean read.
- **N-H6 anon floor = pool** ✓: successful cells' steady anon ≈ pool + base + unpooled + RocksDB
  (e.g. 264+124+78+RDB ≈ 517 measured); big pools now kill via rc=137 — pool sizing is THE knob, lean
  pools win both footprint and survival under the pooled architecture (no longer a tradeoff).

Open items from this run:
1. **soft-from-pool re-derivation** (the N3 follow-up): re-test 2048 cells with SOFT≈POOL/3 — the
   corridor question at 2048 is still open, now with a concrete mis-specification to fix.
2. **512/16k/r150 regression**: dies rc=141 with avail_buf=3371 > reserve at the last spiller wake —
   a sub-100ms burst kill between samples (or a second allocation path); needs finer sampling. The
   fast-ingest race (r150 ingests ~3.4M tps vs r200's self-throttled 2.5M) remains the suspect — the
   N4 max_inflight knob is the designed answer.
3. G5 cost cell must be re-sited to a pool-feasible point (1024/32k/r200).

## 8. FOLLOW-UP RUN (pre-registered 2026-06-11, runner `followup_n3n4.sh`) — N3 + N4 + cost re-site

All cells pooled (OPBUFSIZE=8192). SOFT/HARD now derive from POOL (pool_MB/3, 2×pool_MB/3), fixing the
§7 mis-specification. New env hook `MAXINFLIGHT` → `--worker.default_max_inflight_buffers` (default 64).

| id | hypothesis | decision rule |
|---|---|---|
| F-H1 | the N1 2048/r200 failures were spec error: soft=pool/3 converts them | B2 ≥6/8 at 16k or 32k ⇒ spec error; 0/8 ⇒ real closure (emit/in-flight demand at uncapped ingest) |
| F-H2 | the 2048 corridor reopens at r300 with correct soft | any B2 completions at 64k (soft=176) |
| F-H3 | N4 bounds the fast-ingest race: lower admission converts 512/16k/r150 | i32 or i16 ≥4/8 vs i64 control (expected 0/8, replicating §7) |
| F-H4 | OOC cost at a pool-feasible cost cell | 1024/32k/r200 B2 vs in-mem (10/10, 3.25M tps, 111 µs): tps within −10%, p99 ratio reported |

B0 ×4 at 2048/32k/r200 for attribution (backpressure alone vs levers under correct soft).
68 cells ≈ 45–75 min sequential.

### §8 RESULTS (2026-06-11, 68 cells, run 00:01–00:24) — all four hypotheses resolved, three NEGATIVE

**One-line: the soft-threshold spec was NOT the binding cause, max_inflight is NOT the governor, and the
2048 corridor stays closed — every failure shares one signature (store fully spilled, pool consumed by
the ingest→partition→emit pipeline at uncapped speed), which isolates N2 (bound the emit flood /
convert the 1000 ms allocation-timeout abort into sustained backpressure) as the one remaining lever.**

| id | verdict | evidence |
|---|---|---|
| F-H1 soft-from-pool | **REFUTED** | 2048/r200: 0/8 at 16k AND 32k with soft=pool/3 (B0 attribution arm also 0/4) |
| F-H2 corridor reopens | **NO** | 2048/64k/r300 0/8 rc141; 128k 0/8 rc137 (pool floor) — closed pending N2 |
| F-H3 max_inflight governs the race | **REFUTED** | 512/16k/r150: i64 0/8 (replicates §7 ✓), i32 0/8, i16 0/8; POOLG_MAX still 15884/16000 at i16 — the knob does not reduce pipeline pool draw |
| F-H4 cost at re-sited cell | **infeasible** | 1024/32k/r200 0/8 rc141 — no cost read; the flood kills it before steady state |

**The unified mechanism (every rc=141 in §7+§8):** at death `resident_mb≈0, avail_buf=0` after 110+
slices spilled and rescue fires — everything spillable IS spilled; the pool is full of non-store
buffers. The three-regime feasibility law of the pooled architecture:

1. **Fits**: pipeline + hot-band demand ≤ pool → completes at full ingest speed
   (1024/32k/r150: 7/8 at 3.38M tps).
2. **Throttled**: demand > pool but the cgroup cap is tight enough that memory pressure throttles
   ingest to the spiller's pace → completes (512/16k/r200: 23/24 at 2.5M tps — same workload that
   dies 0/8 at cap 1024 with DOUBLE the pool, dying at 3.2M tps).
3. **Flood**: demand > pool and ingest unconstrained → rc=141 regardless of soft spec, lever dose,
   or admission knob (all 2048 cells, 1024/32k/r200).

Regime 2 is the accidental governor: the cgroup, not our controller, couples ingest to drain. The
designed governor must live where the abort lives — `getBufferBlocking`'s 1000 ms timeout on the
emit/work paths. When the store is empty the rescue listener has nothing to free and the abort fires;
converting that abort into block-until-headroom (with progress guard) is exactly N2. Deadlock risk
note (from the design review): the wait must not block the thread that drains the emit queue.

Run discipline note: i64 control replicating §7's 0/8 confirms cross-run comparability.
