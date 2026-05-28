# STORE-SPILL: Slice Store + Spill Backend + Serializer internals (for OOC L1/L2/L3 redesign)

Branch `feat/ooc-phase1-spill-skeleton` @ `1f30a75822`. All paths absolute under the repo root
`nebulastream-Out-of-Core-Stream-Processing/`.

## Summary

The spill subsystem has three layers:

- **SpillBackend** (`SpillBackend.hpp`) is a 3-method KV interface keyed by `(SliceEnd, WorkerThreadId)`.
  Granularity is **one opaque blob per (sliceEnd, workerThreadId)** — i.e. one blob *per worker hash-map*, NOT
  per-key. There are typically a handful of records per slice (one per worker thread that touched it).
- **SliceSerializer** (`SliceSerializer.hpp/.cpp`) turns ONE `ChainedHashMap` ⇄ one self-describing byte blob.
  It walks entries one at a time on the way out and replays `insertEntry` on the way in. It is *internally*
  streamable but its public API is whole-map (`serialize(map)→bytes`, `deserialize(bytes)→unique_ptr<map>`).
- **SpillableAggregationSlice** (`SpillableAggregationSlice.hpp/.cpp`) holds one `ChainedHashMap` per worker;
  `spill()` serializes+frees all of them, `unspill()` rebuilds all of them from the backend renting pages from
  the **global buffer manager** (passed as `AbstractBufferProvider&`).
- **SpillableTimeBasedSliceStore** orchestrates: residency tracking, a background `jthread` spiller, soft/hard
  byte thresholds, and **unspill-on-read** (the current terminal-flush behavior the redesign targets).

**Crucial L2 finding:** RocksDB stores each window as a SMALL SET OF BLOBS (one per worker), each blob being a
fully-serialized hash map. It is **NOT** a range of `(sliceEnd|key)→value` records. A RocksDB iterator can range
over the per-worker blobs of one slice (the big-endian prefix makes them contiguous — `erase()` already does this),
but the (key→aggregate) records live *inside* each blob and are only accessible by deserializing the whole blob.
So L2 "stream records without a full deserialize" is **not possible at the storage layer as-is** — it requires a
storage-format change (see L2 STORAGE FEASIBILITY).

---

## 1. SpillBackend interface, granularity, and RocksDB on-disk layout

### 1a. The interface (`nes-physical-operators/include/SliceStore/SpillBackend.hpp:40-66`)

```cpp
using SpillRecord = std::vector<std::byte>;                 // L29: owning value payload

class SpillBackend {
public:
    // L54
    virtual void put(SliceEnd sliceEnd, WorkerThreadId workerThreadId, std::span<const std::byte> record) = 0;
    // L61  (TODO note: owning return forces a copy out of RocksDB's pinned slice)
    virtual std::optional<SpillRecord> get(SliceEnd sliceEnd, WorkerThreadId workerThreadId) = 0;
    // L65  (idempotent: erases EVERY per-thread record of the slice)
    virtual void erase(SliceEnd sliceEnd) = 0;
};
```

Copy/move deleted (used only via `unique_ptr<SpillBackend>`). **There is no batch/iterator/scan method and no
per-key API.** The header comment (L36) states a slice is identified by `SliceEnd`; there is no `SliceId` type.

### 1b. Granularity = whole-map blob, one per worker

- `NullSpillBackend.hpp:34-38` — all no-ops; `get` always misses (default while spilling disabled).
- `InMemorySpillBackend.hpp:37-66` — `std::map<SliceEnd, std::map<WorkerThreadId, SpillRecord>> records;`
  i.e. value is a single opaque `SpillRecord` byte vector per `(sliceEnd, worker)`. Round-trips bytes; not
  thread-safe; used by serializer tests.
- The *producer* of those bytes is `SpillableAggregationSlice::spill()` (`SpillableAggregationSlice.cpp:99-113`):
  it calls `SliceSerializer::serialize(*chained, …)` per worker and `backend.put(getSliceEnd(), WorkerThreadId(pos), record)`.
  So **one put = one entire worker hash-map serialized into one blob.**

### 1c. RocksDB layout — BLOB, not range-scannable records (`RocksDBSpillBackend.cpp`)

Key construction (`.cpp:40-72`):
- `SLICE_END_BYTES=8` (uint64), `WORKER_BYTES=4` (uint32), `KEY_BYTES=12`.
- `appendBigEndian` (L45-53) writes MSB-first so lexicographic key order == numeric order.
- `recordKey(sliceEnd, worker)` (L65-72) = `<sliceEnd:8 BE><workerThreadId:4 BE>` → the documented 12-byte key.
- `slicePrefix(sliceEnd)` (L56-62) = the 8-byte BE slice-end → shared prefix of all per-worker records of a slice.

Operations:
- `put` (L108-116): `db->Put(WriteOptions, key, value)` — value is the **entire serialized map blob**, opaque to
  RocksDB.
- `get` (L118-133): single-key `db->Get` → copies bytes out into an owning `SpillRecord` (the TODO-noted copy).
- `erase` (L135-156): **this is the only range scan in the codebase.** It seeks `prefix`, iterates while
  `key().starts_with(prefix)`, batches `Delete`s — confirming all per-worker records of a slice ARE a contiguous,
  iterable key range. (`#include <rocksdb/iterator.h>` is already present, L29.)

**On-disk verdict (explicit):** RocksDB holds, per slice, **N blobs** (N = number of workers that touched the
slice), each blob = `[SliceSerializer header][hash+key+value]*` for that worker's whole map. The (key→aggregate)
records of the window are NOT stored as individual sorted RocksDB entries — they are buried inside the blobs.
Range-scanning by RocksDB iterator yields *blobs*, not *records*.

---

## 2. SliceSerializer — streamable internally, whole-map externally

`nes-physical-operators/include/SliceStore/SliceSerializer.hpp:46-64`:

```cpp
static constexpr uint32_t MAGIC = 0x4E535331;   // 'NSS1'
static constexpr uint32_t VERSION = 1;
static constexpr std::size_t HEADER_SIZE = (sizeof(uint32_t)*2) + (sizeof(uint64_t)*4); // 40 bytes

static SpillRecord serialize(const ChainedHashMap& map, uint64_t entrySize, uint64_t pageSize, uint64_t numberOfBuckets);
static std::unique_ptr<ChainedHashMap> deserialize(std::span<const std::byte> record, AbstractBufferProvider& bufferProvider);
```

**Record format** (`.cpp:57-108`): header = `[MAGIC][VERSION][entrySize][pageSize][numberOfBuckets][numEntries]`,
then body = `numEntries × ( uint64 hash | payloadSize bytes )` where `payloadSize = entrySize - sizeof(ChainedHashMapEntry)`
= the fixed `keySize+valueSize`. The chain `next` pointers (absolute addresses) are DROPPED; the chain index is
rebuilt on read by replaying `insertEntry`.

**Fixed-size assumption (in scope for L2):** header comment L37-40 + `.cpp:33` — only fixed-size keys/values are
supported; var-sized (`varSizedSpace` out-of-line pointers) explicitly NOT supported. So every record is a constant
stride `bytesPerEntry = sizeof(uint64_t) + payloadSize` (`.cpp:149`). This is exactly the property L2 streaming
needs: the body is a flat array of equal-size `(hash, key, value)` records.

**Serialize walk (streamable today, `.cpp:86-99`):** iterates `for chain in [0,numberOfChains): for entry =
getStartOfChain(chain); entry; entry = entry->next` — visits each entry once, emits `hash + payload`. This loop is
already a one-entry-at-a-time emitter; it just appends into one growing `SpillRecord` instead of yielding.

**Deserialize (`.cpp:110-171`):** validates header, then `make_unique<ChainedHashMap>(entrySize, numberOfBuckets,
pageSize)` and loops `numEntries` times calling `map->insertEntry(hash, &bufferProvider)` + `memcpy` payload. **This
is the full-map-rebuild that L2 wants to avoid** — it materializes the entire map into freshly-rented buffer-pool
pages before any record can be read.

**Can it expose a record iterator without building the map?** YES, trivially, *given a byte buffer*: the body is a
fixed-stride array after the 40-byte header, so a `(hash,key,value)` cursor over `std::span<const std::byte>` is a
~20-line addition needing no map and no buffer pool. The blocker is upstream: you must first have the *bytes*, and
today the only way to get one slice's bytes from RocksDB is `get()` → a full owning copy of the whole blob.

---

## 3. SpillableTimeBasedSliceStore — members, locks, key methods

Header `SpillableTimeBasedSliceStore.hpp:88-278`; impl `…/src/SliceStore/SpillableTimeBasedSliceStore.cpp`.

### Members (hpp:219-277)
- `std::unique_ptr<SpillBackend> backend;` (219)
- `folly::Synchronized<std::shared_ptr<AbstractBufferProvider>> bufferProvider;` (220) — the stashed GLOBAL buffer mgr.
- `const uint64_t entriesPerPage, pageSize, softThresholdBytes, hardThresholdBytes, emitLag;` (221-231)
- `folly::Synchronized<std::set<SliceEnd>> spilledKeys;` (233) — residency truth for `isSliceResident`/GC erase.
- `folly::Synchronized<std::set<SliceEnd>> emittedKeys;` (235) — emitted-to-probe guard; `forceSpill` refuses these.
- `folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices;` (239) — self-tracked, sorted asc
  (oldest-first scan); weak so base GC still frees.
- `std::atomic<Timestamp::Underlying> lastSeenWatermark{...};` (244) — learned build watermark (relaxed CAS-max).
- `mutable std::mutex evictionMutex;` (253) — OUTER serializer for **in-memory eviction decisions ONLY**; NO RocksDB
  I/O under it; lock order `evictionMutex → (createdSlices | one folly set)`; base maps NEVER under it.
- `std::condition_variable spillerCv;` (257) — spiller wakeup + trigger-wait (always with evictionMutex).
- `std::set<SliceEnd> spillInProgress;` (261) — per-slice in-flight reservation (under evictionMutex). Doubles as the
  fence that makes `residentBytes()` SKIP a slice whose maps are transitioning.
- `std::set<SliceEnd> pinnedKeys;` (266) — Increment C: late-write-reactivated band slices the spiller must not re-spill.
- `bool quiesced=false;` (269); `std::atomic<bool> spillerStarted{false};` (271); `std::atomic<size_t> spillCount{0};` (274)
- `std::jthread spiller;` (277) — declared LAST so it joins first; also stopped explicitly in dtor/deleteState.

### Key method bodies
- **constructor** (cpp:96-125): stores deps; `PRECONDITION` non-null backend, entriesPerPage>0, pageSize>0.
- **setBufferProvider** (cpp:141-159): stashes the provider ONCE (idempotent), then lazily `startSpiller()`.
  Caller: `DefaultTimeBasedSliceStoreRef.cpp:133` → `spillableStore->setBufferProvider(pipelineCtx->getBufferManager())`.
- **getSlicesOrCreate** (cpp:161-198): base call → `recordCreatedSlice`; if `emitLag>0 && cold` →
  `pinAndUnspillForLateWrite`; on genuinely-new slice, if `residentBytes() > hard` → `evictUntilUnderHard()` (inline,
  worker thread), else if `> soft` → `spillerCv.notify_one()`.
- **unspillAndMarkEmitted** (cpp:210-278): **THE redesign target.** For EACH returned spillable slice: under
  evictionMutex wait out any in-flight spill, insert into `emittedKeys`, drop pin, reserve in `spillInProgress` if
  non-resident; THEN OUTSIDE the mutex `spillable->unspill(*backend, *provider)` (full readback of ALL workers' maps
  into the pool), `spilledKeys.erase`, release reservation, notify. Iterates the whole window-slice map — this is the
  "unspill the whole retained band at once" behavior L1 wants to make incremental.
- **pinAndUnspillForLateWrite** (cpp:280-328): pins, waits out in-flight spill, unspills one slice on the worker path.
- **forceSpill** (cpp:503-561): lookup OUTSIDE mutex; under mutex check emitted/in-flight/resident + reserve; spill()
  OUTSIDE mutex (re-throws on failure via ReservationGuard); `++spillCount`; `finalizeSpill`.
- **pickAndReserveColdSlice** (cpp:563-637): under evictionMutex, snapshot `createdSlices` (ascending), break at first
  `end >= watermark` (not cold); skip in-flight/expired/non-resident/emitted/pinned; reserve first eligible → return.
- **finalizeSpill** (cpp:639-654): under mutex `spilledKeys.insert(end)`, `spillInProgress.erase(end)`; then
  `spillerCv.notify_all()`.
- **spillOneColdSlice** (cpp:656-697): `pickAndReserveColdSlice` → lookup → `spill()` OUTSIDE mutex (swallows on
  failure, O-B5) → `++spillCount` → `finalizeSpill`.
- **evictUntilUnderHard** (cpp:699-712): `while residentBytes() > hard: spillOneColdSlice()` (break if no candidate).
- **spillerLoop** (cpp:794-837): jthread; `spillerCv.wait_for(SPILLER_POLL_INTERVAL, stop)`; then while
  `residentBytes() > soft: spillOneColdSlice()`. **Never holds evictionMutex across residentBytes()/spillOneColdSlice.**
- **garbageCollectSlicesAndWindows** (cpp:415-472): base GC; collect spilledKeys the base dropped (non-unspilling base
  lookup) → `backend->erase` + clear emittedKeys/pinnedKeys; prune expired weak_ptrs in createdSlices.
- **deleteState** (cpp:474-501): `stopSpiller()`; clear spilledKeys/emittedKeys/pinnedKeys/createdSlices; `backend->erase`
  every key; base deleteState.

### residentBytes math (the ⌈keys/25⌉×1024 page model)
`residentBytesOf` (cpp:719-738): 0 if spilled; else Σ over workers of
`((tuples + entriesPerPage - 1) / entriesPerPage) * pageSize` — i.e. **⌈tuples_w / entriesPerPage⌉ × pageSize**.
The memory note's "⌈keys/25⌉×1024" is this with entriesPerPage=25, pageSize=1024. `residentBytes` (cpp:740-771):
holds evictionMutex, scans `createdSlices`, SKIPS slices in `spillInProgress`, sums `residentBytesOf`. Caller MUST
NOT already hold evictionMutex.

---

## 4. SpillableAggregationSlice — how a slice holds & moves its data

`include/Aggregation/SpillableAggregationSlice.hpp:39-68`, `src/Aggregation/SpillableAggregationSlice.cpp`.

- `final : public HashMapSlice`; data = `hashMaps` vector of `unique_ptr<ChainedHashMap>` (inherited from
  HashMapSlice), one per worker, indexed `workerThreadId % hashMaps.size()`. Private state: `bool resident=true;`
  `uint64_t spilledTupleCount=0;`.
- `getHashMapPtr` / `getHashMapPtrOrCreate` (cpp:51-72): `INVARIANT(resident, …)` — terminate if accessed while
  spilled; `OrCreate` lazily builds a `ChainedHashMap(keySize,valueSize,numberOfBuckets,pageSize)`.
- `getNumberOfTuples` (cpp:79-83): live sum if resident, else `spilledTupleCount` snapshot.
- **spill(backend)** (cpp:85-117): for each non-null worker map: `SliceSerializer::serialize(*chained, entrySize,
  pageSize, numberOfBuckets)` → `backend.put(sliceEnd, WorkerThreadId(pos), record)` → `hashMaps[pos].reset()`
  (frees the map; its TupleBuffers return to the BufferManager). Records `spilledTupleCount`, sets `resident=false`.
- **unspill(backend, bufferProvider)** (cpp:119-137): for each worker pos, `backend.get(sliceEnd, pos)`; if present,
  `hashMaps[pos] = SliceSerializer::deserialize(*record, bufferProvider)` (rebuilds the WHOLE map, renting pages from
  the passed provider). Sets `resident=true`. **Allocates from the global buffer pool** via `bufferProvider`.

`ChainedHashMap` (`nes-nautilus/.../ChainedHashMap.hpp:64-98`): page-backed storage (`struct Page{TupleBuffer buffer;
uint64_t numberOfEntries;}`, `getPage`/`getNumberOfPages`), fixed entry layout `|next 64b|hash 64b|keys|values|`
(L34-37). Iteration primitives already public: `getStartOfChain(idx)`, `getNumberOfChains()`, `getNumberOfTuples()`,
`getPage(i)`, `getNumberOfPages()`, `insertEntry(hash, provider)`. NOT thread-safe (L62).

---

## 5. Buffer-pool coupling (why unspill consumes pool buffers)

Confirmed end-to-end:
1. `DefaultTimeBasedSliceStoreRef.cpp:133`: `spillableStore->setBufferProvider(pipelineCtx->getBufferManager())` —
   the provider IS the query's global buffer manager.
2. Stashed in `bufferProvider` (folly::Synchronized) at `SpillableTimeBasedSliceStore.cpp:141-154` ("Stashed buffer
   provider for spillable slice store").
3. On readback, `unspillAndMarkEmitted` (cpp:221) does `auto provider = bufferProvider.copy();` and hands it to
   `spillable->unspill(*backend, *provider)`.
4. `unspill` → `SliceSerializer::deserialize(record, bufferProvider)` → `ChainedHashMap::insertEntry(hash,
   &bufferProvider)` rents `TupleBuffer` pages from the pool.

So **every unspilled entry materializes into a real buffer-pool page.** Unspilling a whole retained band at the
terminal flush rents pages for *all* its slices' maps simultaneously — the exact pool-pressure the redesign attacks.

---

## 6. L2 STORAGE FEASIBILITY — can the backend/serializer yield a bounded streaming iterator without a full map rebuild?

**Verdict: NOT possible with the current on-disk format. The blocker is the storage layer, not the serializer.**

Why:
- RocksDB holds **one opaque blob per (sliceEnd, worker)**. The (key→aggregate) records are concatenated *inside*
  each blob (`[40B header][hash|key|value]*`). RocksDB cannot range over the *records* — only over the *blobs*
  (`erase()` already does a prefix scan over blobs). So a record-level RocksDB iterator does not exist.
- `SpillBackend::get` returns the whole blob as an owning copy; `SliceSerializer::deserialize` then rebuilds the
  entire `ChainedHashMap` into pool pages. There is no partial path.

**Two ways to get a bounded streaming iterator:**

**(A) Stream from the blob bytes (cheapest; no storage format change).** Because the body is a fixed-stride array of
`(hash, key, value)` after the 40-byte header (Section 2), you can add:
- `SliceSerializer::recordCursor(std::span<const std::byte> blob) -> iterator` that yields fixed-stride records WITHOUT
  building a map or renting pool pages. ~1 small new file.
- A backend addition to obtain the blob bytes per worker without forcing the whole-window copy: `get` already returns
  ONE worker's blob, so L2 can iterate workers `0..N` and stream-decode each blob's records in bounded batches into a
  small staging buffer, emitting + freeing as it goes. This still copies one worker-blob at a time out of RocksDB (the
  `SpillBackend::get` owning-copy TODO at `SpillBackend.hpp:58-60`), but **never rebuilds a ChainedHashMap and never
  rents pool pages for the whole map** — that satisfies "no full map rebuild / bounded memory."
  - Optional refinement: a pinned/zero-copy `get` (the header's own Phase-3 TODO) to avoid even the per-blob copy.
- Files changed: `SliceSerializer.hpp/.cpp` (add cursor), `SpillBackend.hpp` (optionally a pinned-view get +/or a
  `streamSpilledRecords(sliceEnd, callback)` convenience that loops workers and feeds the cursor), each backend
  (`RocksDB/InMemory/Null`) for the new method, and `SpillableTimeBasedSliceStore` (an L2 emit path that calls the
  stream instead of `unspill` in `unspillAndMarkEmitted`). The hot fill path and `spill()`/`unspill()` are untouched.

**(B) Change RocksDB to store range-scannable per-key records** (`<sliceEnd|worker|hash|key> → value`). This makes a
native RocksDB record iterator possible (true demand-driven readback, partitionable by key — ideal for L3) but is a
**format/`spill()`/`erase()` rewrite** (`put` becomes many puts; `serialize` becomes a per-key writer; `erase` prefix
stays). Larger blast radius; defer to L3.

**Recommended for L2:** option (A) — the fixed-stride blob is already a streamable record array, so a serializer-side
cursor + a backend stream method delivers bounded batches with **zero pool-page allocation and no full map rebuild**,
changing only `SliceSerializer`, `SpillBackend` (+3 impls), and the emit path in `SpillableTimeBasedSliceStore`. The
deeper per-record RocksDB layout (option B) is what unlocks L3 demand-driven readback + keyspace partitioning.
