---
title: "OOC resident-memory finding — spill bounds the emit-lag band; the floor is per-window pinned pooled buffers, not the spill path"
date: 2026-06-07
tags: [thesis, nebulastream, ooc, rocksdb, RQ1, RQ3, finding, emit-lag, buffer-pool, resident-memory]
status: concrete-evidence; ready to elevate to written RQ1/RQ3 findings
supersedes_context: FINDINGS-watermark-pool.md (the rc=141 = N_w-gated pool result, here CONFIRMED by the pool sweep)
---

# OOC resident memory: what spilling buys, and what actually dominates the footprint

> **One-line:** With a *correct, lag-honoring* control, out-of-core spilling is the difference
> between **survival and OOM** on a memory-constrained device — it cuts the retained-window state
> ~5.5× and peak RSS ~23% at a fixed emit-lag. The *remaining* dominant cost is **not** the spill
> path (RocksDB RAM ≈ 11 MB, unpooled fragmentation ≈ 21 MB): it is **~300 pinned *pooled* buffers
> per retained window (~2.4 MB/window), scaling with `N_w = L/W`.** That per-window pooled
> bookkeeping is the real structural target, and it shrinks directly with larger windows.

## TL;DR for the thesis

1. **OOC works (RQ1/RQ3).** At a 1024 MB cap with `emit_lag` honored in both arms, in-memory
   **OOMs** and spill **survives**; spilling holds the retained-window state to 62 MB vs 343 MB
   (**5.5×**) and lowers peak RSS 1252→967 MB (**~23%**).
2. **The earlier "spill OOMs where in-memory survives" was a measurement artifact.** The in-memory
   store silently ignores `emit_lag`, so every spill-OFF cell ran a *different (no-lag) workload*
   that never built the retained band. Fixed by running both arms on the spillable store.
3. **The footprint is dominated by a per-retained-window POOLED cost (~2.4 MB/window), not the
   spill machinery.** RocksDB resident ≈ 11 MB (measured); unpooled allocator fragmentation ≈ 21 MB.
   Neither is worth optimising. The pooled per-window cost (~300 buffers/window, scales with `N_w`)
   is the real Option-B lever — and is mitigated for free by using larger windows.

---

## 1. Setup

Single-node Docker, i7-10700K, 4 worker threads pinned to dedicated physical-core pairs
(`--cpuset-cpus`), `--memory` cap = device budget. Synthetic lockstep workload (`key = ts =
SEQUENCE 0..TUPLES`, step 1): exactly **ρ = 1 distinct key per event-time unit**, so the retained
state is linear in lag and **window-size-independent** — letting us vary window `W` and emit-lag `L`
orthogonally. Void sink. Engine throughput ≈ 19 k keys/s (debug build), so run time ≈ TUPLES/19000.

Measured constant: **ρ·e ≈ 39 bytes/key**, so the retained-band state `S_backlog ≈ 39·L` bytes,
with `N_w = L/W` retained windows. See [[project-ooc-mechanics]].

Instrumentation added this phase (host-side, no JIT changes):
- `UnpooledChunksManager`: live chunk count / bytes / live-segment bytes (`UPOOL` log lines).
- `RocksDBSpillBackend`: resident-memory properties (`rss_memtable_bytes`, `rss_block_cache_bytes`,
  `rss_table_readers_bytes`, `rss_block_cache_pinned_bytes`).

## 2. The methodology correction (load-bearing)

`DefaultTimeBasedSliceStore` (the in-memory store, used when `spill.enabled=false`) has **no
`emit_lag` concept** — no constructor parameter, no code reference. The lag (`laggedEmitWatermark =
wm − L`) lives only in `SpillableTimeBasedSliceStore`. So:

- **spill-ON** holds the retained band `[wm−L, wm)` = `N_w` windows resident → ramps to a large
  footprint.
- **spill-OFF** emits and frees each window immediately → flat ~10 MB steady state.

Time-series proof (live page bytes over a run, from `UPOOL` lines): spill-OFF stays flat ~10 MB;
spill-ON ramps monotonically 6 → 75 MB **before the first spill event** — the footprint is the lag
band accruing during the build, not spill churn. Every prior "spill-OFF survives" cell was a
*no-lag* workload. **Correct control: run both arms on the spillable store; disable spilling in the
baseline by setting the soft/hard thresholds huge so the spiller never fires.**

## 3. Proven result — spilling is the fail/survive difference (correct control)

Both arms honor `emit_lag = 8M`; `W = 50 s`, `N_w = 160`, `TUPLES = 10M`, `pool = 64000`,
RocksDB wbuf16/cache8, P=4. "no-spill" = soft/hard thresholds = 100000 MB (spiller never fires).

| arm | cap | outcome | peak RSS | retained-band state |
|---|---:|---|---:|---:|
| **spill ON** | 1024 | **survives (rc=0)** | 958 MB | 62 MB |
| **no-spill (lag)** | 1024 | **OOM @ 344 s (rc=137)** | hit cap | — |
| spill ON | 1536 | survives | 967 MB | 62 MB |
| no-spill (lag) | 1536 | survives | **1252 MB** | 343 MB |

- **Headline:** at 1024 MB the lag-honored in-memory arm OOMs; spilling survives.
- **Quantified (cap=1536, both survive):** spill cuts retained-band state **343 → 62 MB (5.5×)** and
  peak RSS **1252 → 967 MB (~23%)**, pushing ~355 MB to RocksDB across 173 slices.
- No `rc=141` (pool sized right); no-spill fails at 1024 but survives 1536 → genuine *memory* OOM,
  not a pool artifact.

## 4. What actually dominates the 958 MB peak (measured decomposition)

| term | size | nature |
|---|---:|---|
| **pinned pooled buffers** (~300/retained-window × `N_w`) | **~384–512 MB** | **required floor; scales with `N_w`** |
| live retained-band state (unpooled HashMaps) | ~82 MB | the state we *want* resident |
| RocksDB resident (memtable + block cache) | **~11 MB (measured)** | negligible |
| unpooled allocator slack (fragmentation) | ~21 MB | not worth optimising |
| base process RSS (debug binary/runtime) + emission | remainder | fixed overhead |

### 4a. RocksDB RAM is ~11 MB, not the on-disk SST size
Measured via RocksDB properties at three pool sizes: memtable ~4 MB + block cache ~7 MB +
table-readers ~0.1 MB ≈ **11 MB** (matches the configured 16/8 MB budgets), ~1 % of peak.
`sst_footprint_bytes` (≈ 99 MB) is `rocksdb.total-sst-files-size` — **on disk**, not RAM. Shrinking
RocksDB's buffers cannot help the resident footprint; it was never the cost.

### 4b. The pool floor is fundamental (per-retained-window), not over-provisioned
Pool-minimization sweep (`W=50 s`, `N_w=160`, spill on, cap=1536):

| pool | 8 KB × pool | outcome |
|---|---:|---|
| 64000 | 512 MB | OK (peak 958) |
| 48000 | 384 MB | OK (prior `p1_Nw160_pool48k`) |
| 32000 | 256 MB | **rc=141** (pool exhausted) |
| 24000 | 192 MB | **rc=141** |

The minimum pool for `N_w=160` is ~48000 = **~300 pooled buffers per retained window (~2.4 MB/window)**,
matching the `POOL ≈ 300·N_w` rule from [[FINDINGS-watermark-pool]] (here confirmed). This is a
*required* per-window cost, not slack. The HashMap aggregation state is **unpooled**, so these ~300
pooled buffers/window are *something else* held per retained window — **identifying and reducing them
is the open Option-B question.**

### 4c. Free mitigation: larger windows
`N_w = L/W` and the band state (~82 MB) is `W`-independent. So for a fixed event-time lag `L`,
**larger windows lower `N_w` and shrink the pooled floor proportionally** (halving `N_w` halves the
~384 MB) without changing the retained state. Consistent with the earlier "large windows essential /
`W* ≈ 62 s`" result.

## 5. Optimisation options — status (with data)

- **Option 1 (free-at-emit): infeasible + wrong target.** The aggregation probe reads each slice's
  HashMaps by **raw, non-owning pointer asynchronously** off the task queue
  (`AggregationOperatorHandler.cpp:104-105,191`); freeing at trigger is a use-after-free. And the
  dominant resident is the *pre-emit* lag band, which free-at-emit cannot touch.
- **Option 2 (unpooled freelist / intra-chunk reuse): refuted (~2 %).** Max instantaneous
  chunk-vs-live gap over a full run = **21 MB** (1.23× slack); the allocator already performs well.
  Not worth a high-blast-radius change to shared memory infrastructure.
- **Option B (reduce per-window pinned *pooled* bookkeeping): the real lever** — but first needs an
  investigation into *what* those ~300 pooled buffers/window are.

## 6. Honest record of corrections (this investigation)

The root-cause framing was revised several times as evidence came in; logged for transparency:
1. "Spill has a ~180 MB fixed RocksDB overhead" → **wrong** (RocksDB RAM ≈ 11 MB; the 108 MB was disk).
2. "Overhead is unpooled chunk fragmentation (1.7×)" → **wrong denominator** (vs live = 1.23× ≈ 21 MB).
3. "Dose-response: more spills → more memory" → **superseded**; the footprint is the lag band built
   pre-spill, not spill churn.
4. "spill-OFF survives where spill-ON OOMs" → **confounded** (in-memory ignored `emit_lag`).
5. "The pool floor is just an over-provisioned config knob" → **reversed by the sweep**: ~300
   pooled buffers/window are genuinely required and scale with `N_w`.

The conclusions that **survived** all checks: OOC bounds the lag band (5.5×) and is the fail/survive
difference at a constrained cap; RocksDB RAM is negligible; the dominant cost is per-window pinned
pooled buffers scaling with `N_w`.

## 6b. Build-artifact note — the ~195 MB base RSS is a DEBUG bloat, not a real cost (2026-06-07)

The constant `base process RSS` term above (~195 MB, the "floor") is **dominated by `-O0` debug code**, not
by anything OOC- or workload-related. Evidence (`size`/`file` on the systest binary):

| segment | size | resident? |
|---|---:|---|
| `.text` (code) | **222 MB** | yes — touched pages fault into RSS |
| `.data`+`.bss` | ~4 MB | yes (negligible) |
| DWARF debug info | ~600 MB | **no** (non-loadable; only a debugger reads it) |
| on-disk total | 850 MB | — |

`USE_SANITIZER=none` (ruled out ASan). The cause is unoptimised, fully-templated, statically-linked code
(NES + RocksDB + deps) at `-O0`: no inlining/DCE, every template instantiation emitted. A large fraction of
the 222 MB `.text` becomes resident as the engine exercises the window/spill/RocksDB paths → ~195 MB floor.

**Methodological consequence (how to treat it):**
- It is **common-mode**: identical in the OOC arm and the in-mem baseline (same binary), so it **cancels in
  the OOC-vs-baseline *delta*** (throughput/latency/RSS overhead). It is NOT differential OOC overhead.
- It **does** distort the **absolute cap experiment** (RQ1): it shrinks the realistic state budget under the
  512 MB cap (~317 MB usable instead of ~430+ MB), so the OOM *operating point*, the *margin* (smooth skew run
  landing at exactly 512 MB), and the *bursty headroom* are debug artifacts — not device-representative.
- A **release** binary (`-O3 -DNDEBUG`) still has a *real, smaller* base RSS (optimised code + RocksDB +
  thread stacks + heap, est. ~40–80 MB) that legitimately counts. The fix is to **re-measure on release**, not
  to subtract the debug base.
- **What transfers from debug:** only the *qualitative mechanism* (OOC spills cold state and survives; lag-honored
  in-mem OOMs). All *quantitative* thresholds/margins and the bursty survive/die boundary require release.

This is logged as an additional measurement; treat all absolute-budget numbers here as debug-conservative.

## 7. Cells backing this (in `results/`)
- Correct control: `cln_spill_c1024`, `cln_nospill_c1024`, `cln_spill_c1536`, `cln_nospill_c1536`.
- Pool sweep + RocksDB RAM: `sweep_pool64k/32k/24k/16k`; `p1_Nw{40,80,160}_pool*`.
- emit-lag time series: `trace_off_L2M`, `trace_on_L2M` (NES `UPOOL` logs).
- Earlier confounded comparisons (kept for the record): `p2_*`, `ctrl_*`, `dec_*`, `recon_*`, `opt*`.
