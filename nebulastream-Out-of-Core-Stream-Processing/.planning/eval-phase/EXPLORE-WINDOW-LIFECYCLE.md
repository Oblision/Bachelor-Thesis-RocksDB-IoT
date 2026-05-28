# EXPLORE: Window/Slice Lifecycle vs. the OOC Spill Target

Branch: `feat/ooc-phase1-spill-skeleton`. Method: read-only code audit, every claim
quoted with `file:line`. Repo paths are absolute from
`nebulastream-Out-of-Core-Stream-Processing/`.

TL;DR verdict (details below):
- `WAITING_ON_TERMINATION` does **NOT dwell** during normal streaming. It is reached
  ONLY on input-pipeline termination (multi-pipeline joins), never on watermark
  advance. For a single-pipeline tumbling aggregation it is never entered.
- A retained-but-unemitted population **does NOT exist today** without engine changes:
  `getTriggerableWindowSlices` does `WINDOW_FILLING → EMITTED_TO_PROBE` in the SAME
  call that advances the build watermark. "Cold" and "emitted" are produced atomically.
- A quiescent (past-watermark, not-yet-emitted) window **CAN still be written** by a
  late tuple — the build path has no watermark gate. So a spilled quiescent window is
  NOT write-final → option (c) needs unspill-on-write + cache invalidation.
- The fix that creates a dwellable spill population is a **trigger/quiescence-watermark
  decoupling (option b)**, which is a host-C++ handler/store change and stays OUT of
  Nautilus JIT. Recommended: **b as the primary redefinition, hardened by the
  unspill-on-write half of c** (because of late writes), with a as a non-viable fallback.

---

## 1. The window/slice state machine

### 1.1 Enum and per-window record (state is per-WINDOW, confirmed)
`nes-physical-operators/include/SliceStore/Slice.hpp:40-45`:
```cpp
enum class WindowInfoState : uint8_t
{
    WINDOW_FILLING,
    WAITING_ON_TERMINATION,
    EMITTED_TO_PROBE
};
```
The doc-comment transition table is at `Slice.hpp:31-39` (note it is written from the
join's perspective — "Left or Right Pipeline Terminated"):
```
WINDOW_FILLING          | Global Watermark Ts > WindowEnd   | EMITTED_TO_PROBE
WINDOW_FILLING          | Left or Right Pipeline Terminated | WAITING_ON_TERMINATION
WAITING_ON_TERMINATION  | Query/Pipeline Terminated         | EMITTED_TO_PROBE
EMITTED_TO_PROBE        | Tuples join in Probe              | CAN_BE_DELETED
```
(`CAN_BE_DELETED` is documentary only — there is no enum value for it; deletion is the
GC erasing the map entry.)

State lives **per window**, NOT per slice. It is a field of `SlicesAndState`
(`nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp:44-53`):
```cpp
struct SlicesAndState {
    explicit SlicesAndState(const uint64_t numberOfExpectedSlices)
        : windowState(WindowInfoState::WINDOW_FILLING) { ... }
    std::vector<std::shared_ptr<Slice>> windowSlices;
    WindowInfoState windowState;
};
```
stored in `folly::Synchronized<std::map<WindowInfo, SlicesAndState>> windows;`
(`DefaultTimeBasedSliceStore.hpp:88`). `Slice` itself
(`Slice.hpp:48-67`) carries NO state field — only `sliceStart`/`sliceEnd`. The `slices`
map (`DefaultTimeBasedSliceStore.hpp:89`, keyed by `SliceEnd`) is the build-path
structure; the `windows` map is the trigger-path structure. **Both base maps are
`private`** — the subclass cannot enumerate window state directly (it works around this
with its own `createdSlices`/`spilledKeys`/`emittedKeys`; see `SpillableTimeBasedSliceStore.hpp:55-87`).

### 1.2 Transition: `Start → WINDOW_FILLING`
`DefaultTimeBasedSliceStore.cpp:90-99` (in `getSlicesOrCreate`):
```cpp
for (auto windowInfo : sliceAssigner.getAllWindowsForSlice(*newSlice)) {
    const auto [it, success] = windowsWriteLocked->try_emplace(windowInfo, numberOfExpectedSlices);
    INVARIANT(it->second.windowState != WindowInfoState::EMITTED_TO_PROBE,
        "We should not add slices to a window that has already been triggered.");
    it->second.windowState = WindowInfoState::WINDOW_FILLING;
    it->second.windowSlices.emplace_back(newSlice);
}
```
Condition: a NEW slice (cache miss / never-seen `sliceEnd`) is created for an incoming
timestamp. **Key danger**: the `INVARIANT` at line 94-96 means routing a tuple into an
already-`EMITTED_TO_PROBE` window terminates the engine (see §3).

### 1.3 Transition: `WINDOW_FILLING → EMITTED_TO_PROBE` (the only normal-streaming exit)
`DefaultTimeBasedSliceStore.cpp:104-139` (`getTriggerableWindowSlices`):
```cpp
for (auto& [windowInfo, windowSlicesAndState] : *windowsWriteLocked) {
    if (windowInfo.windowEnd >= globalWatermark) { break; }              // 119
    if (windowSlicesAndState.windowState == WindowInfoState::EMITTED_TO_PROBE) { continue; } // 124
    windowSlicesAndState.windowState = WindowInfoState::EMITTED_TO_PROBE;  // 130
    const auto newSequenceNumber = SequenceNumber(sequenceNumber++);
    for (auto& slice : windowSlicesAndState.windowSlices) {
        windowsToSlices[{windowInfo, newSequenceNumber}].emplace_back(slice);
    }
}
```
Condition: `windowEnd < globalWatermark` (the `break` at 119 means the loop only ever
touches windows strictly below the watermark). State is set to `EMITTED_TO_PROBE`
**directly from `WINDOW_FILLING`** at line 130 — `WAITING_ON_TERMINATION` is **never set
here**. This is the watermark-driven emit and it is the ONLY exit during steady-state
streaming.

### 1.4 Transition: `→ WAITING_ON_TERMINATION` (termination-only; the key dwell question)
`WAITING_ON_TERMINATION` is set at **exactly one site**:
`DefaultTimeBasedSliceStore.cpp:179-189` inside `getAllNonTriggeredSlices`:
```cpp
case WindowInfoState::WINDOW_FILLING: {
    if (numberOfActiveInputPipelines > 0) {                                // 181
        windowSlicesAndState.windowState = WindowInfoState::WAITING_ON_TERMINATION; // 183
        NES_TRACE("Waiting on termination ...");
        break;
    }
    addAllSlicesToReturnMap(windowInfo, windowSlicesAndState);
    break;
}
```
`getAllNonTriggeredSlices` is the **termination path** — it is invoked by
`triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91-96`), and it decrements
`numberOfActiveInputPipelines` (`DefaultTimeBasedSliceStore.cpp:157-158`). The state is
set to `WAITING_ON_TERMINATION` ONLY when more than one input pipeline still has not
terminated (`numberOfActiveInputPipelines > 0`), i.e. the multi-pipeline join case where
one side terminated but the other is still live. It then leaves via
`WAITING_ON_TERMINATION → EMITTED_TO_PROBE` at `DefaultTimeBasedSliceStore.cpp:193-204`
(when the last pipeline terminates).

**Verdict on dwell:** `WAITING_ON_TERMINATION` is NOT a steady-state, watermark-driven
state. During normal streaming it is **never entered** — the only normal exit is the
watermark emit in §1.3. For a single-input tumbling aggregation
(`numberOfActiveInputPipelines == 1`), even at termination the `> 0` test is false, so
the window goes straight to `EMITTED_TO_PROBE` (line 190) and never visits
`WAITING_ON_TERMINATION` at all. **It is therefore NOT a usable dwellable spill target.**

### 1.5 Does emit free the state? (No — raw HashMap* handed to async probe)
`AggregationOperatorHandler::triggerSlices`
(`nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:80-157`):
- snapshots **raw** `HashMap*` (line 104 `allHashMaps.emplace_back(hashMap)`),
- packs them into `EmittedAggregationWindow` via placement-new (line 144),
- dispatches asynchronously: `pipelineCtx->emitBuffer(tupleBuffer)` (line 148).

So `EMITTED_TO_PROBE` does NOT free the slice's HashMaps; it publishes raw pointers to
the async probe. The maps stay alive until the probe drains and the probe-side watermark
GC erases them — `garbageCollectSlicesAndWindows` (`DefaultTimeBasedSliceStore.cpp:212-272`)
erases a window only when `windowEnd < newGlobalWaterMark && state == EMITTED_TO_PROBE`
(line 227) and a slice only when `sliceEnd + windowSize < newGlobalWaterMark` (line 252).
This is the **async-probe-drain invariant**: an `EMITTED_TO_PROBE` slice must never be
spilled (its raw pointer is in flight). The Spillable subclass enforces this via
`emittedKeys` (`SpillableTimeBasedSliceStore.cpp:228, 435-439, 527-531`).

---

## 2. Is there a retained-but-not-emitted population at all?

**No, not today, without engine changes.** The chain that decides "triggerable" is purely
`windowEnd < globalWatermark`:
- `WindowBasedOperatorHandler::checkAndTriggerWindows`
  (`nes-physical-operators/src/WindowBasedOperatorHandler.cpp:73-89`) computes
  `newGlobalWatermark = watermarkProcessorBuild->updateWatermark(...)` then **immediately**
  `getTriggerableWindowSlices(newGlobalWatermark)` (line 87) followed by `triggerSlices(...)`
  (line 88). No buffering, no lateness, no termination-delay between "watermark passes
  window" and "window emitted".
- It is driven per output buffer from `WindowBuildPhysicalOperator::close`
  (`nes-physical-operators/src/WindowBuildPhysicalOperator.cpp:71-84` →
  `checkWindowsTriggerProxy` line 37-52).

Because `getTriggerableWindowSlices` sets `EMITTED_TO_PROBE` (state transition) AND the
SpillableStore advances `lastSeenWatermark` **in the same call** (the cold predicate),
"cold" and "emitted" are produced atomically:
`SpillableTimeBasedSliceStore::getTriggerableWindowSlices`
(`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:264-280`):
```cpp
auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(globalWatermark); // 267
unspillAndMarkEmitted(result);                                                         // 268  -> marks emittedKeys
auto current = lastSeenWatermark.load(std::memory_order_relaxed);                      // 271
const auto incoming = globalWatermark.getRawValue();
while (incoming > current && !lastSeenWatermark.compare_exchange_weak(current, incoming, ...)) {} // 273-275
```
Cold is defined as `sliceEnd < lastSeenWatermark` (`pickAndReserveColdSlice`,
`SpillableTimeBasedSliceStore.cpp:498`: `if (end.getRawValue() >= wm) break;`). So at the
instant a slice becomes cold (its watermark passes), the very same trigger pass has
already marked it `EMITTED_TO_PROBE` / inserted it into `emittedKeys`. The cold set and
the emitted set coincide → the spillable population (cold ∧ ¬emitted) is **empty in a real
tumbling query**. This is precisely the E0 finding, and it is structural, not a bug in the
subclass.

The unit tests can only manufacture a cold-but-unemitted slice by **bypassing** the
feeders with the test hook `setLastSeenWatermarkForTest`
(`SpillableTimeBasedSliceStore.cpp:673-677`), which the tests explicitly acknowledge:
`tests/SpillableTimeBasedSliceStoreTest.cpp:410-412`:
```cpp
/// Make all three slices COLD (sliceEnd < watermark) WITHOUT emitting them (the base feeders couple
/// watermark-advance to emit; seeding the watermark is the only way to a cold-but-unemitted slice).
store->setLastSeenWatermarkForTest(Timestamp(WINDOW_SIZE * 10));
```
**This is direct in-code confirmation that no production path produces a cold-but-unemitted
slice.** No state holds windows across multiple watermark advances; each window is emitted
on the first trigger pass whose `globalWatermark > windowEnd`.

---

## 3. Late / out-of-order writes (decides spill-safety scope)

**A tuple CAN still be routed into a past-the-watermark window, and the build path has NO
watermark/lateness gate.** Trace:
- Build operator `AggregationBuildPhysicalOperator::execute`
  (`nes-physical-operators/src/Aggregation/AggregationBuildPhysicalOperator.cpp:85-132`),
  pure Nautilus/JIT:
  ```cpp
  const auto timestamp = timeFunction->getTs(ctx, record);                              // 92
  const auto hashMapPtr = sliceStoreRef->getDataStructureRef(timestamp, ctx.workerThreadId, operatorHandler); // 93
  ```
  It uses the **record's own event time**, with no comparison to any watermark, then
  writes the record into that slice's HashMap (lines 107-131). There is no "drop if late"
  branch anywhere on this path.
- `getDataStructureRef` (`DefaultTimeBasedSliceStoreRef.cpp:93-113`) → cache, and on miss
  → `defaultTimeBasedSliceStoreRefCacheMissProxy` (line 40-66) → `getSlicesOrCreate`
  (line 59).
- Slice mapping is `sliceAssigner.getSliceStartTs/getSliceEndTs(timestamp)`
  (`DefaultTimeBasedSliceStore.cpp:62-63`; `SliceAssigner.hpp:45-64`) — a pure function of
  the timestamp.

What happens to a late tuple depends on the target window's state:
- **Window still `WINDOW_FILLING` (not yet emitted):** the slice exists in `slices`;
  `getSlicesOrCreate` returns it (`DefaultTimeBasedSliceStore.cpp:66-69`) and the tuple is
  appended. Silent, allowed, common (out-of-order within the watermark slack).
- **Window already `EMITTED_TO_PROBE` but slice not yet GC'd:** if it comes through the
  create branch, the `INVARIANT` at `DefaultTimeBasedSliceStore.cpp:94-96` **terminates
  the engine**. (In practice the `slices` entry usually still exists so it takes the
  early-return path at 66-69 and silently appends to an already-emitted map — also unsafe,
  but that is a pre-existing engine property, not introduced by OOC.)
- **No lateness/grace/allowed-lateness knob exists.** Grep across `nes-physical-operators`,
  `nes-nautilus`, `nes-runtime`, `nes-operators` for
  `lateness|allowedLateness|grace|outOfOrder|watermarkDelay|delay` found NO window-lateness
  configuration — only unrelated hits (RocksDB "reopen", QueryLog out-of-order events,
  NLJSlice's multi-thread comment). Watermark slack is purely whatever the
  `MultiOriginWatermarkProcessor` emits; once `globalWatermark > windowEnd` the window is
  emitted on the next trigger pass and any later tuple for it is late.

**CRUCIAL conclusion for spill safety:** a quiescent (past-watermark) window is **NOT
write-final**. A spilled quiescent window CAN be targeted by a later tuple. Therefore any
spill target that includes slices still reachable by the build path **requires
unspill-on-write** (rebuild the map before the write) **and cache invalidation** (§4). The
relevant infra already exists: `SpillableAggregationSlice` accepts re-inserts after
unspill — `tests/SpillableAggregationSliceTest.cpp:198-210` ("The rebuilt map must accept
further inserts (late-arriving tuples after unspill)").

---

## 4. SliceCache coherence (decides whether build-path changes are needed)

### 4.1 What the cache stores and when it misses
`SliceCacheEntry` (`nes-physical-operators/include/SliceStore/SliceCache/SliceCache.hpp:33-48`):
```cpp
struct SliceCacheEntry {
    using DataStructure = const std::byte*;
    Timestamp::Underlying sliceStart;
    Timestamp::Underlying sliceEnd;
    DataStructure dataStructure;   // raw pointer to the slice's HashMap data
};
```
It caches a **raw HashMap data pointer** keyed by the `[sliceStart, sliceEnd)` range.
Lookup/replacement is entirely Nautilus/JIT
(`SliceCacheSecondChance.cpp:48-113`): a HIT (`searchInCache`, line 48-63; range test
`sliceStart <= timestamp < sliceEnd`, line 57) returns the cached `dataStructure`
**without ever consulting the store** (`SliceCacheSecondChance.cpp:84-88`). A MISS runs
the second-chance ring (line 94-112) and calls `replaceEntry` →
`defaultTimeBasedSliceStoreRefCacheMissProxy` (host C++) → `getSlicesOrCreate`. Even
`SliceCacheNone` re-resolves on EVERY access (`SliceCacheNone.cpp:37-45`: it always calls
`replaceEntry`), so with the None cache the store override sees every write.

### 4.2 Is there any existing invalidation/eviction by SliceEnd? No.
There is **no generation/epoch/version field** on `SliceCacheEntry` (the struct above is
the whole thing, and it is `static_assert(std::is_trivially_destructible_v<SliceCacheEntry>)`,
`SliceCache.hpp:52`). Entries are only overwritten when the second-chance ring picks them
as a victim on a miss. There is **no host-side hook to invalidate a cache entry by
SliceEnd**. So if a slice is spilled while a worker's cache still holds its old
`dataStructure` pointer, nothing detects the staleness on the HIT path.

### 4.3 Why this matters: spill frees and unspill relocates the HashMap
`SpillableAggregationSlice::spill` (`nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp:85-117`):
```cpp
backend.put(getSliceEnd(), WorkerThreadId(pos), record);
hashMaps[pos].reset();  // 112  free the map; its TupleBuffers return to the BufferManager
...
resident = false;       // 116
```
`unspill` (line 119-137) rebuilds a **brand-new** `ChainedHashMap` via
`SliceSerializer::deserialize` (line 133) — a **different address**. And
`getHashMapPtrOrCreate` / `getHashMapPtr` **INVARIANT (terminate) on a spilled slice**
(`SpillableAggregationSlice.cpp:53, 60`: `INVARIANT(resident, "... unspill() first")`).

Consequences:
- A cache HIT after spill returns a **dangling** pointer (freed map) → use-after-free /
  write into returned BufferManager memory. No code currently detects this.
- A cache MISS after spill goes through the proxy → `getSlicesOrCreate` → extractor
  (`DefaultTimeBasedSliceStoreRef.cpp:65` calls `dataStructureExtractor`, which calls
  `getHashMapPtrOrCreate`) → **terminates** on the still-spilled slice unless
  `getSlicesOrCreate` unspilled it first.

### 4.4 Is the override point host C++ (no JIT)? YES.
The cache-miss → `getSlicesOrCreate` path is **host C++**
(`defaultTimeBasedSliceStoreRefCacheMissProxy`, `DefaultTimeBasedSliceStoreRef.cpp:40-66`):
it is reached via `nautilus::invoke` from JIT, but the proxy body is plain C++ and calls
the virtual `sliceStore->getSlicesOrCreate(...)` (line 59), which the subclass already
overrides (`SpillableTimeBasedSliceStore.cpp:157-185`). **So unspill-on-write can be added
inside `getSlicesOrCreate` with NO Nautilus/codegen change.** The HIT path, however, is
JIT and bypasses the store entirely — invalidating a stale HIT requires either:
  (i) never spilling a slice that any worker cache might HIT (i.e. only spill provably
      unreachable slices), or
  (ii) adding a cheap residency check the JIT HIT path can read (a new field / flag), which
      WOULD touch codegen and is therefore OUT of the proposal's no-JIT scope.

**Implication:** the no-JIT constraint forces the spill target to be a set the build cache
can never HIT, OR forces unspill-on-write to be combined with a guarantee that the HIT path
cannot reach a spilled slice. The cleanest no-JIT story is: spill only slices whose
`sliceEnd < quiescenceWatermark`, accept that a late write to such a slice takes a cache
MISS (range test fails for an evicted entry — but NOT guaranteed; see Risk R1), and unspill
on that MISS in `getSlicesOrCreate`. The residual race (a stale HIT on a not-yet-evicted
entry whose slice was spilled) is the one place where pure no-JIT cannot be made fully
safe without §3's "write-final" guarantee.

---

## 5. The minimal viable redefinition — ranked

Spillable target candidates, evaluated against: dwell (is the population non-empty?),
blast radius, build-path/cache changes, JIT scope, async-probe-drain.

### (a) Spill `WAITING_ON_TERMINATION` slices — REJECTED
- **Dwell:** none in normal streaming (§1.4). Entered only on partial multi-pipeline-join
  termination; never for single-input tumbling aggregation. The population is empty for the
  thesis workload.
- **Access:** the subclass cannot even enumerate it — `windows` is private
  (`DefaultTimeBasedSliceStore.hpp:88`) and there is no public accessor that exposes
  per-window state. Would require a base-class API addition.
- **Verdict:** does not solve the problem; not a steady-state population. Out.

### (b) Decouple a "quiescence watermark" from the emit watermark — RECOMMENDED (primary)
Idea: introduce a quiescence threshold `Q < emitWatermark` (e.g. `emitWatermark -
windowSize`, or a configured lag). A slice is **spillable** when
`sliceEnd < Q` but **not yet** `EMITTED_TO_PROBE`. To make this population non-empty you
must delay emit relative to "watermark passed window" so windows DWELL in a
done-filling-but-not-emitted band. Concretely:
- The store keeps `lastSeenWatermark` as the build-progress signal (already does:
  `SpillableTimeBasedSliceStore.cpp:271-275`) and adds a second, lagging "spillable
  watermark". Cold-for-spill = `sliceEnd < spillableWatermark`. The emit feeder keeps using
  `globalWatermark` so result correctness/sequence numbering is unchanged.
- To create the dwell band you must NOT emit a window the instant its watermark passes —
  i.e. introduce an emit delay in `checkAndTriggerWindows` / `getTriggerableWindowSlices`
  (e.g. only emit windows with `windowEnd < globalWatermark - emitLag`). This is the
  semantic change: it **increases result latency by `emitLag`** but does NOT change result
  values (same tuples, same window assignment) as long as GC and the probe watermark are
  kept consistent.
- **Blast radius:** medium. Touches `getTriggerableWindowSlices`'s emit condition
  (`DefaultTimeBasedSliceStore.cpp:119`) OR the handler's call site
  (`WindowBasedOperatorHandler.cpp:87`), plus the subclass cold predicate. All **host
  C++**, no JIT. Risk: must keep the probe-side GC watermark
  (`garbageCollectSlicesAndWindows`, base line 227/252) consistent with the delayed emit so
  nothing is GC'd before emit.
- **Cache:** because the dwell band is `[Q, emitWatermark)` and still below the build
  watermark, late writes into it are possible (§3) → MUST combine with the unspill-on-write
  half of (c). Without that, (b) alone is unsafe.
- **Async-probe-drain:** preserved — only ¬`EMITTED_TO_PROBE` slices are spilled; the
  existing `emittedKeys` guard (`SpillableTimeBasedSliceStore.cpp:527-531`) still holds.
- **Verdict:** the only candidate that produces a real, configurable, dwellable spill
  population on the thesis workload without touching JIT. Cost = one tunable latency knob +
  GC-watermark care. This is the design pivot.

### (c) Spill any non-currently-written slice, with unspill-on-read AND unspill-on-write — RECOMMENDED (hardening, partial)
- **Dwell:** maximal population (every slice not actively being written is a candidate),
  but it overlaps the build-reachable set, so it is the riskiest.
- **Cache/build:** REQUIRES unspill-on-write in `getSlicesOrCreate` (host C++, no JIT —
  feasible, §4.4) AND a story for the JIT cache HIT path (§4.2-4.3), which is the genuinely
  hard, partly-out-of-scope part. Pure no-JIT cannot fully close the stale-HIT race.
- **Verdict:** adopt its **unspill-on-write half** as a safety net for (b)'s late writes,
  but do NOT adopt full (c) as the primary target under the no-JIT constraint — the HIT-path
  coherence gap is unresolved without codegen changes.

### Recommendation (ranked)
1. **(b) quiescence/emit-watermark decoupling** as the redefined spill target — the only
   one with a dwellable, non-empty population on a single-pipeline tumbling aggregation,
   fully host-C++/no-JIT, async-probe-drain preserved.
2. **+ unspill-on-write (the (c) half)** in `getSlicesOrCreate` to make a spilled quiescent
   slice safe against late tuples (§3 proves they are not write-final). Host C++, no JIT.
3. **(a)** rejected — no steady-state population, needs base API just to enumerate it.

Net: **b hardened by the unspill-on-write half of c.** Blast radius: emit condition +
cold/quiescence predicate + GC-watermark consistency + `getSlicesOrCreate` unspill — all in
`DefaultTimeBasedSliceStore` / `WindowBasedOperatorHandler` / `SpillableTimeBasedSliceStore`
(host C++). Stays out of Nautilus JIT EXCEPT the residual stale-HIT coherence gap (§4.4),
which a runtime experiment must characterize.

---

## Ambiguities / what a runtime experiment must resolve
- **R1 — stale cache HIT after spill:** the JIT `SliceCacheSecondChance` HIT path
  (`SliceCacheSecondChance.cpp:84-88`) is range-keyed and not invalidated on spill. It is
  unproven from code alone whether, in a real tumbling run, a worker can HIT an entry for a
  slice that was spilled between the entry's creation and the next write to it. Needs a
  concurrency experiment (build + background spiller) under the SecondChance cache to see if
  a stale HIT actually occurs, or if the second-chance eviction always displaces the entry
  before a late write returns. If it can occur, a JIT-side residency flag (out of current
  scope) is required.
- **R2 — emit-lag vs. result semantics/latency:** confirm empirically that delaying emit by
  `emitLag` (option b) leaves result *values* unchanged and only shifts latency, and that
  the probe-side GC watermark stays behind the delayed emit (no premature free). Drive with
  a deterministic watermark sequence and compare output to the non-OOC store.
- **R3 — late-write frequency:** measure how often a tuple actually arrives for a window in
  the `[Q, emitWatermark)` dwell band in the target IoT workload. If effectively zero in
  practice, full (c) becomes viable and unspill-on-write is a rarely-taken safety net; if
  non-trivial, (b)'s lag must be tuned so the dwell band sits below the realistic late-write
  horizon (slices old enough that no further writes arrive).
- **R4 — `getSlicesOrCreate` INVARIANT at line 94-96:** verify whether the engine ever
  legitimately routes a tuple into an `EMITTED_TO_PROBE` window in the target workload
  (would already crash today, OOC or not). If so, that is a pre-existing engine bound that
  also caps how aggressively we can shrink `emitLag`.
