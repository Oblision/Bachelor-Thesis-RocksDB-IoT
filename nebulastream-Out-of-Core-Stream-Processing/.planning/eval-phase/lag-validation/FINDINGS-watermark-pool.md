---
title: "OOC finding — spill watermark, buffer pool, and emit-lag are a coupled triple"
date: 2026-06-06
tags: [thesis, nebulastream, ooc, rocksdb, RQ1, finding, buffer-pool, emit-lag]
status: concrete-evidence (calibration); to be elevated to a written RQ1 finding
---

# Spill watermark, buffer pool, and emit-lag are a coupled triple

> **One-line:** `rc=141` (buffer-pool exhaustion) that we kept hitting is governed by the
> **number of retained windows** `N_w = L/W`, **not** by the spill watermark — and is fixed by
> sizing the buffer pool to the retained-window count. The OOC spill backend works; it was being
> pre-empted by pool starvation. This is an **RQ1 integration finding** (how OOC couples to
> NES's push-based execution model), to be written up once the complete run is in.

## Context

While building the lag-methodology validation we kept seeing OOC cells fail with `rc=141`
("Global buffer pool could not allocate buffer before timeout"). Before launching the boundary
validation (Group 2) we ran a focused calibration to isolate the cause. Single-node Docker,
synthetic lockstep workload (`key = ts = SEQUENCE`, so distinct-key rate `ρ = 1` key / event-unit,
independent of window size). Research-informed RocksDB config (lz4 / wbuf=64 / cache=32, P=4,
4 threads). Cells pinned to 2 dedicated physical cores.

## Group 1 — keyed-state characterization (cap=1024, vary L, W=50 000)

| L (event-units) | N_w = L/W | store_peak (keyed) | total peak | status |
|---|---|---|---|---|
| 1 M | 20 | 46 MB | 272 MB | ok |
| 2 M | 40 | 86 MB | 348 MB | ok |
| 3 M | 60 | 125 MB | 426 MB | ok |
| 4 M | 80 | 164 MB | 615 MB | err (rc=141) |

- **Keyed state is linear in lag:** `store_peak ≈ 39·(L/1M) + 7` MB → **`ρ·e ≈ 39 bytes/key`** (slope dead-constant: 40, 39, 39). Confirms `S_backlog = ρ·e·L`, window-size-independent.
- **Total resident** grows ~**77 MB per 1M keys**, intercept **`floor ≈ 195 MB`**. So total ≈ `2×` the keyed state per retained key — the extra is pool + in-flight + RocksDB working set.

## Calibration — soft/hard watermark × buffer pool, at fixed L=4M (N_w=80), cap=1024

| soft (MB) | POOL=16000 | POOL=32000 | POOL=64000 |
|---|---|---|---|
| **4** (spill aggressively) | rc=141 · 99 slices · keyed 9 MB | **ok** · 99 slices | **ok** |
| **96** | rc=141 · 54 slices · keyed 97 MB | **ok** · 54 slices | ok (expected) |
| **182** (H4) | rc=141 · 0 slices · keyed 164 MB | **ok** · 0 slices | ok (expected) |

*(POOL=64000 / soft∈{96,182} confirmatory cells finishing; soft=4/64000 already ok.)*

## What this shows

1. **The watermark does not cause `rc=141`.** At `soft=4` the spiller is working hard — 99 slices
   spilled, keyed state held to **9 MB** — yet the cell still dies `rc=141` at POOL=16000. Lowering
   the watermark (more spilling) does not help.

2. **The buffer pool is the sole discriminator.** Every watermark fails at POOL=16000 and every
   watermark survives at POOL=32000. Perfect separation by pool size.

3. **The pool pressure scales with the retained-window count, not the keyed state.** With keyed
   state spilled to 9 MB, total resident is still ~513 MB at `N_w=80`. The ~300 MB above `floor`
   is **per-retained-window resident overhead that spilling the hashmap does NOT reclaim**
   (window/slice-store buffers held from the pool). Estimated **~200–400 pool buffers per retained
   window** (16000/80 fails, 32000/80 works); ≈ 3.7 MB/window.

## The model (coupled triple)

```
keyed state        S_backlog = ρ·e·L            (ρe ≈ 39 B/key)          — set by lag, W-independent
retained windows   N_w       = L / W                                     — smaller for larger W
pool requirement   POOL      ≳ k · N_w  = k · L/W   (k ≈ 200–400 buffers/window)
total resident     ≈ floor(≈195 MB) + ~77 B/key · ρL + pool(N_w)
```

Two independent failure modes, two independent fixes:
- **`rc=141` (pool exhaustion)** ⇐ `N_w` too high for `POOL` ⇒ fix by **`POOL ∝ L/W`** (or larger W, or bounded L).
- **`rc=137` (OS OOM)** ⇐ keyed state + floor > cap with spill off ⇒ fix by **spill on** (the OOC value proposition, validated separately in Group 2).

**Window-size corollary (matches the a-priori intuition):** larger `W` ⇒ fewer retained windows
⇒ less pool pressure. Small windows need a proportionally larger pool.

## Why the 10k synthetic phase "worked" and the realistic phase didn't

The 10k phase used `soft=4/hard=8` *and* small `N_w` (100 s windows → few windows). The realistic
(Nexmark) phase used 10 s windows → `N_w` up to thousands at scale, with `POOL=16000` fixed → pool
starvation everywhere. The fix is `POOL ∝ N_w`, not a workload or watermark change.

## RQ1 framing (to write up)

This is an **integration property of OOC in NES's push-based model**: the spill subsystem cannot
relieve memory pressure that is held in the *buffer pool* by retained-window bookkeeping; therefore
a correct OOC deployment must size the global buffer pool to the retained-window count
(`POOL ∝ L/W`), in addition to setting spill watermarks. Watermark and pool are not independent
knobs. Keep the calibration grid above as the evidence.

## Implications for the complete run

- Set **`POOL` as a function of `N_w = L/W`** (≥ ~400 buffers/window with margin), not a fixed 16000.
- Then validate `L*` (spill-OFF OOM / spill-ON survive) on a pool-correct config — **Group 2, next.**

## Related
- [[project-thesis-scope-anchor]] — RQ priorities, synthetic-first scope
- [[project-ooc-err-policy]] — earlier rc=141 boundary observation (now explained here)
- emit-decouple design: `../DESIGN-OOC-EMIT-DECOUPLE.md`
