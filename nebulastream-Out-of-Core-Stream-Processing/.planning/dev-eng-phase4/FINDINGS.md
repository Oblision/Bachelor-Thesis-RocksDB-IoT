# Phase 4 architecture findings (post-exploration, HEAD f245b9c8d8)

## What the design brief assumed vs. reality
Brief: "handler untouched, the rest is glue." Reality (from code):
- `AggregationSlice` is `final` → cannot subclass. SpillableAggregationSlice must subclass `HashMapSlice` (not final; `hashMaps` is protected) and re-implement getHashMapPtr/getHashMapPtrOrCreate.
- `AggregationOperatorHandler` is `final` AND hardcodes `std::make_shared<AggregationSlice>` in `getCreateNewSlicesFunction` (AggregationOperatorHandler.cpp:54-68), plus `dynamic_cast<AggregationSlice&>` in `triggerSlices` (~:82). The lowering's DataStructureExtractor lambda also `dynamic_cast<AggregationSlice&>`. Injecting a spillable slice type REQUIRES editing these (a common base or a spill-aware branch).
- `createSliceStoreRef` is NOT on `WindowSlicesStoreInterface` — concrete on `DefaultTimeBasedSliceStore` (final). It returns a `SliceStoreRef` (Nautilus/JIT hot-path object with a SliceCache). A spillable store needs its own `SpillableTimeBasedSliceStoreRef : SliceStoreRef`.

## Store contract (DefaultTimeBasedSliceStore) — for parity
8 methods over `folly::Synchronized<std::map<SliceEnd, shared_ptr<Slice>>> slices` + `folly::Synchronized<std::map<WindowInfo, SlicesAndState>> windows`. Slice "closed" = its window is `EMITTED_TO_PROBE` (state lives in the STORE's windows map, not on Slice). Closed slices are read on the TRIGGER path (`triggerSlices`→getHashMapPtr), not the JIT build hot path (build only touches WINDOW_FILLING/resident slices). So the unspill-on-read hook belongs on the trigger/read path, not the JIT ref.

## Key consequence
Spill targets CLOSED slices (EMITTED_TO_PROBE). Build hot-path slices are always resident → SpillableTimeBasedSliceStoreRef can largely mirror DefaultTimeBasedSliceStoreRef. Unspill happens when the trigger path reads a non-resident closed slice.

## Scope decision: split Phase 4
- **4a (new, isolated, low-risk, fully unit-testable NOW):** `SpillableAggregationSlice : HashMapSlice` with `isResident()`, `spill(SpillBackend&, numberOfBuckets/pageSize)`, `unspill(SpillBackend&, AbstractBufferProvider&)`. Serializes each per-worker ChainedHashMap via Phase 2 SliceSerializer, stores via SpillBackend (keyed by sliceEnd+workerThreadId), frees the unique_ptrs; unspill rebuilds. Unit tests: fill N workers' maps → spill → assert freed + backend has records → unspill → assert identical entries (parity), multi-worker, idempotent re-spill, isResident transitions. No final-class edits, no JIT.
- **4b (larger, invasive, separate effort):** SpillableTimeBasedSliceStore (8 methods) + SpillableTimeBasedSliceStoreRef + edits to the two `final` core classes (slice-type injection) + lowering wire-in (replace Phase 1 guard) + oldest-closed-first eviction + parity/integration tests.

Recommendation: do 4a now as a reviewed+tested+committed increment; design 4b deliberately (it needs core NES changes — un-final/refactor handler, or add a slice factory + common slice base).
