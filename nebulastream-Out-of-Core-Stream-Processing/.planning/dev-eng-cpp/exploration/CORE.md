# CORE.md — Runtime, Lifetime & Data-Structure Exploration (Explorer 1)

Scope: INCREMENT A (single-threaded de-risking spike). Branch `feat/ooc-phase1-spill-skeleton` @ `40de3f9a55` (verified, matches DESIGN_BRIEF).
All paths relative to repo root. READ-ONLY: nothing modified.

---

## 0. TL;DR for the architects

- **The trigger read path that matters for aggregation is NOT `getSliceBySliceEnd`.** The aggregation probe never re-reads the
  store; it consumes **raw `HashMap*` pointers snapshotted into the emitted buffer at `triggerSlices` time**
  (`EmittedAggregationWindow.hashMaps`). So **D3 unspill-on-read must complete before those raw pointers are taken** — i.e. unspill must
  happen inside `getTriggerableWindowSlices()`/`getAllNonTriggeredSlices()` (store), and the slice must STAY resident until the
  probe finishes consuming the buffer (async). This is the single most load-bearing fact for D6 and refines D3.
  *(`getSliceBySliceEnd` is used only by the NLJ join probe — out of scope for 4b, but it's still on the virtualization list in D2,
  which is correct/harmless.)*
- **O1 resolved:** `PipelineExecutionContext::getBufferManager()` returns `std::shared_ptr<AbstractBufferProvider>` (query-lifetime
  stable). The store can stash it during `setupSliceStoreProxy` (which already receives `pipelineCtx` and already calls
  `getBufferManager()`). Concrete recommendation below.
- **O3 resolved:** in-place factory branch in `getCreateNewSlicesFunction` is clean and low-risk; the handler does NOT need un-`final`.
  But see the recommendation — the backend pointer wants to live on the **store**, not the handler, so the branch needs the store's
  spill-enabled flag, not the handler's.
- **Build hot path confirmed:** only ever touches resident WINDOW_FILLING slices via `getHashMapPtrOrCreate`. The JIT ref can be reused
  unchanged (D2 justification holds).
- **Contradiction with brief wording:** DESIGN_BRIEF O1 says the store stashes the provider "during `setupSliceStore(pipelineCtx)`".
  `setupSliceStore` is a method of the **SliceStoreRef** (Nautilus hot path), not the store and not the handler. The actual C++ landing
  point is the proxy `setupSliceStoreProxy(...)` in `DefaultTimeBasedSliceStoreRef.cpp:114`, which already has both the store pointer and
  `pipelineCtx`. The intent is right; the seam is the proxy, not a handler method.

---

## 1. The trigger call chain (state machine + who flips state)

### Entry points (both from the JIT-compiled build pipeline)
`WindowBuildPhysicalOperator` drives everything (`src/WindowBuildPhysicalOperator.cpp`):
- `close()` (:71) → `nautilus::invoke(checkWindowsTriggerProxy, ...)` → `checkWindowsTriggerProxy` (:37) →
  `WindowBasedOperatorHandler::checkAndTriggerWindows` (`src/WindowBasedOperatorHandler.cpp:73`).
- `terminate()` (:104) → `triggerAllWindowsProxy` (:54) → `WindowBasedOperatorHandler::triggerAllWindows` (:91).
- `setup()` (:86) → `registerActivePipeline` (:64) → `store.incrementNumberOfInputPipelines()`; then `sliceStoreRef->setupSliceStore(...)` (:91).
- Probe side: `WindowProbePhysicalOperator` (:50) → `garbageCollectSlicesAndWindows`.

### The two-step trigger (read path)
`WindowBasedOperatorHandler::checkAndTriggerWindows` (`src/WindowBasedOperatorHandler.cpp:73-89`):
```
newGlobalWatermark = watermarkProcessorBuild->updateWatermark(...)            // :76
slicesAndWindowInfo = sliceAndWindowStore->getTriggerableWindowSlices(wm)     // :87  <-- D3 unspill site
triggerSlices(slicesAndWindowInfo, pipelineCtx)                               // :88  <-- handler, sees resident slices
```
`triggerAllWindows` (:91-96) is the same but calls `getAllNonTriggeredSlices()` instead. **This second method ALSO returns slice
shared_ptrs whose hashmaps `triggerSlices` will dereference — so it ALSO needs unspill-on-read** (the brief lists it in D2's virtual set;
make sure the spillable override unspills there too, not only in `getTriggerableWindowSlices`).

### Window state machine (lives in the store's `windows` map; `DefaultTimeBasedSliceStore.cpp`)
States: `WINDOW_FILLING` → `WAITING_ON_TERMINATION` (only the termination path) → `EMITTED_TO_PROBE`.
- **WINDOW_FILLING set** in `getSlicesOrCreate` (:97) when a slice is created/added (build path).
- **→ EMITTED_TO_PROBE** flipped in `getTriggerableWindowSlices` (:130) once `windowEnd < globalWatermark` and not already emitted.
  This is THE point a window becomes "closed". The flip happens under `windows.tryWLock()` (:108) — note: opportunistic, returns `{}`
  if the lock is contended (:109-112).
- **WAITING_ON_TERMINATION** is only entered from `getAllNonTriggeredSlices` (:183) when `numberOfActiveInputPipelines > 0`; resolved to
  EMITTED_TO_PROBE on a later termination (:203). Single-source spike: typically one input pipeline, so this branch is rarely exercised,
  but the spike's `terminate()` will run it once at end-of-stream.

### Ordering relative to `triggerSlices`
`getTriggerableWindowSlices`/`getAllNonTriggeredSlices` (STORE) run FIRST and return `map<WindowInfoAndSequenceNumber, vector<shared_ptr<Slice>>>`.
`triggerSlices` (HANDLER) runs SECOND on that returned map. So **D3 is exactly right: unspill belongs in the store methods, before they
return the vector. The handler then only needs the D1 seam cast.** (Confirmed: handler currently casts to `AggregationSlice`,
`AggregationOperatorHandler.cpp:82`, then calls `getHashMapPtr` :85.)

---

## 2. Buffer provider at trigger time — O1 RESOLVED

### What's reachable
- `PipelineExecutionContext::getBufferManager()` → `std::shared_ptr<AbstractBufferProvider>`
  (`nes-executable/include/PipelineExecutionContext.hpp:59`). Pure virtual; concrete impl returns the query-global buffer manager
  (`NodeEngine::getBufferManager` returns the durable `std::shared_ptr<BufferManager>`, `nes-runtime/include/Runtime/NodeEngine.hpp:57`).
  This is query-lifetime stable. **Stashing the `shared_ptr` is safe and durable.** ✅
- `triggerSlices` itself already uses `pipelineCtx->getBufferManager()->getUnpooledBuffer(...)`
  (`AggregationOperatorHandler.cpp:108`) — so a provider is reachable AT trigger time via `pipelineCtx`. But
  `getTriggerableWindowSlices` (where D3 unspill lives) has NO `pipelineCtx` parameter — confirming the O1 problem is real.

### Recommendation
**Stash the provider in `SpillableTimeBasedSliceStore` during `setupSliceStoreProxy`**, NOT via a handler method:
- `setupSliceStoreProxy(DefaultTimeBasedSliceStore* sliceStore, const PipelineExecutionContext* pipelineCtx, ... self)`
  (`DefaultTimeBasedSliceStoreRef.cpp:114-126`) already holds both `sliceStore` and `pipelineCtx`, and already dereferences
  `pipelineCtx->getBufferManager()` (:118, :124). Add (in the spillable store) a `setBufferProvider(shared_ptr<AbstractBufferProvider>)`
  call here, guarded so it's set once (this proxy runs once per pipeline — there's already a `pipelineIdToSliceCacheStarts` "called once"
  invariant at `DefaultTimeBasedSliceStore.cpp:294`).
- Store it as `std::shared_ptr<AbstractBufferProvider>` (keep ownership alive; do not store a raw `&`). `unspill` wants
  `AbstractBufferProvider&` — deref the stashed shared_ptr at unspill time.
- Single-worker spike caveat: `setupSliceStore` is invoked per build pipeline; with a single source there is exactly one such call, so a
  set-once stash is fine. (For Increment B with multiple build pipelines sharing one store, all provide the same query-global manager, so
  set-once-from-any is still correct — note this for the architect but it's not a spike risk.)

Alternative (rejected for the spike): thread a provider through `getTriggerableWindowSlices` — would change the
`WindowSlicesStoreInterface` signature and ripple into NLJ. The stash is strictly less invasive.

---

## 3. Slice "closed" lifetime & GC interaction (D6)

### Where state lives
Two `folly::Synchronized` maps in the store (`DefaultTimeBasedSliceStore.hpp:85-86`):
- `windows : map<WindowInfo, SlicesAndState>` — holds `windowState` + `vector<shared_ptr<Slice>>`.
- `slices  : map<SliceEnd, shared_ptr<Slice>>` — the canonical owner of each slice.
Both hold **shared_ptr**, so a slice stays alive as long as either map references it.

### Can a closed slice still be reached by a build worker or live SliceCache entry?
**Build worker:** A worker only reaches a slice via `getSlicesOrCreate` (cache-miss proxy, `DefaultTimeBasedSliceStoreRef.cpp:58`).
That returns the slice for the *current record's timestamp*. Once `windowEnd < watermark` the window is EMITTED_TO_PROBE and no new record
with a timestamp in that window will arrive past the watermark (watermark monotonic). So **a build worker cannot newly create-or-fetch a
closed slice** under correct watermark semantics. ✅ (This is the D6 "only spill closed" enabler — it rests on watermark monotonicity.)

**Live SliceCache entry (the dangerous one — D6 "no dangling cache entry"):** the per-worker `SliceCache` caches `(sliceStart, sliceEnd,
HashMap* dataStructure)` keyed by **timestamp-range membership** (`SliceCacheSecondChance::searchInCache`, `.cpp:48-63`:
`sliceStart <= ts < sliceEnd`). Entries are second-chance evicted (`.cpp:90-112`). The cache holds a **raw `HashMap*`** (the
`dataStructure`), captured at fill time via `dataStructureExtractor` (`DefaultTimeBasedSliceStoreRef.cpp:64`).
- A closed slice CAN still have a stale cache entry: the cache is never explicitly invalidated when a window flips to EMITTED_TO_PROBE.
  It only gets evicted lazily when its slot is reused or when a new ts lands in the same range (which won't happen post-watermark).
- **Why the spike is still safe:** post-watermark, no record with a ts in the closed slice's range arrives, so `searchInCache` will never
  match that stale entry again, so the stale `HashMap*` is never dereferenced by the build path. BUT if `spill()` frees that map's
  TupleBuffers (`SpillableAggregationSlice::spill` :112 `hashMaps[pos].reset()`), the cached raw pointer becomes **dangling**. It's only
  safe because it's unreachable. **This is a latent footgun the architect must treat as an invariant + test target**, not a comfort: the
  invariant "a spilled slice's stale cache entries are never matched again" depends entirely on watermark monotonicity. Recommend an
  explicit Increment-B test that fills, closes, spills, then feeds a (incorrectly) late record and asserts it does NOT hit the stale
  entry — or, cleaner, document that the spike never spills until after the trigger has flipped the window.

### GC timing margin
`garbageCollectSlicesAndWindows` (`DefaultTimeBasedSliceStore.cpp:212`):
- Erases a **window** entry only when `windowEnd < newGlobalWaterMark` AND state == EMITTED_TO_PROBE (:227-229).
- Erases a **slice** only when `sliceEnd + windowSize < newGlobalWaterMark` (:252) — i.e. a full extra `windowSize` of slack beyond
  the slice end. Slice destruction is deferred outside the lock (:255-258, `slicesToDelete.clear()` at :271).
- **Implication for the spillable store:** `garbageCollectSlicesAndWindows` is on the D2 virtual list. The spillable override must
  `backend.erase(sliceEnd)` for any slice it removes (the on-disk copy survives `unspill`, see `SpillableAggregationSlice` header note:
  "the on-disk copy is kept until the slice is erased"). The natural seam: override GC, call `DefaultTimeBasedSliceStore::garbageCollectSlicesAndWindows`,
  then erase backend records for the deleted sliceEnds. To know WHICH sliceEnds were deleted you either (a) snapshot slice keys before/after,
  or (b) refactor the base to expose deleted keys. (a) is lower-risk for the spike.

### The async-probe lifetime hazard (most important, ties to §0)
`triggerSlices` snapshots **raw `HashMap*`** into `EmittedAggregationWindow` (`AggregationOperatorHandler.cpp:92` builds `allHashMaps`,
:132 placement-new into the emitted buffer). The buffer is dispatched to the probe via `pipelineCtx->emitBuffer` (:136) and consumed
**asynchronously** by `AggregationProbePhysicalOperator` (reads `EmittedAggregationWindow.hashMaps` raw pointers,
`AggregationProbePhysicalOperator.cpp:69`). The probe does NOT go back to the store.
→ **The slice (and its resident hashmaps) MUST stay resident from unspill-in-trigger until the probe is done with the emitted buffer.**
A naive "spill again right after trigger" would dangle the probe's raw pointers. For the single-threaded spike this is fine (spill is
only driven before/at-trigger, never re-spilling an already-triggered slice), but it is a hard D6 invariant for Increment B:
**never spill a slice whose window is EMITTED_TO_PROBE but whose probe buffer is still in flight.** Since D5 spill candidates are
"closed slices" (EMITTED_TO_PROBE), this directly tensions D5 — the architect must scope spill candidates to closed slices whose probe
has *completed*, OR rely on GC's `windowSize` slack guaranteeing the probe drained first. **Flag for architect.**

---

## 4. Build hot path — confirms D2 reuse-the-ref justification

`getDataStructureRef` (`DefaultTimeBasedSliceStoreRef.cpp:92`) → `sliceCache->getDataStructureRef(...)` with a cache-miss lambda →
`defaultTimeBasedSliceStoreRefCacheMissProxy` (:39) → `sliceStore->getSlicesOrCreate(...)` (:58) → `dataStructureExtractor(*slice, wtid)` (:64).
The extractor (lowering rule, `LowerToPhysicalWindowedAggregation.cpp:237-242`) does
`dynamic_cast<AggregationSlice&>(slice).getHashMapPtrOrCreate(workerThreadId)` — i.e. it **lazily creates** the map. This path runs only
for slices the current record's timestamp maps to → WINDOW_FILLING by construction (a record never targets a closed window post-watermark).
**Conclusion:** the build hot path only ever touches resident, filling slices and only ever calls `getHashMapPtrOrCreate`. The JIT ref +
SliceCache need ZERO spill awareness. D2's "reuse `createSliceStoreRef`/`SliceCache`/ref unchanged" is justified. ✅
**D1 consequence to verify in implementation:** change the extractor's `dynamic_cast<AggregationSlice&>` to `dynamic_cast<HashMapSlice&>`
(`.cpp:239`) so it dispatches polymorphically to either concrete slice. This is on the brief's edit list and is correct.

---

## 5. Threading model for the single-worker spike

Even at `--workers=1`, the system is NOT fully single-threaded:
- The build pipeline (record ingestion → `getSlicesOrCreate`, build) and the trigger (`checkAndTriggerWindows` in build `close()`) run on
  the **worker thread**.
- The probe pipeline is a **separate task** dispatched via `emitBuffer` — it can run on the same or a different worker thread depending on
  `ContinuationPolicy` (`POSSIBLE` here, `AggregationOperatorHandler.cpp:136`). With one worker it's serialized onto the same thread, but
  the buffer outlives the trigger call.
- GC (`garbageCollectSlicesAndWindows`) runs from the **probe** side (`WindowProbePhysicalOperator.cpp:50`) — a logically distinct phase
  from build, even if on the same physical thread in the spike.
- The store's maps are `folly::Synchronized`; `getTriggerableWindowSlices` uses `tryWLock` (non-blocking), GC uses `tryWLock`.

**Implication:** Even in the spike, build/trigger and probe/GC interleave as separate logical actors over the same store. D6 cannot be
"assumed away" by single-threading. The minimum spike invariants that must hold:
1. unspill happens inside the store trigger method, under the same `windows`/`slices` lock that flips/reads state (serialized + idempotent
   — D6.3). 4a `unspill` is already idempotent (`SpillableAggregationSlice.cpp:121` no-op if resident).
2. a slice is not spilled while its probe buffer is in flight (§3 hazard).
3. `backend.erase` only after the slice shared_ptr is actually dropped by GC.

---

## 6. Where O3 (factory branch) and O2 (backend ownership) most naturally live

### O3 — slice factory branch
`getCreateNewSlicesFunction` (`AggregationOperatorHandler.cpp:54-68`) hardcodes `make_shared<AggregationSlice>` (:66). The handler is
`final` (`AggregationOperatorHandler.hpp:57`). **In-place branch is the right call** (brief's preference): inside the lambda, branch
`make_shared<SpillableAggregationSlice>` vs `AggregationSlice` on a spill-enabled bool. No un-`final` needed.
- **But:** the lambda is created `const` and captures by value at construction. The cleanest spill flag source is the **store**, not the
  handler (the store owns the backend — see O2). Two viable wirings:
  - (a) Pass a `bool spillEnabled` into the `AggregationOperatorHandler` ctor (one extra member; lowering already constructs the handler at
    `LowerToPhysicalWindowedAggregation.cpp:254`). Lowest-risk, matches brief's ".hpp carry spill-enabled flag".
  - (b) Have the factory ask the store. Awkward: the handler holds the store via `WindowBasedOperatorHandler::sliceAndWindowStore`
    (base `unique_ptr<WindowSlicesStoreInterface>`), so the handler would `dynamic_cast` to the spillable store — re-introduces a
    concrete cast Route B is trying to kill. **Prefer (a).**
- The branch needs the same `CreateNewHashMapSliceArgs` (`HashMapSlice.hpp:31`) — `SpillableAggregationSlice` ctor takes exactly these
  (`SpillableAggregationSlice.hpp:42`). The args carry `keySize/valueSize/pageSize/numberOfBuckets` needed by both. ✅

### O2 — backend ownership / lifecycle
**Backend belongs to `SpillableTimeBasedSliceStore`** (matches D2 "Owns the SpillBackend"). Rationale grounded in code:
- The store's trigger/GC methods are the only places that spill/unspill/erase (D3/D5), and they have no `pipelineCtx`; the store is the
  one query-lifetime object that survives across build+probe+GC. The handler is also query-lifetime but should stay backend-agnostic (the
  slice factory only needs a bool).
- Construction site: `LowerToPhysicalWindowedAggregation::apply` builds the store at `.cpp:234`. Replace the Phase-1 guard (:163-166)
  with: when `conf.spill.enabled`, construct `RocksDBSpillBackend` from `conf.spill` (path/threshold/compression from
  `SpillConfig.hpp`) and a `SpillableTimeBasedSliceStore` owning it; else the current `DefaultTimeBasedSliceStore`. The page-math inputs
  D4 needs (`entrySize` :212, `pageSize` :214, `entriesPerPage` :215) are **already computed here** and can be passed to the spillable
  store ctor — no recomputation/drift. ✅
- **rocksdbPath uniqueness:** `conf.spill.rocksdbPath` is a single config value; with one aggregation operator per query the spike is
  fine, but the architect should derive a per-operator subdir (e.g. append `handlerId`/`outputOriginId`) to avoid collisions when a query
  has >1 windowed aggregation. `handlerId` is available at the construction site (`.cpp:183`). Flag for O2.
- **Error propagation:** `RocksDBSpillBackend` throws `SpillStoreFailure` (per brief). Unspill happens inside `getTriggerableWindowSlices`
  which is called from `checkAndTriggerWindows` on the worker thread — an exception there propagates up through the JIT proxy
  `checkWindowsTriggerProxy` (`WindowBuildPhysicalOperator.cpp:37`). Confirm the proxy boundary doesn't swallow it (it currently has no
  try/catch — exceptions propagate, consistent with NES factory-fn/ErrorCode idiom).

---

## Risks & gotchas for the architects

1. **[HIGH] Probe consumes snapshotted raw `HashMap*`, not the store.** Unspill MUST finish before `triggerSlices` snapshots pointers,
   and the slice must stay resident until the async probe buffer drains. Re-spilling a triggered-but-not-yet-probed slice dangles the
   probe. This tensions D5 ("spill closed slices") — scope candidates to closed slices whose probe completed, or lean on GC's `windowSize`
   slack. (`AggregationOperatorHandler.cpp:92,132,136`; `AggregationProbePhysicalOperator.cpp:69`.)

2. **[HIGH] Stale SliceCache entries are never invalidated on close/spill.** The per-worker cache holds raw `HashMap*` keyed by ts-range
   (`SliceCacheSecondChance.cpp:48-63`). After `spill()` frees the map, the cached pointer dangles; safety relies entirely on watermark
   monotonicity making the stale entry unmatchable. Treat as an explicit invariant + Increment-B test, not an assumption. (D6.2.)

3. **[MED] `getAllNonTriggeredSlices` also needs unspill.** The brief emphasizes `getTriggerableWindowSlices`/`getSliceBySliceEnd`, but the
   termination path (`triggerAllWindows` → `getAllNonTriggeredSlices`, `DefaultTimeBasedSliceStore.cpp:150`) also feeds `triggerSlices`.
   The spillable override must unspill there too, else end-of-stream flush of a spilled slice crashes the `INVARIANT(resident)` in
   `SpillableAggregationSlice::getHashMapPtr` (`.cpp:53`).

4. **[MED] O1 seam is the ref proxy, not a handler method.** DESIGN_BRIEF says "stash during `setupSliceStore(pipelineCtx)`";
   `setupSliceStore` is on `DefaultTimeBasedSliceStoreRef` (Nautilus). The real C++ landing point is `setupSliceStoreProxy`
   (`DefaultTimeBasedSliceStoreRef.cpp:114`), which already has `sliceStore` + `pipelineCtx`. Add a `setBufferProvider` on the spillable
   store and call it there. Stash the `shared_ptr<AbstractBufferProvider>` (durable), deref to `&` at unspill.

5. **[MED] GC must `backend.erase` deleted slices, and only after the shared_ptr drops.** The base GC defers slice destruction outside the
   lock (`DefaultTimeBasedSliceStore.cpp:255-271`) and doesn't report which keys it removed. The spillable override needs to determine the
   deleted sliceEnds (snapshot-diff is lowest-risk) and erase backend records — without erasing while a slice could still be unspilled.

6. **[MED] `getNumberOfTuples` is non-virtual** on `HashMapSlice` (`HashMapSlice.cpp:73`) and `SpillableAggregationSlice` *shadows* it
   (`.hpp:56`, returns `spilledTupleCount` while spilled). D4 budget math (`Σ ⌈tuples/entriesPerPage⌉ × pageSize`) must call it on the
   concrete `SpillableAggregationSlice` (or via a new virtual on `HashMapSlice`), or it will read 0 for a spilled slice. If D4 lives in
   the store, the store holds `shared_ptr<Slice>` → `dynamic_pointer_cast<SpillableAggregationSlice>` (or add a virtual). The brief's D1
   only virtualizes `getHashMapPtr[OrCreate]`, NOT `getNumberOfTuples` — **flag: D4 needs a tuple-count accessor that's spill-aware and
   polymorphic.** Cleanest: add `virtual uint64_t getNumberOfTuples() const` to `HashMapSlice` alongside the D1 virtuals.

7. **[LOW] `getTriggerableWindowSlices` uses `tryWLock` and silently returns `{}` on contention** (`.cpp:108-112`). Unspill inside it runs
   only when the lock is acquired — fine, but means a missed trigger is retried on the next watermark. Idempotent unspill makes this safe.

8. **[LOW] rocksdbPath collision** if a query has >1 windowed aggregation (single `conf.spill.rocksdbPath`). Derive per-operator subdir
   from `handlerId` at the lowering construction site (`LowerToPhysicalWindowedAggregation.cpp:183`).

9. **[INFO] D4 page-math inputs already exist at construction** (`entrySize` :212, `pageSize` :214, `entriesPerPage` :215 in the lowering
   rule) — pass them into the spillable store ctor; no recompute, no drift (matches D4 intent).

10. **[INFO] Handler stays `final`; no un-`final` needed for O3** — in-place factory branch + a ctor `bool spillEnabled` is sufficient and
    keeps Route B's "no scattered concrete casts" intact.
