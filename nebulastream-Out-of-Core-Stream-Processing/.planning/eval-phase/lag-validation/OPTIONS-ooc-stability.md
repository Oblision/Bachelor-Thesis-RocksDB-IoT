---
title: "OOC stability levers — code analysis of the pool/spill resource model and ranked improvement options"
date: 2026-06-10
tags: [thesis, nebulastream, ooc, stability, buffer-pool, spill, RQ3, options, code-analysis]
status: analysis complete; options ranked, none implemented yet
---

# Why bounded-OOC is unstable where in-mem is not — and the levers that fix it

> **One-line:** The spill controller defends the wrong resource. It regulates a *logical byte count of
> slice-store data pages* (`residentBytes()`), while the actually-contended resource is the **fixed,
> eagerly-allocated buffer pool** that data pages, unspill floods, probe accumulators, and engine I/O all
> share — and the allocator's only response to pool exhaustion is a 1-second timeout followed by query
> abort (rc=141). In-mem is stable because its only limit is the cgroup cap; bounded-OOC has **two
> additional internal failure surfaces** (pool exhaustion, unbounded emit floods) that no config knob can
> simultaneously remove — but three small host-side changes can.

## 1. The measured instability this explains (large 2D matrix, 630 cells)

| cap | pool sweet spot | what kills the neighbors |
|---|---|---|
| 512 | 16k for ρ≥1.4, 32k for ρ<1 | bigger pool → OOM (pool floor eats cap); smaller → rc=141 |
| 1024 | 64k for ρ≥1, 32k for ρ<0.5 | same corridor, shifted |
| 2048 | 128k for ρ=0.5; **nothing works at ρ=0.77** | 16k–64k → rc=141; 128k → OOM. **Corridor is empty.** |

In-mem completes 10/10 at every (cap, ρ) with ρ<1 — no internal failure surface, only the cap.

## 2. The resource model (from code, with anchors)

### 2.1 Where every byte lives

| component | allocator | elastic? | spillable? | counted by spill controller? |
|---|---|---|---|---|
| HashMap **data pages** (40 B/entry incl. 24 B header) | **global pool** (`getBufferBlocking`, `ChainedHashMap.cpp:203`) when `pageSize <= bufferSize`, else unpooled | NO — fixed pool | YES (cold slices) | YES (only these) |
| HashMap **bucket directory** (`(chains+1)×8 B`) | unpooled chunks (`ChainedHashMap.cpp:181`) | yes | NO | **NO** |
| Probe **final accumulator** (clone per triggered window) | pool (same `insertEntry` path) | NO | NO (emitted) | **NO** |
| Unspilled-for-emit maps awaiting probe (chunkRegistry) | pool | NO | NO (emitted) | **NO** |
| Spill serialization blobs (`SpillRecord = std::vector<std::byte>`) | malloc, transient | yes | n/a | NO |
| Nautilus Arena / var-sized scratch | unpooled chunks | yes | NO | NO |
| RocksDB (memtable+cache, ~11–22 MB measured) | malloc | bounded by config | n/a | NO |
| Buffer pool itself | **one upfront allocation** (`BufferManager::initialize`, `nes-memory/BufferManager.cpp`) | NO — touched pages stay resident forever | n/a | n/a |

**Key structural facts:**
1. `BufferManager::initialize()` allocates the whole pool as ONE region; control blocks are written at
   startup and every rented buffer page stays resident after first touch. The pool is a **permanent
   resident floor ≈ its high-water mark** (in practice ≈ full size).
2. The pooled-vs-unpooled decision for HashMap data pages is `pageSize <= bufferProvider->getBufferSize()`
   (`ChainedHashMap.cpp:201`). At `page_size=8192` = engine buffer size, ALL ChainedHashMaps — slice maps,
   unspill-rematerialized maps, probe accumulators — draw 8 KB pool buffers. (This is the bounded variant's
   mechanism; it also explains the pageSize=1024 sweep disaster: each 1 KB page burned a full 8 KB buffer.)
3. `getBufferBlocking()` waits at most `GET_BUFFER_TIMEOUT = 1000 ms` (`nes-memory/TupleBufferImpl.hpp:49`)
   then **throws** → query fails rc=141. There is **no hook from allocation pressure to the spiller** — the
   two subsystems share one resource but never communicate.

### 2.2 The spill controller's blind spots

`SpillableTimeBasedSliceStore::residentBytes()` (SpillableTimeBasedSliceStore.cpp:912) sums
`ceil(tuples/entriesPerPage) × pageSize` over **slice-store maps only**. It does not see:
- bucket directories (~12–15% extra),
- the unspill-for-emit re-materialization (deserialize → `insertEntry` → pool),
- probe accumulator clones (a second full copy of every triggered window, in the pool),
- chunkRegistry maps held until the async probe drains them,
- pool availability itself.

The spiller (`spillerLoop`) wakes on: slice creation over soft, watermark advance, 100 ms poll. It drains
**to soft** and can only evict **cold, un-emitted, un-pinned** slices.

### 2.3 Anatomy of each failure mode

**rc=141 (pool exhaustion) — the dominant high-cap killer.**
Pool demand = open-window band (~L/W × workers maps) + cold-not-yet-spilled + unspilled-emitted windows
awaiting the probe + probe accumulators + source/emit I/O. Three ways demand outruns the spiller:
1. *Trigger flood:* `getTriggerableWindowSlices` → `unspillAndMarkEmitted(result)` re-materializes **every
   triggerable window in the batch at once** (SpillableTimeBasedSliceStore.cpp:239–250). When the file
   source ends, the watermark jumps to max and the whole retained band (≈80 windows) becomes triggerable in
   ONE call — a mass unspill into the pool through the *steady-state* path. (The EOS terminal path was
   already fixed: `WindowBasedOperatorHandler::triggerAllWindows` paces per-window, and the P>1 partitioned
   path streams 1/P at a time. The steady path got no such fix — and our `anon_flush` spikes, e.g.
   993→1240 MB at cap=2048/64k/r200, are this flood.)
2. *Emitted pile-up:* emitted windows are un-spillable by design (probe holds raw pointers). If the probe
   (2 worker threads, shared with build) drains slower than windows trigger, pooled maps accumulate with no
   backpressure and no spill recourse.
3. *Build stall:* with the pool near-empty, the build path's `getBufferBlocking()` has 1000 ms to find a
   buffer. The spiller might free some (spill returns pages to the pool: `hashMaps[pos].reset()`,
   SpillableAggregationSlice.cpp:180) — but it only runs if `residentBytes() > soft`, which can be FALSE
   while the pool is empty (the pool is consumed by maps it doesn't count).

**rc=137 (OOM) — the dominant low-cap / big-pool killer.**
anon ≈ pool-resident-floor + unpooled (dirs + Arena + chunk-retention slack) + malloc (RocksDB, SpillRecord
transients) + base RSS. The pool floor is paid even when mostly free. At cap=2048/r300 with pool=128k:
1 GB floor + ~0.66 GB unpooled + base + transients > 2 GB → OOM despite the pool never exhausting.

**The corridor.** A run succeeds iff
`peak_pool_demand ≤ pool_size` **and** `pool_floor + unpooled + base + transients ≤ cap`.
Demand grows with ρ; the floor grows with pool size. At cap=2048/ρ=0.77 the two constraints cross — no pool
size satisfies both → the measured 0/10 at every pool. **This is not a tuning problem; the corridor is
structurally empty.** The levers below widen it from both sides.

## 3. Why in-mem is stable until ρ>1 (the asymmetry, precisely)

1. In-mem never unspills, never clones accumulator floods at end-of-stream gradually (windows trigger as
   the watermark passes them — `DefaultTimeBasedSliceStore` ignores `emit_lag`, so there is no retained
   band to flush at once).
2. Its pool demand is just open windows + I/O — smooth in time, so a fitting pool keeps fitting.
3. Its only failure surface is the cap itself: state > cap → OOM. One constraint, one knob.

Bounded-OOC adds the retained band (+L/W windows), the unspill/emit double-residency, and the fixed-pool
ceiling — three surfaces, coupled through one pool, regulated by a controller that sees none of them.

## 4. Options, simple → complex

### Tier 0 — config only (in-scope, zero code, runnable today)

| # | change | mechanism | expected effect | risk |
|---|---|---|---|---|
| 0a | **Derive pool from a demand formula, per (cap, ρ)** — `pool ≥ openband_pages + soft/8KB + K_emit×window_pages + IO_margin`; check `8.25KB×pool + unpooled_est + base ≤ 0.9×cap` | size for peak demand, verify floor fits | turns the knife-edge into an explicit feasibility check; documents WHEN no pool exists | none — it's a sizing rule + the formula is a thesis contribution |
| 0b | **Lower `max_number_of_buckets` below the 10k default** (already plumbed: `MAXBUCKETS` arg in run-cell.sh → `--worker.default_query_execution.max_number_of_buckets`) | dirs are `calcCapacity(clamp(avgKeys,1,max))` → 16384 chains = 128 KB/map today; 2048 → 16 KB/map | shrinks un-spillable unpooled memory; bucket-tweak data says chain-walk cost is small (p=0.31–1.0) | longer chains at high cardinality; measure tput |
| 0c | **Reduce `emit_lag` or enlarge `W`** (fewer retained windows: N_w = L/W) | smaller band → less pool demand AND smaller flush flood | direct, already-proven lever (W* finding) | changes the experiment's semantics — only valid where the use-case allows |

Tier 0 prediction: 0a+0b should rescue cap=2048/r300 **if** the corridor is merely narrow (floor side).
If it stays 0/10, the demand side (floods) is binding → Tier 1 is required. Either result is a finding.

### Tier 1 — host-side code, non-Nautilus (~10–60 lines each; the actual lever)

| # | change | where | effort | what it fixes |
|---|---|---|---|---|
| **1a** | **Pool-pressure spill trigger:** wake + drain the spiller when `availableBuffers < reserve` (e.g. 15% of pool), independent of `residentBytes()` | expose `getNumberOfAvailableBuffers()` via `AbstractBufferProvider` (1-line virtual; BufferManager already implements it for POOLGAUGE); add the condition in `spillerLoop` + the `getSlicesOrCreate` check (SpillableTimeBasedSliceStore.cpp:213-224) | ~20 lines | the controller finally defends the contended resource; rc=141 from slow-leak exhaustion disappears; soft/hard stop being knife-edge (they become anon-side tuning, not survival) |
| **1b** | **Allocation-pressure backpressure instead of abort:** before `getBufferBlocking` would time out on the HashMap-page path, synchronously evict (`evictUntilUnderHard`-style, but pool-target) and retry | wrap the provider handed to the build/unspill paths in a `SpillingBufferProvider` decorator (host class implementing `AbstractBufferProvider`) that on empty-pool calls `store->spillForPoolPressure()` then retries; no JIT change — the JIT already calls through `AbstractBufferProvider*` | ~60 lines | converts the 1 s-then-die cliff into bounded backpressure (the DB-engine standard: allocation pressure ⇒ eviction); kills rc=141 class entirely when cold state exists |
| **1c** | **Bound the emit pipeline:** cap in-flight emitted windows at K (semaphore acquired before `unspillAndMarkEmitted`'s per-window unspill, released in `releaseChunk`); process the trigger batch through the SAME per-window paced loop the terminal path already uses | `SpillableTimeBasedSliceStore::unspillAndMarkEmitted` + `AggregationOperatorHandler::releaseChunk` | ~40 lines | the end-of-watermark band flood (the `anon_flush` spike) becomes a paced drain; emitted pile-up bounded at K×window instead of unbounded |
| 1d | **Count dirs in the budget:** add `(numberOfChains+1)×8` to `residentBytesOf` | SpillableTimeBasedSliceStore.cpp:891 | 3 lines | soft/hard correspond ~15% closer to reality; cheap accuracy win |

**Recommended bundle: 1a + 1b + 1c.** They attack the three failure mechanisms separately (slow leak,
acute stall, flood) and compose: 1a keeps a reserve, 1b makes breaching the reserve survivable, 1c stops
the one event that overwhelms both. With these, pool size stops being a survival parameter (anything
comfortably above the open band works) — which is precisely the "in-mem-like stability" target: one
constraint (the cap), one knob.

### Tier 2 — small Nautilus-adjacent changes (the bounded variant already lives here)

| # | change | effect | note |
|---|---|---|---|
| 2a | **Pool the bucket directory too** when it fits a pool buffer (post-0b a dir is ≤16 KB... at 2048 chains it's 16 KB = 2 buffers; at 1024 chains 8 KB = 1 buffer) | unifies ~all map memory under the pool → the pool-pressure controller (1a/1b) governs everything; unpooled shrinks toward Arena+slack | same file/idiom as the existing bounded change (`ChainedHashMap.cpp:176-192`); precedent set |
| 2b | **Per-(cap,ρ)-free `numberOfBuckets` from live stats** — feed `rollingAverageNumberOfKeys` into dir sizing more aggressively (smaller initial dirs, grow only on chain-length pain) | removes the power-of-2 jump (calcCapacity doubles at thresholds) | moderate; measure chain-walk p99 |

### Tier 3 — out-of-scope for the thesis, documented as future work (cite the measurements)

| # | change | measured upside |
|---|---|---|
| 3a | Remove the never-dispatched virtual dtor on `AbstractHashMapEntry` → 24 B → 16 B header | −20%/entry; header is 51% of HashMap bytes at r150 (FINDINGS-hashmap-entry-vtable) |
| 3b | Spill-aware probe (drain spilled windows partition-streamed on the steady path, not just terminal) | removes the unspill round-trip entirely; the 2d machinery (`streamWindowPartition`) already exists for EOS |
| 3c | Return-to-OS for unpooled chunks / arena trimming | reclaims retention slack counted in UPOOLGAUGE |
| 3d | Elastic pool (grow/shrink between floor and cap) | dissolves the floor-vs-ceiling corridor at the root |

## 5. Suggested validation sequence (cheap → decisive)

1. **Tier 0 probe (config-only, ~1 h):** cap=2048 × r300 × pool∈{64k,96k} × maxBuckets∈{default,2048} ×
   8 reps. If maxBuckets=2048 rescues r300 → unpooled floor was binding; report as config finding.
2. **Implement 1a (+1d) first** (smallest), rerun the cap=2048 column: prediction — rc=141 at 16k/32k
   becomes either success or *graceful* soft-limited throughput, not abort.
3. **Add 1c**, rerun r200/r300 at all caps: prediction — `anon_flush` spike collapses to ≈ steady + K
   windows; the 512/32k OOMs (flush-driven) flip to success.
4. **Add 1b** only if acute stalls remain (it's the most invasive of the three).
5. Final stability matrix (the thesis artifact): bounded+levers vs in-mem across cap×ρ — the claim becomes
   "with a pool-pressure-coupled spiller, OOC matches in-mem stability for ρ<1 and extends the operating
   window to ρ≈2 at every cap", which is exactly the RQ3 story.

## 7. DESIGN REVIEW 2026-06-10 (post-lever-experiment) — a load-bearing discovery

**The "bounded pooled-pages" mechanism never engaged in ANY experiment of this evaluation.**

Evidence chain:
1. `BufferManager::create(workerConfiguration.defaultQueryExecution.operatorBufferSize, …)`
   (NodeEngineBuilder.cpp:38) with `DEFAULT_OPERATOR_BUFFER_SIZE = 4096` — never overridden by run-cell.sh.
   **The engine's pool buffers are 4 KB**, not the assumed 8 KB.
2. `ChainedHashMap::insertEntry` pools pages only when `pageSize <= bufferSize`; our runs use
   `page_size=8192 > 4096` → **every HashMap page (slice maps, probe accumulators, unspill
   re-materialization) was allocated UNPOOLED**, in all L_ cells, threshold sweeps, and the lever matrix.
3. Measured proof: in `S_A1_c1024_p16k_r150_r1`, the pool sits at 15933/16000 free for the whole build
   while slice-store resident grows to 110–213 MB and UPOOLG_MAX=364 MB. The maps live on the unpooled heap.

**Consequences (errata):**
- All pool-size labels are 2× too high: pool=16000 = **62.5 MB**, 64k = 256 MB, 128k = 512 MB.
- The pageSize=1024 sweep disaster was 4× (not 8×) buffer waste — and it was the only configuration in the
  whole eval where the pooled branch actually ran.
- The bounded-vs-unpooled 512 comparison measured "8 KB unpooled pages + new spill code" vs "old code" —
  its benefits (lower anon, stability, throughput) are real but attributable to allocation granularity and
  code differences, NOT page recycling through the pool.
- **The lever experiment's "un-spillable hot band floor" conclusion is retracted.** The pool the levers
  defended contains NO map state, so rescue spills could not free pool buffers *by construction*. The
  smoking-gun cell (`resident=9 MB, avail=0`) reads correctly as: state fully spilled (anon side healthy),
  **I/O pool exhausted by the emit/ingest pipeline** — an independent failure axis the levers never touched.
  The marginal c512/r200 improvement is likely noise (p=0.077, n=8).
- The clean part that SURVIVES: the rc-taxonomy now means `rc=141 = I/O-pipeline flood` vs
  `rc=137 = state overflow (anon)`. The completion windows, Fisher results, cost numbers, and the
  anon-contrast win at cap=2048 are all unaffected (they never depended on the pooling mechanism).

**Revised non-JIT lever list (supersedes Tiers above where they conflict):**
| # | change | kind | note |
|---|---|---|---|
| N1 | `operator_buffer_size=8192` (one CLI flag) | config | **Engages the intended maps-in-pool architecture for the first time.** Re-running the lever matrix on top makes 1a/1b live for real (spills then DO free pool buffers). A/B maps-in-pool vs maps-on-heap = thesis-grade architecture comparison, ~1 flag + the existing harness |
| N2 | bound the emit/output flood (cap un-drained chunks; bounded wait in emitChunk) | host code (~30 lines) | now clearly aimed at the REAL rc=141 killer: probe-output/trigger bursts through the unbounded task queue |
| N3 | pool sizing per role | config | with maps on the heap, the pool is I/O-only: size it for queue depth (the 2048 cells wanting 128k=512 MB were buying QUEUE space, not state space) |
| N4 | `default_max_inflight_buffers` (=64) sweep | config | source-side admission exists; verify semantics (alloc-to-release?) and whether the ingest-rate sensitivity (r300 vs r700) responds to it |
| N5 | vtable removal (24→16 B header) | header + rebuild, no JIT-pipeline edit | anon-side −20%: helps the rc=137 class; JIT re-traces sizeof at query compile |
| N6 | W-sweep at fixed L | config | causal test of the N_w model on the anon side |

## 6. Scope note

1a/1b/1c/1d touch `nes-physical-operators` (slice store, handler) and one virtual on
`AbstractBufferProvider` — host-side runtime, no JIT/codegen, consistent with the established "non-Nautilus"
scope boundary (the bounded variant itself already edited `ChainedHashMap.cpp`, so 2a has precedent if
needed). All numbers cited are from `results/L_*` (630-cell matrix), `combined3_dataset.csv`, and the
HMGAUGE/POOLGAUGE/UPOOLGAUGE instrumentation already in the binary.
