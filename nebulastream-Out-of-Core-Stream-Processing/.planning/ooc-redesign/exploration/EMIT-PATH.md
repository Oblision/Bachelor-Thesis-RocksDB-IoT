# EMIT / PROBE / TRIGGER / TERMINAL-FLUSH path map

Repo: nebulastream-Out-of-Core-Stream-Processing
Branch: `feat/ooc-phase1-spill-skeleton` @ `1f30a75822`
Scope: read-only exploration for the OOC emit-path redesign (L1 incremental emit, L2 stream-from-RocksDB, L3 demand/backpressure). No source edited.

## Summary

The emit path is a **two-pipeline, asynchronous** design. The slice store hands triggerable slices to the host-side `AggregationOperatorHandler::triggerSlices`, which (1) **eagerly dereferences every slice's raw `HashMap*`**, (2) builds an `EmittedAggregationWindow` struct holding **N raw `HashMap*` snapshots + one freshly-allocated empty `finalHashMap`**, packs it into a `TupleBuffer`, and (3) `emitBuffer`s it as a **new Task** to the downstream probe pipeline. The actual read-out — combining the N per-worker maps into the final map and lowering each key to an output row — happens **inside `AggregationProbePhysicalOperator::open()`, which is FULLY Nautilus/JIT-traced code** (`nautilus::val<>`, `ChainedHashMapRef` iterators, `nautilus::static_iterable`, `nautilus::invoke`). The spill store unspills any spilled slice **before** it is handed off (so the raw `HashMap*` snapshot is always live), and marks it `emittedKeys` so it can never be re-spilled while the async probe drains it.

**Bottom line for L2:** the read-out is JIT, and it is hard-wired to `HashMap*` / `ChainedHashMap*`. "Stream key→aggregate pairs directly from RocksDB instead of a HashMap" cannot be done without touching the JIT-traced probe (out of scope). L1 (incremental emit) and an L2-lite "chunked rebuild" variant are achievable as host-only changes. See verdict.

---

## 1. Trigger → emit flow

### 1a. Store side — what `getTriggerableWindowSlices` returns
`DefaultTimeBasedSliceStore::getTriggerableWindowSlices` (`nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStore.cpp:104-139`) returns
```cpp
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
```
For each window whose `windowEnd < globalWatermark` and not yet `EMITTED_TO_PROBE`, it flips state to `EMITTED_TO_PROBE`, assigns a fresh `SequenceNumber` (`sequenceNumber++`, line 132), and copies that window's `shared_ptr<Slice>`s into the result. So the unit handed downstream is **`shared_ptr<Slice>` (a whole slice = all worker hashmaps), keyed by (window, sequence number)** — NOT a raw buffer yet.

The spillable override `SpillableTimeBasedSliceStore::getTriggerableWindowSlices` (`SpillableTimeBasedSliceStore.cpp:330-355`) wraps the base: it computes a saturating `globalWatermark - emitLag` emit cutoff (line 339), delegates to the base, then calls `unspillAndMarkEmitted(result)` (line 342) so every returned slice is **resident** and recorded in `emittedKeys` before the handler ever touches its `HashMap*`.

### 1b. Handler dispatch — `WindowBasedOperatorHandler`
`WindowBasedOperatorHandler::checkAndTriggerWindows` (`nes-physical-operators/src/WindowBasedOperatorHandler.cpp:73-89`):
```cpp
const auto slicesAndWindowInfo = sliceAndWindowStore->getTriggerableWindowSlices(newGlobalWatermark);
triggerSlices(slicesAndWindowInfo, pipelineCtx);
```
`triggerSlices` is a pure-virtual host method (`WindowBasedOperatorHandler.hpp:90-93`).

### 1c. The aggregation handler — what is built and handed off
`AggregationOperatorHandler::triggerSlices` (`nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp:80-157`). Per (window, seq):
- iterates `allSlices`, casts each to `HashMapSlice` (line 94), and for every non-empty worker map **stores the raw `HashMap*`** into `allHashMaps` (line 104) and accumulates `totalNumberOfTuples`;
- creates one empty `finalHashMap` via `ChainedHashMap::createNewMapWithSameConfiguration(...)` (line 108) — a probe-private map so thread-local maps are not overwritten;
- allocates an **unpooled** `TupleBuffer` of `sizeof(EmittedAggregationWindow) + allHashMaps.size()*sizeof(HashMap*)` (lines 119-125);
- placement-news the struct into the buffer (line 144):
```cpp
new (tmp.data()) EmittedAggregationWindow{windowInfo.windowInfo, std::move(finalHashMap), allHashMaps};
```
- sets origin/seq/chunk/lastChunk/watermark/numberOfTuples (lines 132-139) and `pipelineCtx->emitBuffer(tupleBuffer)` (line 148).

The struct handed downstream (`AggregationOperatorHandler.hpp:39-55`):
```cpp
struct EmittedAggregationWindow
{
    EmittedAggregationWindow(const WindowInfo windowInfo, std::unique_ptr<HashMap> finalHashMap, const std::vector<HashMap*>& allHashMaps);
    WindowInfo windowInfo;
    HashMap* finalHashMapPtr;
    std::unique_ptr<HashMap> finalHashMap;   // probe-private accumulator
    uint64_t numberOfHashMaps;
    HashMap** hashMaps;                       // raw snapshots of the slice worker maps (stored just after the struct)
};
```

So the answer to "what is handed to the downstream stage": **a `TupleBuffer` whose payload is an `EmittedAggregationWindow` carrying `numberOfHashMaps` RAW `HashMap*` (= `ChainedHashMap*`) pointers into the still-live slice maps, plus one empty final `ChainedHashMap`.** The slice itself is not handed over; the raw map pointers are.

---

## 2. The TERMINAL FLUSH / quiescence path

End-of-stream flush is driven by the build operator's `terminate`, not by `close`/watermark:

`WindowBuildPhysicalOperator::terminate` (`nes-physical-operators/src/WindowBuildPhysicalOperator.cpp:104-108`):
```cpp
void WindowBuildPhysicalOperator::terminate(ExecutionContext& executionCtx) const
{
    auto operatorHandlerMemRef = executionCtx.getGlobalOperatorHandler(operatorHandlerId);
    invoke(triggerAllWindowsProxy, operatorHandlerMemRef, executionCtx.pipelineContext);
}
```
`triggerAllWindowsProxy` (same file, lines 54-61) → `opHandler->triggerAllWindows(piplineContext)`.

`WindowBasedOperatorHandler::triggerAllWindows` (`WindowBasedOperatorHandler.cpp:91-96`):
```cpp
const auto slicesAndWindowInfo = sliceAndWindowStore->getAllNonTriggeredSlices();
triggerSlices(slicesAndWindowInfo, pipelineCtx);
```

`DefaultTimeBasedSliceStore::getAllNonTriggeredSlices` (`DefaultTimeBasedSliceStore.cpp:150-210`) decrements `numberOfActiveInputPipelines` and, once it reaches 0, **adds EVERY remaining window's slices** (`WINDOW_FILLING` and `WAITING_ON_TERMINATION`) to the returned map via `addAllSlicesToReturnMap` (lines 162-170, called at 190/203).

The spillable override (`SpillableTimeBasedSliceStore.cpp:357-365`):
```cpp
std::map<...> SpillableTimeBasedSliceStore::getAllNonTriggeredSlices()
{
    auto result = DefaultTimeBasedSliceStore::getAllNonTriggeredSlices();
    unspillAndMarkEmitted(result);   // <-- THE OOM POINT: unspills the WHOLE retained band at once
    return result;
}
```
`unspillAndMarkEmitted` (`SpillableTimeBasedSliceStore.cpp:210-278`) loops over **all** returned slices and, for each non-resident one, calls `spillable->unspill(*backend, *provider)` (line 266), rebuilding every worker map back into the buffer pool. **This is the terminal unspill of the entire band → peak ≈ whole band.** Then `triggerSlices` (section 1c) is run over all of them, allocating an `EmittedAggregationWindow` per window and `emitBuffer`-ing them all into the task queue — so on top of the unspilled maps, every window's emit buffers are in flight simultaneously.

The actual per-window emit loop that runs at shutdown is `AggregationOperatorHandler::triggerSlices` (`AggregationOperatorHandler.cpp:84-156`) — the same loop quoted in section 1c, here driven over the full non-triggered set.

---

## 3. ★ CRITICAL L2 QUESTION: is the read-out JIT/Nautilus or host C++?

**Definitive answer: the aggregation read-out (probe/scan that turns a finished window's hash map into output rows) is FULLY JIT / Nautilus-GENERATED.** It lives in `AggregationProbePhysicalOperator::open()` (`nes-physical-operators/src/Aggregation/AggregationProbePhysicalOperator.cpp:51-170`), and every value in it is a `nautilus::val<>` traced into the compiled probe pipeline.

Evidence (all from that `open()`):
- The emitted window is read as a Nautilus pointer and its fields via `getMemberRef` / `readValueFromMemRef` (JIT mem-ref ops):
```cpp
const auto aggregationWindowRef = static_cast<nautilus::val<EmittedAggregationWindow*>>(recordBuffer.getMemArea());          // :63
auto hashMapRefs = readValueFromMemRef<HashMap**>(getMemberRef(aggregationWindowRef, &EmittedAggregationWindow::hashMaps)); // :69
auto finalHashMapPtr = readValueFromMemRef<HashMap*>(getMemberRef(aggregationWindowRef, &EmittedAggregationWindow::finalHashMapPtr)); // :70
```
- The combine loop iterates the raw maps through a **`ChainedHashMapRef`** (a Nautilus reference whose iterator yields `nautilus::val<ChainedHashMapEntry*>` — see `nes-nautilus/include/Nautilus/Interface/HashMap/ChainedHashMap/ChainedHashMapRef.hpp:42-107`, all members are `nautilus::val<>`):
```cpp
for (nautilus::val<uint64_t> curHashMap = 0; curHashMap < numberOfHashMaps; ++curHashMap) {        // :75
    const nautilus::val<HashMap*> hashMapPtr = hashMapRefs[curHashMap];                            // :77
    const ChainedHashMapRef currentMap(hashMapPtr, ...);                                           // :78
    for (const auto entry : currentMap) { ... finalHashMap.insertOrUpdateEntry(...); }             // :80-128
}
```
- The lower loop iterates `finalHashMap` (a `ChainedHashMapRef`), and per entry runs `aggFunction->lower(...)` over `nautilus::static_iterable(aggregationPhysicalFunctions)` and `executeChild(executionCtx, outputRecord)` (lines 133-157) — all JIT.
- It even frees the final map back through the JIT boundary via `nautilus::invoke(+[](EmittedAggregationWindow*){ ... finalHashMap.reset(); }, aggregationWindowRef)` (lines 160-169).

The consumer's **only contract with the producer is raw `HashMap*` / `ChainedHashMap*` pointers** packed in `EmittedAggregationWindow`. There is no host-side scan of the window's keys anywhere in the emit/probe path. Therefore "stream (key→aggregate) pairs from RocksDB instead of a HashMap*" is impossible without modifying this JIT-traced operator (OUT OF SCOPE).

---

## 4. What does the consumer MINIMALLY need?

The probe needs an `EmittedAggregationWindow` containing **one or more valid `ChainedHashMap*`** (the `hashMaps[]` array, `numberOfHashMaps`) plus an empty `finalHashMap` of the same configuration, and the window's `WindowInfo` (start/end). It does NOT require that those maps be "the slice's real maps" — it only walks them via `ChainedHashMapRef` and combines into `finalHashMap`.

**Is the probe driven per-hashmap or per-window?** It is driven **per-emitted-buffer = per-window**: one `EmittedAggregationWindow` carries N maps and the probe combines all N into one `finalHashMap`, then lowers `finalHashMap` once and emits each key as an output row. Within that, the combine step is genuinely **per-hashmap** (the `for curHashMap` loop, line 75) — it already tolerates an arbitrary number of independent `ChainedHashMap*`. So feeding it a **bounded, chunk-at-a-time rebuilt `ChainedHashMap`** is mechanically compatible IF and only if a chunk is itself a complete window (one chunk = one `EmittedAggregationWindow`).

**Per-window assumptions that break if ONE window is split into multiple emit chunks:**
- **Final aggregation correctness:** the probe combines per-key across maps and lowers ONCE per key. If a key's tuples are split across two chunks (two separate `EmittedAggregationWindow`s, each with its own private `finalHashMap`), each chunk lowers that key independently → **duplicate / partial output rows** for the same (key, window). So the keyspace MUST be partitioned by key, not arbitrarily, for chunking to be correct (this is exactly the L3 "keyspace partitioning" requirement — chunking is only safe along key boundaries).
- **Sequence number:** `triggerSlices` assigns ONE `windowInfo.sequenceNumber` per window (from the store, `DefaultTimeBasedSliceStore.cpp:132/164`) and sets it on the buffer (`AggregationOperatorHandler.cpp:133`). Multiple chunks for one window would need distinct/ordered sequence numbers; the downstream sequencing/watermark machinery (`MultiOriginWatermarkProcessor`, `SequenceData(seqNumber, chunkNumber, lastChunk)`) keys on (seq, chunk, lastChunk). The buffer already has `setChunkNumber(INITIAL)` + `setLastChunk(true)` (`AggregationOperatorHandler.cpp:134-135`) — chunking would have to use the **chunk-number / lastChunk** mechanism instead of forcing a single last chunk, which the infrastructure already models (`SequenceData`, `ContinuationPolicy`).
- **EOS / watermark:** the buffer's watermark is `windowInfo.windowStart` (line 136); GC (`garbageCollectSlicesAndWindows`) and the probe watermark processor advance per emitted buffer. Splitting one window into several buffers with the same windowStart watermark is fine for GC, but the probe-side global watermark advance assumes one buffer per (window, seq).

Net: a host-only "chunked rebuild" that keeps **one chunk == one whole window** (i.e. bound peak to one window's maps at a time, not the whole band) is feasible and is exactly L1. True sub-window chunking (one window emitted as several buffers) requires key-partitioned chunks AND chunk-number sequencing — that is L3 territory, not a free L2 win.

---

## 5. The async-probe-drain invariant

**Confirmed: triggered slices are dispatched to the probe ASYNCHRONOUSLY (as a task), not run synchronously.** `AggregationOperatorHandler::triggerSlices` calls `pipelineCtx->emitBuffer(tupleBuffer)` (`AggregationOperatorHandler.cpp:148`). `emitBuffer` resolves to `PipelineExecutionContext::emitBuffer(buffer, ContinuationPolicy::POSSIBLE)` (`nes-executable/include/PipelineExecutionContext.hpp:42-48`), and the engine impl just forwards to a queued task handler — `QueryEngine.cpp:261-265`:
```cpp
bool emitBuffer(const TupleBuffer& buffer, ContinuationPolicy policy) override { ...; return handler(buffer, policy); }
```
So the `EmittedAggregationWindow` buffer (holding the **raw `HashMap*` snapshots**) is enqueued and the probe pipeline consumes it **later, on a worker thread** — the producing trigger thread does NOT block until the read-out finishes.

**Where the raw `HashMap*` is snapshotted:** in `triggerSlices`, `allHashMaps.emplace_back(hashMap)` (`AggregationOperatorHandler.cpp:104`) and the placement-new copy into the buffer (line 144). These pointers alias the slice's live maps and stay valid only as long as the slice's maps are not freed.

**When it is safe to free / why spilling an in-flight emitted slice is a use-after-free:** the store guards this with `emittedKeys`. Before the handler dereferences any map, `getTriggerableWindowSlices` → `unspillAndMarkEmitted` inserts the sliceEnd into `emittedKeys` **under `evictionMutex`, after waiting out any in-flight spill** (`SpillableTimeBasedSliceStore.cpp:234-253`):
```cpp
std::unique_lock lock(evictionMutex);
if (spillInProgress.contains(sliceEnd)) spillerCv.wait(lock, [&]{ return !spillInProgress.contains(sliceEnd); }); // wait out spill
emittedKeys.wlock()->insert(sliceEnd);   // claim BEFORE releasing the mutex
pinnedKeys.erase(sliceEnd);
```
Both spill pickers reject emitted slices: `pickAndReserveColdSlice` skips `emittedKeys.contains(end)` (`:621-624`) and `forceSpill` throws `SpillStoreFailure` for an emitted slice (`:525-529`). The slice's maps are only actually freed by base GC (`garbageCollectSlicesAndWindows`) once `windowEnd < newGlobalWaterMark && state == EMITTED_TO_PROBE` (`DefaultTimeBasedSliceStore.cpp:227`), i.e. AFTER the probe has advanced the output watermark past the window — so the snapshotted `HashMap*` outlives the async probe drain. The spillable GC also backend-erases + `emittedKeys.erase` at that point (`SpillableTimeBasedSliceStore.cpp:439-444`). The whole "spill must never touch an emitted slice" rule IS the async-probe-drain invariant.

---

## L1 / L2 FEASIBILITY VERDICT

- **L1 — incremental emit (unspill→emit→free one window/slice at a time): ACHIEVABLE AS A HOST-ONLY CHANGE.** The terminal flush is host code: `getAllNonTriggeredSlices` + `unspillAndMarkEmitted` + `triggerSlices` (`SpillableTimeBasedSliceStore.cpp:357-365`, `:210-278`; `AggregationOperatorHandler.cpp:80-157`). The whole-band unspill is a single `unspillAndMarkEmitted(result)` over the full map (`:360`), and the emit is a per-window loop. Restructuring so that each window is unspilled, emitted (`emitBuffer`), and its maps freed before moving to the next window touches ONLY these host functions — the JIT probe is unchanged because it still receives one ordinary `EmittedAggregationWindow` per window. **Caveat:** because emit is async (section 5), "free before next" must respect the `emittedKeys`/GC lifecycle (the maps cannot be freed until the probe drains them); true peak reduction needs either backpressure on the emit queue or the L3 demand-driven readback so the band of *in-flight* emit buffers stays bounded. Bounding the **unspill** to one window at a time is the straightforward host-only win; bounding the in-flight emitted set is L3.

- **L2 — stream spilled (key→aggregate) pairs directly from RocksDB to output WITHOUT rebuilding the in-memory hash map: NOT ACHIEVABLE AS A HOST-ONLY CHANGE.** The read-out is the JIT/Nautilus `AggregationProbePhysicalOperator::open()` (section 3), whose sole input contract is raw `ChainedHashMap*` pointers in `EmittedAggregationWindow`. Streaming from RocksDB instead of a `HashMap*` requires changing what the probe consumes (a RocksDB cursor / a different scan source), which means editing the Nautilus-traced probe operator — **OUT OF SCOPE**.
  - **Host-only L2-lite that IS feasible:** rebuild the spilled window into a **bounded, chunk-sized `ChainedHashMap`** on the host and feed the *existing* JIT probe one `EmittedAggregationWindow` per chunk. This caps peak at one chunk instead of the whole band while leaving the JIT path untouched. It is only correct if chunks are split along **key boundaries** (otherwise a key splits across chunks → duplicate output rows, section 4) and if multi-chunk windows use the existing chunk-number/`lastChunk` sequencing rather than forcing a single last chunk. That key-partitioned, bounded rebuild is effectively the L3 design; a naive sub-window chunk is incorrect.
