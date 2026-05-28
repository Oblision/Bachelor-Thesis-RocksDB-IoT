# 2c1 — partition-aware spill + per-partition streaming read-out (DONE)

Scope: `SpillableAggregationSlice` only. Files touched (the 3 owned):
- `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp`
- `nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp`
- `nes-physical-operators/tests/SpillableAggregationSliceTest.cpp`

No store / handler / serializer / backend / CMake edits. Did NOT build (orchestrator owns the Docker gate).

## Exact signatures added (with defaults)

```cpp
// public
void spill(SpillBackend& backend, uint64_t numberOfPartitions = 1);

void streamEmitByPartition(
    SpillBackend& backend,
    AbstractBufferProvider& bufferProvider,
    uint64_t numberOfPartitions,
    PartitionId partition,
    const std::function<void(std::vector<std::unique_ptr<HashMap>>)>& emit) const;

void unspill(SpillBackend& backend, AbstractBufferProvider& bufferProvider, uint64_t numberOfPartitions = 1);

// private
void mergePartitionsInto(
    std::unique_ptr<HashMap>& base,
    SpillBackend& backend,
    AbstractBufferProvider& bufferProvider,
    WorkerThreadId worker,
    uint64_t numberOfPartitions) const;
```

The two default params (`numberOfPartitions = 1`) keep the 5 existing `spill(*backend)` /
`unspill(*backend,*provider)` callers in `SpillableTimeBasedSliceStore.cpp` (lines 296/352/442/600/731)
source-compatible — they bind to the P=1 fast path unchanged.

## P==1 fast path (L1)
`spill(...,1)` and `unspill(...,1)` take an explicit `if (numberOfPartitions == 1)` branch that calls the
*verbatim* historic code: `SliceSerializer::serialize` + single `backend.put(getSliceEnd(), worker, record)`
(default p=0) on spill, and single `backend.get(getSliceEnd(), worker)` + `deserialize` on unspill. Behaviour
is byte-identical; only logging (`NES_DEBUG`/`NES_TRACE`, no payload bytes) was added around the loop.

## Skip-empty detection chosen (L3, write side)
A zero-entry partition record from `partitionedSerialize` is exactly the fixed header with no body, i.e.
`records[p].size() == SliceSerializer::HEADER_SIZE`. So the skip test is:
`if (records[p].size() <= SliceSerializer::HEADER_SIZE) continue;` — no extra per-partition count needed, and
it reuses the serializer's public `HEADER_SIZE` constant. (The per-partition numEntries lives inside that
header; an empty blob's body length is 0.)

## Merge helper + reused ChainedHashMap insertion API (unspill P>1)
Extracted a file-local `replayEntriesInto(dst, src, entrySize, bufferProvider)` (anon namespace) and a private
member `mergePartitionsInto(...)`. `mergePartitionsInto` does: `p=0` -> first PRESENT blob becomes `base`
(move the deserialized map in); each subsequent present blob -> `replayEntriesInto(base, temp, ...)`. Absent
blobs (skip-empty / never-populated) are skipped via `get().has_value()`.

`replayEntriesInto` reuses the *exact* entry-replay shape from `SliceSerializer::deserialize`
(`SliceSerializer.cpp:279-286`):
```cpp
const auto hash = readPod<uint64_t>(record, offset);
auto* const entry = static_cast<ChainedHashMapEntry*>(map->insertEntry(hash, &bufferProvider));
auto* const payload = reinterpret_cast<std::byte*>(entry) + ENTRY_HEADER_SIZE;
std::memcpy(payload, record.data() + offset, payloadSize);
```
i.e. for each source entry: `insertEntry(entry->hash, &bufferProvider)` then `memcpy` the
`entrySize - sizeof(ChainedHashMapEntry)` payload bytes from src entry to the freshly inserted dst entry.
Correctness rests on the locked invariant that a group key's hash fixes its partition (`hash % P`), so keys
are DISJOINT across partitions -> pure insert, no aggregate-combine (matches DESIGN-BRIEF O4 / L8).

## streamEmitByPartition (centerpiece, const, leaves slice spilled)
`PRECONDITION(!resident, ...)` (2d guarantees spilled before call — documented in the header + a comment) and
`PRECONDITION(partition < numberOfPartitions, ...)`. For the fixed partition it loops workers `[0,
hashMaps.size())`, does `backend.get(getSliceEnd(), worker, partition)`; on `has_value` it
`SliceSerializer::deserialize(*record, bufferProvider)` and `push_back`s into
`std::vector<std::unique_ptr<HashMap>>` (implicit `unique_ptr<ChainedHashMap>` -> `unique_ptr<HashMap>`
upcast). Absent blobs contribute nothing. Then `emit(std::move(collected))` — ownership transfers to the
callee (L8). `resident` stays false; the slice is never mutated. `NES_TRACE` logs partition index + #maps.

## Tests added (4) in SpillableAggregationSliceTest.cpp
1. `spillUnspillSinglePartitionUnchanged` — P==1 spill/unspill reproduces original (L1).
2. `streamEmitByPartitionUnionEqualsOriginalAndStaysSpilled` — P=4; union across all p == original
   (disjoint+complete); `isResident()` stays FALSE throughout.
3. `streamEmitReclaimsPagesBetweenPartitions` — drop partition-0 maps in `emit`, assert
   `getNumberOfAvailableBuffers()` returns to >= pre-stream count before partition 1 (1/P residency).
4. `spillPWayThenFullUnspillReproducesOriginal` — O4 gate: spill P=4 -> unspill(...,4) -> per-worker
   contents == original; empty worker 2 stays nullptr.

Test helpers refactored: `sortedEntries` now delegates to new `collectFromMap(const ChainedHashMap*, ...)`
+ `sortEntries(...)` so a worker map and a streamed partition map can both be collected.

## Orchestrator: double-check at build time
- `unique_ptr<ChainedHashMap>` -> `unique_ptr<HashMap>` upcast in `streamEmitByPartition` and
  `mergePartitionsInto`: safe because `HashMap` has `virtual ~HashMap() = default`
  (HashMap.hpp:32) and `ChainedHashMap final : public HashMap` overrides it (ChainedHashMap.hpp:79).
- `getNumberOfAvailableBuffers()` is a concrete `BufferManager` method (BufferManager.hpp:124, public),
  NOT on `AbstractBufferProvider`; the test calls it via the `std::shared_ptr<BufferManager> bufferManager`
  fixture member, so it resolves. (Returns the FREE count; `getNumOfPooledBuffers()` returns total pool size
  — don't confuse them.)
- `InMemorySpillBackend::contains`/`get`/`put` all take a `PartitionId partition = 0` (already from 2b);
  test uses `contains(..., 0)` for the P==1 case.
- `partitionedSerialize` already validates `numberOfPartitions != 0` and `entrySize`; the slice always
  passes P>=2 on the partitioned branch, P=1 on the fast path, so no new validation needed here.
- New includes added to the .cpp: `<cstddef> <cstring> <functional> <utility> <vector>` +
  `<Util/Logger/Logger.hpp>`; header adds `<functional> <memory> <vector>`.

## Review fixes (2c1-REVIEW.md) — applied 2026-05-24

Cleared 2 Medium + 1 Low. Touched ONLY `SpillableAggregationSlice.cpp` and
`SpillableAggregationSliceTest.cpp` (no header/store/serializer/backend/CMake).

- **M1 (Medium, real bug) — null-check `insertEntry` in `replayEntriesInto`** (`.cpp`):
  added `INVARIANT(inserted != nullptr, "insertEntry returned null: bufferProvider exhausted during partition merge");`
  immediately after the `insertEntry` call and before the `memcpy`. Mirrors the NES idiom for a
  resource/contract violation (terminate, not user input). Note: `SliceSerializer::deserialize`'s twin
  `insertEntry` call (SliceSerializer.cpp:282) currently omits the check; this sets the better precedent
  per the review without weakening the deserialize path (out of scope / not owned). Used INVARIANT, not a
  `CannotDeserialize` throw, because deserialize uses no throw for this case and the review names INVARIANT
  as the correct idiom here.
- **M4 (Medium, test vacuity) — `streamEmitReclaimsPagesBetweenPartitions`** (test): made non-vacuous.
  Switched P=4 → **P=2** (pigeonhole over two densely populated workers => at least one partition is
  non-empty). The test now loops partitions until it finds one with maps, and for that partition asserts:
  (a) `ASSERT_GT(mapsSeen, 0u)` — the partition actually materialized >=1 worker map;
  (b) `ASSERT_LT(availableWhileAlive, availableBefore)` — pages were actually rented while the maps were
  ALIVE (measured INSIDE the emit callback, before the maps destruct);
  (c) `EXPECT_GE(availableAfterDrop, availableBefore)` — those rented pages are RECLAIMED after the maps
  are dropped (original intent preserved).
  A final `ASSERT_TRUE(exercisedNonEmptyPartition)` guarantees the reclaim path was exercised at all, so a
  degenerate all-empty run can no longer pass silently.
- **M2 (Low, cheap) — comment in `streamEmitByPartition`** (`.cpp`): added a `///` note that
  `numberOfPartitions` is consumed only by the range PRECONDITION; the loop reads the single fixed
  `partition` index from the backend and never iterates over partitions (it is NOT an unused parameter).

Skipped (as instructed): dynamic_cast→static_cast Low in `mergePartitionsInto` — kept `dynamic_cast` +
`INVARIANT` to match the existing idiom and not weaken the check. Did NOT build (orchestrator owns Docker gate).
