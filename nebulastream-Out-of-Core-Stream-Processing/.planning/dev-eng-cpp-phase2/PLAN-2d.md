# PLAN — Phase 2 step 2d (partitioned terminal-emit + number_of_partitions knob)

Wires 2a+2b+2c0+2c1 together. Locked by DESIGN-BRIEF §5 "2d" + L1/L3/L6/L8. Gate ≥865 unit + differential P=1-vs-P=8 systest. Manual push.

## Architecture decision (DECIDED with code visibility)

**Seam: `AggregationOperatorHandler` OVERRIDES the virtual `triggerAllWindows`** (base `WindowBasedOperatorHandler::triggerAllWindows`, hpp:80, is virtual; the join handler keeps the base, untouched).
- `P = dynamic_cast<SpillableTimeBasedSliceStore*>(sliceAndWindowStore.get()) ? store->getNumberOfPartitions() : 1`.
- **P == 1 → delegate to `WindowBasedOperatorHandler::triggerAllWindows(pipelineCtx)`** verbatim. This GUARANTEES the P=1 path is byte-identical to today (the differential gate's baseline) — zero new code on that path.
- **P > 1 → partitioned terminal flush** (below).

**Why terminal only:** L6 — steady-state (`getTriggerableWindowSlices` → `unspillAndMarkEmitted` → `triggerSlices`) STAYS P=1, untouched. Only the terminal flush partitions.

**Resident-slice handling under P>1:** spill-then-stream (uniform). In the partitioned path, every slice of the window is ensured SPILLED P-way before streaming (a resident/never-spilled slice is spilled via the existing 2c1 `spill(backend, P)` so it has P blobs). Output is identical (same entries, routed through partitions) — only timing/IO differs, so the differential test still passes. Avoids needing a new in-memory partition-filter primitive; reuses tested code.

## Files + changes

### 1. `nes-query-compiler/interface/SpillConfig.hpp` (config knob)
- Add after `emitLag`: `UIntOption numberOfPartitions = {"number_of_partitions", "1", "Number of grace-hash partitions for spilled window state at terminal emit; 1 (default) = no partitioning (byte-identical to non-partitioned emit)."};`
- Register `&numberOfPartitions` in `getOptions()`.

### 2. `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` (plumb P)
- At the `SpillableTimeBasedSliceStore` ctor call site, pass `conf.spill.numberOfPartitions.getValue()` as the new trailing arg.
- ⚠️ Do NOT touch `conf.numberOfPartitions` at line ~205 (UNRELATED query-level bucket knob). Use the fully-qualified `conf.spill.numberOfPartitions`.

### 3. `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` + `.cpp` (carry P + partitioned streaming)
- Ctor: add trailing `uint64_t numberOfPartitions = 1`; store as `const uint64_t numberOfPartitions` member; init in ctor list.
- Add `[[nodiscard]] uint64_t getNumberOfPartitions() const { return numberOfPartitions; }`.
- Add **two** methods (the concurrency-sensitive part — MIRROR the existing `ensureWindowSlicesResident` discipline exactly):
  - `void claimWindowForPartitionedEmit(const std::vector<std::shared_ptr<Slice>>& windowSlices);`
    Per spillable slice, replicate `ensureWindowSlicesResident`'s **claim** half: under `evictionMutex` wait `spillerCv` until `!spillInProgress.contains(sliceEnd)`, then `emittedKeys.wlock()->insert(sliceEnd)` + `pinnedKeys.erase(sliceEnd)` BEFORE releasing the mutex (so no new spill can pick it). Then, OUTSIDE the mutex, ensure the slice is SPILLED P-way: if `spillable->isResident()`, reserve in `spillInProgress` (fence the footprint reader), `spillable->spill(*backend, numberOfPartitions)` under a `ReservationGuard`, erase `spilledKeys`?? (NO — spill ADDS to spilledKeys; mirror the spill sites at .cpp:600/731 for the exact spilledKeys/spillInProgress bookkeeping, do NOT copy the unspill bookkeeping). Net postcondition: every slice is emitted-claimed AND spilled P-way. Document that a slice already spilled is left as-is (it already has P blobs because spill always writes P when P>1).
  - `std::vector<std::unique_ptr<HashMap>> streamWindowPartition(const std::vector<std::shared_ptr<Slice>>& windowSlices, uint64_t partition);`
    For the fixed `partition`, for each spillable slice call `spillable->streamEmitByPartition(*backend, *provider, numberOfPartitions, partition, emit)` where `emit` appends the materialized W maps into the result vector. Slices STAY spilled. Returns all per-(slice,worker) partition-`partition` maps for the window.
- **CAUTION:** study the exact spilledKeys/spillInProgress/evictionMutex/spillerCv/ReservationGuard usage at the existing spill sites (`spill` call sites ~line 600 and ~731) and the unspill sites (ensureWindowSlicesResident 284-308) and mirror them. A wrong fence here is a UAF. If spilling-resident-at-terminal proves too entangled, FALL BACK: call the existing `ensureWindowSlicesResident(slices)` to make resident+claimed, then `spill(backend,P)` each — but prefer the direct claim to avoid an unspill→spill round-trip.

### 4. `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` + `.cpp` (P-chunk emit)
- Override `void triggerAllWindows(PipelineExecutionContext* pipelineCtx) override;`
- **Refactor** the emit core of `triggerSlices` (the buffer-alloc + metadata + `registerChunk` + placement-new `EmittedAggregationWindow` + `emitBuffer`, currently .cpp:114-167) into a private helper:
  `void emitChunk(const WindowInfo& windowInfo, SequenceNumber seq, ChunkNumber chunk, bool lastChunk, std::unique_ptr<HashMap> finalAccumulator, std::vector<std::unique_ptr<HashMap>> inputMaps, const std::vector<HashMap*>& allHashMapPtrs, uint64_t numberOfTuples, PipelineExecutionContext* pipelineCtx);`
  - It calls `registerChunk(seq, chunk, std::move(finalAccumulator), std::move(inputMaps))` and placement-news `EmittedAggregationWindow{windowInfo, finalHashMapPtr, seq, chunk, allHashMapPtrs}`, sets buffer metadata (originId, seq, **chunk**, **lastChunk**, watermark=windowStart, **numberOfTuples**), `emitBuffer`.
  - `triggerSlices` (P=1) calls `emitChunk(..., ChunkNumber::INITIAL, /*lastChunk=*/true, ..., inputMaps={}, allHashMaps, totalTuples, ...)` — preserving today's exact behavior (registerChunk's `inputs` stays empty at P=1; the slice-owned maps stay slice-owned, byte-identical).
- `triggerAllWindows` override (P>1):
  ```
  auto windows = sliceAndWindowStore->getAllNonTriggeredSlices();
  for (auto& [windowInfo, slices] : windows) {
      store->claimWindowForPartitionedEmit(slices);          // claim + spill P-way (once per window)
      for (uint64_t p = 0; p < P; ++p) {
          auto partitionMaps = store->streamWindowPartition(slices, p);   // vector<unique_ptr<HashMap>>, slices stay spilled
          // build the final accumulator for this partition from the materialized maps:
          uint64_t partitionTuples = 0; std::vector<HashMap*> ptrs; std::unique_ptr<ChainedHashMap> finalAcc;
          for (auto& m : partitionMaps) { if (m && m->getNumberOfTuples()>0) { ptrs.push_back(m.get()); partitionTuples += m->getNumberOfTuples(); if(!finalAcc) finalAcc = ChainedHashMap::createNewMapWithSameConfiguration(*dynamic_cast<ChainedHashMap*>(m.get())); } }
          // ALWAYS emit chunk p (L3 empty-chunk-p) so chunkNumber/lastChunk stay correct even if partitionTuples==0:
          emitChunk(windowInfo.windowInfo, windowInfo.sequenceNumber, ChunkNumber(ChunkNumber::INITIAL + p), /*lastChunk=*/(p==P-1), std::move(finalAcc), std::move(partitionMaps), ptrs, partitionTuples, pipelineCtx);
      }
  }
  ```
  - L3: emit chunk p even when `partitionTuples==0` (empty `partitionMaps` → registerChunk gets a null finalAccumulator → `finalHashMapPtr==nullptr`; the emitted buffer has 0 tuples; OPEN O1 confirmed ChunkCollector tolerates this — `ChunkCollector::collect` only checks seq/chunk validity, not tuple count).
  - **Ownership (L8):** `partitionMaps` (the freshly-deserialized W maps) are moved into `registerChunk`'s `inputs` so the handler registry OWNS them; `EmittedAggregationWindow` carries only raw `ptrs` + the (seq,chunk) key (trivially destructible). On probe drain, `releaseChunk(seq, INITIAL+p)` frees them; `stop()` sweeps any undrained. The 2c0 leak-totality holds at P>1.

## Tests
- `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp`: assert `number_of_partitions` is registered in `getOptions()`, default "1", parses a set value.
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`: a P>1 partitioned-emit drive — build a store with P>1, populate+complete a window, drive the terminal flush (or `claimWindowForPartitionedEmit` + `streamWindowPartition` directly), assert P chunks' union == the window's full aggregate, key-disjoint (no dup rows), each chunk's `numberOfTuples` is that partition's count, `lastChunk` only on p==P-1, and the leak-totality counters (`registered==released` after drain). Reuse the fixture's MockedPipelineContext if present (see 2c0's registry test).
- **Differential systest (the correctness gate, run SEPARATELY — Systest label is excluded from the unit gate):** find the spill systest (grep "SpillSmoke" / spill `.test` files) and add/extend a case running the SAME query with `number_of_partitions=1` and `=8`, asserting byte-identical output (same rows, no dups, no drops). This is the key-disjoint dup-row guard.

## Gates
- Unit: full build green + `ctest -LE 'Systest|DockerCompose'` ≥ 865 (no regression), incl. the new store + config tests, run UNPIPED (no `| tail` under set -e).
- Leak-totality at P>1: the store/handler test asserts registry `registered==released` + empty across drain + stop().
- Differential: P=1 vs P=8 identical output (separate systest run).

## Risks
- **Concurrency (highest):** `claimWindowForPartitionedEmit` must mirror the emittedKeys/spillInProgress/evictionMutex/spillerCv/ReservationGuard discipline EXACTLY or risk a spill-vs-emit UAF. Concurrency-focused review REQUIRED.
- **0-tuple chunk (O1):** RESOLVED — see FINAL DESIGN below; empty chunks are NOT emitted (skip-empty-renumber), so the probe never crashes.
- **P=1 byte-identity:** guaranteed by delegating to the base `triggerAllWindows` + the shared `emitChunk` helper with INITIAL/lastChunk=true/empty-inputs.

---

# FINAL DESIGN (post arch-2d — supersedes "Approach A" above)

**Approach B (claim + stream-spilled / filter-resident) — chosen over A (re-spill-uniform).** A would re-spill resident slices through RocksDB for data already in memory and widen the concurrency surface against the live spiller for zero output benefit. The spiller is provably LIVE during `triggerAllWindows` (it stops only in `deleteState`/dtor — `SpillableTimeBasedSliceStore.cpp:523,147` — never on the `terminate` path `WindowBuildPhysicalOperator.cpp:104-108`), so the per-slice claim is MANDATORY.

### Store (`SpillableTimeBasedSliceStore.{hpp,cpp}`)
1. Ctor `+ uint64_t numberOfPartitions = 1`; `const uint64_t numberOfPartitions` member; `[[nodiscard]] uint64_t getNumberOfPartitions() const`.
2. **Extract** `[[nodiscard]] bool claimSliceForEmit(const std::shared_ptr<SpillableAggregationSlice>&);` from the claim half currently inline at `.cpp:260-283` (wait `spillerCv` on `spillInProgress` under `evictionMutex`; `emittedKeys.insert` + `pinnedKeys.erase` BEFORE releasing the mutex; return `needsUnspill = !isResident()`). Rewrite `ensureWindowSlicesResident` to call it then keep its existing unspill half (`:284-308`). The claim must NOT touch `spillInProgress` (that fence is only for the unspill path). This keeps the load-bearing concurrency logic in ONE place.
3. `void claimWindowForPartitionedEmit(const std::vector<std::shared_ptr<Slice>>&);` — call `claimSliceForEmit` on each spillable slice (discard the return; partitioned emit does not unspill). Post: every slice emitted-claimed; spiller can never touch them; residency now stable.
4. `[[nodiscard]] std::vector<std::unique_ptr<HashMap>> streamWindowPartition(const std::vector<std::shared_ptr<Slice>>&, uint64_t partition);` — `provider = bufferProvider.copy()`; per spillable slice: `!isResident()` → `streamEmitByPartition(*backend,*provider,numberOfPartitions,partition,emit)` (stays spilled); `isResident()` → `materializeResidentPartition(*provider,numberOfPartitions,partition)`. Append all per-(slice,worker) maps to the result. Runs OUTSIDE `evictionMutex` (safe: slices claimed).

### Slice (`SpillableAggregationSlice.{hpp,cpp}`) — one new pure read-only primitive
`[[nodiscard]] std::vector<std::unique_ptr<HashMap>> materializeResidentPartition(AbstractBufferProvider&, uint64_t numberOfPartitions, PartitionId partition) const;` — `PRECONDITION(resident, ...)`. For each worker map, build a fresh `ChainedHashMap` containing only entries with `hash % numberOfPartitions == partition`, renting from the provider (reuse the entry-iteration + insertEntry+payload-memcpy shape used by `deserialize`/`unspill`'s merge). Read-only (slice unchanged). Skip empty (a worker contributing no entries to partition p → no map). Mirrors `partitionedSerialize`'s `hash % P` routing so spilled and resident paths produce identical partitions.

### Handler (`AggregationOperatorHandler.{hpp,cpp}`)
1. **Refactor** the emit core (`.cpp:114-167`) into private `void emitChunk(const WindowInfo&, SequenceNumber seq, ChunkNumber chunk, bool lastChunk, std::unique_ptr<HashMap> finalAccumulator, std::vector<std::unique_ptr<HashMap>> inputMaps, const std::vector<HashMap*>& allHashMapPtrs, uint64_t numberOfTuples, PipelineExecutionContext*);` — calls `registerChunk(seq, chunk, std::move(finalAccumulator), std::move(inputMaps))`, placement-news `EmittedAggregationWindow{windowInfo, finalHashMapPtr, seq, chunk, allHashMapPtrs}`, sets metadata (chunk, lastChunk, numberOfTuples per-args; originId/seq/watermark as today), `emitBuffer`. `triggerSlices` (P=1) calls it with `INITIAL`, `lastChunk=true`, `inputMaps={}` → byte-identical to today.
2. Override `void triggerAllWindows(PipelineExecutionContext*) override;`:
   - `auto* store = dynamic_cast<SpillableTimeBasedSliceStore*>(sliceAndWindowStore.get()); P = store ? store->getNumberOfPartitions() : 1;`
   - `P==1` → `WindowBasedOperatorHandler::triggerAllWindows(pipelineCtx)` (byte-identical).
   - `P>1` → `auto windows = sliceAndWindowStore->getAllNonTriggeredSlices();` then per window:
     - `store->claimWindowForPartitionedEmit(slices);`
     - First pass p=0..P-1: `auto maps = store->streamWindowPartition(slices, p);` build that partition's accumulator (`finalAcc = createNewMapWithSameConfiguration` from first non-empty; `ptrs`; `tuples`). Collect the NON-EMPTY partitions into an ordered list `[(maps, finalAcc, ptrs, tuples)]` (size `K`).
     - **REVISED L3 — emit only the K non-empty partitions, contiguous renumber:** for `j` in `[0,K)`: `emitChunk(windowInfo.windowInfo, windowInfo.sequenceNumber, ChunkNumber(ChunkNumber::INITIAL + j), /*lastChunk=*/(j==K-1), std::move(finalAcc_j), std::move(maps_j), ptrs_j, tuples_j, pipelineCtx);`
     - If `K==0` (does not occur for a triggered window): fall back to `ensureWindowSlicesResident(slices)` + one `emitChunk(..., INITIAL, lastChunk=true, null, {}, {}, 0, ...)` (matches P=1 empty-window emit).
   - `ChunkNumber(ChunkNumber::INITIAL + j)` is valid (INITIAL=1, raw uint arithmetic + explicit ctor; `NESStrongType` has no `operator+`).
3. **Ownership (L8/2c0):** `maps_j` move into `registerChunk` `inputs` → handler registry owns them; `EmittedAggregationWindow` carries only raw `ptrs` + (seq,chunk); probe `releaseChunk(seq, INITIAL+j)` frees on drain; `stop()` sweeps. Leak-totality holds at P>1.

### NO probe edit. (Skip-empty means the probe never sees a null/empty final map — its P=1 invariant is preserved.)

### Tests
- `SpillConfigTests.cpp`: `number_of_partitions` registered, default "1", parses.
- `SpillableTimeBasedSliceStoreTest.cpp`: P>1 drive — `claimWindowForPartitionedEmit` + `streamWindowPartition` over a window with BOTH spilled and resident slices AND a P large enough that some partition is empty (exercise skip-empty + renumber + resident-filter). Assert union of streamed partitions == full window aggregate (key-disjoint, complete); the handler emits exactly K contiguous chunks with lastChunk on the last; leak counters `registered==released` after drain + stop().
- `SpillableAggregationSliceTest.cpp`: `materializeResidentPartition` — union over p == resident map; disjoint; slice stays resident; empty partition → no map.
- **Differential systest (separate run, Systest label):** same query `number_of_partitions=1` vs `=8` → byte-identical output rows. Find via grep for the existing spill systest.
