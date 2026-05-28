# PLAN — Phase 2 step 2c0 (handler-owned registry; P=1 byte-identical)

## PR boundary
Single PR (one coherent ownership refactor): handler.hpp + handler.cpp + probe.cpp + new test. Reviewed as C++ (cpp-reviewer) with a lifetime/concurrency focus.

## Section 1 — `AggregationOperatorHandler.hpp`
- **Refactor `EmittedAggregationWindow` to trivially destructible.** Drop `std::unique_ptr<HashMap> finalHashMap`. Keep `WindowInfo windowInfo`, `HashMap* finalHashMapPtr`, `uint64_t numberOfHashMaps`, `HashMap** hashMaps` (names/types unchanged — probe reads them by member-ref). ADD trivial `SequenceNumber sequenceNumber; ChunkNumber chunkNumber;` (the registry key). New ctor: `EmittedAggregationWindow(WindowInfo, HashMap* finalHashMapPtr, SequenceNumber, ChunkNumber, const std::vector<HashMap*>& allHashMaps)` — sets members, `hashMaps = bit_cast<HashMap**>(this+1)`, copies allHashMaps into the tail. (No unique_ptr → trivially destructible.)
- **Registry on the handler.** Add nested `struct ChunkMaps { std::vector<std::unique_ptr<HashMap>> inputs; std::unique_ptr<HashMap> finalAccumulator; };` and member `folly::Synchronized<std::map<std::pair<SequenceNumber, ChunkNumber>, ChunkMaps>> chunkRegistry;` (protected). Counters: `std::atomic<uint64_t> chunksRegistered{0}; std::atomic<uint64_t> chunksReleased{0};` (protected).
- **Public API:**
  - `HashMap* registerChunk(SequenceNumber, ChunkNumber, std::unique_ptr<HashMap> finalAccumulator, std::vector<std::unique_ptr<HashMap>> inputs = {});` — inserts entry, `chunksRegistered++`, returns `finalAccumulator.get()` BEFORE moving in (raw ptr for the struct's finalHashMapPtr). At P=1 `inputs` is empty.
  - `void releaseChunk(SequenceNumber, ChunkNumber);` — erase entry; if erased>0 `chunksReleased++`. Idempotent.
  - `[[nodiscard]] uint64_t numRegisteredChunks() const;` `[[nodiscard]] uint64_t numReleasedChunks() const;` `[[nodiscard]] std::size_t pendingChunkCount() const;` (test accessors).
  - `void stop(QueryTerminationType, PipelineExecutionContext&) override;` (declare).
- Includes: `<atomic>`, `<folly/Synchronized.h>` (likely already via RollingAverage), `<utility>` (pair), `<Sequencing/SequenceData.hpp>` for SequenceNumber/ChunkNumber if not already (ChunkNumber used in probe/handler already).

## Section 2 — `AggregationOperatorHandler.cpp`
- **`triggerSlices`:** after building `finalHashMap` (`unique_ptr<ChainedHashMap>`) + `allHashMaps`:
  - `HashMap* finalPtr = nullptr;`
  - `if (finalHashMap) { finalPtr = registerChunk(windowInfo.sequenceNumber, ChunkNumber(ChunkNumber::INITIAL), std::move(finalHashMap)); }` (null window → no registration, finalPtr stays null — byte-identical to today's null unique_ptr).
  - placement-new: `new (tmp.data()) EmittedAggregationWindow{windowInfo.windowInfo, finalPtr, windowInfo.sequenceNumber, ChunkNumber(ChunkNumber::INITIAL), allHashMaps};`
  - Everything else (buffer sizing, metadata set: seq/chunk=INITIAL/lastChunk=true/watermark/numTuples, emitBuffer, NES_TRACE) UNCHANGED.
- **`registerChunk`:** `auto* raw = finalAccumulator.get(); chunkRegistry.withWLock([&](auto& reg){ reg.emplace(std::pair{seq,chunk}, ChunkMaps{std::move(inputs), std::move(finalAccumulator)}); }); chunksRegistered.fetch_add(1, relaxed); return raw;` + `NES_TRACE`.
- **`releaseChunk`:** `const auto erased = chunkRegistry.withWLock([&](auto& reg){ return reg.erase(std::pair{seq,chunk}); }); if (erased>0) chunksReleased.fetch_add(1, relaxed);` + `NES_TRACE`.
- **`stop`:** `chunkRegistry.withWLock([&](auto& reg){ chunksReleased.fetch_add(reg.size(), relaxed); reg.clear(); }); WindowBasedOperatorHandler::stop(type, ctx);` + `NES_DEBUG` (sweep count). (Base is empty today; call it for forward-compat.)
- Accessors: trivial loads / `chunkRegistry.rlock()->size()`.

## Section 3 — `AggregationProbePhysicalOperator.cpp`
- Replace the cleanup `nautilus::invoke` (currently `finalHashMap.reset()`, :160-169) with a handler release:
  ```cpp
  const auto handlerRef = executionCtx.getGlobalOperatorHandler(operatorHandlerId);
  nautilus::invoke(
      +[](OperatorHandler* handler, EmittedAggregationWindow* eaw) {
          auto* aggHandler = dynamic_cast<AggregationOperatorHandler*>(handler);
          PRECONDITION(aggHandler != nullptr, "AggregationProbe handler must be AggregationOperatorHandler");
          NES_TRACE("Releasing chunk seq={} chunk={} (window {}-{})", eaw->sequenceNumber, eaw->chunkNumber, eaw->windowInfo.windowStart, eaw->windowInfo.windowEnd);
          aggHandler->releaseChunk(eaw->sequenceNumber, eaw->chunkNumber);
      },
      handlerRef, aggregationWindowRef);
  ```
- `operatorHandlerId` is the protected base member (WindowProbePhysicalOperator.hpp:49). `getGlobalOperatorHandler` returns `nautilus::val<OperatorHandler*>` (idiom AggregationBuildPhysicalOperator.cpp:53-54, but here in traced `open` so pass the val into invoke). NO change to the combine/lower loops. Header for AggregationOperatorHandler already included (EmittedAggregationWindow lives there).

## Section 4 — NEW test `nes-physical-operators/tests/AggregationOperatorHandlerRegistryTest.cpp` + CMakeLists line
Fixture `BaseUnitTest` + real `BufferManager`. Reuse `MockedPipelineContext` (EmitPhysicalOperatorTest.cpp:63-111) to capture emitted buffers, and `SpillableAggregationSliceTest` helpers (`makeArgs`/`populate`) for slice construction. Drive emit via the public `triggerAllWindows(pipelineCtx)` after populating the slice store with non-triggered slices (or a minimal handler-level driver). Tests:
1. **registryBalancedOnCleanDrain** — trigger K windows (non-empty) → for each captured buffer read seq/chunk, call `handler.releaseChunk(seq,chunk)` (simulating the probe drain) → assert `pendingChunkCount()==0` AND `numReleasedChunks()==numRegisteredChunks()==K`.
2. **stopSweepsUndrainedChunks (mid-flush stop)** — trigger K, release J<K, then `handler.stop(QueryTerminationType::Graceful, ctx)` → assert `pendingChunkCount()==0` AND `numReleasedChunks()==numRegisteredChunks()==K` (J drained + (K-J) swept).
3. **stopSweepsAllWhenNoneDrained (shutdown)** — trigger K, release none, `handler.stop(...)` → assert empty + released==registered==K.
4. **emptyWindowRegistersNothing** — a window with all-empty slices emits a buffer but registers no chunk → `numRegisteredChunks()` unchanged; releaseChunk on its (seq,chunk) is a no-op.
5. **releaseChunkIdempotent** — double releaseChunk(seq,chunk) counts once.
6. (behavior-identical is covered by the existing systest aggregation suite + differential — keep green; note in test file.)
Register with `add_nes_physical_operator_test(AggregationOperatorHandlerRegistryTest AggregationOperatorHandlerRegistryTest.cpp)` in `nes-physical-operators/tests/CMakeLists.txt` (after line ~27). Label-free ⇒ runs in `ctest -LE 'Systest|DockerCompose'`.

## Logging
`registerChunk`/`releaseChunk` NES_TRACE (hot-ish, per chunk); `stop` sweep NES_DEBUG with count; probe release NES_TRACE.

## Build/test
- `ninja -C cmake-build-debug nes-physical-operators` (refactor) then full `ninja`.
- `ctest -R 'AggregationOperatorHandlerRegistry' --output-on-failure` (new test).
- Regression: `ctest -j8 -LE 'Systest|DockerCompose' --timeout 200` ≥ 835.

## Byte-identical checklist (must hold at P=1)
seq/chunk/lastChunk/watermark/numTuples metadata unchanged; combine loop + lowering unchanged; `hashMaps[]` still slice-owned; only the accumulator's OWNER moved (handler vs buffer) — the accumulator pointer the probe uses (`finalHashMapPtr`) is identical; output rows identical. Regression gate + existing aggregation systests prove it.
