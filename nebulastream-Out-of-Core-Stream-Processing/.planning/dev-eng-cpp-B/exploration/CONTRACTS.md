# CONTRACTS — Explorer 3: Store Internals, Enumeration & Variable-Sized Serialization

Branch `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b`. READ-ONLY exploration. All paths absolute-relative to repo root.

Files studied:
- `nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp` / `src/SliceStore/DefaultTimeBasedSliceStore.cpp`
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` / `src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/include/SliceStore/Slice.hpp`, `WindowSlicesStoreInterface.hpp`, `SliceAssigner.hpp`, `HashMapSlice.hpp`, `SpillBackend.hpp`
- `nes-physical-operators/include/SliceStore/SliceSerializer.hpp` / `src/SliceStore/SliceSerializer.cpp`
- `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp` / `src/Aggregation/SpillableAggregationSlice.cpp`
- `nes-nautilus/include/Nautilus/Interface/HashMap/ChainedHashMap/ChainedHashMap.hpp` / `src/.../ChainedHashMap.cpp`
- `nes-nautilus/src/Nautilus/Interface/HashMap/ChainedHashMap/ChainedEntryMemoryProvider.cpp`  ← **the var-sized layout truth**
- Tests: `tests/SliceSerializerTest.cpp`, `tests/SpillableTimeBasedSliceStoreTest.cpp`

---

## 1. Cold-slice enumeration (O-B1) — concrete API design

### Current encapsulation reality
`DefaultTimeBasedSliceStore.hpp:82-99`: `windows` (`folly::Synchronized<std::map<WindowInfo, SlicesAndState>>`) and `slices` (`folly::Synchronized<std::map<SliceEnd, std::shared_ptr<Slice>>>`) are **`private`**, as is `sliceAssigner`. The subclass cannot read them. The base exposes nothing iteration-shaped: the only public accessors that touch slices are `getSliceBySliceEnd(SliceEnd)` (point lookup, `cpp:141`) and the three trigger feeders (which *mutate* state).

`SlicesAndState` (`DefaultTimeBasedSliceStore.hpp:44-53`) holds `windowSlices` + a single `WindowInfoState windowState` **per window** (not per slice). A slice can appear in multiple `SlicesAndState` (sliding) — coldness is a property derived across *all* windows referencing the slice.

### Option (a) — store SELF-TRACKS (no base changes). VERDICT: insufficient alone; needs a state probe.

A already keeps two tracked sets: `spilledKeys`, `emittedKeys` (`SpillableTimeBasedSliceStore.hpp:114-116`). For B-evict the subclass would additionally:
- Override `getSlicesOrCreate` to record each created slice: `createdSlices : map<SliceEnd, weak_ptr<Slice>>` (weak, so GC's `slicesToDelete.clear()` at `DefaultTimeBasedSliceStore.cpp:271` still frees them; the store must not pin slices alive).
- It already learns "emitted" via `unspillAndMarkEmitted` → `emittedKeys` (`cpp:90-115`).

**What the store CAN compute from self-tracking + the watermark it sees on `getTriggerableWindowSlices(globalWatermark)`:**
- `sliceEnd` of every created slice (from `createdSlices`).
- `residentBytes()` store-wide = `Σ over createdSlices residentBytesOf(*slice)` — `residentBytesOf` already exists (`cpp:234`) and returns 0 for spilled slices. **This part works with self-tracking, no base change.**
- "emitted?" — from `emittedKeys`.

**What is MISSING / unreliable with self-tracking (the gap):**
1. **"No longer filling" cannot be derived from `sliceEnd < watermark` reliably at the store level.** The base marks a *window* `EMITTED_TO_PROBE` only when `windowInfo.windowEnd < globalWatermark` AND the trigger feeder runs (`DefaultTimeBasedSliceStore.cpp:104-139`). Between "watermark passed the slice end" and "the trigger feeder actually ran and emitted", a slice is cold-eligible but `emittedKeys` does not yet contain it. The store *does* see the watermark on each `getTriggerableWindowSlices` call, but it never sees the watermark on the build hot path (`getSlicesOrCreate` has no watermark argument — `WindowSlicesStoreInterface.hpp:68`). So if eviction triggers on slice creation (O-B2 "inline"), the store has **no current watermark** to compare against. It would have to cache "last watermark seen in a trigger feeder" — a lagging, racy value.
2. **Multi-window coldness (sliding).** With self-tracking the store never learns *which windows* a slice belongs to, only its `sliceEnd`. For sliding windows a slice with `sliceEnd < watermark` can still belong to a *later* window whose `windowEnd >= watermark` and is therefore still `WINDOW_FILLING` / not emitted. `emittedKeys` is keyed by sliceEnd and is set the first time *any* window emits the slice — but emitting one window does **not** make the slice cold for its other windows; conversely a slice can be safe-to-spill before *any* emit if all its windows are still filling but it has stopped receiving tuples. Self-tracking cannot see per-window state, so it cannot implement the precise predicate from §2. It would have to **over-approximate** ("spill only slices in `emittedKeys`" → but those are exactly the ones we must NOT spill) or **under-approximate** dangerously.

**Conclusion on (a):** self-tracking gives a correct `residentBytes()` cheaply, but it **cannot safely identify cold candidates for sliding windows** because per-window `WindowInfoState` lives only in the private `windows` map. For tumbling (size==slide, the A test config) it happens to work because one slice == one window, but the brief explicitly wants sliding correct. **Reject (a) as the sole mechanism.**

### Option (b)/(c) — protected accessor on the base. VERDICT: RECOMMENDED (option c, a read-only enumeration hook).

Add a **protected, read-only** helper to `DefaultTimeBasedSliceStore` that snapshots cold candidates while holding the base locks, so the subclass never touches the raw maps and the lock discipline stays inside the base. This is option (b)/(c) merged into the smallest possible surface — NOT exposing the maps, only a computed result.

```cpp
// DefaultTimeBasedSliceStore.hpp — new protected section
protected:
    /// A cold slice = its sliceEnd is below `watermark`, it is present in `slices`, and EVERY window in
    /// `windows` that references it has windowState != WINDOW_FILLING-with-future-end, i.e. none of its
    /// windows is still accepting tuples. Computed under the windows+slices locks; returns a snapshot of
    /// shared_ptrs (refcount-pinned for the caller's spill, released when the vector is dropped).
    /// EMITTED_TO_PROBE slices are EXCLUDED here too (defense in depth; the subclass also guards via emittedKeys).
    [[nodiscard]] std::vector<std::shared_ptr<Slice>> getColdSlices(Timestamp watermark) const;

    /// Sum of a caller-supplied per-slice cost over every live slice in `slices`, computed under the slices
    /// lock. Lets the subclass compute store-wide residentBytes() without seeing the map. `cost` must not
    /// re-enter the store (no lock re-acquisition) — it only reads the slice (residentBytesOf is lock-free).
    [[nodiscard]] uint64_t
    forEachSliceAccumulate(const std::function<uint64_t(const Slice&)>& cost) const;
```

Implementation sketch (inside the base, reusing existing lock helpers):
- `getColdSlices`: `auto [s, w] = acquireLocked(slices, windows);` (the deadlock-free folly multi-lock already used at `cpp:78`,`cpp:276`). Build `set<SliceEnd> stillFilling` by scanning `*w`: for each window with `windowState == WINDOW_FILLING` **and** `windowInfo.windowEnd >= watermark`, mark every slice's `sliceEnd` as not-cold; also mark every slice in any `EMITTED_TO_PROBE`/`WAITING_ON_TERMINATION` window as not-cold (emitted = unsafe; waiting = pending termination, conservative). Then iterate `*s` for `sliceEnd < watermark` not in the not-cold set, push the `shared_ptr`. Returns oldest-first naturally (`std::map<SliceEnd,...>` is sorted ascending) — matches "oldest-first" eviction in the brief.
- `forEachSliceAccumulate`: `slices.rlock()`, fold `cost(*ptr)`.

**Lock discipline note (feeds O-B3):** `getColdSlices` takes BOTH base locks at once via `acquireLocked` — but this is the SAME pairing the base already does in `getSlicesOrCreate`/`deleteState`, so it does not introduce a new lock-order. A's rule was "never hold two of *our* (subclass) locks" (`SpillableTimeBasedSliceStore.cpp:156-158`); the base freely pairs its own two. The subclass must call `getColdSlices`, **release** the implicit base locks (they're released when the function returns), then take `emittedKeys`/`spilledKeys` one at a time during the actual spill — no nesting. There is still the M1 TOCTOU (`cpp:201-210`): a window can flip to `EMITTED_TO_PROBE` between `getColdSlices` returning and the subclass spilling. The §2 predicate + the `emittedKeys` re-check inside the spill loop (already there in `forceSpill`) bounds this; O-B3's job is to make the check+spill atomic (likely a single coarse eviction mutex held across "pick → re-confirm not-emitted → spill → record"). `getColdSlices` being a pure snapshot keeps that mutex small.

### Blast radius of (b)/(c)
- **Subclasses of `DefaultTimeBasedSliceStore`:** exactly ONE — `SpillableTimeBasedSliceStore` (`SpillableTimeBasedSliceStore.hpp:54`, `grep` confirms no others). New `protected` members are invisible to all the public users.
- **Public users of `DefaultTimeBasedSliceStore`** (lowering rules only): `LowerToPhysicalWindowedAggregation.cpp:229,252`, `LowerToPhysicalHashJoin.cpp:290`, `LowerToPhysicalNLJoin.cpp:116`. They construct it and call only the public interface — **unaffected** by new protected methods.
- Adding methods (not changing existing signatures, not reordering members, not touching the private maps' types) is **ABI-additive only**. Regression risk vs the 805 baseline is minimal: no existing call path changes.
- Cost: ~30 LoC in the base + 2 declarations. Encapsulation is preserved (maps stay private; only a computed snapshot leaks). This is strictly cleaner than making the maps `protected:` (which would let the subclass mutate them and re-implement locking — high risk).

**RECOMMENDATION: Option (c) — two `protected` read-only snapshot helpers (`getColdSlices(watermark)`, `forEachSliceAccumulate(cost)`) on `DefaultTimeBasedSliceStore`.** Do NOT expose the maps. Keep self-tracking ONLY for the `residentBytes()` fast path if profiling shows `forEachSliceAccumulate` is too hot on the build path (it isn't called per-tuple if eviction cadence is decoupled — see O-B2). For the watermark needed by `getColdSlices`, the store caches `lastSeenWatermark` updated in the trigger feeders (the only place a watermark enters); eviction uses that conservative (lagging) value, which is SAFE because a lagging watermark only ever *under*-selects cold slices (never spills something the engine still considers hot).

---

## 2. WindowInfoState & the precise "spillable cold slice" predicate

`Slice.hpp:40-45` (NOTE: enum lives in **`Slice.hpp`**, not in DefaultTimeBasedSliceStore): `WindowInfoState { WINDOW_FILLING, WAITING_ON_TERMINATION, EMITTED_TO_PROBE }`. State is **per window**, stored in `SlicesAndState.windowState` (`DefaultTimeBasedSliceStore.hpp:52`), NOT per slice.

State transitions, from the base code:
- Created / new tuples expected → `WINDOW_FILLING` (`DefaultTimeBasedSliceStore.cpp:97`).
- `windowEnd < globalWatermark` AND trigger feeder runs → `EMITTED_TO_PROBE` (`cpp:124-130`). **This is the point of no return for spilling** — the raw `HashMap*` is snapshotted for the async probe (async-probe-drain invariant).
- Pipeline terminated while others active → `WAITING_ON_TERMINATION` (`cpp:179-189`); then last termination → `EMITTED_TO_PROBE` (`cpp:203`).

A slice belongs to all windows from `sliceAssigner.getAllWindowsForSlice(slice)` (`SliceAssigner.hpp:69-94`); for sliding (size > slide) that's multiple windows.

### Predicate (computable inside the base under the windows+slices locks)

```
spillableCold(slice, watermark) :=
      slice.sliceEnd < watermark                                  // (a) past the watermark
  AND slices.contains(slice.sliceEnd)                             // (b) still live (not GC'd)
  AND for EVERY window W in windows that contains this slice:
          W.windowState != EMITTED_TO_PROBE                       // (c) async-probe-drain: never spill emitted
      AND NOT (W.windowState == WINDOW_FILLING AND W.windowEnd >= watermark)  // (d) no window still filling
```

Plain-language: **spill only if the slice is past the watermark, still resident in the store, NO window that references it has been emitted to the probe, and NO window that references it is still accepting tuples.** `WAITING_ON_TERMINATION` is treated conservatively as not-cold (a pending termination may still emit it; safest to leave resident). Clauses (c)+(d) together capture the brief's "NONE of its windows is EMITTED_TO_PROBE and it's no longer filling".

This subsumes the existing A guard (`emittedKeys`): the predicate's clause (c) is the windows-map-level truth; `emittedKeys` is the subclass-side shadow of the same fact and remains as the atomic re-check at spill time (defense against the M1 TOCTOU). The store MUST also exclude `spilledKeys` (already spilled → no-op, `forceSpill` handles it at `cpp:219-223`).

---

## 3. Variable-sized serialization (B-varsized)

### Current fixed-size format (`SliceSerializer.cpp:57-171`)

Header (`HEADER_SIZE = 2×u32 + 4×u64 = 40 bytes`, `SliceSerializer.hpp:54`), little-endian POD append:

| field | type | source |
|---|---|---|
| MAGIC | u32 | `0x4E535331` = "NSS1" (`SliceSerializer.hpp:50`) |
| VERSION | u32 | `1` (`SliceSerializer.hpp:52`) |
| entrySize | u64 | caller (= `sizeof(ChainedHashMapEntry)+keySize+valueSize`) |
| pageSize | u64 | caller |
| numberOfBuckets | u64 | **pre-rounding** value passed to ctor (so `calcCapacity` reproduces the exact chain count — `cpp:76-79`) |
| numberOfEntries | u64 | `map.getNumberOfTuples()` |

Body: for each entry, walking every chain (`cpp:89-98`): `[u64 hash][payloadSize bytes payload]`, where `payloadSize = entrySize - sizeof(ChainedHashMapEntry)` (`ENTRY_HEADER_SIZE`, `cpp:33`). The `next` pointer is **dropped** (absolute address, rebuilt by replaying `insertEntry` on deserialize — `cpp:160-168`). Round-trip integrity checks: `writtenEntries == numberOfEntries` (`cpp:101`), overflow-safe body-size check (`cpp:150-158`), magic/version/bucket/pageSize guards (`cpp:118-146`).

### The explicit fixed-size guard
There is **no runtime guard** that the map is fixed-size — only the **doc PRECONDITION** in `SliceSerializer.hpp:38-40` ("the map uses fixed-size keys and values… not supported in this phase") and the structural assumption that the payload is `entrySize - ENTRY_HEADER_SIZE` self-contained bytes. **Today, serializing a var-sized map would silently corrupt:** the payload contains an 8-byte **absolute pointer** into `varSizedSpace` (see below), which is captured verbatim as bytes and becomes a dangling pointer after a round-trip — exactly the "next ptr drop in Phase 2" class of bug, but worse because nothing rebuilds it.

### Var-sized layout (the truth, from `ChainedEntryMemoryProvider.cpp` + `ChainedHashMap.cpp`)

- A VARSIZED field's slot **inside the fixed entry payload** holds an **`int8_t*` absolute pointer** (8 bytes), written at `ChainedEntryMemoryProvider.cpp:141`: `*memoryAddressInEntry = reinterpret_cast<const signed char*>(spaceForVarSizedData.data());`
- That pointer targets a region in `ChainedHashMap::varSizedSpace` (a `std::vector<TupleBuffer>`, `ChainedHashMap.hpp:106`) allocated by `allocateSpaceForVarSized` (`ChainedHashMap.cpp:132-149`).
- The pointed-to region layout (`ChainedEntryMemoryProvider.cpp:136-141`): `[u32 varSizedDataSize][varSizedDataSize bytes payload]`. So total allocation = `varSizedDataSize + sizeof(uint32_t)`.
- Read path (`ChainedEntryMemoryProvider.cpp:91-99`): deref the slot pointer → read `u32` size → payload follows. Confirms the `[u32 len][bytes]` framing.
- `allocateSpaceForVarSized` over-allocates (`neededSize * NUMBER_OF_PRE_ALLOCATED_VAR_SIZED_ITEMS=100`, `ChainedHashMap.cpp:103,137`) into an unpooled buffer; uses `TupleBuffer::numberOfTuples` as a **byte-fill cursor** (`cpp:147`: `setNumberOfTuples(getNumberOfTuples() + neededSize)`), and returns a subspan. There can be MULTIPLE var-sized buffers (a new one when the current is full, `cpp:134`). These buffers are **unpooled and NOT part of `storageSpace`** — they are never walked by the chain iteration the serializer uses.

### What a var-sized-aware serialize/deserialize MUST capture

The chain-walk already visits every entry's fixed payload (`cpp:91-98`). The problem is purely the per-entry **absolute pointer → variable blob** indirection. The serializer must:

1. **Know which fields in the payload are VARSIZED pointers and at what offset.** The serializer today is field-agnostic (it treats payload as opaque bytes). It needs a description: a list of `{offsetInPayload, nullablePrefix}` for each VARSIZED field. This comes from the same `Schema`/`FieldOffsets` the `ChainedEntryMemoryProvider` builds (`ChainedEntryMemoryProvider.cpp:41-70`). **This is new caller-supplied metadata** (analogous to how `entrySize`/`pageSize`/`numberOfBuckets` are already passed in rather than read from private state — `SliceSerializer.hpp:55-59`).
2. **For each VARSIZED slot:** instead of writing the raw 8-byte pointer, follow it, read the `[u32 len]`, and serialize **the blob inline** (`[u32 len][len bytes]`) into the record, writing a **sentinel/relative marker** (not the absolute pointer) into the payload copy. On deserialize, after `insertEntry` rebuilds the fixed entry, call `map->allocateSpaceForVarSized(provider, len + sizeof(u32))`, copy the blob in with its `[u32 len]` header, and patch the entry slot to the **new** pointer — exactly mirroring `storeVarSized` (`ChainedEntryMemoryProvider.cpp:123-148`). This rebuilds the indirection the same way the chain `next` pointers are rebuilt (replay, not relocate).

### Risks
- **Absolute pointers vs offsets (the central risk).** Same failure mode as the Phase-2 `next` drop, but the var-sized pointer is *inside the payload bytes* the serializer currently copies verbatim. If B forgets to neutralize it, the record contains a stale heap address that survives to disk and is dereferenced on read → UAF / corruption. The format MUST NOT persist the absolute pointer; it must persist `[len][bytes]` and rebuild the pointer on load.
- **Null var-sized fields.** A VARSIZED slot can be null (nullable prefix byte, `ChainedEntryMemoryProvider.cpp:84-89`). Serializer must check the null byte and emit a "no blob" marker, not deref a null/garbage pointer.
- **Multiple blobs per entry** (several VARSIZED fields) and **ordering** must be deterministic (iterate fields in `FieldOffsets` order).
- **Endianness / 32-bit length cap** — the framing uses `u32` length; keep it (matches the runtime `storeVarSized`).
- **`numberOfBuckets` idempotency** is unaffected; var-sized blobs do not change chain count.

### Format extension — **bump VERSION to 2.**

`SliceSerializer.hpp:52` `VERSION` → `2`. A V2 record is V1 + a var-sized section. Proposed minimal extension (keeps V1 readers rejecting V2 via the existing version guard at `cpp:122-125`):

- Header: add one u64 `varSizedFieldCount` (or a small descriptor table) so a record is self-describing. (Bump `HEADER_SIZE` accordingly, and update the test's `OFFSET_*` constants — `SliceSerializerTest.cpp:135-138`.)
- Per entry, after the fixed payload, append for each VARSIZED field, in field order: `[u8 isNull]` then if non-null `[u32 len][len bytes]`. The fixed payload's pointer slot is written as zero (sentinel) in the record.
- Deserialize: rebuild entry, then for each VARSIZED field replay `allocateSpaceForVarSized` + patch the slot.
- **Keep V1 fully working** (fixed-size with `varSizedFieldCount == 0` is byte-identical behavior) so existing `SliceSerializerTest` round-trips and the existing `SpillableAggregationSlice::spill/unspill` path (`SpillableAggregationSlice.cpp:97-137`) are unchanged for fixed-size aggregations. Decision point for the architect: V2-always vs V1-when-no-varsized. **Recommend: emit V1 when the field descriptor has zero VARSIZED fields, V2 otherwise** — minimizes regression surface and keeps the 805 baseline records identical.
- `SpillableAggregationSlice::spill` (`cpp:97-110`) must pass the new var-sized field descriptor to `serialize`. It already has `createNewHashMapSliceArgs` (keySize/valueSize/etc.) but NOT the field schema today — the descriptor must be threaded through `CreateNewHashMapSliceArgs` (new field) from the lowering rule, same plumbing path as `numberOfBuckets`.

---

## 4. residentBytesOf with var-sized — does the page-math budget still hold?

`residentBytesOf` (`SpillableTimeBasedSliceStore.cpp:234-255`) = `Σ_workers ⌈tuples_w / entriesPerPage⌉ × pageSize`. This counts ONLY the `storageSpace` pages (the fixed entry pages). It is exact for fixed-size, as the brief states.

**With var-sized it UNDER-counts.** The `varSizedSpace` buffers (`ChainedHashMap.hpp:106`) are **separate unpooled buffers**, each over-allocated to `neededSize × 100` (`ChainedHashMap.cpp:137`) and not reflected in `getNumberOfTuples()` (which counts entries, not bytes). So a var-sized slice's true resident footprint = fixed pages + Σ var-sized buffer sizes, and the latter can dominate. The current page-math budget would let var-sized state blow the memory ceiling undetected.

**How to account for it:** ChainedHashMap exposes `getNumberOfPages()`/`getPage()` for storage but **nothing for `varSizedSpace`** today. To account correctly, add a read-only accessor on `ChainedHashMap`, e.g. `getVarSizedBytes() const` returning `Σ varSizedSpace[i].getBufferSize()` (the allocated size — what's actually held from the BufferManager) or the tighter `Σ getNumberOfTuples()` byte-cursor (the used bytes). Then `residentBytesOf` adds, per resident map, the `getVarSizedBytes()` of the underlying `ChainedHashMap`. Note: `residentBytesOf` currently dispatches through `HashMapSlice::getHashMapPtr` returning `HashMap*` (`cpp:247`), so it would need a `dynamic_cast<ChainedHashMap*>` (the slice already guarantees ChainedHashMap, see `SpillableAggregationSlice.cpp:105`). For the budget's soft/hard thresholds to be meaningful under var-sized, this accessor is **required**; otherwise the brief's "Option 1 per-store resident page math" silently excludes the largest allocations. Recommend: add `ChainedHashMap::getVarSizedBytes()` (sum of buffer sizes) and include it; a spilled slice still returns 0 (var-sized buffers are freed on `clear()`/map reset at `SpillableAggregationSlice.cpp:112`).

Caveat for the eval phase: because var-sized buffers over-allocate ×100, `getBufferSize()` over-states live data but accurately reflects memory *held* — which is what a memory ceiling cares about. Use buffer size (held), not used bytes, for the budget.

---

## 5. Test contracts

### B-evict / enumeration — extend `tests/SpillableTimeBasedSliceStoreTest.cpp`
Reuse its harness verbatim: `makeStore(&backend)` (`SpillableTimeBasedSliceStoreTest.cpp:95-110`) already plumbs `softThresholdBytes`/`hardThresholdBytes`; `spillableSliceFactory()`, `populate(...)`, `residentBytesOf`, `triggeredContains` (`:88-164`) are ready. New deterministic single-thread tests:
- `residentBytesAggregatesAcrossSlices` — fill several tumbling slices, assert store-wide `residentBytes()` == Σ `residentBytesOf` (drives `forEachSliceAccumulate`).
- `getColdSlicesExcludesFillingAndEmitted` — create slices, advance watermark partially, assert `getColdSlices(wm)` returns only the past-watermark, not-yet-emitted ones; then trigger one and assert it drops out (excluded because emitted).
- `getColdSlicesSlidingWindowSafety` — **the key new coverage**: configure `WINDOW_SIZE > WINDOW_SLIDE` so a slice spans 2 windows; assert a slice whose later window is still filling is NOT returned cold (the §2 predicate). (Current tests only use tumbling `WINDOW_SIZE==WINDOW_SLIDE==100`, `:73-74` — sliding is untested.)
- `evictionSpillsOldestUntilUnderSoftThreshold` — set a small soft threshold, fill past it, run the eviction entry point, assert oldest-cold spilled first, `residentBytes() <= soft`, correctness preserved on later unspill-on-read.
- `hardThresholdBehaviorWhenAllColdSpilled` (O4) — fill so even all-cold-spilled exceeds hard threshold; assert the chosen policy (block / error / proceed-over-budget) per O-B5.
- `overBudgetButNoColdSliceIsNoOp` (O-B5) — everything filling/emitted, over budget → no spill, logged, proceed.

### B-varsized — extend `tests/SliceSerializerTest.cpp`
Reuse `expectRoundTrip`/`populate`/`sortedEntries`/`validRecord`/`patchField` (`SliceSerializerTest.cpp:69-138`). The existing `populate` writes raw fixed bytes; add a `populateVarSized` that writes via the var-sized path (allocate a blob through `allocateSpaceForVarSized`, write `[u32 len][bytes]`, store the pointer in the slot) — or build through `ChainedEntryMemoryProvider` for fidelity. New tests:
- `roundTripVarSizedSingleField` / `MultiField` / `MultiPage` — parity of fixed payload + var-sized blob contents after serialize→deserialize; compare via a var-sized-aware `sortedEntries` that dereferences blobs (not raw pointers, which differ).
- `roundTripVarSizedNullField` — null var-sized slot survives.
- `roundTripVarSizedThroughInMemoryBackend` — mirror `:157-172` but var-sized.
- `v1RecordStillRoundTrips` / `rejectsVersionMismatchAcrossV1V2` — V2 reader rejects nothing it shouldn't; existing V1 fixed tests stay green (regression).
- Update `OFFSET_*` header offset constants (`:135-138`) if `HEADER_SIZE` grows.
- Adversarial: truncated var-sized blob, length larger than remaining record (extend the overflow-safe checks at `:150-158`).

### B-concurrency (informational, owned by other explorers)
TSan/stress tests live in the store test file; the §1 `getColdSlices` snapshot + single eviction mutex (O-B3) are what those tests must exercise (build + trigger + evict, no UAF, correct aggregates). `cmake/Sanitizers.cmake` exists (per brief O-B4) — wire a TSan target if feasible.

### Regression
Full `ctest -LE 'Systest|DockerCompose'` stays at the 805 baseline; the V1-when-no-varsized format choice keeps existing serializer/store records byte-identical.

---

## Recommended enumeration API + varsized format

**Enumeration (O-B1):** Add two `protected` read-only snapshot helpers to `DefaultTimeBasedSliceStore` (option c) — do NOT expose the private maps:
```cpp
protected:
    [[nodiscard]] std::vector<std::shared_ptr<Slice>> getColdSlices(Timestamp watermark) const; // oldest-first
    [[nodiscard]] uint64_t forEachSliceAccumulate(const std::function<uint64_t(const Slice&)>& cost) const;
```
`getColdSlices` computes the §2 predicate under the existing `acquireLocked(slices, windows)` pairing and returns refcount-pinned snapshots; `forEachSliceAccumulate` powers store-wide `residentBytes()`. The subclass keeps `spilledKeys`/`emittedKeys` for the atomic spill-time re-check and caches `lastSeenWatermark` (updated only in the trigger feeders) to feed `getColdSlices` — a lagging watermark only ever under-selects, which is safe. Self-tracking alone (option a) is rejected: it cannot see per-window `WindowInfoState` and is unsafe for sliding windows.

**Var-sized format (B-varsized):** Bump `SliceSerializer::VERSION` 1→2. V2 = V1 header + a `varSizedFieldCount`/descriptor, and after each entry's fixed payload, per VARSIZED field in field order: `[u8 isNull]` then if non-null `[u32 len][len bytes]`; the entry's pointer slot is zeroed in the record and rebuilt on deserialize via `ChainedHashMap::allocateSpaceForVarSized` (mirroring `storeVarSized`). NEVER persist the absolute `int8_t*`. Emit V1 when there are no VARSIZED fields (byte-identical to today → zero regression). Thread the var-sized field descriptor through `CreateNewHashMapSliceArgs` from the lowering rule, same path as `numberOfBuckets`.

## Contract changes & blast radius

| Change | File | Blast radius |
|---|---|---|
| +2 `protected` methods `getColdSlices`, `forEachSliceAccumulate` | `DefaultTimeBasedSliceStore.{hpp,cpp}` | ABI-additive. Only subclass = `SpillableTimeBasedSliceStore` (sole subclass, grep-confirmed). Public users (3 lowering rules) unaffected. ~30 LoC. Maps stay private. |
| Cache `lastSeenWatermark` + eviction entry point + soft/hard enforcement | `SpillableTimeBasedSliceStore.{hpp,cpp}` | Self-contained; `softThresholdBytes`/`hardThresholdBytes` already plumbed (`hpp:111-112`, currently `[[maybe_unused]]`). |
| `SliceSerializer` V2 + new var-sized field descriptor param | `SliceSerializer.{hpp,cpp}` | Adds an overload/param. `SpillableAggregationSlice::spill/unspill` (`cpp:97-137`) must pass the descriptor. Existing fixed-size callers/tests unchanged if V1-when-empty is honored. |
| `+CreateNewHashMapSliceArgs` var-sized field descriptor | `HashMapSlice.hpp:31-55` | Constructor gains a field; constructed in lowering (`LowerToPhysicalWindowedAggregation.cpp`) and tests (`SpillableTimeBasedSliceStoreTest.cpp:81-86`, `makeArgs`). Additive. |
| `+ChainedHashMap::getVarSizedBytes() const` | `ChainedHashMap.{hpp,cpp}` | Read-only accessor over `varSizedSpace`. Touches nes-nautilus (core) but additive; needed for §4 budget correctness. Used by `residentBytesOf`. |
| Tests: extend `SliceSerializerTest.cpp`, `SpillableTimeBasedSliceStoreTest.cpp` | tests | New cases; update `OFFSET_*` if `HEADER_SIZE` grows. |

**Gating decision (O-B1): option (c) protected snapshot helpers** — lowest safe blast radius (one subclass, additive, encapsulation preserved), and the only option that makes per-window sliding coldness correct, which self-tracking cannot. This unblocks B-evict.
