# CONTRACTS.md — Phase 1 contract surface (returned inline by the read-only explorer; persisted here)

## Hierarchy (EXACTLY two concrete stores — verified exhaustive)
- `WindowSlicesStoreInterface` (pure-virtual base) — `nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp:63-101`. 8 pure virtuals incl. `getTriggerableWindowSlices`, `getAllNonTriggeredSlices` (both return `map<WindowInfoAndSequenceNumber, vector<shared_ptr<Slice>>>`), `garbageCollectSlicesAndWindows`, `deleteState`, `incrementNumberOfInputPipelines`, `getWindowSize`.
- `DefaultTimeBasedSliceStore : public WindowSlicesStoreInterface` — NOT final (un-final'd in Increment A), `DefaultTimeBasedSliceStore.hpp:55-99`. Overrides all 8.
- `SpillableTimeBasedSliceStore final : public DefaultTimeBasedSliceStore` — `SpillableTimeBasedSliceStore.hpp:88`. Overrides 6 (not incrementNumberOfInputPipelines/getWindowSize).
- NO other subclass. Join handlers (HJ/NLJ) HOLD a `unique_ptr<WindowSlicesStoreInterface>` (they instantiate `DefaultTimeBasedSliceStore`), they do NOT subclass it. NO gmock/hand-rolled store fakes anywhere in tests (only `ThrowingSpillBackend`, a SpillBackend fake).

## Adding a base virtual is safe & in-bounds
A DEFAULTED (no-op body) virtual on `WindowSlicesStoreInterface` ⇒ `DefaultTimeBasedSliceStore` inherits no-op (correct for in-memory), only `SpillableTimeBasedSliceStore` overrides. Zero ripple to join lowering (they construct, never subclass). MUST be on the INTERFACE (not on Default), because the handler calls through `WindowSlicesStoreInterface*`. Single static binary ⇒ no cross-DSO ABI risk; CMake auto-rebuilds includers (nes-physical-operators + nes-query-compiler lowering includes the header).

## Handler trigger path (the seam consumer)
- `WindowBasedOperatorHandler::sliceAndWindowStore` is `unique_ptr<WindowSlicesStoreInterface>` (protected) — `WindowBasedOperatorHandler.hpp:95`.
- `triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91-96`): `getAllNonTriggeredSlices()` then `triggerSlices(wholeMap)`. NOT overridden by anyone.
- `triggerSlices` is pure-virtual (`WindowBasedOperatorHandler.hpp:90-93`), implemented by AggregationOperatorHandler + the join handlers. Iterates per `(window,seq)` entry → `vector<shared_ptr<Slice>>`.
- `AggregationOperatorHandler::triggerSlices` (`AggregationOperatorHandler.cpp:84`): for each window, `dynamic_pointer_cast<HashMapSlice>`, then `getHashMapPtr(workerId)` (the Route-B virtual seam) — REQUIRES slice resident. `SpillableAggregationSlice::getHashMapPtr` enforces `INVARIANT(resident)` ⇒ std::terminate if not unspilled first. Then emitBuffer (async).

## Slice virtuals (Increment A Route-B seam)
`HashMapSlice` (NOT final) virtuals: `getNumberOfTuples()`, `getHashMapPtr(WorkerThreadId)`, `getHashMapPtrOrCreate(WorkerThreadId)`. Overridden by `SpillableAggregationSlice` (resident-precondition). `AggregationSlice final` uses base impl. `HJSlice`/`NLJSlice` unaffected (separate paths).

## Existing tests that touch the terminal path (WILL need updating if getAllNonTriggeredSlices goes lazy)
- `emitLagBacklogFullyFlushedOnTerminate` (C-T4, ~line 846): calls `incrementNumberOfInputPipelines()` then `getAllNonTriggeredSlices()` and asserts EVERY drained slice `isSliceResident()`. Under the new lazy contract this assertion moves to AFTER the per-window ensureResident hook.
- `emitLagDifferentialIdenticalResults` (C-T3, ~line 801): drains via `getAllNonTriggeredSlices()`.
- Concurrency test (~line 436): drains via `getAllNonTriggeredSlices()`.
Fixture helpers: makeStore/makeStoreWithThresholds(soft,hard,&backend,emitLag), spillableSliceFactory, populate(slice,worker,count,offset), forceSpill, isSliceResident, residentBytes/residentBytesOf, triggeredContains, getMetrics, sortedEntries. Representative: `unspillOnTriggerableReadRestoresEntries` (~line 265).

## Build (from BUILD.md)
All candidate files in target `nes-physical-operators` (CMakeLists.txt:21). Adding a method to existing hpp/cpp ⇒ NO CMake change. Test registered: `add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest ...)` tests/CMakeLists.txt:28 (no label ⇒ runs in regression gate). InMemorySpillBackend header-only. RocksDB PRIVATE to nes-physical-operators (forward-declared in public header) — keep the factory inside the lib; do not leak rocksdb to query-compiler.
