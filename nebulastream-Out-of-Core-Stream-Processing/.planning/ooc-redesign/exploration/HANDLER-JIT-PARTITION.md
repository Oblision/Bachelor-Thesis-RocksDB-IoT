# HANDLER + BUILD-SIDE JIT BOUNDARY + L3 PARTITIONING FEASIBILITY

Read-only exploration for the OOC redesign (L1 incremental emit, L2 streaming read-out,
L3 demand-driven readback + keyspace partitioning). Repo
`nebulastream-Out-of-Core-Stream-Processing` @ `feat/ooc-phase1-spill-skeleton` (1f30a75822).
No source was edited. Method: Grep/Read; file:line + quotes below; no build/run performed.

## TL;DR
- The windowed-aggregation operator handler is `AggregationOperatorHandler : WindowBasedOperatorHandler`.
  The slice store is built **in the lowering** (`LowerToPhysicalWindowedAggregation.cpp`), which
  constructs either `DefaultTimeBasedSliceStore` (in-mem) or `SpillableTimeBasedSliceStore` (OOC) and
  wires it via `createSliceStoreRef(extractor, createSlicesFn)`. The emit-lag L is **already plumbed**
  (Increment C): `conf.spill.emitLag` → store ctor; the store, not the handler, applies the lag.
- **Exact host/JIT boundary:** the build hot path `AggregationBuildPhysicalOperator::execute` is
  Nautilus-traced and calls `sliceStoreRef->getDataStructureRef(ts, workerThreadId, handler)`. On a
  **cache HIT this returns the cached `HashMap*` entirely in-trace — no host call**; only a **MISS**
  crosses into host C++ via the single `nautilus::invoke(defaultTimeBasedSliceStoreRefCacheMissProxy, …)`.
  The key→bucket routing (`hashMap.findOrCreateEntry(record, MurMur3HashFunction, …)`) is **fully
  JIT-generated**. That routing is what L3 partitioning would have to change → it would be FORBIDDEN.
- **L3 partitioning verdict:** **POST-HOC, HOST-ONLY partitioning at spill/emit time is FEASIBLE and
  in-scope.** Per-key routing on the INSERT path into a sub-map is **NOT feasible in-scope** (it lives
  in generated code). So L3 must be "build one map per window as today, then split it host-side when
  spilling / streaming it out" — it does **not** bound the *peak* per-window resident map, but it does
  let spill/emit/readback work in bounded chunks. A grace-hash style that bounds the *building* map
  would require JIT changes = OUT OF SCOPE.

---

## 1. Windowed-aggregation operator handler + slice-store construction

### Handler classes
- Base: `WindowBasedOperatorHandler`
  (`nes-physical-operators/include/WindowBasedOperatorHandler.hpp:57`; impl `…/src/WindowBasedOperatorHandler.cpp`).
  Owns `std::unique_ptr<WindowSlicesStoreInterface> sliceAndWindowStore`
  (`WindowBasedOperatorHandler.hpp:95`), two `MultiOriginWatermarkProcessor`s (build + probe,
  `:96-97`), and exposes `checkAndTriggerWindows` (virtual, `:77`), `triggerAllWindows` (`:80`),
  `garbageCollectSlicesAndWindows` (`:73`), and pure-virtual `getCreateNewSlicesFunction` (`:84`) +
  `triggerSlices` (`:90`).
- Concrete: `AggregationOperatorHandler final : WindowBasedOperatorHandler`
  (`nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp:57`; impl
  `…/src/Aggregation/AggregationOperatorHandler.cpp:44`). It does **NOT** override
  `checkAndTriggerWindows` — it only implements `getCreateNewSlicesFunction` (chooses
  `SpillableAggregationSlice` vs `AggregationSlice` by `spillEnabled`,
  `AggregationOperatorHandler.cpp:72-77`) and `triggerSlices` (builds the `EmittedAggregationWindow`
  buffer and `pipelineCtx->emitBuffer(...)`, `AggregationOperatorHandler.cpp:80-157`).

### The store is created in the lowering, NOT the handler
`nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`. The
handler is constructed AFTER the store and merely takes ownership of it
(`LowerToPhysicalWindowedAggregation.cpp:277-278`):
```cpp
auto handler = std::make_shared<AggregationOperatorHandler>(
    inputOriginIds | std::ranges::to<std::vector>(), outputOriginId,
    std::move(sliceAndWindowStore), conf.maxNumberOfBuckets, spillEnabled);
```

`SpillableTimeBasedSliceStore` construction (the exact call asked for) —
`LowerToPhysicalWindowedAggregation.cpp:234-245`, includes the emit-lag pass:
```cpp
auto backend = SpillableTimeBasedSliceStore::makeRocksDBBackend(
    conf.spill.rocksdbPath.getValue() + "/agg-" + std::to_string(handlerId.getRawValue()),
    conf.spill.compression.getValue());
sliceAndWindowStore = std::make_unique<SpillableTimeBasedSliceStore>(
    windowType->getSize().getTime(),
    windowType->getSlide().getTime(),
    conf.sliceCacheConfiguration,
    std::move(backend),
    entriesPerPage,
    pageSize,
    conf.spill.softThresholdMB.getValue() * 1024ULL * 1024ULL,
    conf.spill.hardThresholdMB.getValue() * 1024ULL * 1024ULL,
    conf.spill.emitLag.getValue());   /// Increment C: event-time emit lag L (ms). Default 0.
```
(In-mem branch: `DefaultTimeBasedSliceStore(size, slide, sliceCacheConfiguration)`,
`:254-255`.) `SpillConfig` exposes `enabled / rocksdb_path / soft_threshold_mb / hard_threshold_mb /
compression / emit_lag` (`nes-query-compiler/interface/SpillConfig.hpp:41,45,48,51,59,65`).

### createSliceStoreRef — the two host callbacks
`LowerToPhysicalWindowedAggregation.cpp:257-276`:
```cpp
auto sliceStoreRef = sliceAndWindowStore->createSliceStoreRef(
    [](Slice& slice, const WorkerThreadId workerThreadId) -> std::span<const std::byte> {
        auto& hashMapSlice = dynamic_cast<HashMapSlice&>(slice);          // Route-B seam
        auto* ptr = hashMapSlice.getHashMapPtrOrCreate(workerThreadId);   // build-path = always resident
        return {reinterpret_cast<const std::byte*>(ptr), sizeof(ChainedHashMap)};
    },
    [hashMapOptions](WindowBasedOperatorHandler& handler) { /* getCreateNewSlicesFunction */ ... });
```
This `dataStructureExtractor` is the **host** function the MISS proxy calls to obtain the
`HashMap*` it then caches (see §2).

### Watermark / trigger ownership
- `checkAndTriggerWindows` (`WindowBasedOperatorHandler.cpp:73-89`): updates
  `watermarkProcessorBuild`, gets `newGlobalWatermark`, then
  `sliceAndWindowStore->getTriggerableWindowSlices(newGlobalWatermark)` → `triggerSlices(...)`.
- The **emit-lag is applied inside the store, not the handler.**
  `SpillableTimeBasedSliceStore::getTriggerableWindowSlices` drives the EMIT cutoff with a saturating
  `globalWatermark - emitLag` but learns the cold-pick watermark from the TRUE `globalWatermark`
  (`SpillableTimeBasedSliceStore.cpp:331-353`). So the handler is unchanged for L; only the store
  subclass differs. The handler also owns `garbageCollectSlicesAndWindows` (probe-side watermark →
  `sliceAndWindowStore->garbageCollectSlicesAndWindows`, `WindowBasedOperatorHandler.cpp:59-71`).
- The **SpillBackend is owned by the store**, not the handler:
  `std::unique_ptr<SpillBackend> backend` (`SpillableTimeBasedSliceStore.hpp:219`), built via the
  store's static `makeRocksDBBackend` so RocksDB stays a private dep of nes-physical-operators (the
  lowering never links rocksdb, `SpillableTimeBasedSliceStore.hpp:106-109`).

---

## 2. Build-side JIT SliceCache — the EXACT host/JIT boundary

### The JIT-traced build hot path
`AggregationBuildPhysicalOperator::execute(ExecutionContext&, Record&)` is the traced build pipeline
(`nes-physical-operators/src/Aggregation/AggregationBuildPhysicalOperator.cpp:85-132`). It obtains
the `HashMap*` and then routes/inserts the key entirely in generated code:
```cpp
const auto timestamp = timeFunction->getTs(ctx, record);
const auto hashMapPtr = sliceStoreRef->getDataStructureRef(timestamp, ctx.workerThreadId, operatorHandler); // line 93
ChainedHashMapRef hashMap(hashMapPtr, ...fieldKeys/fieldValues...);
... record.write(fieldIdentifier, value) ...                                      // key fields, traced
const auto hashMapEntry = hashMap.findOrCreateEntry(record, *hashMapOptions.hashFunction, ... );  // line 107 — KEY→BUCKET ROUTING, JIT
... aggFunction->lift(state, ...) ...                                             // aggregate update, traced
```
**`findOrCreateEntry(record, MurMur3HashFunction, …)` (line 107) is the key→bucket routing — it is
generated code.** This is precisely what an L3 grace-hash *insert-time* router would have to modify =
FORBIDDEN.

### The seam: HIT = no host call; MISS = the one invoke
`DefaultTimeBasedSliceStoreRef::getDataStructureRef`
(`nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStoreRef.cpp:93-113`):
```cpp
nautilus::val<DefaultTimeBasedSliceStore*> sliceStoreVal{sliceStore};
return sliceCache->getDataStructureRef(timestamp, workerThreadId,
    [&](const nautilus::val<SliceCacheEntry*>& entryToReplace) {
        nautilus::invoke(                                 // <-- THE host boundary, MISS ONLY
            defaultTimeBasedSliceStoreRefCacheMissProxy,
            entryToReplace, operatorHandler, timestamp, workerThreadId,
            nautilus::val<const DefaultTimeBasedSliceStoreRef*>(this), sliceStoreVal);
    });
```
`SliceCacheSecondChance::getDataStructureRef` is fully `nautilus::val<>`-traced
(`SliceCacheSecondChance.cpp:65-113`). The **HIT path returns the cached pointer in-trace and never
calls host** (`SliceCacheSecondChance.cpp:84-88`):
```cpp
if (auto [sliceCacheEntryToReplace, foundInCache] = searchInCache(threadLocalStart, timestamp); foundInCache) {
    sliceCacheEntryToReplace.get(&SliceCacheEntrySecondChance::secondChanceBit) = nautilus::val<bool>{true};
    return nautilus::val<SliceCacheEntry*>{sliceCacheEntryToReplace}.get(&SliceCacheEntry::dataStructure);  // raw HashMap*, no host call
}
```
The MISS proxy (`DefaultTimeBasedSliceStoreRef.cpp:40-66`) is host C++: it calls
`sliceStore->getSlicesOrCreate(...)` (`:59`) and writes the cached entry via the host extractor
(`entryToReplace->dataStructure = sliceStoreRef->dataStructureExtractor(*slices[0], workerThreadId).data();`,
`:65`).

### What is cached (the seam payload) — raw pointer, no epoch
`SliceCacheEntry { Timestamp::Underlying sliceStart; Timestamp::Underlying sliceEnd; const std::byte*
dataStructure; }` (`SliceCache.hpp:33-48`) + `bool secondChanceBit`
(`SliceCacheSecondChance.hpp:28-32`); both asserted trivially destructible "because we use the structs
purely in nautilus" (`SliceCache.hpp:50-52`). The Route-B virtual seam on `HashMapSlice` —
`getHashMapPtr`, `getHashMapPtrOrCreate`, `getNumberOfTuples` made virtual
(`HashMapSlice.hpp:81,85,87`), overridden by `SpillableAggregationSlice`
(`SpillableAggregationSlice.hpp:46,48,56`) — lets the host extractor and `triggerSlices` work
polymorphically. **None of getHashMapPtr*/getNumberOfTuples is called from generated code on the HIT
path; they are reached only via the host MISS proxy and host `triggerSlices`.**

**Boundary bound (what we may NOT touch):** `SliceCacheSecondChance::getDataStructureRef` /
`searchInCache`, `AggregationBuildPhysicalOperator::execute` (incl. `findOrCreateEntry`),
`ChainedHashMapRef`, and the `SliceCacheEntry`/`…SecondChance` layout (a layout change reshapes the
generated access). The single sanctioned host crossing is `defaultTimeBasedSliceStoreRefCacheMissProxy`
+ the `dataStructureExtractor`/`createSlicesFunction` callbacks — both already host C++ and free to change.

---

## 3. Watermark / trigger / GC machinery for incremental or demand-based emit

- **Where trigger is driven:** `WindowBuildPhysicalOperator::close(ExecutionContext&, RecordBuffer&)`
  (`nes-physical-operators/src/WindowBuildPhysicalOperator.cpp:71-84`) issues
  `invoke(checkWindowsTriggerProxy, …)` → `opHandler->checkAndTriggerWindows(bufferMetaData, pipelineCtx)`
  (`:49-51`). `close` runs **per processed buffer at the build pipeline's close** — so emit is
  **incremental as watermarks advance**, NOT a single tight loop at quiescence. End-of-stream flush is
  `WindowBuildPhysicalOperator::terminate` → `triggerAllWindowsProxy` → `triggerAllWindows`
  (`:104-108`, `:54-61`) → `getAllNonTriggeredSlices()` (drains the full retained backlog incl. the
  lagged band, `SpillableTimeBasedSliceStore.cpp:357-405`).
- **Global watermark computation:** `MultiOriginWatermarkProcessor::updateWatermark` over input
  origins gives `newGlobalWatermark` per buffer (`WindowBasedOperatorHandler.cpp:76-77`); probe-side
  GC uses a separate `watermarkProcessorProbe` (`:61-70`).
- **Emit cadence vs. lag:** with `emitLag=0` emit is byte-identical to the pre-Increment-C engine
  (`SpillableTimeBasedSliceStore.cpp:337`). With `L>0`, completed windows in
  `[globalWatermark-L, globalWatermark)` are RETAINED (still cold-but-unemitted) and become spillable
  (`:333-340`). This is the existing L1-ish "decoupled emit" hook the redesign builds on.
- **Spill loop:** background `std::jthread spiller` (`SpillableTimeBasedSliceStore.hpp:277`,
  `spillerLoop` `:217`) wakes on `spillerCv` when resident memory crosses SOFT and spills oldest cold
  slices to SOFT; a synchronous worker-path HARD backstop (`evictUntilUnderHard`, `…Store.hpp:199`)
  spills on the worker thread when over HARD. All in-mem decisions under `evictionMutex`; all RocksDB
  I/O OUTSIDE it (`…Store.hpp:246-261`).
- **Backpressure observation point (the eval's NetworkSink 1000-buffer stall):** emit is a
  `pipelineCtx->emitBuffer(tupleBuffer)` in `AggregationOperatorHandler::triggerSlices`
  (`AggregationOperatorHandler.cpp:148`). Backpressure manifests as that `emitBuffer` / the buffer
  manager's `getUnpooledBuffer` (`:120-124`, throws `CannotAllocateBuffer` on exhaustion) — i.e. the
  natural place to observe sink backpressure is the trigger/emit path in the handler, downstream of
  `checkAndTriggerWindows`. There is no explicit backpressure signal fed back into the spill decision
  today; an L1/L2 incremental emit would want to throttle `triggerSlices` against `emitBuffer`
  availability.

---

## 4. L3 keyspace-partitioning feasibility

### How keys map to storage TODAY (tumbling agg)
- **One slice per `sliceEnd`** covering ALL keys of that window:
  `DefaultTimeBasedSliceStore::getSlicesOrCreate` enforces `INVARIANT(newSlices.size() == 1, …)`
  (`DefaultTimeBasedSliceStore.cpp:76-77`) and stores `slices[sliceEnd] -> oneSlice`.
- Inside a slice: **one `ChainedHashMap` per worker thread** (indexed `workerThreadId % numberOfHashMaps`,
  `SpillableAggregationSlice.cpp:54,61`; layout in `HashMapSlice.hpp:57-61`). Each per-worker map holds
  **all keys that worker saw**, routed by `MurMur3HashFunction` into `numberOfChains` buckets — this
  routing is the JIT `findOrCreateEntry` (§2).
- Spill granularity = **whole per-worker map**: `SpillableAggregationSlice::spill` serializes each
  resident map in full (`SliceSerializer::serialize(*chained, entrySize, pageSize, numberOfBuckets)`)
  keyed by `(sliceEnd, workerThreadId)` and frees it (`SpillableAggregationSlice.cpp:85-117`); unspill
  rebuilds the whole map (`:119-137`).

### Does partitioning cross into JIT insert code?
- **Insert-time routing to a sub-map (grace-hash while BUILDING): YES → FORBIDDEN.** The hot path
  computes the bucket and inserts via generated `findOrCreateEntry`
  (`AggregationBuildPhysicalOperator.cpp:107`); to route `hash(key)%N` to one of N sub-maps the JIT
  would have to (a) pick a sub-map and (b) call a different `getDataStructureRef` per partition. Both
  are in `val<>` code (the cache lookup, the hash, the map ref). That is a Nautilus/codegen edit →
  excluded by proposal §2.3.2.
- **Post-hoc partitioning at SPILL/EMIT time (split an already-built map): NO JIT crossing → HOST-ONLY,
  FEASIBLE.** The map exposes a **host-side** read API sufficient to walk and re-bucket entries:
  `getNumberOfChains()`, `getStartOfChain(idx)`, `getNumberOfPages()`/`getPage(idx)`,
  `findChain(hash)`, `getNumberOfTuples()`, and `createNewMapWithSameConfiguration(...)`
  (`ChainedHashMap.hpp:80-97`). `SliceSerializer::serialize/deserialize` already operates host-side on
  whole maps (`SliceSerializer.hpp:59,63`). So a host routine can, at spill or streaming-emit time,
  iterate the built map and emit N partition records (`backend.put((sliceEnd, partitionId), record)`),
  and read them back partition-by-partition for a bounded-memory streaming emit/probe.

### Verdict
**L3 = HOST-ONLY post-hoc partitioning is feasible and in-scope; insert-time (grace-hash while
building) partitioning is OUT OF SCOPE (JIT).** Consequences to state honestly in the thesis:
- Post-hoc partitioning bounds **spill/emit/readback working-set** (process one partition at a time),
  enabling L2 streaming read-out and L3 demand-driven readback with bounded memory.
- It does **NOT** bound the **peak resident size of a single window's build map** (depth case), because
  the full per-worker map is built in RAM before it can be split. Bounding the *building* map is the
  grace-hash insert-router that needs JIT changes → declared out of scope (consistent with
  DESIGN-OOC-EMIT-DECOUPLE.md's "Depth = OUT OF SCOPE").
- The natural unit to partition is `(sliceEnd, workerThreadId, partitionId)` — extends the existing
  spill key `(sliceEnd, workerThreadId)` (`SpillableAggregationSlice.cpp:111`) with a host-chosen
  `partitionId = hash(entry.key) % N`, computed while iterating the built map host-side. No generated
  code touched.

---

## 5. SCOPE GUARDRAILS

| Proposed change | Allowed? | Why |
|---|---|---|
| Add emit-lag L / lagged-emit watermark (L1 incremental emit) | **ALLOWED** | Already host-side in `SpillableTimeBasedSliceStore::getTriggerableWindowSlices` (`…Store.cpp:331-353`) + `SpillConfig::emitLag`. Handler/lowering host code only. |
| Throttle `triggerSlices`/`emitBuffer` against sink backpressure (incremental emit pacing) | **ALLOWED** | Lives in `AggregationOperatorHandler::triggerSlices` host C++ (`AggregationOperatorHandler.cpp:148`); no traced code. |
| L2 streaming read-out of spilled windows from RocksDB (read partition/page at a time on emit) | **ALLOWED** | Emit/trigger feeders (`getTriggerableWindowSlices`/`getAllNonTriggeredSlices`) + `unspill`/`SliceSerializer` are host-side (`…Store.cpp:357-405`, `SpillableAggregationSlice.cpp:119-137`). |
| L3 post-hoc keyspace partitioning at SPILL/EMIT time (split built map into N partition records) | **ALLOWED** | Uses host `ChainedHashMap` read API (`ChainedHashMap.hpp:80-97`) + host `SliceSerializer`; spill key already `(sliceEnd, workerThreadId)` and is host-chosen (`SpillableAggregationSlice.cpp:111`). |
| Extend spill key to `(sliceEnd, workerThreadId, partitionId)` + per-partition put/get on the backend | **ALLOWED** | `SpillBackend` put/get are host C++; no generated code references the key shape. |
| Host-side SliceCache invalidate-on-spill (zero a POD entry's start/end to force a MISS) | **ALLOWED (with caveat)** | Entry is POD the host already writes (`DefaultTimeBasedSliceStoreRef.cpp:63-65`); but lock-free per-worker cache ⇒ only race-free on the OWNING worker thread (see EXPLORE-SLICECACHE.md §4). Optional backstop, not load-bearing for the quiescent target. |
| New store/handler methods, setters, GC/threshold logic, RocksDB backend code | **ALLOWED** | All host C++ in nes-physical-operators; RocksDB stays a private dep via `makeRocksDBBackend`. |
| **Insert-time keyspace routing to a sub-map (grace-hash while BUILDING the window)** | **FORBIDDEN** | The route + insert is generated: `findOrCreateEntry(record, MurMur3HashFunction, …)` in `AggregationBuildPhysicalOperator::execute:107` + `getDataStructureRef` cache lookup — Nautilus/codegen edit (§2.3.2). |
| **Residency/epoch/generation check on the cache HIT path** (robust depth-case guard) | **FORBIDDEN** | The HIT decision is in `SliceCacheSecondChance::getDataStructureRef`/`searchInCache` = `nautilus::val<>` traced (`SliceCacheSecondChance.cpp:65-113`). |
| **Change `SliceCacheEntry` / `…SecondChance` layout** (e.g. add a residency field) | **FORBIDDEN** | The layout is consumed by generated pointer arithmetic (`SliceCacheSecondChance.cpp:54-112`); changing it reshapes codegen. |
| **Make cache entries atomic** for cross-thread invalidation | **FORBIDDEN** | Changes the generated access type on the traced HIT path. |
| **Sliding windows / variable-sized keys / general serialization** | **FORBIDDEN** | Excluded by proposal §2.3.2. (Tumbling-only is enforced: `INVARIANT(newSlices.size()==1)`, `DefaultTimeBasedSliceStore.cpp:77`; VARSIZED key handling exists in lowering `:196-199` but stays untouched.) |
| **Bounding the PEAK resident size of one window's build map (depth case)** | **FORBIDDEN (in-scope path)** | Requires the insert-time grace-hash router (JIT). Document as a limitation/future work. |

---

## 6. Test & build harness

### Macros / where new tests go
- `nes-physical-operators/tests/CMakeLists.txt:13-17` defines
  `add_nes_physical_operator_test(<name> <src>)` (wraps `add_nes_test`, links
  `nes-data-types nes-physical-operators nes-memory-test-utils nes-test-util`).
- Existing OOC tests registered there: `SliceCacheTest`, `SliceSerializerTest`,
  `NullSpillBackendTest`, `InMemorySpillBackendTest`, `RocksDBSpillBackendTest`,
  `SpillableAggregationSliceTest`, **`SpillableTimeBasedSliceStoreTest`** (line 28),
  `SpillableTimeBasedSliceStoreConcurrencyTest` (line 29).
- **`SpillableTimeBasedSliceStoreTest`** (`nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`):
  `class SpillableTimeBasedSliceStoreTest : public BaseUnitTest` (line 93); `TEST_F` cases use the
  deterministic drive hooks `forceSpill`, `setLastSeenWatermarkForTest`, `spillColdSlicesUnderHardForTest`,
  `spillCountForTest`, `createdSlicesSizeForTest` (e.g. lines 244, 399-502). **New L1/L2/L3 store-level
  unit tests belong here (or a new `add_nes_physical_operator_test(...)` sibling).** For an emit-lag /
  partition routing check at the lowering, mirror
  `nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp`.
  End-to-end spill behavior is exercised by the systest
  `nes-systests/operator/aggregation/SpillSmoke.test`.

### Docker build / test commands (DO NOT build — reported only)
Image `nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug`, repo mounted at
`/workspace` (or `$(pwd)`). From `EXPLORE-INFRA.md:38-63`, `E0-SMOKE-RESULTS.md:70-89`:
```bash
IMG=nebulastream/nes-development:local-rocksdb
# Build (ninja under the hood via cmake --build):
docker run --workdir $(pwd) -v $(pwd):$(pwd) $IMG cmake --build cmake-build-debug -j --target systest
# (per-test target: --target SpillableTimeBasedSliceStoreTest)
# Unit/systest gate (exclude Systest + DockerCompose label suites as the mission specifies):
docker run --workdir $(pwd) -v $(pwd):$(pwd) $IMG \
  ctest --test-dir cmake-build-debug -LE 'Systest|DockerCompose' -j
# A single systest (memory-ceiling repro for the §3.3 demo):
docker run --rm --memory=512m --memory-swap=512m --cpus=1 --workdir /workspace -v "$PWD:/workspace" $IMG \
  cmake-build-debug/nes-systests/systest/systest -t nes-systests/operator/aggregation/SpillSmoke.test \
    --workingDir /workspace/cmake-build-debug/systest-run --show-query-performance
```
ctest label for systests = `"Systest"` (`nes-systests/systest/CMakeLists.txt`); the harness does NOT
impose a memory cgroup itself — the `--memory/--memory-swap/--cpus` flags are added to `docker run`
(`EXPLORE-INFRA.md:156-163,287-327`). Each PR per the existing process: Docker build + ctest gate +
cpp-review, regression ≥ 820 (DESIGN-OOC-EMIT-DECOUPLE.md "Resume entry point").
