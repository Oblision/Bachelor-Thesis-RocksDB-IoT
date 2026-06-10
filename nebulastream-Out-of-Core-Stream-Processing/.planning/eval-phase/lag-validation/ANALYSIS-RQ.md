---
title: "RQ analysis — full evidence base across cap ∈ {512, 1024, 2048} MB, pool ∈ {16k..128k}, ρ ∈ [0.45, ~3.1]"
date: 2026-06-10
tags: [thesis, nebulastream, ooc, RQ1, RQ2, RQ3, RQ5, analysis, significance]
status: analysis of ~700 cells (L_ matrix incl. corrected r500/r700 high-ρ cells; r1000 + unc partially pending)
---

# RQ-by-RQ analysis of the complete evaluation

Data: the 512 MB three-variant matrix (`combined3_dataset.csv`, ~330 runs), the 2D cap×pool matrix
(`results/L_*`, 630 cells + corrected r500/r700/r1000 cells), threshold sweep (`p8k_*`), memory
instrumentation (mem_trace, POOLGAUGE, UPOOLGAUGE, HMGAUGE), and the code-level root-cause analysis
(`OPTIONS-ooc-stability.md`). All configs: W=50k, L=4M (N_w=80), --cpus=2, pageSize=8192, lz4/wbuf16/cache8,
tmpfs-in → SD-spill-out, release build. ρ_meas = uncapped/in-mem steady anon ÷ cap.

## RQ1 — Does OOC prevent OOM beyond the memory budget (and where)?

**Verdict: yes, in a cap-dependent operating window; validated with p < 4×10⁻⁴ at two caps; output correct
(checksum MATCH while spilling).**

Completion where in-mem OOMs (best pool per cell, 10 reps):

| cap | ρ_meas | workload | in-mem | OOC bounded | Fisher (1-sided) |
|---:|---:|---|---|---|---|
| 512 | 1.44 | r150 | 0/10 OOM | **8/10** (pool=16k) | **p = 3.6×10⁻⁴** |
| 512 | 2.02 | r200 | 0/10 OOM | 3/10 (pool=16k) | p = 0.11 (n.s.) |
| 1024 | 1.54 | r300 | 0/10 OOM | **8/10** (pool=64k) | **p = 3.6×10⁻⁴** |
| 2048 | ~1.03 | r1000 | 0/3 OOM (pending) | 0/10 (all pools) | — (no win yet at 2048) |

Plus the original 512 MB flagship (8 reps, 3 ρ-points pooled): bounded-OOC 14/24 vs in-mem 0/18,
**Fisher p = 4.1×10⁻⁵**; checksum MATCH at ρ=1.56 while actively spilling.

**The operating window scales with cap** (lower edge ≈ where in-mem still fits, upper edge ≈ spill-drain ceiling):
512 MB → ρ ∈ [≈1.2, ≈2.2]; 1024 MB → up to ≥1.54 (r300, the largest tested that fits the envelope);
2048 MB → not yet demonstrated above ρ=1 (r700 at ρ=0.80 completes 10/10, r1000 at ρ≈1.03 fails — gap untested).

**Critical honesty caveat — ρ alone does not determine completion.** Old-generator r300 at cap=2048
(ρ≈0.74) fails at every pool, while new-generator r700 (ρ≈0.80) completes 10/10 at pool=16k. The decisive
difference is **ingest rate vs drain rate**: r300 ingests at 2.2–2.9 M tps (light maps), r700 self-throttles
to 1.14 M tps (heavy maps) — the spiller keeps up at 1.1 M but is outrun at 2.9 M. Completion is a race
between ingest and spill-drain, not a function of state size alone. (This directly motivates the Tier-1
backpressure lever 1b, which converts that race into a bounded stall — experiment queued.)

## RQ2 — At what cost?

**Verdict: 3–22% throughput, with the cost growing in state size and shrinking in headroom; p99 cost grows
sharply with state (μs → ms at 10⁷-key working sets).**

Cells where both arms succeed (means over ok reps):

| cap | workload | ρ | in-mem tps | OOC tps (pool) | Δtps | in-mem p99 | OOC p99 |
|---:|---|---:|---:|---|---:|---:|---:|
| 512 | r075 | 0.64 | 3.15M | 2.77M (16k) | **−12%** | 95 µs | 116 µs |
| 1024 | r100 | 0.45 | 3.14M | 2.85M (32k) | −9% | 96 µs | 120 µs |
| 1024 | r200 | 1.01 | 3.25M | 2.63M (64k) | −19% | 111 µs | 129 µs |
| 2048 | r200 | 0.50 | 3.25M | 2.92M (128k) | −11% | 109 µs | 126 µs |
| 2048 | r500 | 0.66 | 1.58M | 1.28M (32k) | **−19%** | 410 µs | 1852 µs |
| 2048 | r700 | 0.80 | 1.46M | 1.14M (16k) | **−22%** | 460 µs | 2509 µs |

(The historic 512 flagship measured ~3% for bounded at its sweet spot — the cost is config-point-dependent;
report the range, not one number.) The p99 blow-up at r500/r700 (×4–5 over in-mem) is the spill/unspill
round-trip surfacing in tail latency at large per-window cardinality — the honest price of the smaller
footprint below.

## RQ3 — Memory behavior: what does spilling actually buy, and why do failures happen?

**Verdict (new, from the corrected high-ρ cells): bounded-OOC now delivers a real resident-RAM reduction —
14–22% lower steady anon than in-mem on identical workloads — overturning the earlier "spilling frees the
logical store but not resident RAM" finding, which was an artifact of the unpooled variant.**

| cap=2048 | OOC anon (pool) | in-mem anon | OOC advantage |
|---|---|---:|---:|
| r500 | 1113 MB (32k) | 1358 MB | **−245 MB (−18%)** |
| r700 | 1282 MB (16k) | 1645 MB | **−363 MB (−22%)** |
| r700 | 1414 MB (32k) | 1645 MB | −231 MB (−14%) |

Mechanism (measured): pooled HashMap pages are recycled on spill (HashMap residual 620→210 MB at
1024/r200, a 3× reduction), while the OOC machinery adds back pool floor + RocksDB + transients. The net
is negative (a win) once state is large and the pool is sized lean. Corollary: **OOC's resident advantage
GROWS with leaner pools** — the opposite of the completion-maximizing choice; footprint and survival trade
off through the same knob.

**Failure anatomy (the 2D map's structure):** two independent constraints —
`peak_pool_demand ≤ pool` (else rc=141) and `pool_floor + unpooled + base ≤ cap` (else rc=137) — form a
feasibility corridor per (cap, ρ, workload-rate). The corridor narrows with ingest rate (see RQ1 caveat)
and pool size is a knife-edge within it (e.g. cap=2048/r700: 16k ✓, 32k ✓, 64k = 0/10 OOM — the floor).
Root cause at the code level (OPTIONS-ooc-stability.md): the spill controller regulates slice-store
data-page bytes while the contended resource is the shared pool; allocation failure aborts instead of
spilling; the steady-state trigger path floods the pool at watermark jumps (measured anon_flush spikes of
+326…+442 MB at r500/r700). Tier-1 levers (pool-pressure spiller, allocation rescue, paced flush) are
implemented on branch `ooc-stability-levers`; the A/B experiment is queued behind the current rerun.

**End-of-stream flood (1c's target), measured:** anon_flush − anon_steady = 326 MB (r500@32k), 444 MB
(r700@16k), 442 MB (r700@32k) vs in-mem's 270/365 MB — the OOC flush spike is the trigger-batch unspill
flood on top of the band emit that in-mem also pays.

## RQ5 (additional) — Footprint composition and reduction levers

From HMGAUGE (996 maps, 25.9 M entries, r150): **header 51.1%** (24 B/entry, 8 B of it a never-dispatched
vtable pointer), payload 34.1%, bucket directory 14.1%, page slack 0.7%. Documented options: vtable removal
(−20%/entry, measured, deferred — Nautilus scope), bucket-dir clamp via existing `max_number_of_buckets`
config (cheap, chain-cost ≈ nil per the bucket-tweak data), pool-sizing formula
`pool ≥ band + soft + emit-pipeline + I/O` with feasibility check `8.25 KB·pool + unpooled + base ≤ 0.9·cap`.
Negative results recorded: bucket-tweak sizing change (no effect — wrong call site), aggressive/lazy soft
thresholds at 512 (two-sided failure), pool=32k "fix" at ρ≈1 (wrong resource).

## Open items before write-up freeze
1. r1000 in-mem reps + remaining cells (rerun in flight) — pins the upper edge at cap=2048.
2. `L_unc_r500/700/1000` will likely fail rc=141 (bounded uncapped = unbounded pool demand); ρ-truth for
   the new files therefore comes from the in-mem@2048 footprints (r500: 1358, r700: 1645, r1000: >2048 MB)
   — methodological note for the thesis.
3. The lever experiment (H1–H6) — its result decides whether the RQ3 story ends at "diagnosed + designed"
   or "diagnosed + fixed + validated".
