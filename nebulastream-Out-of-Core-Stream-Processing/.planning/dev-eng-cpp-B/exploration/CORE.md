# CORE.md — Explorer 1 (Core, Runtime, Concurrency & Lifetime) — Phase 4b Increment B

Branch `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b`. READ-ONLY exploration. All file:line refs verified.

This report maps the **actual** threading reality of the windowed-aggregation build/trigger/probe/GC pipeline so the
architects can design auto-eviction (B-evict) and the locking model (B-concurrency) on facts, not assumptions.

---

## 0. TL;DR for the architects (read this first)

- **Build is genuinely multi-threaded on ONE slice store.** Any of N worker threads can pull a `WorkTask` for the same
  build pipeline off the shared TaskQueue (`QueryEngine.cpp:478-528`). They concurrently call
  `getSlicesOrCreate` and write into the *same* slice — isolation is purely by `workerThreadId % numHashMaps` indexing
  into **disjoint** per-worker `unique_ptr<HashMap>` slots (`HashMapSlice.cpp:84-105`, `SpillableAggregationSlice.cpp:51-72`).
- **The trigger (`getTriggerableWindowSlices`) runs ON the build hot path**, on a worker thread, once per buffer at
  build-`close()` (`WindowBuildPhysicalOperator.cpp:71-84` → `checkAndTriggerWindows` → `getTriggerableWindowSlices`).
  It is NOT a separate maintenance thread. So "build" and "trigger/unspill+markEmitted" interleave across worker threads
  on the same store *all the time*, today.
- **GC (frees the maps) runs on a DIFFERENT pipeline** — the probe pipeline's `close()`
  (`WindowProbePhysicalOperator.cpp:84-99` → `garbageCollectSlicesAndWindows`), on whatever worker runs the probe.
- **The async-probe-drain invariant is real and confirmed.** `triggerSlices` snapshots raw `HashMap*` into a buffer and
  `emitBuffer`s it to the probe asynchronously (`AggregationOperatorHandler.cpp:80-156`); the maps are freed only later
  at GC. Eviction must never spill an EMITTED slice. A's `emittedKeys` guard is the enforcement.
- **SliceCache cannot go stale by re-lookup of a spilled slice IF watermark monotonicity holds** — and it does
  (`MultiOriginWatermarkProcessor` is built on a `NonBlockingMonotonicSeqQueue`). But this guarantee is *subtle and
  partial*; see §2 for the exact argument and the one residual hazard (per-worker cache entries are NOT invalidated on
  spill, so the safety rests entirely on "no build tuple ever maps to a COLD slice's range again").
- **The single biggest design fact:** because trigger already runs on the build path under per-map folly locks, adding
  eviction at the *same* cadence (build-`close()`, after trigger) is the natural, lowest-surprise trigger point (O-B2),
  and it lets eviction reuse the moment when `emittedKeys` has just been updated.

---

## 1. The real concurrency model

### 1.1 Thread topology — who runs what, on which thread

| Phase | Entry point | Cadence | Thread | Touches store via |
|---|---|---|---|---|
| Build per-tuple | `AggregationBuildPhysicalOperator::execute` (`AggregationBuildPhysicalOperator.cpp:85-132`) | per tuple (JIT hot path) | any worker thread (thread-local `WorkerThread::id`) | `sliceStoreRef->getDataStructureRef` → SliceCache; on miss → proxy |
| Build cache-miss | `defaultTimeBasedSliceStoreRefCacheMissProxy` (`DefaultTimeBasedSliceStoreRef.cpp:40-66`) | per cache miss | same worker thread | `getSlicesOrCreate` + `dataStructureExtractor` (→ `getHashMapPtrOrCreate`) |
| Build close (TRIGGER) | `WindowBuildPhysicalOperator::close` (`WindowBuildPhysicalOperator.cpp:71-84`) → `checkAndTriggerWindows` (`WindowBasedOperatorHandler.cpp:73-89`) | once per input buffer | same worker thread that processed the buffer | `getTriggerableWindowSlices` (A: unspill + markEmitted) |
| Build terminate | `WindowBuildPhysicalOperator::terminate` (`WindowBuildPhysicalOperator.cpp:104-108`) → `triggerAllWindows` | once per pipeline termination | a worker thread | `getAllNonTriggeredSlices` (A: unspill + markEmitted) |
| Probe | `AggregationProbePhysicalOperator` consumes the `EmittedAggregationWindow` buffer | per emitted window | a worker thread (decoupled via TaskQueue) | reads snapshotted raw `HashMap*` (NOT through the store) |
| Probe close (GC) | `WindowProbePhysicalOperator::close` (`WindowProbePhysicalOperator.cpp:84-99`) → `garbageCollectSlicesAndWindows` | per probe buffer | a worker thread | `garbageCollectSlicesAndWindows` → frees maps |

Key engine facts:
- `QueryEngine.cpp:478-528` (`ThreadPool::WorkerThread::operator()(WorkTask&)`): a `WorkTask` is `task.pipeline.lock()`ed
  and `pipeline->stage->execute(...)` is run by *whatever* worker dequeued it. A `DefaultPEC` is constructed per task
  with `WorkerThread::id` (`QueryEngine.cpp:491-493`). Nothing pins a build pipeline to one thread — **N workers run the
  same build stage concurrently against the same `AggregationOperatorHandler` and its one slice store.**
- `WorkerThread::id` is a `thread_local WorkerThreadId` (`QueryEngine.cpp:430,476`), assigned `INITIAL + id` per pool
  thread (`QueryEngine.cpp:749`). This is the value that flows to `ctx.workerThreadId` and into every store/slice call.
- Worker count: `config.numberOfWorkerThreads` (`QueryEngine.cpp:782`), surfaced as
  `pipelineCtx->getNumberOfWorkerThreads()` and stamped on the handler in `start()` (`WindowBasedOperatorHandler.cpp:45-48`)
  and on each slice as `numberOfHashMaps` (`AggregationOperatorHandler.cpp:74`, `getCreateNewSlicesFunction`).

### 1.2 Per-worker isolation — exactly how it is achieved (and where it is NOT locked)

- **Slice creation** is locked: `getSlicesOrCreate` (`DefaultTimeBasedSliceStore.cpp:58-102`) does a double-checked
  insert under `slices`/`windows` folly locks (`acquireLocked(slices, windows)` at line 78). Two workers racing to create
  the same `sliceEnd` are correctly de-duplicated (lines 79-82). The created `shared_ptr<Slice>` is shared by all workers.
- **Per-worker map creation is NOT locked** and relies entirely on slot disjointness:
  `getHashMapPtrOrCreate(workerThreadId)` does `pos = workerThreadId % hashMaps.size()` then lazily
  `hashMaps.at(pos) = make_unique<ChainedHashMap>(...)` (`SpillableAggregationSlice.cpp:58-72`,
  `HashMapSlice.cpp:91-105`). Correctness depends on **every worker thread having a distinct `pos`**, i.e.
  `numberOfHashMaps == numberOfWorkerThreads` (set at `AggregationOperatorHandler.cpp:74`). If that ever breaks (two
  workers share a `pos`), this is an unsynchronized concurrent write to the same `unique_ptr` slot → UB. **B must treat
  `numHashMaps == numWorkerThreads` as a load-bearing invariant** (assert it; varsized work must not perturb it).
- The extractor returns the raw `HashMap*` (`reinterpret_cast<const std::byte*>(ptr)`) from `getHashMapPtrOrCreate`
  (`LowerToPhysicalWindowedAggregation.cpp:255-263`) — this pointer is then cached in the per-worker SliceCache entry
  (`DefaultTimeBasedSliceStoreRef.cpp:65`). **The cached value is a raw pointer into a `unique_ptr<ChainedHashMap>` that
  spill() will `reset()`** (`SpillableAggregationSlice.cpp:112`). This is the crux of the stale-entry question (§2).

### 1.3 Trigger vs build — concurrent on the same store, today

`getTriggerableWindowSlices` (`DefaultTimeBasedSliceStore.cpp:104-139`) takes `windows.tryWLock()` and, if it can't get
it, returns `{}` (lines 108-112) — a *best-effort* trigger that silently skips when another worker holds the windows
lock. It marks each triggerable window `EMITTED_TO_PROBE` (line 130) and collects its slices. The A override
(`SpillableTimeBasedSliceStore.cpp:117-123`) then unspills + records `emittedKeys` for every returned slice. Meanwhile
other workers can be inside `execute()`/cache-miss writing into *filling* slices (different `sliceEnd`s). So build-write
and trigger-read already run concurrently on the same store and are made safe by (a) the per-map folly `Synchronized`
on `slices`/`windows`, and (b) the COLD-only spill rule. **Auto-eviction is a third concurrent actor that A never had.**

---

## 2. SliceCache lifetime — the stale-entry invariant (CRITICAL)

### 2.1 What the cache is

- `SliceCacheSecondChance` (`SliceCacheSecondChance.{hpp,cpp}`): a clock/second-chance cache. Each entry
  (`SliceCacheEntry`, `SliceCache.hpp:33-48`) stores `{sliceStart, sliceEnd, dataStructure}` where
  `dataStructure = const std::byte*` is **the raw HashMap\* for one worker's map of that slice**.
- **Keyed by timestamp range:** lookup is "is `sliceStart <= ts < sliceEnd`" (`SliceCacheSecondChance.cpp:51-63`), not by
  `sliceEnd` identity.
- **Per-worker, NOT shared:** memory is partitioned `[Thread0 entries][Thread1 entries]...` and each call computes
  `threadLocalStart = startOfSliceCache + workerThreadId * numberOfEntries` (`SliceCacheSecondChance.cpp:77-79`). "Each
  worker thread uses its own partition of the cache to avoid data races" (comment, `SliceCacheSecondChance.cpp:76`). So
  there is **no cross-thread sharing of cache entries** — but also **no central registry the store can invalidate.**
- Lives in the `DefaultTimeBasedSliceStoreRef` (one per build operator, cloned per pipeline copy;
  `DefaultTimeBasedSliceStoreRef.cpp:80-91`). Memory is an unpooled buffer allocated once at setup
  (`DefaultTimeBasedSliceStore.cpp:291-308` `allocateSpaceForSliceCache`). It is process memory the **store has no
  pointer to** — the store cannot reach in and clear a worker's entry.

### 2.2 When populated / looked up / evicted (within the cache)

- **Populated**: on a build cache miss, `replaceEntry` runs the proxy which writes `sliceStart/sliceEnd/dataStructure`
  into the chosen victim entry (`DefaultTimeBasedSliceStoreRef.cpp:99-112`, `SliceCacheSecondChance.cpp:108-112`).
- **Looked up**: every build tuple, `searchInCache` by ts range (`SliceCacheSecondChance.cpp:84-88`).
- **Evicted (from the cache, not spilled)**: clock replacement — only when a NEW miss needs the slot
  (`SliceCacheSecondChance.cpp:93-106`). **There is no time/watermark-driven cache invalidation.** An entry for slice S
  persists in worker W's partition until W incurs enough misses to clock S out.

### 2.3 Could a cached entry point to a spilled (freed) map? — the exact argument

The dangerous sequence would be: worker W caches `HashMap*` for slice S → eviction spills S (`reset()`s that map,
`SpillableAggregationSlice.cpp:112`) → worker W gets a build tuple in S's range, the cache HITS the stale entry, and W
writes through the dangling `HashMap*` → **use-after-free.**

Whether this can happen reduces to: **after S is spilled, can a build tuple ever map to S's `[sliceStart, sliceEnd)` range
again on the build path?**

- A spills only COLD slices: `sliceEnd < globalWatermark`, stopped filling, not emitted (brief LOCKED decision; A's
  `forceSpill` enforces not-emitted via `emittedKeys`, `SpillableTimeBasedSliceStore.cpp:206-210`).
- The build watermark is **monotonic non-decreasing**: `MultiOriginWatermarkProcessor` wraps a
  `NonBlockingMonotonicSeqQueue<uint64_t>` per origin and returns the min across origins
  (`MultiOriginWatermarkProcessor.cpp:45-83`). Once the global watermark passes `sliceEnd`, it never goes back below it.
- Watermark advancement is the standard "no late data below the watermark" contract: a build tuple with
  `ts < globalWatermark` is out-of-order/late. For a tumbling/sliding time window, a new tuple landing in S's range
  requires `ts` such that `sliceStart <= ts < sliceEnd < watermark`, i.e. a **late tuple below the watermark**.

**Conclusion:** *If the engine never delivers a build tuple whose ts falls into an already-watermark-passed slice's
range, the stale cache entry is never re-hit, and no invalidation is needed.* Watermark monotonicity is *necessary*;
the "no-late-data" delivery contract is the *sufficient* part. Two caveats the architects must weigh:

1. **The guarantee is a contract, not a mechanism.** Nothing in the build path *rejects* a late tuple that would re-hit
   S's range. If late data ever reaches the build (allowed-lateness configs, multi-origin skew, replay), the stale entry
   IS reachable and IS a UAF. A's design implicitly bets this never happens. **B should either (a) prove the delivery
   contract holds for the target queries and document it as a hard precondition, or (b) defensively invalidate.**
2. **Defensive invalidation is HARD here** precisely because the cache is per-worker process memory with no store-side
   registry (§2.1). Options if (b) is chosen: (i) a per-store generation/epoch counter bumped on every spill that the
   cache lookup re-validates against the slice's residency — but the cache lookup is JIT/Nautilus code with no store
   call, so this adds a per-tuple branch + a store read on the hot path (expensive, defeats the cache's purpose);
   (ii) make spill() leave the `ChainedHashMap` object alive but empty (don't `reset()` the `unique_ptr`), so the cached
   pointer stays valid and a re-hit just appends to a resident-again map — but that contradicts "free the maps" and the
   budget math (residentBytesOf returns 0 for spilled). Neither is clean. **Recommendation: rely on watermark
   monotonicity + COLD-only spill (option a), add a stress test that asserts no build tuple ever lands in a spilled
   slice's range, and document the late-data precondition loudly.** This is the cheapest correct path and matches A's
   existing bet — but the bet must be made explicit and tested, per the brief's "Test it."

### 2.4 The store unit test path side-steps the cache

Note: `SpillableTimeBasedSliceStoreTest` (and the spill-skeleton tests) drive the store API directly and use
`SliceCacheConfiguration{}` (default — likely cache-disabled / `SliceCacheNone`). So **the stale-entry hazard is NOT
exercised by any current gate test.** It only manifests in a full JIT query with the SecondChance cache enabled. This is
the highest-risk untested seam in B (see §7).

---

## 3. Where eviction can safely run (O-B2)

Candidate trigger points, by cadence and thread:

| Candidate | Cadence | On JIT per-tuple hot path? | Notes |
|---|---|---|---|
| Inside `getSlicesOrCreate` after a create | once per *new slice* | NO (only on cache miss + create) | Tempting (brief mentions it), but creation runs under the `slices`+`windows` write locks (`DefaultTimeBasedSliceStore.cpp:78`) — running eviction here risks holding those locks while spilling (lock-order/latency hazard). Would need to release first. |
| **After `getTriggerableWindowSlices` in build-`close()`** | once per input buffer | **NO** (close, not per tuple) | **Recommended.** Already the cadence where trigger + unspill + markEmitted happen; `emittedKeys` is freshly updated; watermark just advanced so the set of COLD slices is freshly well-defined. Lowest blast radius, no new thread. |
| Piggyback on GC (`garbageCollectSlicesAndWindows`) | per probe buffer | NO | Runs on the *probe* pipeline/thread, decoupled from build pressure — reacts late to build-side memory growth. Wrong feedback loop for a build-driven OOM. |
| Dedicated eviction step/thread | independent | NO | Cleanest isolation but adds a thread + its own lock-ordering surface; heaviest blast radius; hardest to test in-gate. |
| Inline per-tuple in `execute()` | per tuple | **YES — forbidden** | Never. The brief explicitly excludes this. |

**`getDataStructureRef` is the per-tuple hot path (`AggregationBuildPhysicalOperator.cpp:93`); eviction must never run
there.** The natural maintenance hooks that already exist and run off the per-tuple path are exactly the three store
override points A already specialises: the trigger feeders and GC. **Build-`close()` (right after trigger) is the only
one driven by build-side memory pressure on the build thread** — it is the right home for B-evict's check+spill.

There is **no pre-existing periodic/timer maintenance hook** in the store or handler; the only "ticks" are buffer
boundaries (build close, probe close). `DelayedTaskSubmitter` exists in the engine but is for delayed *task* resubmission,
not store maintenance.

---

## 4. Cold-slice enumeration (O-B1) — options and blast radius

The blocker: `slices` and `windows` are **PRIVATE** in `DefaultTimeBasedSliceStore` (`DefaultTimeBasedSliceStore.hpp:88-89`),
so the subclass cannot scan them to find COLD candidates or compute store-wide `residentBytes()`.

State a slice needs to be classified COLD = `sliceEnd < watermark` AND window done filling AND **not** `EMITTED_TO_PROBE`.

| Option | Mechanism | Can it cheaply find COLD slices? | "emitted" knowable? | Blast radius |
|---|---|---|---|---|
| **(a) Self-track** | Override `getSlicesOrCreate` to record each created `sliceEnd`→`weak_ptr<Slice>` in a new `Synchronized<map>`; learn "emitted" from `emittedKeys` (already populated by the trigger feeders, `SpillableTimeBasedSliceStore.cpp:112`). | Yes — store keeps its own sorted `sliceEnd` set; oldest-first scan is trivial. residentBytes = Σ over tracked resident slices of `residentBytesOf`. | **Yes, authoritatively** — `emittedKeys` is THE set of slices the store handed to a trigger feeder (the only way a slice becomes EMITTED). A already maintains it. | **Lowest** — zero core edits; pure subclass. Cost: one extra tracked map + overriding `getSlicesOrCreate` (today not overridden — the subclass would add an override that calls base then records). Risk: must keep the tracked map in sync with base GC (already handled for `spilledKeys`/`emittedKeys` in `garbageCollectSlicesAndWindows`, `SpillableTimeBasedSliceStore.cpp:151-181` — extend the same pattern). |
| (b) `protected` maps / accessor | Make `slices`/`windows` `protected`, or add `protected: getSlicesSnapshot()`. | Yes, directly. | Window state is right there (`SlicesAndState.windowState`), so EMITTED is readable from `windows` without `emittedKeys`. | **Higher** — touches the base store's encapsulation, widens the 805-baseline regression surface, and exposes folly-locked internals to a subclass (easy to introduce lock-order bugs by locking base maps + subclass sets together). |
| (c) `protected virtual` hook | Base calls e.g. `onSliceCreated(sliceEnd, slice)` / exposes `forEachColdSlice(cb)` the subclass overrides. | Yes. | Depends on what the hook surfaces. | **Medium** — small, intentional core change; cleaner than (b) but still a core edit + a new vtable contract to maintain. |

**Recommendation: (a) self-track.** It is the lowest blast radius, keeps the 805 baseline untouched, and `emittedKeys` is
already the authoritative "emitted" signal A relies on (the trigger feeders are the *only* producers of EMITTED state,
and they all funnel through `unspillAndMarkEmitted`). The store can maintain a `Synchronized<std::map<SliceEnd,
std::weak_ptr<Slice>>>` of created slices (use `weak_ptr` so GC's `shared_ptr` drop still frees the slice; the eviction
scan lock()s the weak_ptr and skips expired ones). Cold test for `sliceEnd`: `sliceEnd < watermark`
(store must stash the latest build watermark — currently the store only sees the *probe* watermark via
`garbageCollectSlicesAndWindows`; **the store does NOT currently receive the build watermark** — see §7 risk R5) AND
`!emittedKeys.contains(sliceEnd)` AND `isSliceResident(sliceEnd)`.

**Caveat on (a) for residentBytes():** the brief's Option-1 budget is `Σ_workers ⌈tuples_w/entriesPerPage⌉ × pageSize`.
`residentBytesOf(Slice&)` already computes this per slice (`SpillableTimeBasedSliceStore.cpp:234-255`). A store-wide
`residentBytes()` = sum over tracked resident slices. Cheap, but recomputed each eviction tick (acceptable at
once-per-buffer cadence).

---

## 5. Lock discipline under auto-eviction (O-B3)

### 5.1 A's current rule and locks

A's rule (verbatim, `SpillableTimeBasedSliceStore.cpp:155-158`): **"Never hold two of our locks at once"** — the trigger
path acquires `spilledKeys` and `emittedKeys` *independently*, so nesting them would be a lock-order inversion (labeled
H1). Existing locks:
- Base: `slices`, `windows`, `pipelineIdToSliceCacheStarts` (each a folly `Synchronized`); `getSlicesOrCreate` takes
  `slices`+`windows` together via `acquireLocked` (`DefaultTimeBasedSliceStore.cpp:78`) — the ONE sanctioned two-lock
  acquisition, and it is **ordered (slices, windows)**.
- A's subclass: `spilledKeys`, `emittedKeys`, `bufferProvider` — each taken alone.

### 5.2 The TOCTOU A flagged (M1)

`forceSpill` (`SpillableTimeBasedSliceStore.cpp:201-227`): the `emittedKeys.rlock()->contains` check (line 206) and the
`spillable->spill()` + `spilledKeys.insert` (lines 225-226) are **not atomic**. A concurrent trigger
(`unspillAndMarkEmitted`) can insert into `emittedKeys` *between* the check and the spill → a slice gets spilled *after*
its raw `HashMap*` was snapshotted for the async probe → **UAF in the probe.** A documents this as acceptable only under
the single-threaded driver. **B must close this for auto-eviction**, which runs concurrently with triggers by design.

### 5.3 Does "never hold two store locks" survive? — No, not as-is; here is the minimal fix.

Auto-eviction must do, atomically w.r.t. a concurrent trigger marking the slice emitted: **pick a COLD slice → confirm
not-emitted → spill → update tracked sets.** The race partner is `unspillAndMarkEmitted` doing
"unspill (erase spilledKeys) + insert emittedKeys" for the *same* slice.

The clean, minimal design that preserves A's spirit:

1. **Add ONE dedicated `evictionMutex` (a plain `std::mutex`) that serialises whole evictions** *and* the
   emitted-marking of the trigger path **for the residency decision only**. Concretely: the *decision* "is this slice
   spillable right now?" + the *act* of spilling (or of marking emitted) must be under a common lock so they can't
   interleave. This is a single coarse lock, not nested fine locks — A's "no two fine locks nested" rule still holds for
   `spilledKeys`/`emittedKeys`; we add one *outer* serialiser, not a second inner lock.

   - Eviction: `lock(evictionMutex)` → choose oldest COLD slice from the tracked set → check `!emittedKeys.contains` →
     `spill()` → `spilledKeys.insert` → unlock. (The individual `spilledKeys`/`emittedKeys` accesses are still each taken
     alone *inside* the eviction critical section, preserving H1's no-nesting.)
   - Trigger's mark-emitted step: take `evictionMutex` around the `emittedKeys.insert` (and the `spilledKeys.erase` from
     unspill) so it cannot interleave with an in-flight spill of the same slice. **This is the change to A's rule** —
     the emitted-marking now participates in the eviction serialiser.

2. **Spill/unspill of a single slice stay idempotent** (already true: `spill()` no-ops if `!resident`,
   `SpillableAggregationSlice.cpp:87-89`; `unspill()` no-ops if `resident`, lines 121-123; `spilledKeys`/`emittedKeys` are
   sets). Idempotency means no double-spill/double-unspill even if the coarse lock is briefly contended.

3. **Per-slice map mutation (build writes) stays OUTSIDE evictionMutex** — build writes only touch *filling* slices that
   are not eviction candidates (COLD-only), and per-worker slot disjointness already protects them. **Do not let
   evictionMutex wrap the per-tuple path.**

### 5.4 Deadlock / lock-order hazards to flag

- **H1 (existing):** `spilledKeys` ↔ `emittedKeys` nesting. Keep them un-nested; the new `evictionMutex` is the *outer*
  serialiser, and inside it each set is still touched one at a time.
- **New hazard NH1 — evictionMutex vs base `slices`/`windows`:** eviction's candidate scan (option a) reads the tracked
  set (subclass lock) but `residentBytesOf`/`spill` call into the slice (no base-map lock needed once you hold the
  `shared_ptr`). **Rule: never acquire base `slices`/`windows` while holding `evictionMutex`** (and `getSlicesOrCreate`
  already takes slices+windows — eviction must not be called from inside it while those are held). If eviction runs at
  build-`close()` (recommended §3), it runs *after* `getTriggerableWindowSlices` has released `windows.tryWLock`, so no
  nesting — good.
- **New hazard NH2 — evictionMutex held across RocksDB I/O:** `spill()` does serialization + `backend.put` (potentially
  slow disk/RocksDB). Holding `evictionMutex` across it serialises *all* triggers' emitted-marking behind disk I/O →
  latency/throughput cliff and a stall risk. **Mitigation:** under `evictionMutex`, *select + atomically transition the
  slice to a "SPILLING" reserved state and mark it not-eligible-for-emit*, release the mutex, do the I/O outside, then
  re-take briefly to finalize `spilledKeys`. This needs a per-slice spill-in-progress flag so a concurrent trigger that
  *wants* this slice can either wait or skip it. (A doesn't have this state today — B must add it. This is the genuinely
  hard part of O-B3.)
- **GC interaction:** `garbageCollectSlicesAndWindows` already erases `spilledKeys`/`emittedKeys` for dropped slices
  (`SpillableTimeBasedSliceStore.cpp:159-180`) using one-lock-at-a-time. Eviction's tracked-set must be GC'd the same
  way; ensure eviction doesn't pick a slice GC is concurrently dropping (the `weak_ptr` lock() guard handles this — an
  expired weak_ptr means GC already dropped it; skip).

---

## 6. The async-probe lifetime (re-confirmed for B)

Full chain, verified:
1. Trigger snapshots raw `HashMap*`: `triggerSlices` iterates slices, calls `getHashMapPtr(workerThreadId)` and pushes
   the raw `HashMap*` into `allHashMaps` (`AggregationOperatorHandler.cpp:90-111`). It also builds a `finalHashMap` for
   the probe to merge into.
2. Those pointers are bit-copied into an `EmittedAggregationWindow` placement-new'd into a tuple buffer
   (`AggregationOperatorHandler.cpp:142-144`; struct `AggregationOperatorHandler.hpp:39-55`).
3. `pipelineCtx->emitBuffer(tupleBuffer)` dispatches the buffer to the probe pipeline **via the TaskQueue**
   (`AggregationOperatorHandler.cpp:148`; engine emit closure `QueryEngine.cpp:496-508` → `emitWork` to successors).
   **This is asynchronous** — the probe runs as a separate `WorkTask`, possibly on a different worker thread, possibly
   much later.
4. The maps the probe reads are **NOT freed at trigger time.** `triggerSlices` does not destroy slices; the slice
   `shared_ptr`s remain in the store's `slices`/`windows` maps.
5. **Maps are freed ONLY at GC**: `garbageCollectSlicesAndWindows` (`DefaultTimeBasedSliceStore.cpp:212-272`) erases the
   slice from `slices` and clears the local `slicesToDelete` vector at line 271 → last `shared_ptr` drop →
   `~HashMapSlice` (`HashMapSlice.cpp:50-66`) frees the maps. GC runs on the **probe** pipeline's close
   (`WindowProbePhysicalOperator.cpp:84-99`), gated by the **probe** watermark (`garbageCollectSlicesAndWindows` in the
   handler uses `watermarkProcessorProbe`, `WindowBasedOperatorHandler.cpp:59-71`). So a slice is only GC'd after the
   probe has advanced past it — i.e. after the probe has consumed the emitted window. The lifetime is correct **today**.

**Where eviction could race the probe:** the ONLY hazard is eviction spilling a slice whose `HashMap*` is already
snapshotted in an in-flight `EmittedAggregationWindow`. That is exactly what `emittedKeys` + the COLD-only rule prevent
— *provided the check+spill is atomic w.r.t. the emitted-marking* (the §5.2 TOCTOU). With the §5.3 fix (evictionMutex
serialising spill against emitted-marking), there is **no window** for eviction to spill an emitted slice. Without it,
there is a real UAF race that A only avoided by being single-threaded. **This is the single most important correctness
requirement of Increment B.**

One subtlety to flag: `emittedKeys` is set by the trigger feeders, which are *best-effort* (`getTriggerableWindowSlices`
returns `{}` if it can't take `windows.tryWLock`, `DefaultTimeBasedSliceStore.cpp:108-112`). A slice that becomes
COLD but whose window hasn't *yet* been triggered (because trigger was skipped under contention) is **legitimately
spillable** — and that's fine, because it has NOT been emitted, so no `HashMap*` is in flight. The invariant is keyed on
"emitted," not on "cold," which is correct. But it means eviction and the *next* trigger of that same slice race for it:
eviction must reserve it (NH2 spill-in-progress state) so a trigger that picks it up doesn't snapshot a half-freed map.

---

## 7. Concurrency invariants & risks for the architects

### Invariants B MUST preserve (each with its enforcement point)

- **INV-1 (async-probe-drain):** A slice whose raw `HashMap*` has been (or is being) snapshotted for the probe must not
  be spilled until GC frees it. Enforcement today: `emittedKeys` + COLD-only spill. **B must make the
  not-emitted-check + spill atomic** (close the M1 TOCTOU, §5.2/§5.3). Absolute.
- **INV-2 (per-worker slot disjointness):** `numberOfHashMaps == numberOfWorkerThreads`, and each worker only ever
  touches `hashMaps[workerThreadId % n]`. The unsynchronized lazy map creation in `getHashMapPtrOrCreate`
  (`HashMapSlice.cpp:91-105`) is safe ONLY under this. B must assert it and varsized work must not break it.
- **INV-3 (watermark monotonicity ⇒ no COLD re-lookup):** A spilled slice's `[sliceStart,sliceEnd)` range is never
  re-looked-up by the build hot path, because the build watermark is monotonic (`MultiOriginWatermarkProcessor` over
  `NonBlockingMonotonicSeqQueue`) and only COLD (`sliceEnd<watermark`) slices spill, and no late tuple below the
  watermark is delivered to the build. This is what makes the per-worker SliceCache stale-entry-safe **without
  invalidation.** It is a *contract*, not a mechanism — B must either prove/document the no-late-data precondition for
  target queries, or pay the (expensive) price of cache invalidation (§2.3). **Test this explicitly.**
- **INV-4 (idempotent per-slice spill/unspill):** already holds (`SpillableAggregationSlice.cpp:87-89,121-123`); B's
  eviction must not break it (no partial spill state observable as resident).
- **INV-5 (lock ordering):** the only sanctioned multi-lock acquisition is base `(slices, windows)` in
  `getSlicesOrCreate`. New `evictionMutex` is an OUTER serialiser; never acquire base maps while holding it; keep
  `spilledKeys`/`emittedKeys` un-nested (H1).

### Hazards (ranked)

- **R1 — TOCTOU spill-vs-emit (UAF, certain under MT):** the M1 race. Highest stakes. Fixed by §5.3.
- **R2 — eviction reserves a slice mid-trigger (NH2):** `spill()` does RocksDB I/O; holding the serialiser across it
  stalls all triggers, and not holding it opens a spill-vs-snapshot race. Needs a per-slice "spill-in-progress" state A
  doesn't have. This is the hardest new mechanism in B.
- **R3 — stale per-worker SliceCache entry (UAF, only if INV-3's contract is violated):** invisible in all current gate
  tests (the store tests bypass the cache, §2.4). If late data ever reaches the build, this is a silent UAF. Document +
  stress test.
- **R4 — option-(b)/(c) widening base encapsulation:** if O-B1 picks protected maps, the 805 baseline regression surface
  and lock-order surface both grow. Prefer (a) self-track.
- **R5 — the store has no build watermark:** to classify COLD by `sliceEnd < watermark`, the store needs the *build*
  watermark, but today it only receives the *probe* watermark (via `garbageCollectSlicesAndWindows`). The build
  watermark lives in `watermarkProcessorBuild` on the handler and is computed in `checkAndTriggerWindows`
  (`WindowBasedOperatorHandler.cpp:73-89`) but **not passed to the store.** B must plumb it: either pass the build
  watermark into a new store eviction entry point called from `checkAndTriggerWindows`/build-close, or have the store
  derive "cold" from window state (`EMITTED`/done-filling) instead of a raw watermark compare. (The window-state route
  needs O-B1 option b/c to see `windows`; the watermark-plumb route fits option a. This couples O-B1 and O-B2.)
- **R6 — best-effort trigger skip:** `getTriggerableWindowSlices` silently returns `{}` under lock contention. Eviction
  must not assume "cold ⇒ already emitted-or-safe"; it must check `emittedKeys` at spill time, not infer from cadence.

### What is genuinely hard to test (be honest — O-B4)

- **The TOCTOU and the stale-cache UAF are Heisenbugs.** Deterministic single-threaded tests (A's style) cannot reach
  them. Coverage options, in order of value:
  1. **TSan build is feasible** — `cmake/Sanitizers.cmake` supports `USE_SANITIZER=thread` / `VCPKG_SANITIZER=thread`
     (lines 22-25). Wire a TSan-instrumented multi-thread stress test (N threads hammering build `getSlicesOrCreate` +
     concurrent trigger + concurrent evict on one store, many iterations) to catch the spill-vs-emit data race and the
     dangling-pointer write. **This is the strongest in-gate signal available.** Caveat: TSan won't run the JIT/Nautilus
     build path, so it validates the *store* races (R1, R2) but **not R3** (the cache UAF lives in JIT'd code).
  2. **R3 (stale cache)** can only be exercised by a full JIT query with `SliceCacheSecondChance` enabled under a tiny
     budget so cold slices spill while the cache still holds their pointers — that is an **out-of-gate systest**, and even
     then it only catches R3 if the test induces a build tuple into a spilled range (which the no-late-data contract is
     *supposed* to prevent — so a passing test proves little; a *crashing* test under deliberately injected late data
     proves the hazard is real). Best honest coverage: an ASan systest under a tiny budget + injected out-of-order input.
  3. **Stress tests are probabilistic** — they reduce but never eliminate Heisenbug risk. Per the brief's escalation
     rule: if R1/R2 can't be made TSan-clean in-gate, the run should ESCALATE rather than ship unverified concurrency.

### Bottom line for SYNTHESIS

The build is real-MT today and trigger already runs on the build thread, so B's concurrency work is *closing one atomic
gap* (R1, the spill-vs-emit TOCTOU) plus *adding a reservation state for in-flight spills* (R2), not redesigning the
threading. The cleanest path: O-B1 = self-track (option a) + plumb the build watermark to the store (R5); O-B2 = evict at
build-`close()` after trigger; O-B3 = one coarse `evictionMutex` serialising spill-decision against emitted-marking, with
a per-slice spill-in-progress flag and I/O done outside the lock; O-B4 = TSan stress test for the store races + ASan
out-of-gate systest for the cache UAF, escalate if TSan can't be made clean. INV-1 and INV-3 are the two that, if broken,
silently corrupt or crash; everything else is recoverable.
