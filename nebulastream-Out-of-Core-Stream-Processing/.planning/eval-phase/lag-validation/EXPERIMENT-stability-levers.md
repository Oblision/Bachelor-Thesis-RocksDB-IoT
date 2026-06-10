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

## 6. Interpretation guardrails
- If A0-regression deviates from historic L_ numbers → STOP, the gating leaks; fix before interpreting A1/A2.
- H3 negative (G1 stays 0) is a FINDING, not a failure: it pins the corridor on the floor side and promotes
  the Tier-0 maxBuckets probe + Tier-2 (pool the bucket dirs) as the remaining path.
- Watch rescue_spills at G5: if >0 there, the reserve is mis-sized (it should never fire when A0 succeeds
  comfortably) — re-run G5 with RESERVEPCT=5 before concluding H4.
