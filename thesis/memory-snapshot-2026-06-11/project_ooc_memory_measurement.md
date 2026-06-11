---
name: project-ooc-memory-measurement
description: RIGOROUS measured memory breakdown (anon working set vs reclaimable cache) showing OOC spilling does NOT reduce resident RAM on this build — the unpooled allocator retains spilled chunks. Decisive RQ1/RQ5 finding, 2026-06-08.
metadata:
  node_type: memory
  type: project
  originSessionId: 8037b78d-3cfd-4e00-818d-30ecb34b0eeb
---

The user (2026-06-08) demanded accurate, DEFINED per-component memory measurement because the reported numbers "came and go." Built it into run-cell.sh (cgroup v2 `memory.stat` sampling at 0.2s → peak **anon** [non-reclaimable working set] vs peak **file** [reclaimable page cache]; plus exact pool bytes and RocksDB resident properties). See [[project-release-eval-setup]].

**MEASURED breakdown (release, --cpus=2, pool=26000, emit_lag-scaled el workload, logical state 232MB, UNCAPPED so nothing OOMs). Numbers in MB anon = the real OOM driver:**

| component | base | in-mem (spill off) | OOC (spill on) |
|---|---:|---:|---:|
| code/runtime | 38 | 38 | 38 |
| buffer pool (26k×8KB) | 203 | 203 | 203 |
| HashMap state (unpooled, RESIDENT) | ~0 | **432** | **396** |
| RocksDB resident | 0 | 0 | 19 |
| **anon working set (total)** | **242** | **673** | **656** |
| reclaimable file cache (SST/input) | 2 | 0 | 114 |
| logical slice-store (accounting) | 1 | 232 | 60 |

**🔑 DECISIVE FINDING — OOC does NOT reduce the resident working set on this build.**
- Spilling drops the LOGICAL store 232→60 MB (looks like −74%), but the RESIDENT HashMap anon only drops 432→396 MB (**−36 MB, 8%**). The spilled state's RAM is **not returned to the OS** — the `UnpooledChunksManager` frees a chunk only when ALL its segments drain, so spilled-but-mixed chunks stay resident. Now DIRECTLY MEASURED (anon), not inferred.
- Net: OOC working set **656 MB ≈ in-mem 673 MB**. OOC even ADDS RocksDB (19) + reclaimable cache (114). ⇒ at any memory cap OOC and in-mem OOM at ~the same point ⇒ **no survival contrast is achievable until chunk-retention is fixed.** This explains all the teetering/OOMs at 512.
- The HashMap unpooled anon is ~1.85× the logical store (432 vs 232) — allocator chunking/bucket-dir overhead. The "unaccounted ~227MB" from earlier was this.
- The buffer **pool alone is 203 MB** (fixed, both arms) — ~40% of a 512MB budget before any state.

**Methodology fix (why numbers were fuzzy before):** total `memory.peak` includes reclaimable SST page cache (114 MB for OOC); the valid metric is **anon working set**. Always report anon, separate file cache.

**Implication / open decision (2026-06-08):** a real OOC memory win requires addressing the unpooled-chunk-retention (return spilled chunks to the OS) — the "Option B" engine change previously deferred. Without it, OOC on this build spills to disk but keeps the RAM. This is itself a publishable RQ1/RQ5 result (honest negative finding about the integration). See [[project-ooc-mechanics]], [[project-thesis-scope-anchor]].

**POOL right-sizing (measured 2026-06-08, pool sweep both arms x3 reps, results/pool_sweep_points.csv):**
- Memory is PERFECTLY LINEAR in pool size: anon = ~1.04·pool_MB + ~450 (R²≈1.0) — the pool is 100% resident, so every pool-MB is a pool-MB stolen from state. Intercept (~440-466MB) = base+state.
- Throughput is ~FLAT vs pool (slightly decreasing) for this workload — it only uses ~3-6MB of pool. So a small pool costs ~nothing in throughput and saves memory 1:1. (My earlier "throughput rises with pool" was contaminated by failed-run tps in the capped sweep.)
- Floor: pool ≤ ~500 buffers (≤4MB) → rc141 exhaustion (both arms). cap=512 feasibility: in-mem OOMs at every pool (band too big); OOC survives ONLY near pool≈16000 (125MB) — above that the pool itself eats the budget. So pool≈16000 is the 512-appropriate size; the earlier "512 infeasible" was the oversized pool=26000.

**🏁 FLAGSHIP COMPARISON RESULT (2026-06-08, `FINDINGS-flagship-comparison.md`, ~120 runs, scipy stats):**
Well-config OOC vs in-mem @512 (pool=16000, W=50k, cpus=2), rho sweep. **OOC prevents OOM in a WINDOW, not universally:** rho~1.5-2.2 → in-mem OOMs, OOC completes 6-7/8 with CORRECT output (checksum MATCH @rho1.56), ~15% tput / ~20% p99 cost. rho~1 → OOC is COUNTERPRODUCTIVE (in-mem fits 6/6, OOC OOMs 8/8 — overhead>benefit). rho~3.5 → both OOM (OOC envelope). Pooled rho>=1: OOC 13/24 vs in-mem 0/18, Fisher p=1.16e-4 (significant). Failures are OOM not crashes (1 rc134/40). ROOT CAUSE of narrow window = chunk-retention (spill frees logical store, not resident RAM ⇒ OOC footprint≈in-mem+overhead). SCOPE: holds only for this config; qualitative claim transfers, thresholds don't. Improvement path: fixing chunk-retention is in nes-memory (UnpooledChunksManager) — NON-Nautilus — and is the biggest lever; only the 24B per-entry header needs Nautilus; the JIT/codegen pipeline (the actual proposal exclusion) is needed for NONE of it.

**BOUNDED (pooled-pages) comparison (2026-06-08, branch ooc-pooled-bounded, same matrix b_ labels, page_size=8192, `results/combined_dataset.csv`):** Bounding the HashMap DATA pages (rent from the fixed pool, recycled on spill) — vs unpooled OOC: **did NOT widen the window** (same rho~1.5-2.2; Fisher bounded-vs-unpooled completion p=1.0; bounded 14/24 vs unpooled 13/24 @rho>=1) BUT **slashed throughput overhead ~15%→~3%, lowered p99, and fixed the teetering (8/8 @rho0.69 vs 4/8; 8/8 @rho2.21 vs 7/8).** vs in-mem still significant (p=4.1e-5). ⇒ data-page chunk-retention was NOT the binding CAPACITY limit; the window is capped by the still-unpooled BUCKET DIRECTORIES (Nautilus struct) + fixed pool floor + un-spillable hot window. So widening the window needs Nautilus (bucket-dir/entry-header); the non-Nautilus page-bounding only buys cost+reliability. JIT/codegen pipeline needed for none of it.

**BUCKET-TWEAK (branch ooc-bucket-tweak, numberOfBuckets=avg/4) — NEGATIVE (2026-06-09):** Tried to shrink the ~30% bucket directory. Failed: bucket_dir stayed ~34% because ~2/3 of maps (per-worker build / unspill, buckets 32768/65536) bypass the one-line change in getCreateNewSlicesFunction (only slice-create maps got avg/4 → 16384/8192/4096). Completion 10/24 vs bounded 14/24 @rho>=1, Fisher bt-vs-bounded p=0.31-1.0 (NOT significant); footprint unchanged. ⇒ real dir reduction needs sizing at ALL call sites (still non-Nautilus, more invasive); and per page-pooling evidence it'd cut footprint not the window. **bounded (pooled pages, on main) remains the best variant; bucket-tweak NOT merged.**

**🔬 HASHMAP COMPOSITION — MEASURED via HMGAUGE (per-map destructor log: entries/entrySize/hdr/buckets/entriesPerPage/pageSize), 2026-06-08:**
Of the resident ChainedHashMap memory: **header=41%, key+value(data)=27%, bucket_dir=30%, page_slack=2%.** Only ~27% is real data; ~73% is overhead.
- entrySize=40B, header=**24B** (next-ptr 8 + hash 8 + 8B vptr because ChainedHashMapEntry is polymorphic) vs 16B key+value ⇒ entry is 2.5× the data; header alone > data.
- bucket_dir 30% = fixed ~128KB/map (numberOfBuckets clamped ≤10000→16384) × MANY per-window maps (309 maps for the el run), many holding few entries ⇒ fewer/larger windows (W*) shrink this directly.
- Levers: make ChainedHashMapEntry non-polymorphic (−8B/entry), right-size bucket clamp per window, larger windows. The unpooled chunk-allocator ×1.4 sits on top. Instrumentation: HMGAUGE in ChainedHashMap dtor (throttled 1/4, read-only); POOLGAUGE (BufferManager) + UPOOLGAUGE (UnpooledChunksManager) for pool-used + unpooled-live over time. All on `main`; pooled-state experiment isolated on branch `ooc-pooled-state`.
