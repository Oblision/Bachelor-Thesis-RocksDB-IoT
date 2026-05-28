# 2a done — `SliceSerializer::partitionedSerialize`

## Signature added (SliceSerializer.hpp)
```cpp
static std::vector<SpillRecord> partitionedSerialize(
    const ChainedHashMap& map, uint64_t entrySize, uint64_t pageSize,
    uint64_t numberOfBuckets, uint64_t numberOfPartitions);
```
- Param is a plain `uint64_t numberOfPartitions` (a COUNT, deliberately NOT the `uint16_t PartitionId` index the 2b backend uses).
- Added `#include <vector>` to the header (return type).

## Helper extracted
- `writeHeader(SpillRecord&, entrySize, pageSize, numberOfBuckets, numEntries)` in the anonymous
  namespace of SliceSerializer.cpp. Writes the 6-field NSS1 header (MAGIC, VERSION, entrySize,
  pageSize, numberOfBuckets, numEntries) so every partition record is byte-shape-identical to a
  `serialize()` header, differing only in `numEntries`. Defined before `serialize`; reused only by
  `partitionedSerialize` (serialize still inlines its own header appends, unchanged).
- Added includes to the cpp: `<string>` (count diagnostic), `<vector>`, `<Util/Logger/Logger.hpp>`
  (NES_TRACE).

## Implementation notes (L1/L3/L4)
- **L1 fast-path:** `numberOfPartitions == 1` returns `{ serialize(map, ...) }` verbatim →
  byte-identical to `serialize`.
- **L4 two-pass:** Pass 1 counts `entriesPerPartition[hash % P]`; allocate P records, each with an
  exact `reserve()` and `writeHeader(... entriesPerPartition[p])`. Pass 2 walks again appending
  `[hash][payload]` into `records[hash % P]`. `entrySize/pageSize/numberOfBuckets` identical across
  all P; only `numEntries` differs.
- **L3:** always returns a vector of size exactly P; an empty partition yields a valid NSS1 record
  with `numEntries == 0`. No skip-empty here (that is the spill caller's job in a later step).
- **Validation (CannotSerialize factory, ErrorCode 2001):** `numberOfPartitions == 0` throws;
  `entrySize <= ENTRY_HEADER_SIZE` throws — same constant and check `serialize` uses. The P==1
  fast-path additionally inherits serialize()'s identical entrySize check.
- **NES_TRACE** logs per-partition counts only (no payload bytes):
  `"partitionedSerialize: P={} entries split as [{}]"`.
- `deserialize` UNCHANGED.

## ChainedHashMap entry-iteration API relied on (verbatim from `serialize`)
```cpp
for (uint64_t chain = 0; chain < numberOfChains; ++chain)
    for (const auto* entry = map.getStartOfChain(chain); entry != nullptr; entry = entry->next)
        { entry->hash;  /* payload at */ reinterpret_cast<const std::byte*>(entry) + ENTRY_HEADER_SIZE; }
```
- `map.getNumberOfChains()`, `map.getNumberOfTuples()`, `map.getStartOfChain(chain)`,
  `entry->next`, `entry->hash` (ChainedHashMap.hpp:80-87, 41-42).
- Empty-map guard copied from serialize: skip the walk when `getNumberOfTuples() == 0` because the
  bucket array is lazily allocated (`getStartOfChain` would deref an uninitialised array).
- Routing uses the per-entry stored `entry->hash` (the same hash serialize persists); `record[p] =
  hash % numberOfPartitions`.

## Tests added to SliceSerializerTest.cpp (existing fixture/populate/sortedEntries reused)
1. `partitionedSplitIsDisjointAndComplete` — COUNT=50, P=4: union of deserialized partitions ==
   full `serialize` entry set (disjoint + complete), total tuples == COUNT.
2. `partitionedEmptyPartitionsTolerated` — COUNT=3, P=8: asserts some partition has numEntries==0
   and deserializes to an empty map; total tuples == COUNT.
3. `partitionedSingleIsByteIdenticalToSerialize` — P==1 returns vector size 1 byte-equal to
   `serialize`.
4. `partitionedRejectsZeroPartitions` — P==0 throws `ErrorCode::CannotSerialize`.

## Assumptions / orchestrator double-checks at build time
- Used the test fixture's existing 4-arg ctor `ChainedHashMap{KEY_SIZE, VALUE_SIZE,
  NUMBER_OF_BUCKETS, PAGE_SIZE}` (matches surrounding tests). No CMake edits — `SliceSerializerTest`
  is an existing target; only test CASES added.
- `writeHeader` references `SliceSerializer::MAGIC` / `::VERSION` (public `static constexpr` →
  implicitly inline in C++17, no out-of-line def needed; `serialize` already uses them the same way).
- Disjointness in test 1 is proven by equal-size union == expected (no element duplicated when the
  multiset union equals the set). The per-partition `numEntries==0` round-trip exercises the empty
  body path of the existing `deserialize` (HEADER_SIZE-only record), which is already covered by the
  pre-existing `roundTripEmptyMap`, so no deserialize change is needed.

---

## Review fixes (2a-REVIEW.md — 3 Medium nits + 1 test gap), applied 2026-05-24

- **M1 (overflow guard on per-partition reserve sizing):** hoisted `bytesPerEntry = sizeof(uint64_t)
  + payloadSize` and added the overflow-safe check before each partition's `reserve()`, mirroring the
  exact idiom in `deserialize` (`x > (max - HEADER_SIZE) / bytesPerEntry`). Throws via the existing
  `CannotSerialize(...)` factory. Swapped the cpp include `<string>` → `<limits>` (needed for
  `std::numeric_limits`).
- **M3 (NES_TRACE counts string built unconditionally):** removed the manual P-allocation `std::string
  counts` loop; now passes `fmt::join(entriesPerPartition, ",")` directly into the `NES_TRACE(...)`
  fmt call so formatting is lazy (only runs when TRACE is active). Matches the codebase idiom in
  `MultiOriginWatermarkProcessor.cpp`. Added `#include <fmt/ranges.h>`; `<string>` no longer needed
  (resolves nit N3).
- **M2 (guard-ordering comment):** added a `///` comment above the `numberOfPartitions`/`entrySize`
  guards stating the validation MUST stay above the L1 `numberOfPartitions == 1` early-return so the
  multi-partition path is always validated. Comment only, no logic change.
- **Test gap:** added `partitionedRejectsTooSmallEntrySize` — calls `partitionedSerialize` with
  `entrySize = sizeof(ChainedHashMapEntry)` (too small) and `numberOfPartitions = 4`, asserts it
  throws `ErrorCode::CannotSerialize` (same code as the existing `rejectsTooSmallEntrySize`). Exercises
  the multi-partition validation path directly. Reuses the existing fixture/`populate`.

Files touched: `nes-physical-operators/src/SliceStore/SliceSerializer.cpp`,
`nes-physical-operators/tests/SliceSerializerTest.cpp`. Header untouched. No CMake/backend changes.
