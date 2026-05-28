# PARTITION-AT-SPILL — Implementation-Design Exploration

> Source: `everything-claude-code:architect` (read-only) exploration, persisted by orchestrator.
> Decision context: we implement **partition-at-spill** (grace-hash "Option b") and REJECT partition-at-read.

## Executive summary

Partition-at-spill (grace-hash "Option b") lands cleanly as a **host-only, post-hoc split of the already-built `ChainedHashMap` during serialization**, with zero Nautilus/JIT exposure. The split is a one-pass walk of each per-worker map routing entries by `hash % P` into P `SpillRecord`s, each in the *unchanged* fixed-stride `[40B header][hash|payload]*` layout, written under an extended key `(sliceEnd, workerThreadId, partition)`. Because each partition blob is a self-contained NSS1 record, the existing `SliceSerializer::deserialize` reads exactly one partition whole with **no filtering** — that is the resource win over partition-at-read, which would deserialize the full blob and discard `(P-1)/P` of it on every partition pass. The key extension is a small **additive** change: append a 1- or 4-byte big-endian partition suffix to the RocksDB key (after the worker bytes, before any prefix-sweep boundary) and add a partition dimension to the InMemory map; Null stays a no-op. Total bytes written are unchanged; the only write-amp is `P × HEADER_SIZE` (40 bytes/blob) plus per-blob RocksDB key/SST overhead. The locked `number_of_partitions = 1` default is kept byte-identical via a **P==1 fast-path that bypasses partitioning entirely** (recommended over "p=0 key == old key", because the latter forces the key encoding to special-case the suffix and is more error-prone). The concurrency-sensitive surface is contained: P writes per `(sliceEnd, worker)` happen entirely inside the *existing* per-slice `spillInProgress` reservation + `ReservationGuard` window (no new lock, no new invariant) — the reservation already covers the whole `spill()` call regardless of how many backend `put`s it issues. The genuinely new and riskiest piece is **2c**: who owns the re-materialized per-worker partition maps while the async JIT probe drains chunk `p`. The answer maps onto the existing `EmittedAggregationWindow` ownership model: the partition maps must be owned by/alongside the emitted-window object and freed in the host cleanup lambda the probe runs at `open()` end (extended to free the partition maps, not just `finalHashMap`), never before the drain. Blast radius is ~8 files; pure/isolated steps (serializer, backend key, slice method) come first, the store/handler emit-loop wiring last. Build sequence: **2a** partitionedSerialize, **2b** backend key extension, **2c** streamEmitByPartition + ownership, **2d** partitioned terminal-emit loop + `number_of_partitions` knob, **2e** (conditional) partition-granularity pacing. Regression gate ≥ 835 at each step.

---

## Blast-radius table

| File | Change | Risk | Build-step |
|---|---|---|---|
| `nes-physical-operators/include/SliceStore/SliceSerializer.hpp` | Add `partitionedSerialize(map, entrySize, pageSize, numberOfBuckets, P) -> std::vector<SpillRecord>` (P entries). `deserialize` unchanged. | Low (pure, additive) | 2a |
| `nes-physical-operators/src/SliceStore/SliceSerializer.cpp` | Implement the P-way split: one chain walk, route each entry by `hash % P`, append to record[p]. Reuse `appendPod`/header logic. | Low (pure host) | 2a |
| `nes-physical-operators/include/SliceStore/SpillBackend.hpp` | Add a `partition` parameter to `put`/`get` (default 0). `erase(SliceEnd)` unchanged (still drops the whole slice). | Medium (interface change, 4 impls) | 2b |
| `nes-physical-operators/src/SliceStore/RocksDBSpillBackend.cpp` | `recordKey` appends a big-endian partition suffix after worker bytes; `slicePrefix` (erase) unchanged. | Medium (key encoding; prefix-sweep correctness) | 2b |
| `nes-physical-operators/include/SliceStore/InMemorySpillBackend.hpp` | Key the inner map on `(workerThreadId, partition)` (or nest a third map). | Low | 2b |
| `nes-physical-operators/include/SliceStore/NullSpillBackend.hpp` | Add the parameter; still no-op. | Trivial | 2b |
| `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp` + `.cpp` | `spill()` calls `partitionedSerialize` and writes P blobs/worker (P==1 fast-path = today's single `serialize`+`put`). Add `streamEmitByPartition(backend, provider, P, p, callback)` that rebuilds only partition `p` per worker. `unspill` keeps a P==1 fast-path; full-window unspill across P is a loop. | Medium-High (touches spill/unspill semantics; ownership) | 2a→2c |
| `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` + `.cpp` | `EmittedAggregationWindow` gains ownership of the per-worker partition maps; `triggerSlices` (or a new partitioned terminal path) emits P chunks/window with `chunkNumber=INITIAL+p`, `lastChunk=(p==P-1)`. | High (async emit, chunk metadata, ownership lifetime) | 2c→2d |
| `nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp` | Host cleanup lambda at `open()` end also frees the partition maps (host code only; no JIT change to the probe combine loop). | Medium (free-after-drain correctness) | 2c |
| `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` (+ `.hpp`) | New partitioned terminal-emit path hung on `ensureWindowSlicesResident`/`getAllNonTriggeredSlices`; plumb `P` from config. The steady-state `getTriggerableWindowSlices` unspill+emit path may keep P=1 whole-window for now. | High (concurrency-sensitive store) | 2d |
| `nes-query-compiler/interface/SpillConfig.hpp` | Add `UIntOption numberOfPartitions {"number_of_partitions", "1", ...}`; register in `getOptions()`. | Low | 2d |
| `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` | Pass `conf.spill.numberOfPartitions.getValue()` into the store ctor. | Low | 2d |
| Tests: `SliceSerializerTest`, `*SpillBackendTest`, `SpillableAggregationSliceTest`, `SpillableTimeBasedSliceStoreTest`, `SpillConfigTests`, systest `SpillSmoke` + a P=1-vs-P>1 differential | New cases per step. | — | each step |

---

## 1. Current spill write path (call chain, key schema, blob layout)

**Trigger → serialize → put** (two spill drivers, one shared sink):

- **Threshold-driven (background + synchronous hard):**
  `SpillableTimeBasedSliceStore::spillerLoop` (`SpillableTimeBasedSliceStore.cpp:926`) / `evictUntilUnderHard` (`:747`) → `spillOneColdSlice` (`:702`) → `pickAndReserveColdSlice` (`:609`) reserves the slice in `spillInProgress`, then **outside `evictionMutex`** under a `ReservationGuard` → `spillable->spill(*backend)` (`:731`).
- **Forced/manual:** `forceSpill(SliceEnd)` (`:547`) → emitted-check + reserve inside `evictionMutex` → `spillable->spill(*backend)` outside it (`:600`).

Both land in **`SpillableAggregationSlice::spill(SpillBackend&)`** (`SpillableAggregationSlice.cpp:85`): for each non-null per-worker map it computes `entrySize = sizeof(ChainedHashMapEntry) + keySize + valueSize` (`entrySizeFor`, `:36`), serializes via `SliceSerializer::serialize(*chained, entrySize, pageSize, numberOfBuckets)` (`:109`), then `backend.put(getSliceEnd(), WorkerThreadId(pos), record)` (`:111`) and `hashMaps[pos].reset()` to free the map (`:112`).

**Key schema today** (`SpillBackend.hpp:54`, `:61`): `put`/`get` keyed by `(SliceEnd sliceEnd, WorkerThreadId workerThreadId)`. There is no key struct; the RocksDB backend encodes it inline. `erase(SliceEnd)` (`:65`) drops *all* per-worker records of a slice.

**RocksDB key encoding** (`RocksDBSpillBackend.cpp:43-75`): a 12-byte **big-endian** key `<sliceEnd:8><workerThreadId:4>` (`recordKey`, `:68`); `appendBigEndian` (`:49`) makes lexicographic order match numeric order so `erase` is a single prefix sweep on the 8-byte `slicePrefix` (`:59`, used by `erase` at `:172-193`). `KEY_BYTES = 12` (`:45`).

**Blob layout (the NSS1 record)** — `SliceSerializer` (`SliceSerializer.hpp:46-64`, `SliceSerializer.cpp:57-108`):
- Magic `0x4E535331` ("NSS1"), version `1` (`SliceSerializer.hpp:50-52`).
- **Header = 40 bytes**: `HEADER_SIZE = sizeof(uint32_t)*2 + sizeof(uint64_t)*4` (`:54`) = `[magic:u32][version:u32][entrySize:u64][pageSize:u64][numberOfBuckets:u64][numEntries:u64]` (written `SliceSerializer.cpp:72-80`).
- **Body = fixed-stride entries**: per entry `[hash:u64][payload: entrySize − sizeof(ChainedHashMapEntry)]`, i.e. stride `sizeof(uint64_t) + payloadSize` (`:93-96`, `:149`). `ENTRY_HEADER_SIZE = sizeof(ChainedHashMapEntry)` = the `next*` (8B) + `hash` (8B) header (`:33`; `ChainedHashMap.hpp:38-44`); only `hash` + the key/value payload survive (the `next` chain pointer is dropped and rebuilt on deserialize via `insertEntry`, `SliceSerializer.cpp:160-168`).

The chain walk (`:89-98`) visits every entry exactly once across `getNumberOfChains()` chains and asserts `writtenEntries == numberOfEntries` (`:101`). **This is exactly the loop partition-at-spill hooks into.**

---

## 2. Key-schema extension `(sliceEnd, worker, partition p)`

**It is an interface change** to `SpillBackend::put`/`get`, but a minimal, additive one. Recommended signatures (partition defaulted so non-partitioned callers are source-compatible):

```
put(SliceEnd, WorkerThreadId, PartitionId p, std::span<const std::byte>)   // p default 0
get(SliceEnd, WorkerThreadId, PartitionId p) -> std::optional<SpillRecord> // p default 0
erase(SliceEnd)  // UNCHANGED — still drops every (worker, partition) record of the slice
```

Use a small dedicated `PartitionId` (a `uint16_t`/`uint32_t` strong typedef, or just `uint32_t`). Per-backend delta:

- **RocksDBSpillBackend** (`RocksDBSpillBackend.cpp`): `recordKey` appends the partition suffix in big-endian **after** the worker bytes: key becomes `<sliceEnd:8><worker:4><partition:N>`, `KEY_BYTES += N`. Critically, **`slicePrefix` stays the 8-byte sliceEnd prefix**, so `erase`'s prefix sweep (`:177`) still collects all `(worker, partition)` records of the slice with no change. Putting partition *after* worker also keeps all of one worker's partitions contiguous (incidental locality benefit; not required). Use `uint16_t` for partition (P ≤ 65535 is ample for an edge device) to keep the key 14 bytes.
- **InMemorySpillBackend** (`InMemorySpillBackend.hpp:66`): change `std::map<SliceEnd, std::map<WorkerThreadId, SpillRecord>>` to nest a partition level — either `std::map<SliceEnd, std::map<std::pair<WorkerThreadId, PartitionId>, SpillRecord>>` or a third nested map. `erase(SliceEnd)` (`:54`) still erases the whole slice entry. Update `contains` (`:57`) to take `p`.
- **NullSpillBackend** (`NullSpillBackend.hpp`): add the parameter; bodies stay empty no-ops.

This change is **invisible to JIT** — no generated code references the key shape (confirmed: the backend is reached only via host `spill`/`unspill`).

---

## 3. `partitionedSerialize` (host-side split)

Add to `SliceSerializer` a sibling of `serialize` that produces P records in one pass. Pure host code reading an already-built `ChainedHashMap` (the exact same read API `serialize` already uses) — **no JIT.**

Proposed signature (`SliceSerializer.hpp`):

```
static std::vector<SpillRecord> partitionedSerialize(
    const ChainedHashMap& map, uint64_t entrySize, uint64_t pageSize,
    uint64_t numberOfBuckets, uint64_t numberOfPartitions);
```

Implementation shape (`SliceSerializer.cpp`), reusing the existing helpers:
1. Validate `entrySize > ENTRY_HEADER_SIZE` and `numberOfPartitions >= 1` (throw `CannotSerialize` otherwise).
2. **Two-pass option (recommended for exact `reserve` + exact per-partition `numEntries` header):** first walk counts entries per partition (`hash % P`); allocate P `SpillRecord`s; write each header with that partition's own `numEntries`; second walk appends `[hash][payload]` to `record[hash % P]`. The header fields `entrySize/pageSize/numberOfBuckets` are **identical across all P blobs** (same map layout), only `numEntries` differs per partition. (A single-pass variant is possible by back-patching the `numEntries` header field after the walk; the two-pass form is simpler and the count walk is cheap CPU vs the I/O.)
3. Each `record[p]` is byte-for-byte a valid NSS1 record indistinguishable from one produced by `serialize` on a map containing only that partition's entries.

**Where it slots in:** `SpillableAggregationSlice::spill` (`SpillableAggregationSlice.cpp:99-113`) replaces the per-worker `serialize`+single `put` with:
- **P==1 fast-path:** call the *existing* `serialize` + `put(..., /*p=*/0, record)` — byte-identical to today (see §5).
- **P>1:** `auto recs = partitionedSerialize(*chained, entrySize, pageSize, numberOfBuckets, P);` then `for p: if (!recs[p].empty-of-entries) backend.put(getSliceEnd(), worker, p, recs[p]);`. (Empty partitions: writing a header-only blob is harmless and keeps readback uniform; or skip empty partitions and let `get` return `nullopt` — readback already tolerates a missing record exactly like `unspill` tolerates a never-created worker map, `SpillableAggregationSlice.cpp:131`. Skipping is the leaner choice for constrained devices.)

**Crucially, `deserialize` is UNCHANGED** — it already reads "one whole record" and rebuilds a map by replaying `insertEntry` (`SliceSerializer.cpp:160-168`). A partition blob *is* a whole record, so readback of partition `p` is just `deserialize(record_p, provider)`.

---

## 4. Readback / `streamEmitByPartition`

Add to `SpillableAggregationSlice` a per-partition rebuild primitive (host-only):

```
void streamEmitByPartition(SpillBackend& backend, AbstractBufferProvider& provider,
                           uint64_t numberOfPartitions, uint64_t p,
                           const std::function<void(std::vector<HashMap*>&)>& emit);
```

For a fixed partition `p`: for each worker `w`, `backend.get(getSliceEnd(), w, p)` → if present, `SliceSerializer::deserialize(*rec, provider)` → that map **is** partition `p` of worker `w`, with **no filtering**. Collect the W per-worker partition maps, hand their raw `HashMap*`s to `emit` (which builds the `EmittedAggregationWindow` chunk), then release them after the async drain (see §7). The slice itself stays spilled (its `hashMaps[]` are not repopulated; `resident` stays false) — this is a *streaming read-out*, distinct from `unspill` (which restores the whole slice).

**Called from the partitioned terminal-emit loop** (§10, step 2d): `WindowBasedOperatorHandler::triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91`) already iterates one window at a time and calls `ensureWindowSlicesResident(slices)` (`:102`) before `triggerSlices`. The partitioned path replaces the per-window "unspill whole + emit one chunk" with "for p=0..P-1: build partition p across the window's slices → emit chunk p". The handler emits P chunks per window (chunk metadata in §10).

**Resource contrast (the win, explicitly):**

| | partition-at-read (REJECTED) | partition-at-spill (CHOSEN) |
|---|---|---|
| On-disk | 1 blob/(sliceEnd,worker), all keys | P blobs/(sliceEnd,worker), keyed by p |
| Reading partition p | `get` the FULL worker blob, `partitionedDeserialize` skips entries where `hash%P != p` | `get` exactly blob `(sliceEnd,worker,p)`, `deserialize` whole — every byte read is used |
| Bytes read from RocksDB per full window emit | full window × P passes = **P× read-amp** | full window × 1 (each partition read once) = **1× read-amp** |
| Transient heap during one partition pass | one worker's FULL blob (all P partitions' bytes) decompressed/copied, then discard `(P−1)/P` | one worker's partition-p blob only (≈1/P the bytes) |
| Buffer-pool pages rented per pass | one partition's pages (both options) | one partition's pages |

Partition-at-spill pays the cost **once at write time** (re-keying into P blobs, same total bytes); partition-at-read would pay `P×` the read cost **every** emit — unacceptable on constrained devices where the spilled state is read back at least once.

---

## 5. Backward compatibility (P=1 byte-identical)

**Recommendation: a `P==1` fast-path that bypasses partitioning entirely**, in both `spill` and the emit path. When `numberOfPartitions == 1`:
- `spill` calls the existing `SliceSerializer::serialize` (unchanged) and `backend.put(sliceEnd, worker, /*p=*/0, record)`.
- The RocksDB key for `p==0` will carry the partition suffix bytes (e.g. `0x0000`), so on-disk the key is *not* literally the old 12-byte key. **This is acceptable** because the spill store is transient scratch (RocksDB at `/tmp/nes-spill/...`, recreated per query; `SpillConfig.hpp:45`) — there is no persisted on-disk corpus that must remain readable across the change. Behavior (round-trip, erase-by-prefix, emit output) is identical.

Why this over "make the `p==0` key equal the old key": forcing `p==0` to omit the suffix means the key encoder must branch on `p`, and the prefix-sweep / contiguity reasoning must special-case it — more code and a sharper edge for the prefix-sweep boundary, for a property (on-disk wire compatibility) that nothing in scope requires. The clean rule is: **P==1 takes the same logical path as today (one blob per worker), and the only on-disk difference is two constant suffix bytes that no reader outside this code interprets.**

The *behavioral* P==1 guarantees that matter — single logical blob per `(sliceEnd, worker)`, `deserialize` reads it whole, `erase(SliceEnd)` drops it, emit produces one chunk per window (`chunkNumber=INITIAL`, `lastChunk=true`, exactly as `triggerSlices` does today at `AggregationOperatorHandler.cpp:134-135`) — are all preserved by the fast-path. Default `number_of_partitions = "1"` (`SpillConfig`) means the default deployment is unchanged.

---

## 6. Concurrency invariants (does writing P blobs complicate anything?)

**No new lock, no new invariant, and the per-slice reservation covers all P writes atomically — because the reservation brackets the whole `spill()` call, independent of how many `put`s it issues.**

Walking the invariants from `SpillableTimeBasedSliceStore.hpp:74-87` and the `.cpp`:

- **`spillInProgress` reservation + `ReservationGuard`:** A slice is reserved in `spillInProgress` *before* `spill()` is called and released *after* it returns (success via `finalizeSpill`, failure via the guard). `spill()` issuing P `put`s instead of 1 changes nothing — the entire multi-put sequence executes inside the single reserved window, outside `evictionMutex` (uniform no-I/O-under-lock). The reservation already fences `residentBytes()` (`SpillableTimeBasedSliceStore.cpp:809-811`) and any concurrent trigger/unspill for the *whole* spill duration.
- **`emittedKeys` / async-drain:** Unchanged. `emittedKeys` is keyed by `SliceEnd`, not by partition. A slice marked emitted (in `ensureWindowSlicesResident`, `:270`) is still protected at slice granularity; the spiller's `pickAndReserveColdSlice` (`:667`) rejects emitted slices wholesale. Partitioning does not subdivide this protection.
- **`erase`/GC:** `garbageCollectSlicesAndWindows` (`:459`) and `deleteState` (`:518`) call `backend->erase(sliceEnd)`, which (unchanged contract) drops *all* `(worker, partition)` records of the slice via the 8-byte prefix sweep. No per-partition GC bookkeeping is needed.
- **Partial-failure / crash-consistency (the one new wrinkle to spell out):** A single `serialize`+`put` is one RocksDB write; P `put`s are P separate `db->Put` calls (not atomic as a group). If `put` #k throws mid-spill, `spill()` propagates, the `ReservationGuard` releases the reservation, and the slice is left with **k partition blobs written but `resident` still true** (because `spill()` flips `resident=false` and `reset()`s maps only after the put loop today, `SpillableAggregationSlice.cpp:112,116`). To preserve the existing "spill is all-or-nothing for in-memory state" property: **keep the `resident=false` flip and the `hashMaps[].reset()` AFTER the full P-put loop succeeds** (i.e. write all P blobs first, then free). On a throw, the partial on-disk blobs are orphaned but harmless — they are overwritten on a retry (same keys) or dropped by `erase` at GC. Optionally wrap the P puts in a RocksDB `WriteBatch` for atomic durability (the backend already uses `WriteBatch` for `erase`, `RocksDBSpillBackend.cpp:175`), which would make the P writes atomic — recommended as a cheap robustness add in 2b, but not strictly required for correctness given the retry/erase safety net.
- **Lock order** (`evictionMutex → createdSlices | one folly set`, base maps never under it) is untouched: all P puts are I/O, all outside the mutex.

---

## 7. Ownership of re-materialized partition maps during async emit (the "2c" question)

**Today's model** (`AggregationOperatorHandler::triggerSlices`, `AggregationOperatorHandler.cpp:80-156`): the raw `HashMap*`s handed to the probe point into **slice-owned** maps (`getHashMapPtr`, `:97`); the `EmittedAggregationWindow` (placement-new'd into the emitted TupleBuffer, `:144`) owns only the synthesized `finalHashMap` (`AggregationOperatorHandler.hpp:52`). The probe (`AggregationProbePhysicalOperator::open`) combines all `hashMaps[]` into `finalHashMap`, lowers it, and frees **only `finalHashMap`** in the host cleanup lambda at `open()` end (`AggregationProbePhysicalOperator.cpp:160-169`). The slice maps stay alive because the slice is in `emittedKeys` and is freed only by base GC after the output watermark advances past it (the async-probe-drain invariant).

**Problem for partition-at-spill:** in `streamEmitByPartition` the partition maps are **freshly deserialized, NOT slice-owned** — the slice stays spilled. Nothing keeps them alive across the async drain, and `emittedKeys`/GC protects slice-owned memory, which these aren't.

**Solution (maps onto `EmittedAggregationWindow`):** make the emitted-window object **own the per-worker partition maps** for that chunk. Concretely, extend `EmittedAggregationWindow` with a `std::vector<std::unique_ptr<HashMap>> ownedPartitionMaps` (the W per-worker partition-p maps), with `hashMaps[]` pointing into them (instead of into slice memory) and `numberOfHashMaps = W`. The probe's combine loop is **unchanged** — it just reads `hashMaps[0..W)` and `finalHashMap` as today (no JIT edit; the probe never learns these are partition maps). The host cleanup lambda at `open()` end (`AggregationProbePhysicalOperator.cpp:160-169`) is extended to also reset `ownedPartitionMaps` (host code, freeing the partition maps **after** the drain completes, returning their TupleBuffers to the pool). This is the precise "free in the host cleanup lambda after drain + watermark advance, NOT before" requirement.

Per-chunk lifetime: chunk `p`'s `EmittedAggregationWindow` owns chunk `p`'s W partition maps; they live exactly as long as the emitted buffer is in flight and are freed when the probe finishes draining that buffer. Because chunks are key-disjoint (`hash % P`), no key is emitted twice, and GC/output-watermark advance is gated until `lastChunk` (`p == P-1`) so the slice is not collected mid-emit — the same precedent that makes the production N²-chunk join emit safe (`StreamJoinOperatorHandler::triggerSlices`). The slice's own `spilledKeys` membership is dropped (and backend records erased) only at GC after the last chunk drains.

---

## 8. Resource accounting (constrained-device framing)

Let a window's spilled state be `B` total bytes across W per-worker maps, `H = HEADER_SIZE = 40` bytes, `P` partitions.

- **Write-amplification (total bytes written to RocksDB):** `B + W·P·H` (one 40-byte header per blob) vs today's `B + W·H`. The *entry* bytes are identical (each entry is written exactly once, just routed to one of P blobs). Extra write-amp = `W·(P−1)·H` header bytes + per-blob RocksDB key/SST/WAL overhead (~tens of bytes/blob). For W=4, P=8, that is `4·7·40 = 1120` extra header bytes per window — negligible vs `B` (megabytes). **Total entry bytes written: unchanged.** This is the core thesis claim: partition-at-spill is write-neutral on payload.
- **Read-amplification (bytes read per full window emit):**
  - partition-at-spill: each partition blob read **once** → `B + W·P·H` read total = **1× the data**.
  - partition-at-read: each of P passes reads all W full blobs → `P·(B + W·H)` ≈ **P× the data**.
  - Win = factor `P` in read I/O and decompression CPU. On an edge device with LZ4-compressed SSTs (`SpillConfig` default `compression="lz4"`), the avoided `(P−1)/P` decompression per pass is the dominant CPU save.
- **Transient memory at emit (the OOM-relevant peak):**
  - Heap (deserialize scratch): one partition's worker blob ≈ `B/(W·P)` per worker, freed after the map is built. partition-at-read would decompress a whole worker blob (`B/W`) to extract `1/P` of it — `P×` the transient decompression buffer.
  - Buffer-pool pages resident at once: one chunk = W per-worker partition maps ≈ `B/P` pages (vs whole window `B` for the non-partitioned path). This is the bound that lets the constrained-device emit proceed: the build-side never materializes more than `≈ B/P` at a time.
  - **Caveat (from FEASIBILITY §"THE NUANCE"):** emit is async, so the *in-flight emitted set* can still pile up to ≈ the whole band if the terminate loop outruns the probe. Partition-at-spill shrinks each in-flight unit to `1/P` and is RocksDB-I/O-gated per partition (each chunk = a `get`), making the probe more likely to keep up at threads>1 — but a *hard* process-peak bound still needs the optional 2e pacing. State this honestly in the thesis: partition-at-spill bounds the **build-side** working set, not (alone) the async in-flight set.

---

## 9. Skew

`hash % P` is uniform only if the MurMur3 hashes are well-distributed across residues, which holds for typical keys but **not adversarially**. Worst case: one fat partition holds most entries (degenerate: all keys collide mod P), so that one chunk's footprint ≈ the whole window — the `1/P` bound degrades toward `1`. This is **acceptable-with-caveat for the demo**: (a) the *correctness* is unaffected (every key emitted once); (b) the average case on real IoT keys is near-uniform; (c) the spill side still writes the same total bytes regardless of skew. Document in the thesis as a known limitation; a skew-robust split (e.g. range-balanced partition assignment, or splitting the fat partition further) is future work and would not change the host-only/no-JIT property.

---

## 10. Blast radius + build sequence (2a→2e)

**Files to touch** (full list in the blast-radius table). **Pure/isolated first, concurrency-sensitive store last.**

- **2a — `SliceSerializer::partitionedSerialize` (PURE, do first).** Header + impl in `SliceSerializer.{hpp,cpp}`. Independently buildable/testable. Tests in `SliceSerializerTest`: (i) union of P partitions' entries == full `serialize` entries (round-trip each partition via `deserialize`, assert disjoint + complete); (ii) empty partitions tolerated; (iii) P==1 produces a record byte-identical to `serialize`. No store, no JIT, no concurrency. **Gate ≥ 835.**

- **2b — SpillBackend key extension (ISOLATED).** `SpillBackend.hpp` + the 3 impls. Tests in `InMemorySpillBackendTest`/`RocksDBSpillBackendTest`/`NullSpillBackendTest`: put/get round-trip per `(sliceEnd, worker, p)`; `erase(SliceEnd)` drops all partitions; prefix-sweep still bounded to the slice. Optional `WriteBatch` for atomic P-put (§6). No store/handler changes yet. **Gate ≥ 835.**

- **2c — `SpillableAggregationSlice::streamEmitByPartition` + `EmittedAggregationWindow` ownership + probe cleanup.** `SpillableAggregationSlice.{hpp,cpp}` (spill writes P blobs with P==1 fast-path; new per-partition rebuild), `AggregationOperatorHandler.hpp` (extend `EmittedAggregationWindow`), `AggregationProbePhysicalOperator.cpp` (extend host cleanup lambda only — no JIT edit). Tests in `SpillableAggregationSliceTest`: per-partition residency/contents; pages reclaimed between partitions; P==1 == today's spill/unspill. This is the **ownership-critical** step (free-after-drain). **Gate ≥ 835.**

- **2d — Partitioned terminal-emit path + `number_of_partitions` knob (TOUCHES THE STORE — do after 2a–2c are green).** `SpillConfig.hpp` (`numberOfPartitions`, default 1), `LowerToPhysicalWindowedAggregation.cpp` (plumb P into the store ctor), `SpillableTimeBasedSliceStore.{hpp,cpp}` (carry P; hang the partitioned loop on `getAllNonTriggeredSlices`/`ensureWindowSlicesResident`), `AggregationOperatorHandler.cpp` (emit P chunks/window: same `sequenceNumber`, `chunkNumber = INITIAL + p`, `lastChunk = (p == P-1)`, `watermark = windowStart`, `numberOfTuples` = this partition's count — mirroring the single-chunk metadata at `AggregationOperatorHandler.cpp:132-137`). P==1 must produce exactly today's single `INITIAL`/`lastChunk=true` emit. Tests: `SpillableTimeBasedSliceStoreTest` partitioned-emit drive; `SpillConfigTests` for the new knob; **systest differential P=1 vs P=8 → identical output** (the key-disjoint dup-row gate); systest `SpillSmoke` regression. **Gate ≥ 835.**

- **2e — (CONDITIONAL) Partition-granularity backpressure.** Only if 2d's end-to-end memory-ceiling run still OOMs from async in-flight pileup (§8 caveat). Bound the number of in-flight chunks (pace `emitBuffer` in the handler, host-only). Measure first (`run-rss.sh` 320-cell at threads>1 with P>1); add only if needed.

Each step is independently buildable; 2a/2b/2c are pure/isolated and carry the lowest regression risk, 2d is the only step that mutates the concurrency-sensitive `SpillableTimeBasedSliceStore` and the async emit metadata.

---

## 11. Open questions / risks (need code visibility to resolve)

1. **`EmittedAggregationWindow` layout vs placement-new (2c).** Today the object is placement-new'd into a TupleBuffer with `hashMaps` pointing at `this + 1` and the pointer array copied right after the struct (`AggregationOperatorHandler.cpp:144-147`, `AggregationOperatorHandler.hpp:46-47`). Adding `std::vector<std::unique_ptr<HashMap>> ownedPartitionMaps` makes the struct non-trivially-destructible while it lives in raw buffer memory — confirm the buffer is never just bit-dropped (the probe explicitly invokes the host cleanup lambda, `:160-169`, which must now also clear `ownedPartitionMaps`, and the dtor must actually run). Verify no path frees the TupleBuffer without running that cleanup (would leak the partition maps' TupleBuffers). This is the single highest-risk detail.

2. **Empty-partition policy.** Skip-empty (leaner) vs write-header-only (uniform). Confirm `streamEmitByPartition` + the chunk-count loop handle a window where partition `p` is empty for *all* workers (must still emit chunk `p` with 0 tuples, or renumber chunks so `lastChunk` is correct). Renumbering risks an off-by-one in `chunkNumber`/`lastChunk`; emitting empty chunks is simpler and safer — verify the probe and ChunkCollector tolerate a 0-tuple chunk.

3. **Steady-state vs terminal emit.** `getTriggerableWindowSlices` (steady-state, `:366`) currently unspills the whole slice and emits one whole-window chunk. Does partition-at-spill apply there too, or only to the terminal flush (`getAllNonTriggeredSlices`/`triggerAllWindows`)? Applying it to steady-state means a slice may be partition-emitted *before* being fully cold — interacts with `pinnedKeys`/late-write (Increment C). Recommend: **terminal path first (2d)**, leave steady-state at P=1 until measured. Needs visibility into how often steady-state emits spilled (vs resident) slices.

4. **`unspill` semantics under P>1.** `unspill` (`SpillableAggregationSlice.cpp:119`) restores the *whole* slice; with P blobs it must loop p=0..P-1 per worker. But the random-access `getSliceBySliceEnd` unspill path (`SpillableTimeBasedSliceStore.cpp:407`) and the late-write `pinAndUnspillForLateWrite` (`:312`) call `unspill` to make the slice *resident and writable again* — confirm a full multi-partition unspill correctly merges P blobs back into one per-worker `ChainedHashMap` (it does, since `deserialize` replays `insertEntry` and `hash % P` routing is irrelevant on the way back in — all entries land in the same rebuilt map). Add a test for "spill P-way, full unspill, contents == original."

5. **`PartitionId` type + key width.** `uint16_t` (14-byte key) vs `uint32_t` (16-byte). Confirm no RocksDB tuning assumes the 12-byte key width; pick the narrowest that exceeds any realistic P. Cross-check against `WorkerThreadId` underlying width handling already asserted in `spill` (`SpillableAggregationSlice.cpp:92-95`).

6. **Atomicity of P puts (§6).** Decide `WriteBatch`-per-spill (atomic durability, slightly higher latency) vs sequential puts with retry/erase safety net. Needs a call on the crash-consistency bar for the thesis demo (likely sequential is fine; note it).

7. **The async in-flight bound (measure-driven).** Whether 2e is needed is empirical (FEASIBILITY §"THE NUANCE"). Resolve by running the 320-cell at threads>1 with P>1 and observing `store_peak` and rc 137.
