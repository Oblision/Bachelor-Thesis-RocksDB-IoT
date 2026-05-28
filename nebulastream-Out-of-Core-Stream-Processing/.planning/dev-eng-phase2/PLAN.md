# Phase 2 — Slice serialization (ChainedHashMap ⇄ bytes)

Verified against HEAD c2a667536d (on feat/ooc-phase1-spill-skeleton). Goal: encode one ChainedHashMap to a
self-describing byte record and rebuild it, so spilled slices can round-trip through a SpillBackend.

## Decisions (codebase + DESIGN_BRIEF foreclose the alternatives)
- **Format:** hand-rolled binary. Repo only has Protobuf (query plans) + reflect-cpp (operator trees); neither fits
  raw runtime state, no FlatBuffers. DESIGN_BRIEF §"next pointer on disk" already settled Option B (drop next, rebuild chain).
- **Chain handling:** drop `next` (offset 0 of each entry); rebuild chains on deserialize by replaying `insertEntry`.
- **No nes-nautilus changes.** Serializer lives entirely in the spill layer (nes-physical-operators). Entry enumeration
  uses the public chain-scan API (getNumberOfChains/getStartOfChain/next). `entrySize`+`pageSize` are passed in by the
  caller (Phase 4 derives them from CreateNewHashMapSliceArgs) → keeps the diff isolated, upstream-merge friendly.
- **Fixed-size entries only.** Variable-sized keys/values (varSizedSpace) unsupported in Phase 2 (thesis = numeric aggs
  over tumbling windows). Documented precondition.

## Record layout
```
magic   : u32  (SLICE_RECORD_MAGIC)
version : u32  (= 1)
entrySize : u64
pageSize  : u64
numChains : u64   (map.getNumberOfChains())
numEntries: u64   (map.getNumberOfTuples())
then numEntries × { hash:u64, payload:(entrySize - sizeof(ChainedHashMapEntry)) bytes }
```
Native endianness (single-machine spill files). headerSize = 40 bytes.

## serialize(map, entrySize, pageSize) -> SpillRecord
- payloadSize = entrySize - sizeof(ChainedHashMapEntry); reserve; write header.
- chain-scan b in [0,numChains): for e=getStartOfChain(b); e; e=e->next: append e->hash + payload[(byte*)e + sizeof(entry)].
- count must equal numEntries else throw CannotSerialize.

## deserialize(record, bufferProvider) -> unique_ptr<ChainedHashMap>
- validate size>=40, magic, version; entrySize>=sizeof(entry); size == 40 + numEntries*(8+payloadSize) else CannotDeserialize.
- map = make_unique<ChainedHashMap>(entrySize, numChains, pageSize) (createNewMapWithSameConfiguration precedent: numChains round-trips).
- per entry: e = insertEntry(hash, &provider); memcpy((byte*)e + sizeof(entry), payload, payloadSize).

## Files
NEW:
- nes-physical-operators/include/SliceStore/SliceSerializer.hpp
- nes-physical-operators/src/SliceStore/SliceSerializer.cpp
- nes-physical-operators/include/SliceStore/InMemorySpillBackend.hpp   (map<SliceEnd, map<WorkerThreadId, SpillRecord>>; real reusable component + test backend)
- nes-physical-operators/tests/SliceSerializerTest.cpp                 (empty map, populated map round-trip, through-backend round-trip, corrupt-record rejection)
- nes-physical-operators/tests/InMemorySpillBackendTest.cpp            (put/get/miss/erase-by-slice)
EDIT:
- nes-physical-operators/src/SliceStore/CMakeLists.txt  (+ SliceSerializer.cpp)
- nes-physical-operators/tests/CMakeLists.txt           (+ 2 tests)

## Test build/populate (Nautilus-free)
ChainedHashMap m{keySize,valueSize,numBuckets,pageSize}; for each: e=static_cast<ChainedHashMapEntry*>(m.insertEntry(hash, bm.get()));
write payload bytes at (byte*)e + sizeof(ChainedHashMapEntry). Compare entry multiset (hash+payload) before/after.
BufferManager::create(). Round-trip equality is bucket-count-invariant (compare data, not internal layout).
