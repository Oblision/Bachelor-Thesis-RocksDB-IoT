# SYNTHESIS — Phase 2 step 2c0 (handler-owned registry ownership refactor, P=1 behavior-identical)

## Scope (exactly what changes)
Move the `finalHashMap` accumulator OFF the recycle-only emit buffer into a mutex-guarded registry owned by `AggregationOperatorHandler`, keyed `(SequenceNumber, ChunkNumber)`, freed per-chunk on probe drain-ack + swept in `AggregationOperatorHandler::stop()`. Make `EmittedAggregationWindow` trivially destructible (raw pointers + key only). At P=1 (no partitioning) output is byte-identical. Fixes the RED lifetime audit (`exploration/EMITTEDWINDOW-LIFETIME-AUDIT.md`).

## Files affected
1. `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` — struct refactor; add registry + counters + accessors + `releaseChunk`/`registerChunk` + `stop` override decl.
2. `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp` — `triggerSlices` registers the accumulator instead of moving it into the struct; implement `registerChunk`/`releaseChunk`/`stop`.
3. `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp` — swap the `finalHashMap.reset()` invoke for a `releaseChunk(seq,chunk)` invoke (fetch handler via `getGlobalOperatorHandler` + `dynamic_cast`).
4. `nes-physical-operators/tests/AggregationOperatorHandlerRegistryTest.cpp` (NEW) + 1 line in `nes-physical-operators/tests/CMakeLists.txt`.

No new production files, no CMake source edits, no vcpkg/toolchain change (BUILD.md confirmed).

## Key facts from exploration (confirmed at HEAD 05c8520dbb)
- `EmittedAggregationWindow` (handler.hpp:39-55) has exactly ONE non-trivial member: `unique_ptr<HashMap> finalHashMap` (:52). Today's `finalHashMap.reset()` (probe :167) is "accidentally total" only because it's the sole non-trivial member.
- Probe reads struct members by name via `getMemberRef` (probe :63-70): `numberOfHashMaps`, `windowInfo`, `hashMaps`, `finalHashMapPtr`. These MUST keep their names/types. We only DROP `unique_ptr<HashMap> finalHashMap` and ADD trivial `sequenceNumber`/`chunkNumber`.
- `hashMaps` = `this+1` tail (raw pointer array) — keep.
- At P=1, `triggerSlices` builds `finalHashMap` via `ChainedHashMap::createNewMapWithSameConfiguration` (handler.cpp:108) and `allHashMaps` = slice-owned raw ptrs (:104). Only `finalHashMap` is handler-allocated; inputs stay slice-owned (protected by `emittedKeys`/GC). So registry holds only the accumulator at P=1 (`inputs` vector empty; reserved for 2d partition reads).
- Threading: `triggerSlices` runs on the build worker; probe `open()` on a (possibly different) probe worker. Registry needs a lock. Handler already uses `folly::Synchronized` (handler.hpp:79) → use it for the registry.
- `WindowBasedOperatorHandler::stop` (WindowBasedOperatorHandler.cpp:50) is virtual + empty; joins do NOT override it. Override on `AggregationOperatorHandler` (call base, then sweep) — keeps agg logic off the shared base (project-memory pattern). Confirm virtual in the hpp during impl.
- Probe handler fetch idiom: `getGlobalOperatorHandler(operatorHandlerId)` + `dynamic_cast` (per `AggregationBuildPhysicalOperator.cpp:53-54`). Verify exact API during impl.
- Leak detection: ASAN NOT a usable gate vs the `local-rocksdb` image (triplet `none`; upstream CI runs ASAN with `detect_leaks=0`). PRIMARY gate = counter-based (registry `size()` + registered/released counters), asserted in a normal gtest → runs in the ≥835 regression gate.
- Test harness: `MockedPipelineContext` (EmitPhysicalOperatorTest.cpp:63-111) overrides `emitBuffer` to capture buffers, backed by a real `BufferManager` (supplies `getUnpooledBuffer`). Slice construction reuses `SpillableAggregationSliceTest` `makeArgs`/`populate`.

## Risks / lifetime
- **Trivially-destructible struct:** dropping `unique_ptr<HashMap>` makes the struct trivially destructible (raw ptrs + trivial key) — the buffer can be dropped on teardown with no leak (the whole point). Verify `WindowInfo`, `SequenceNumber`, `ChunkNumber` are trivially destructible (they are: PODs/NESStrongType).
- **Per-chunk release must be exact:** `releaseChunk` increments the released-counter only when it actually erases an entry (no double-count). `stop()` sweep counts the swept entries as released so the invariant `released == registered` holds when the registry is empty.
- **Empty windows:** when `finalHashMap` is null (window with 0 non-empty slices), DO NOT register an entry and set `finalHashMapPtr=null` — byte-identical to today (where the unique_ptr was null and reset() was a no-op). The probe's `releaseChunk` for such a (seq,chunk) finds no entry → no-op. Counters unaffected.
- **Double-free safety:** probe combine loop reads `hashMaps[]` read-only (probe :75-130; only free is the cleanup invoke) → registry-owned accumulator is freed exactly once (release or sweep). Confirmed.
- **Lock ordering:** registry lock is on the AGG handler, distinct from the slice store's `evictionMutex`; never nested (the registry critical section makes no store calls). No cycle.

## Locked decisions (from DESIGN-BRIEF — reviewers must NOT flag)
L1 P==1 fast-path / byte-identical; L8+G3 Option 2 = handler-owned registry, buffer trivially-destructible, per-chunk release on drain-ack + stop() sweep; G1/G2/G5 MOOT (buffer owns no heap). NO Nautilus/JIT combine-loop edit. Counter-based leak gate (not ASAN).

## Open questions for impl (resolve with code in hand)
- Exact `getGlobalOperatorHandler` signature + how to pass the handler val into the `nautilus::invoke` release proxy.
- `operatorHandlerId` accessibility from `AggregationProbePhysicalOperator` (base `WindowProbePhysicalOperator`).
- Confirm `WindowBasedOperatorHandler::stop` is `virtual` (override-able) in the hpp.
