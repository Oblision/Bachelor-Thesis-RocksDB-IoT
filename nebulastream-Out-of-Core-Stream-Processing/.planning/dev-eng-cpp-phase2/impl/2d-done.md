# 2d DONE — partitioned terminal-emit + `number_of_partitions` knob

Implements PLAN-2d **FINAL DESIGN (Approach B)**: claim + stream-spilled / filter-resident, REVISED L3
skip-empty + contiguous-renumber, handler-overrides-`triggerAllWindows`. No Nautilus/JIT/probe edit. P==1 delegates
verbatim to the base. DID NOT BUILD (orchestrator owns the Docker gate).

## Signatures added / changed

### `nes-query-compiler/interface/SpillConfig.hpp`
- ADDED `UIntOption numberOfPartitions = {"number_of_partitions", "1", "..."}` after `emitLag`; registered last in `getOptions()`.

### `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`
- Pass `conf.spill.numberOfPartitions.getValue()` as the new trailing arg to the `SpillableTimeBasedSliceStore` ctor (line ~248).
  Did NOT touch `conf.numberOfPartitions` at line ~205 (that is `numberOfBuckets`, unrelated). Added P to the NES_INFO log line.

### `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.{hpp,cpp}`
- Ctor: trailing `uint64_t numberOfPartitions = 1`; new `const uint64_t numberOfPartitions` member (init in list, PRECONDITION>=1, logged).
- `[[nodiscard]] uint64_t getNumberOfPartitions() const` (inline header).
- **EXTRACTED** `[[nodiscard]] bool claimSliceForEmit(const std::shared_ptr<SpillableAggregationSlice>&)` from the claim half of
  `ensureWindowSlicesResident` (was inline at the old `.cpp:260-283`). It does: wait `spillerCv` on `spillInProgress` under
  `evictionMutex`; `emittedKeys.insert` + `pinnedKeys.erase` BEFORE releasing the mutex; returns `needsUnspill = !isResident()`.
  It deliberately **does NOT touch `spillInProgress`** (that fence belongs only to the unspill path).
- `ensureWindowSlicesResident` rewritten to call `claimSliceForEmit(spillable)` then keep its unspill half UNCHANGED in behavior.
  The unspill half now re-acquires `evictionMutex` to insert the `spillInProgress` reservation (the claim no longer holds it across the
  boundary) — safe because the slice is already emitted-claimed, so no spiller can pick it in between. **`ensureWindowSlicesResident`
  observable behavior is preserved**: same wait → claim → (reserve → unspill → erase-spilledKeys → release-reservation → notify) → committed.
- ADDED `void claimWindowForPartitionedEmit(const std::vector<std::shared_ptr<Slice>>&)` — calls `claimSliceForEmit` per spillable slice,
  discards the return (no unspill). Post: every slice emitted-claimed, residency stable, spiller fenced off.
- ADDED `[[nodiscard]] std::vector<std::unique_ptr<HashMap>> streamWindowPartition(const std::vector<std::shared_ptr<Slice>>&, uint64_t partition)`
  — `provider = bufferProvider.copy()`; per spillable slice: resident → `materializeResidentPartition`; spilled → `streamEmitByPartition`.
  Runs OUTSIDE `evictionMutex` (safe: slices claimed). Returns the window's per-(slice,worker) maps for that one partition.
- Forward-declared `class SpillableAggregationSlice;` and added `#include <Nautilus/Interface/HashMap/HashMap.hpp>` to the hpp.

### `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.{hpp,cpp}`
- ADDED `[[nodiscard]] std::vector<std::unique_ptr<HashMap>> materializeResidentPartition(AbstractBufferProvider&, uint64_t numberOfPartitions, PartitionId partition) const`
  — pure read-only `PRECONDITION(resident,...)`; per worker map, build a fresh `ChainedHashMap::createNewMapWithSameConfiguration`
  containing only entries with `hash % numberOfPartitions == partition`, renting from the provider (reuses the exact
  `getStartOfChain`/`insertEntry`/payload-`memcpy` shape used by `deserialize` and `replayEntriesInto`, with the `hash % P` routing
  `partitionedSerialize` uses). Source maps untouched, slice stays resident, empty workers/partitions skipped (no map).

### `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.{hpp,cpp}`
- **REFACTORED** the emit core of `triggerSlices` (old `.cpp:114-167`: buffer alloc + metadata + `registerChunk` + placement-new
  `EmittedAggregationWindow` + `emitBuffer`) into a new private:
  `void emitChunk(const WindowInfo&, SequenceNumber, ChunkNumber, bool lastChunk, std::unique_ptr<HashMap> finalAccumulator,
   std::vector<std::unique_ptr<HashMap>> inputMaps, const std::vector<HashMap*>& allHashMapPtrs, uint64_t numberOfTuples, PipelineExecutionContext*)`.
  `triggerSlices` (P=1) now calls `emitChunk(..., INITIAL, lastChunk=true, std::move(finalHashMap), inputMaps={}, allHashMaps, totalTuples, ...)`
  → byte-identical to before (registerChunk still only runs when the accumulator is non-null; inputMaps stays empty at P=1).
  The `CannotAllocateBuffer` message changed "hash join window trigger" → "aggregation window trigger" (cosmetic; no test asserts it).
- OVERRIDE `void triggerAllWindows(PipelineExecutionContext*) override`:
  - `P = dynamic_cast<SpillableTimeBasedSliceStore*>(sliceAndWindowStore.get()) ? store->getNumberOfPartitions() : 1`.
  - **P==1 → `WindowBasedOperatorHandler::triggerAllWindows(pipelineCtx)` verbatim** (byte-identical baseline).
  - P>1 → per window: `claimWindowForPartitionedEmit(slices)`; pass 1 builds each partition via `streamWindowPartition`, collecting the
    **K NON-EMPTY** partitions (accumulator = `createNewMapWithSameConfiguration` from the first non-empty map; ptrs; tuples); pass 2 emits
    them as **contiguous** chunks `ChunkNumber(ChunkNumber::INITIAL + j)` for j in [0,K), `lastChunk=(j==K-1)`, `numberOfTuples`=that
    partition's count, `inputMaps`=that partition's maps (moved into `registerChunk` → handler-owned, L8). K==0 fallback =
    `ensureWindowSlicesResident(slices)` + one P=1-style empty chunk (INITIAL/lastChunk=true/null acc).
- Added `#include <SliceStore/SpillableTimeBasedSliceStore.hpp>` to the handler .cpp.

## The concurrency seam (the load-bearing part)
- `claimSliceForEmit` is the SINGLE shared copy of the claim discipline; the partitioned terminal flush runs it while the
  **background spiller is LIVE**. The claim keeps `emittedKeys.insert` + `pinnedKeys.erase` BEFORE releasing `evictionMutex` (so the
  spiller's `pickAndReserveColdSlice` can never re-pick a now-emitted slice), and **does NOT touch `spillInProgress`** (the unspill fence).
  `streamWindowPartition` runs OUTSIDE `evictionMutex` because the claim already made residency stable. No spill-vs-emit UAF.

## Ownership / leak-totality (L8)
- The freshly-materialised partition maps are moved into `registerChunk`'s `inputs`, so `AggregationOperatorHandler::chunkRegistry` owns
  them; `EmittedAggregationWindow` stays trivially destructible (raw ptrs + (seq,chunk) key). Probe drain → `releaseChunk(seq, INITIAL+j)`;
  `stop()` sweeps undrained. `registered==released` holds at P>1.

## Tests added (CASES into existing, registered files)
- `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp`: `defaults` asserts `numberOfPartitions==1`; NEW `numberOfPartitionsParsesSetValue`,
  `numberOfPartitionsEmbeddedCommandLineOverride` (spill.number_of_partitions dotted key).
- `nes-physical-operators/tests/SpillableAggregationSliceTest.cpp`: NEW `materializeResidentPartitionUnionEqualsResidentAndStaysResident`
  (union==resident, disjoint+complete, slice stays resident, sources unchanged), `materializeResidentPartitionEmptyPartitionProducesNoMap`
  (empty partition → no map), `materializeResidentPartitionMatchesSpilledStream` (resident filter == spilled stream for the same partition).
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`: NEW `makeStoreWithPartitions` helper; NEW
  `partitionedEmitUnionEqualsWindowMixedResidentAndSpilled` (a window mixing a SPILLED and a RESIDENT slice, P=8 so ≥1 partition empty;
  drives `claimWindowForPartitionedEmit` + loop `streamWindowPartition`; asserts union==full window key-disjoint+complete, residency
  unchanged across streaming, skip-empty exercised (1 < K < P), and post-claim `forceSpill` is refused = spiller fenced); plus
  `defaultStoreReportsSinglePartition`, `configuredStoreReportsPartitionCount`.
- `nes-physical-operators/tests/AggregationOperatorHandlerRegistryTest.cpp`: NEW Group D
  `partitionedTriggerEmitsKContiguousChunksAndSkipsEmpty` — drives the REAL `handler->triggerAllWindows()` over a real
  `SpillableTimeBasedSliceStore` (P=8) via the existing `MockedPipelineContext`; asserts 1 < K < P contiguous chunks
  [INITIAL..INITIAL+K-1], exactly one `lastChunk` (the highest), every emitted chunk has >0 tuples (skip-empty), total tuples == full window
  (key-disjoint+complete), `registered==released` after drain AND after `stop()`.

## Differential systest (PARTIAL — orchestrator must run)
- The `.test` format compares against a FIXED expected block, so it cannot vary the worker config between two query blocks. The
  P=1-vs-P=8 differential is therefore a **runner-level** comparison (same query, two configs, diff output), exactly like `run-rss.sh`.
- ADDED `.planning/eval-phase/phase2d/SpillPartitionDifferential.test` — a 20-window keyed tumbling GROUP BY SUM that spills and exercises
  the partitioned terminal flush at P>1 (Generator: key 0..50000, ts 0..2_000_000, WINDOW 100_000ms). Carries a placeholder expected block.
- ADDED `.planning/eval-phase/phase2d/run-spill-differential.sh` (executable) — runs the test twice inside the `local-rocksdb` image
  with `--worker.default_query_execution.spill.number_of_partitions=1` then `=8` (and `spill.enabled=true` + emit_lag band so it spills),
  collects each run's `<workingDir>/results/*.csv` (path from `SystestState.cpp:167`), sorts, and `diff`s them — PASS iff identical.
  **ORCHESTRATOR TODO**: run `./run-spill-differential.sh 512 16000 4 8 200000 2`; confirm PASS. Adjust the `--workingDir` results-glob if
  the systest harness output layout differs from `SystestState.cpp:167` at build time. The unit Group D test already proves K-contiguous /
  skip-empty / disjoint+complete deterministically, so the systest is the end-to-end confirmation, not the only correctness evidence.

## Orchestrator double-checks at build time
1. **Ctor-arg ripple**: the new trailing `numberOfPartitions=1` is defaulted, so all existing construction sites
   (`SpillableTimeBasedSliceStoreTest.cpp:160/181/202`, `SpillableTimeBasedSliceStoreConcurrencyTest.cpp:211`, the lowering) stay
   source-compatible. Only the lowering was edited to pass a non-default; the new test helper passes it explicitly. Confirm no other site.
2. **unique_ptr upcasts**: `triggerSlices` passes `std::move(finalHashMap)` (`unique_ptr<ChainedHashMap>`) into `emitChunk`'s
   `unique_ptr<HashMap>` param — implicit upcast (HashMap has a virtual dtor). `streamEmitByPartition`/`deserialize` already return
   `unique_ptr<ChainedHashMap>` upcast to `unique_ptr<HashMap>` (pre-existing). The P>1 `finalAccumulator` local is `unique_ptr<HashMap>`.
3. **Arg-eval order** in pass-2 `emitChunk(... std::move(pending.maps) ... pending.ptrs ...)`: moving the vector relocates the vector buffer
   but NOT the heap `HashMap` objects, so the raw `HashMap*` in `pending.ptrs` stay valid regardless of evaluation order (same shape as the
   pre-existing `triggerSlices` `allHashMaps` + moved `finalHashMap`).
4. Gate: `ctest -LE 'Systest|DockerCompose'` (the unit gate) — should pick up the new cases in the four already-registered test targets;
   the differential systest is run SEPARATELY (Systest label / the runner script).
