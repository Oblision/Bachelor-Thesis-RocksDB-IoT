# PLAN-A.md — Phase 4b Increment A (single-threaded spill spike) — Architect A

Independent plan. Verified line-by-line against live code on branch `feat/ooc-phase1-spill-skeleton`
(every `file:line` below was opened and confirmed, not copied from SYNTHESIS). Honors LOCKED D1–D6 and
resolved O1/O2/O3; O4 deferred. Repo root paths are relative to the module root noted per section.

---

## 1. Summary

Increment A wires the already-built isolated spill components into the live keyed-aggregation slice store
on a **single worker thread**, proving the lifetime model end-to-end without the concurrency hardening of
Increment B. After this increment: `HashMapSlice` exposes three virtual accessors (D1 seam) so every
consumer dispatches polymorphically through `HashMapSlice&`; a new `SpillableTimeBasedSliceStore` subclasses
the now-un-`final` `DefaultTimeBasedSliceStore`, owns a `SpillBackend`, unspills any non-resident slice
inside both trigger feeders before returning it (D3), and computes `residentBytes` from the (now-virtual)
polymorphic tuple count (D4). Spill is driven by a **forced/manual** test-callable API (`spillSlice(SliceEnd)`)
restricted to provably-cold slices — the automatic windows-map cold scan and live eviction loop are deferred
to Increment B. The lowering rule replaces the Phase-1 `NotImplemented` guard, constructs the spillable store
+ a `RocksDBSpillBackend` (built inside nes-physical-operators) when `conf.spill.enabled`, and routes the
slice factory to `SpillableAggregationSlice`. The proof gate is a gate-included C++ integration test
(`SpillableTimeBasedSliceStoreTest`) driving the store directly (Route 3b); a manual Route-3a systest is optional.

---

## 2. Sections (one file/header+cpp pair each)

### S1 — D1 seam: `HashMapSlice` base accessors become virtual (the seam)
**Files:** `nes-physical-operators/include/HashMapSlice.hpp` (EDIT), `nes-physical-operators/src/HashMapSlice.cpp` (EDIT)

`.hpp` — verified current state: `getNumberOfTuples()` declared non-virtual at line 80; class declares
NEITHER accessor; `hashMaps` is `protected` (line 83); `createNewHashMapSliceArgs` protected (line 84).
Includes already present: `<Identifiers/Identifiers.hpp>` (23), `<Nautilus/.../HashMap.hpp>` (24).
Changes — in the `public:` block, replace line 80 and add two new virtuals immediately after:
```cpp
[[nodiscard]] virtual uint64_t getNumberOfTuples() const;                 // was non-virtual (line 80)
[[nodiscard]] virtual HashMap* getHashMapPtr(WorkerThreadId workerThreadId) const;
[[nodiscard]] virtual HashMap* getHashMapPtrOrCreate(WorkerThreadId workerThreadId);
```
No new includes. (`getNumberOfHashMaps()` at line 78 stays non-virtual — no polymorphic consumer.)

`.cpp` — verified: includes `<Nautilus/.../ChainedHashMap.hpp>` (25), `<ErrorHandling.hpp>` (28),
`<memory>` (19), `<numeric>` (21) already present; `getNumberOfTuples()` body at 73-82 already null-safe
(stays as-is, now the virtual base impl). Add the two accessor base bodies (verbatim relocation from
`AggregationSlice.cpp:37-58`):
```cpp
HashMap* HashMapSlice::getHashMapPtr(const WorkerThreadId workerThreadId) const
{
    const auto pos = workerThreadId % hashMaps.size();
    INVARIANT(pos < hashMaps.size(), "The worker thread id should be smaller than the number of hashmaps");
    return hashMaps[pos].get();
}
HashMap* HashMapSlice::getHashMapPtrOrCreate(const WorkerThreadId workerThreadId)
{
    const auto pos = workerThreadId % hashMaps.size();
    INVARIANT(pos < hashMaps.size(), "The worker thread id should be smaller than the number of hashmaps");
    if (hashMaps.at(pos) == nullptr)
    {
        hashMaps.at(pos) = std::make_unique<ChainedHashMap>(
            createNewHashMapSliceArgs.keySize, createNewHashMapSliceArgs.valueSize,
            createNewHashMapSliceArgs.numberOfBuckets, createNewHashMapSliceArgs.pageSize);
    }
    return hashMaps[pos].get();
}
```
**NES_* logs:** none (these are per-record-adjacent hot accessors; adding logs would be noise — match the
existing zero-log style of `AggregationSlice.cpp`).
**Tests:** `SpillableTimeBasedSliceStoreTest.SeamDispatch*` (S8) covers virtual dispatch via `HashMapSlice&`,
incl. `getNumberOfTuples`. Existing `SpillableAggregationSliceTest` (unchanged, still green) re-covers concrete dispatch.

### S2 — D1 seam: `AggregationSlice` drops both accessor overrides
**Files:** `nes-physical-operators/include/Aggregation/AggregationSlice.hpp` (EDIT),
`nes-physical-operators/src/Aggregation/AggregationSlice.cpp` (EDIT)

`.hpp` — delete the two declarations at lines 37-38. Class keeps only its ctor (line 32-33); stays `final` (29).
`.cpp` — delete the two definitions at lines 37-58. Only the ctor (28-35) remains. The `<memory>`/
`ChainedHashMap.hpp` includes may stay (harmless) — leave them to minimize diff churn. AggregationSlice now
inherits the identical base impl → zero behavior change.
**NES_* logs:** none. **Tests:** covered transitively by every existing aggregation systest (regression gate)
+ S8 base-dispatch case.

### S3 — D1 seam: `SpillableAggregationSlice` marks all three `override`
**Files:** `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp` (EDIT),
`nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp` (NO edit — bodies unchanged)

`.hpp` — append `override`:
- line 46 `getHashMapPtr(...) const override;`
- line 48 `getHashMapPtrOrCreate(...) override;`
- line 56 `getNumberOfTuples() const override;`
Class stays `final` (39). `.cpp` bodies (51-83) UNCHANGED — keep `INVARIANT(resident,...)` (53, 60) and the
spilled-snapshot branch in `getNumberOfTuples` (79-83). **Do NOT** add the missing `INVARIANT(pos<size)` to the
spillable `getHashMapPtr` (pre-existing harmless micro-divergence; adding it changes behavior).
**NES_* logs:** none. **Tests:** S8 `SeamDispatchGetNumberOfTuplesSpilled` asserts a spilled slice read through
`HashMapSlice&` returns the snapshot (not 0) — the load-bearing R6 proof.

### S4 — D2: un-`final` the base store
**File:** `nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp` (EDIT)

Verified: line 55 `class DefaultTimeBasedSliceStore final : public WindowSlicesStoreInterface`. The five
read/create/GC methods at 61-67 are already `override` (transitively virtual from the pure-virtual interface,
confirmed `WindowSlicesStoreInterface.hpp:68-100`). **Change = delete the single word `final` at line 55.**
No per-method `virtual` keyword. Do NOT touch `createSliceStoreRef` (76) / `allocateSpaceForSliceCache` (71) —
non-virtual, inherited as-is.
**Grep-confirmed safe:** no `dynamic_cast`/`static_cast` to/from `DefaultTimeBasedSliceStore`, no
`static_assert(is_final)`. Only construction sites are the three lowering rules (agg + the two joins, joins
out of scope).
**NES_* logs:** none. **Tests:** compile-time enabled by S5; behavior unchanged for the in-memory path
(regression gate).

### S5 — D2/D3/D4: the new `SpillableTimeBasedSliceStore`
**Files:** `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` (NEW),
`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` (NEW)

**Header** — `class SpillableTimeBasedSliceStore final : public DefaultTimeBasedSliceStore`. Includes:
`<SliceStore/DefaultTimeBasedSliceStore.hpp>`, `<SliceStore/SpillBackend.hpp>`,
`<Runtime/AbstractBufferProvider.hpp>`, `<memory>`, `<cstdint>`. Members:
```cpp
std::unique_ptr<SpillBackend> backend;                        // O2: store owns it
std::shared_ptr<AbstractBufferProvider> bufferProvider;       // O1: stashed once, query-lifetime
uint64_t entriesPerPage;  uint64_t pageSize;                  // D4 page-math inputs (no recompute)
uint64_t softThresholdBytes;  uint64_t hardThresholdBytes;    // D4 thresholds (MB→bytes at ctor)
```
Ctor:
```cpp
SpillableTimeBasedSliceStore(uint64_t windowSize, uint64_t windowSlide,
    SliceCacheConfiguration sliceCacheConfiguration, std::unique_ptr<SpillBackend> backend,
    uint64_t entriesPerPage, uint64_t pageSize, uint64_t softThresholdMB, uint64_t hardThresholdMB);
```
Declarations (all `override` of the base virtuals + new public/forced API):
```cpp
// D3 unspill-on-read — three feeders:
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
    getTriggerableWindowSlices(Timestamp globalWatermark) override;
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
    getAllNonTriggeredSlices() override;
std::optional<std::shared_ptr<Slice>> getSliceBySliceEnd(SliceEnd sliceEnd) override;
// D5/D6 GC: erase backend records after base GC drops the shared_ptr:
void garbageCollectSlicesAndWindows(Timestamp newGlobalWaterMark) override;
// O1 stash (called from setupSliceStoreProxy, S7):
void setBufferProvider(std::shared_ptr<AbstractBufferProvider> provider);
// Forced/manual spill (Increment A driver) + D4 budget + observability:
void spillSlice(SliceEnd sliceEnd);                          // throws if slice unknown/not spillable
[[nodiscard]] bool isSliceResident(SliceEnd sliceEnd) const;
[[nodiscard]] uint64_t residentBytes(const HashMapSlice& slice) const;   // D4 per-slice
[[nodiscard]] uint64_t residentBytes() const;                            // D4 store-wide (Σ resident slices)
```
NOT overridden: `getSlicesOrCreate` (build hot path only touches resident WINDOW_FILLING slices — CORE.md §4
confirmed; inherit unchanged), `deleteState`, `incrementNumberOfInputPipelines`, `getWindowSize`,
`createSliceStoreRef`, `allocateSpaceForSliceCache`.

**Source** — key bodies:
- `getTriggerableWindowSlices` / `getAllNonTriggeredSlices`: call the base
  (`DefaultTimeBasedSliceStore::getTriggerableWindowSlices(wm)` / `...::getAllNonTriggeredSlices()`), then for
  every returned `shared_ptr<Slice>`, `dynamic_pointer_cast<SpillableAggregationSlice>` and `unspill(*backend,
  *bufferProvider)` if `!isResident()`. `unspill` is idempotent (4a, `.cpp:121`), so this is safe under the base's
  `tryWLock` retry semantics (R7). PRECONDITION `bufferProvider != nullptr` before any unspill.
- `getSliceBySliceEnd`: call base; if it returns a non-resident spillable slice, unspill before returning
  (consistency, D3 — out-of-4b-scope consumer but cheap and correct).
- `garbageCollectSlicesAndWindows`: **snapshot-diff** (R5, lowest-risk). Snapshot the set of `SliceEnd` keys
  reachable via `getSliceBySliceEnd` is not available; instead snapshot keys by calling a tiny private helper
  that read-locks the inherited `slices` map — BUT `slices` is `private` on the base. **Decision:** Increment A's
  forced-spill spike does not need GC backend-erase to be exercised by the gate test (GC requires a full
  `windowSize` of extra watermark slack, hard to drive deterministically in a unit test). Implement GC override as:
  call base GC, then `backend->erase(sliceEnd)` for sliceEnds we *tracked as spilled* in a private
  `folly::Synchronized<std::set<SliceEnd>> spilledKeys` set that `spillSlice` inserts into and that we prune
  against "still present in store". Concretely: after base GC, for each key in `spilledKeys`, if the slice is no
  longer retrievable (`!getSliceBySliceEnd(key).has_value()`), `backend->erase(key)` and drop it from
  `spilledKeys`. This avoids needing access to the base `slices` map and avoids erasing while still unspillable.
  *(Flag — see §8 R5/Q3: a cleaner long-term fix is to expose deleted keys from the base GC; deferred to B.)*
- `setBufferProvider`: PRECONDITION `provider != nullptr`; INVARIANT set-once (`bufferProvider == nullptr` on
  entry) mirroring the base `allocateSpaceForSliceCache` once-per-pipeline guard (`DefaultTimeBasedSliceStore.cpp:294`).
- `spillSlice(SliceEnd)`: `auto opt = getSliceBySliceEnd(sliceEnd)` (base, resident or not); if `!opt` throw
  `SpillStoreFailure("spillSlice: no slice for sliceEnd {}", sliceEnd)`. `dynamic_pointer_cast<SpillableAggregationSlice>`;
  if null throw `SpillStoreFailure`. Call `slice->spill(*backend)` (idempotent, 4a `.cpp:85`). Insert into
  `spilledKeys`. **Increment A forced-spill is test-driven and the test only ever passes a COLD sliceEnd** (the
  store does NOT auto-detect coldness here — that scan is Increment B). Document this precondition on the method.
- `residentBytes(const HashMapSlice&)`: `tuples = slice.getNumberOfTuples()` (polymorphic — S1/S3 make this
  spill-aware: returns snapshot while spilled, so a spilled slice still reports its pre-spill page footprint **of
  the on-disk copy** — but for the *resident memory* budget we want 0 for a spilled slice). **Decision:** budget
  semantics = resident memory. `residentBytes(slice)` returns 0 when the slice is non-resident (cast to
  `SpillableAggregationSlice` and check `isResident()`); when resident, `⌈tuples/entriesPerPage⌉ × pageSize`
  summed over... — note `getNumberOfTuples()` already sums all worker maps, but page-rounding is per-worker.
  Use `Σ_{w} ⌈map_w.tuples/entriesPerPage⌉ × pageSize`. Since per-worker counts aren't exposed polymorphically,
  compute via `getNumberOfHashMaps()` + `getHashMapPtr(WorkerThreadId(w))->getNumberOfTuples()` for each resident
  worker map (matches exactly what `spill()` reclaims). For the single-worker spike `getNumberOfHashMaps()` ==
  numberOfWorkerThreads. (The polymorphic `getNumberOfTuples()` virtual remains load-bearing for the *snapshot*
  path and for any future store-wide reads through `HashMapSlice&`.)
- `residentBytes()` store-wide: not needed for the gate test if `residentBytes(slice)` is verified per-slice;
  provide it as `Σ` over resident slices for the soft-threshold check stub. For Increment A it is observability
  only (no automatic eviction); implement and unit-test the per-slice version, and a 2-slice store-wide sum.

**NES_* logs:**
- `spillSlice`: `NES_DEBUG("Spilling slice sliceEnd={} ({} bytes resident)", sliceEnd, residentBytes(...))`
  before spill; `NES_TRACE("Slice {} spilled to backend", sliceEnd)` after.
- unspill-on-read (both feeders): `NES_DEBUG("Unspilling slice sliceEnd={} on trigger read", sliceEnd)` per slice
  unspilled.
- GC override: `NES_TRACE("Erasing backend record for GC'd slice sliceEnd={}", sliceEnd)` per erase.
- `setBufferProvider`: `NES_DEBUG("SpillableTimeBasedSliceStore buffer provider stashed")`.

**Tests:** `SpillableTimeBasedSliceStoreTest` (S8) — all of D2/D3/D4/forced-spill/idempotency.

### S6 — CMake: register the new store source
**File:** `nes-physical-operators/src/SliceStore/CMakeLists.txt` (EDIT)

Verified block at lines 13-19. Add one line after `RocksDBSpillBackend.cpp` (line 18):
```cmake
        SpillableTimeBasedSliceStore.cpp
```
No header registration (auto-published via `target_include_directories`). No `nes-physical-operators/CMakeLists.txt`
edit (RocksDB `find_package` + `RocksDB::rocksdb` PRIVATE already present from Phase 3).
**Tests:** build verification only.

### S7 — O1 stash + O2/O3 wire-in: lowering rule
**File:** `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` (EDIT)

Verified anchors: Phase-1 guard at 163-166; `handlerId` at 183; `entrySize` 212, `numberOfBuckets` 213,
`pageSize` 214, `entriesPerPage` 215; store construction 234-235; extractor `dynamic_cast<AggregationSlice&>`
at 239; handler ctor at 254-255. Includes already present: `<Aggregation/AggregationSlice.hpp>` (29),
`<SliceStore/DefaultTimeBasedSliceStore.hpp>` (46), `<HashMapSlice.hpp>` (58), `<QueryExecutionConfiguration.hpp>`
(61). ADD includes: `<SliceStore/SpillableTimeBasedSliceStore.hpp>`, `<SliceStore/RocksDBSpillBackend.hpp>`,
`<Aggregation/SpillableAggregationSlice.hpp>`.

Changes:
1. **Delete the guard (163-166).** Replace with nothing (lowering proceeds for both paths).
2. **Branch the store construction (234-235).** Introduce `const bool spillEnabled = conf.spill.enabled.getValue();`.
   ```cpp
   std::unique_ptr<DefaultTimeBasedSliceStore> sliceAndWindowStore;
   if (spillEnabled) {
       auto backendPath = std::filesystem::path(conf.spill.rocksdbPath.getValue()) / fmt::format("op-{}", handlerId);
       auto backend = std::make_unique<RocksDBSpillBackend>(backendPath.string(), conf.spill.compression.getValue());
       sliceAndWindowStore = std::make_unique<SpillableTimeBasedSliceStore>(
           windowType->getSize().getTime(), windowType->getSlide().getTime(), conf.sliceCacheConfiguration,
           std::move(backend), entriesPerPage, pageSize,
           conf.spill.softThresholdMB.getValue(), conf.spill.hardThresholdMB.getValue());
   } else {
       sliceAndWindowStore = std::make_unique<DefaultTimeBasedSliceStore>(
           windowType->getSize().getTime(), windowType->getSlide().getTime(), conf.sliceCacheConfiguration);
   }
   ```
   `RocksDBSpillBackend` is constructed here BUT its ctor lives in nes-physical-operators (in-target rocksdb).
   **WAIT — see §3:** to avoid adding `RocksDB::rocksdb` to nes-query-compiler, the backend must be built
   INSIDE nes-physical-operators. **Final decision (§3): the lowering rule does NOT `new RocksDBSpillBackend`
   directly.** Instead it calls a static factory `SpillableTimeBasedSliceStore::create(...)` declared in the new
   header / defined in the new `.cpp` (in-target), which takes the `SpillConfig` scalar values and constructs the
   `RocksDBSpillBackend` internally. The lowering rule includes only `<SliceStore/SpillableTimeBasedSliceStore.hpp>`
   (no `<RocksDBSpillBackend.hpp>`, no rocksdb linkage). See §3 for the exact factory signature.
3. **Extractor cast (239):** `dynamic_cast<AggregationSlice&>(slice)` → `dynamic_cast<HashMapSlice&>(slice)`
   → `.getHashMapPtrOrCreate(workerThreadId)` (now the virtual seam, dispatches to either concrete slice).
4. **Handler ctor (254-255):** pass `spillEnabled` as a new last arg (S9 adds the param).

**NES_* logs:** `NES_INFO("Lowering windowed aggregation handlerId={} with out-of-core spill enabled (rocksdb at
{})", handlerId, backendPath)` in the spill branch.
**Tests:** S10 routing test (rewritten) asserts spillable lowering succeeds.

### S8 — Tests: store unit + integration (Route 3b, the gate)
**Files:** `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (NEW),
`nes-physical-operators/tests/CMakeLists.txt` (EDIT)

CMake — add after line 27 (`SpillableAggregationSliceTest`):
```cmake
add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)
```
Unlabeled → runs in `ctest -LE 'Systest|DockerCompose'`. Test contents enumerated in §5. Fixture cloned from
`SpillableAggregationSliceTest.cpp:45-58` (`SetUpTestSuite` logging + `SetUp` `BufferManager::create()`).
Reuse `makeArgs()`/constants/`populate()`/`sortedEntries()` (`SpillableAggregationSliceTest.cpp:61-114`) and
`InMemorySpillBackend` helpers `contains()`/`sliceCount()`.
**NES_* logs:** test-local logging only.
**Tests:** this IS the test file.

### S9 — O3: handler factory branch + D1 trigger cast
**Files:** `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` (EDIT),
`nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp` (EDIT)

`.hpp` — verified ctor at 60-64, class `final` (57). Add a `bool spillEnabled` param to the ctor (after
`maxNumberOfBuckets`) and a `private`/member `const bool spillEnabled;`. Class stays `final`.
`.cpp` — verified ctor 42-52, factory lambda at 61-67 (hardcodes `make_shared<AggregationSlice>` at 66),
`triggerSlices` cast `dynamic_pointer_cast<AggregationSlice>` at 82. Changes:
1. Ctor: init `spillEnabled(spillEnabled)`.
2. `getCreateNewSlicesFunction` (54-68): capture `spillEnabled` by value into the lambda; branch at 66:
   ```cpp
   if (spillEnabled)
       return {std::make_shared<SpillableAggregationSlice>(sliceStart, sliceEnd, copyOfNewHashMapArgs, numberOfWorkerThreads)};
   return {std::make_shared<AggregationSlice>(sliceStart, sliceEnd, copyOfNewHashMapArgs, numberOfWorkerThreads)};
   ```
   ADD include `<Aggregation/SpillableAggregationSlice.hpp>`.
3. `triggerSlices` (82): `dynamic_pointer_cast<AggregationSlice>(slice)` → `dynamic_pointer_cast<HashMapSlice>(slice)`.
   `getNumberOfHashMaps()` (83) and `getHashMapPtr()` (85) are now called through `HashMapSlice` (both virtual/
   inherited) — NO other change; NO spill logic in the handler (D3 keeps it in the store). ADD include
   `<HashMapSlice.hpp>` (already present at line referencing it? — verified `.hpp` includes it at 30; the `.cpp`
   includes `<Aggregation/AggregationSlice.hpp>` at 27 which pulls `HashMapSlice.hpp` — but add it explicitly to
   `.cpp` for IWYU since the concrete `AggregationSlice` include may later be trimmed).

**NES_* logs:** keep existing `NES_TRACE` at factory (65) and trigger (137); no new logs.
**Tests:** factory branch covered indirectly by S10 (lowering produces spillable handler) and directly only via
the store-level test (S8 constructs `SpillableAggregationSlice` itself). The handler factory branch has no C++
unit harness today (CONTRACTS confirms `AggregationOperatorHandler` is used in ZERO C++ tests) → its proof is
the Route-3a manual systest (S11) + the S10 lowering routing test reaching construction.

### S10 — Rewrite the Phase-1 routing test
**File:** `nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp` (EDIT)

Verified: two throwing cases `spillEnabledThrowsNotImplemented` (56-62) and
`spillEnabledThrowsRegardlessOfOperator` (66-72) assert the now-deleted guard; `spillDisabledByDefault`
(79-83) stays valid. Helper `configWithSpillEnabled()` (33-38) reusable.
Changes:
- **Delete** both `spillEnabledThrows*` cases (the guard they assert is gone).
- **Add** `spillEnabledLowersToSpillableStore`: build a real `WindowedAggregationLogicalOperator` (the existing
  in-memory lowering path is otherwise only exercised by systests; constructing a full logical agg operator in a
  unit test is heavy). **Decision:** keep this test light — assert the *guard is gone* by confirming
  `configWithSpillEnabled()` no longer makes `apply()` throw `NotImplemented` for a non-agg operator the way it
  used to: with the guard removed, `apply(selectionOperator)` now trips the operator-shape PRECONDITION
  (`std::terminate`, uncatchable) instead of throwing — so we CANNOT call `apply()` on a dummy. Therefore replace
  the two deleted cases with a single positive assertion that the spill config flows through:
  `spillEnabledFlagReadable` asserting `configWithSpillEnabled().spill.enabled.getValue() == true`. The actual
  spillable-lowering success is proven end-to-end by the Route-3a systest (S11) and the store gate test (S8).
- Keep `spillDisabledByDefault` unchanged.
*(Flag §8: this weakens the routing test to a config-flow assertion because `apply()` cannot be safely called on a
dummy operator post-guard-removal — the PRECONDITIONs terminate. The real lowering proof moves to S11. This is an
honest consequence of the guard living before the shape PRECONDITIONs.)*
**NES_* logs:** n/a. **Tests:** this IS the test (runs via `add_nes_compiler_test`, in-gate).

### S11 — (OPTIONAL, out-of-gate) Route-3a end-to-end systest
**File:** `nes-systests/operator/aggregation/WindowAggregationSpill.test` (NEW, optional)

A `[Aggregation]`-group `.test` with a SLIDING window (D5: tumbling has ~empty cold population), sized so the
live lowering→handler→store path runs with `spill.enabled=true` + a tiny `soft_threshold_mb`. Systest-labeled →
EXCLUDED from the regression gate; run manually. Asserting "spilling occurred" is hard via expected-output only;
treat as a realism smoke, not the TDD gate. Requires a dedicated ctest entry passing the spill worker flags
(Systest-labeled). **Recommend deferring S11 to after S1-S10 are green** unless a live-query proof is explicitly wanted.

---

## 3. Backend ownership / construction (O2/O3 — CONFIRMED)

- **Owner:** the `SpillableTimeBasedSliceStore` owns `std::unique_ptr<SpillBackend> backend` (one backend per
  store = per query). Matches D2 "owns the SpillBackend"; the store's trigger/GC/forced-spill methods are the only
  spill/unspill/erase sites and the store is the one query-lifetime object spanning build+probe+GC.
- **Built INSIDE nes-physical-operators (not the lowering rule).** `RocksDBSpillBackend` links `RocksDB::rocksdb`
  **PRIVATE** on nes-physical-operators (`nes-physical-operators/CMakeLists.txt`, verified BUILD.md §2) — it is
  NOT transitively visible to nes-query-compiler. If the lowering rule did `new RocksDBSpillBackend(...)` it would
  force adding rocksdb linkage to nes-query-compiler. **Avoid.** Therefore add a static factory in the new store
  (defined in `SpillableTimeBasedSliceStore.cpp`, in-target):
  ```cpp
  // SpillableTimeBasedSliceStore.hpp (declaration)
  static std::unique_ptr<SpillableTimeBasedSliceStore> create(
      uint64_t windowSize, uint64_t windowSlide, SliceCacheConfiguration sliceCacheConfiguration,
      const std::string& rocksdbPath, const std::string& compression,
      uint64_t entriesPerPage, uint64_t pageSize, uint64_t softThresholdMB, uint64_t hardThresholdMB);
  ```
  The `.cpp` body does `auto backend = std::make_unique<RocksDBSpillBackend>(rocksdbPath, compression);` (the only
  TU that includes `<SliceStore/RocksDBSpillBackend.hpp>`), then constructs the store. The lowering rule (S7) calls
  `SpillableTimeBasedSliceStore::create(...)` and includes ONLY `<SliceStore/SpillableTimeBasedSliceStore.hpp>` →
  **zero rocksdb linkage change to nes-query-compiler** (BUILD.md §2 confirmed nes-query-compiler already links
  nes-physical-operators PUBLIC). The ctor that takes `unique_ptr<SpillBackend>` (S5) stays public so the unit
  test can inject an `InMemorySpillBackend` without touching disk.
- **rocksdbPath for the spike:** `conf.spill.rocksdbPath` default `/tmp/nes-spill` (verified `SpillConfig.hpp:45`).
  Derive a per-operator subdir `<rocksdbPath>/op-<handlerId>` (handlerId available at lowering `:183`) to avoid
  collisions when a query has >1 windowed aggregation (R8). The unit/integration gate test uses an
  `InMemorySpillBackend` (no disk) for determinism; a separate test case uses a `RocksDBSpillBackend` on a
  GTest-`TempDir` to exercise the durable path.
- **Errors:** `RocksDBSpillBackend` throws `SpillStoreFailure` (3014, factory fn — `ExceptionDefinitions.inc:89`).
  Propagates through the JIT proxy `checkWindowsTriggerProxy` (`WindowBuildPhysicalOperator.cpp:37`, no try/catch).
- **Backend lifecycle on teardown (Q4):** for the spike, `RocksDBSpillBackend`'s dtor closes the DB; the on-disk
  `op-<handlerId>` subdir is NOT auto-deleted by the spike (RocksDB owns it). To prevent `/tmp/nes-spill` growth
  across spike runs, the gate test uses an `InMemorySpillBackend` (no disk) and the temp-dir RocksDB case uses
  GTest's auto-cleaned `TempDir`. **Production cleanup of the subdir is Increment B / O4-adjacent — flag, deferred.**

---

## 4. The forced-spill store API

```cpp
void  spillSlice(SliceEnd sliceEnd);              // serialize+free the slice's resident maps via backend.
                                                  //   PRECONDITION (caller-enforced for Increment A): sliceEnd is COLD.
                                                  //   throws SpillStoreFailure if no slice / not a SpillableAggregationSlice.
                                                  //   idempotent (4a spill() no-ops if already spilled).
bool  isSliceResident(SliceEnd sliceEnd) const;   // false if spilled; throws/returns false if unknown (return false).
uint64_t residentBytes(const HashMapSlice& slice) const;  // 0 if non-resident; else Σ_w ⌈tuples_w/entriesPerPage⌉×pageSize.
uint64_t residentBytes() const;                   // Σ over all currently-resident slices (observability/soft-threshold stub).
void  setBufferProvider(std::shared_ptr<AbstractBufferProvider> provider);  // O1 stash (set-once).
```

**How a test triggers a spill of a COLD slice then verifies unspill-on-trigger** (the gate proof, single-threaded):
1. Construct `SpillableTimeBasedSliceStore` with `InMemorySpillBackend` (inject via the `unique_ptr<SpillBackend>`
   ctor), `windowSize/windowSlide` for a SLIDING window, `entriesPerPage/pageSize` matching `makeArgs()`.
2. `setBufferProvider(bufferManager)` (the test's `BufferManager::create()`).
3. Feed records via `getSlicesOrCreate(ts, createNewSlice)` where `createNewSlice` returns exactly one
   `SpillableAggregationSlice` (store INVARIANTs size==1, `DefaultTimeBasedSliceStore.cpp:77`). Populate the
   slice's worker map(s) with known (key,value) pairs (reuse `populate()`); record the expected aggregate +
   `residentBytes(slice)`.
4. Advance such that a slice S is COLD: `S.sliceEnd < watermark` AND none of S's windows is `EMITTED_TO_PROBE`
   (single-threaded; for a SLIDING window an earlier slice is cold while a later window still references it). The
   test selects S explicitly (forced-spill spike — store does NOT auto-detect).
5. `store.spillSlice(S.sliceEnd)` → assert `!store.isSliceResident(S.sliceEnd)`, `backend.contains(S.sliceEnd, w)`
   for each populated worker, `residentBytes(S)==0` after spill, and `getNumberOfTuples()` (through `HashMapSlice&`)
   still returns the pre-spill snapshot (R6).
6. `store.getTriggerableWindowSlices(highWatermark)` → assert the returned slice for S is **resident again**
   (D3 unspill-on-read) and its map entries equal the pre-spill fill (`sortedEntries()` order-independent compare),
   so a subsequent `triggerSlices` (handler) would snapshot live pointers — proving no UAF.

---

## 5. Test strategy (mapped to macro + CMake + ctest regex)

All new C++ tests register via `add_nes_physical_operator_test(<Name> <File>.cpp)` in
`nes-physical-operators/tests/CMakeLists.txt` (unlabeled → in `ctest -LE 'Systest|DockerCompose'`). The routing
test registers via `add_nes_compiler_test` (existing) in nes-query-compiler.

**Unit/integration cases in `SpillableTimeBasedSliceStoreTest.cpp` (S8):**
| Case | Asserts | Covers |
|---|---|---|
| `SeamDispatchBaseAccessors` | `getHashMapPtr/OrCreate` via `HashMapSlice&` on an `AggregationSlice` returns the worker map | S1/S2 |
| `SeamDispatchSpillableAccessors` | same via `HashMapSlice&` on a resident `SpillableAggregationSlice` | S1/S3 |
| `SeamDispatchGetNumberOfTuplesResident` | `HashMapSlice&::getNumberOfTuples()` == sum of fills | S1 |
| `SeamDispatchGetNumberOfTuplesSpilled` | after spill, `HashMapSlice&::getNumberOfTuples()` == snapshot (NOT 0) | S1/S3 (R6) |
| `ForcedSpillOfColdSlice` | `spillSlice` → `!isSliceResident`, `backend.contains`, `residentBytes(slice)==0` | S5 forced-spill, D4 |
| `UnspillOnTriggerableRead` | `getTriggerableWindowSlices` returns S resident; entries == pre-spill fill | S5 D3 (R1) |
| `UnspillOnNonTriggeredRead` | spill S, `getAllNonTriggeredSlices` (termination flush) returns S resident, no `INVARIANT(resident)` abort | S5 D3 (R3) |
| `UnspillOnGetBySliceEnd` | spill S, `getSliceBySliceEnd(S)` returns resident | S5 D3 |
| `ResidentBytesExactness` | resident `residentBytes(slice)` == `Σ_w ⌈tuples_w/entriesPerPage⌉×pageSize` for a known fill | D4 |
| `DoubleSpillIdempotent` | `spillSlice(S)` twice → one logical spill, backend unchanged, no throw | D6.4 |
| `DoubleUnspillIdempotent` | trigger-read twice (or unspill twice) → no double-rebuild, entries correct | D6.4 (R7) |
| `EmittedSliceNotSpillable` | drive a window to `EMITTED_TO_PROBE` (via `getTriggerableWindowSlices`), then assert spilling that slice is rejected/never issued — for forced API, assert the test-harness invariant + that calling `spillSlice` on an emitted slice is a documented misuse (assert via `isSliceResident` stays true after a guarded no-op, OR omit the call). Encodes R1/D5/D6.2 | D5/D6 (R1) |
| `SpillSliceUnknownThrows` | `spillSlice(bogusEnd)` → `ASSERT_EXCEPTION_ERRORCODE(_, ErrorCode::SpillStoreFailure)` | S5 (R10) |
| `RocksDBBackendTempDirRoundTrip` | repeat `ForcedSpill`+`UnspillOnTriggerableRead` with a `RocksDBSpillBackend` on a GTest TempDir | O2 durable path |

**In-gate integration proof** = the `UnspillOnTriggerableRead` + `ForcedSpillOfColdSlice` pair driving the store
directly (Route 3b, per BUILD.md — true end-to-end is only via the out-of-gate systest). These are the "gate of
value" for the spike.

**Routing test (`LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp`, S10):** `spillEnabledFlagReadable`
(config flows), `spillDisabledByDefault` (unchanged). `add_nes_compiler_test`, in-gate.

**ctest regex:** new store tests `ctest -R 'SpillableTimeBasedSliceStore'`; all spill `ctest -R
'Spill|SliceSerializer|RocksDB' -LE 'Systest|DockerCompose'`; regression gate `ctest -LE 'Systest|DockerCompose'`
(baseline: confirm count from a clean pre-change run — brief says 797, MEMORY.md 2026-05-20 smoke saw 752 on a
different config; **run `ctest -LE 'Systest|DockerCompose'` in Docker before starting and pin the actual number**).
Out-of-gate (if S11) `ctest -L Systest -R systest_agg`.

---

## 6. Parallel Groups

- **Group P1 (sequential prerequisite, must land FIRST as one atomic change):** S1 + S2 + S3 — the D1 seam.
  The base-impl relocation (S1) MUST land together with the AggregationSlice drop (S2) or you get a duplicate
  symbol / pure-virtual at link; S3 is header-only `override` and rides along. Build `ninja nes-physical-operators`.
- **Group P2 (parallel after P1):**
  - S4 (un-`final`) — trivial, can land with S5.
  - S5 + S6 (new store + its CMake registration) — depends on S1 (virtual `getNumberOfTuples`) and S4 (un-final).
  - S9 (handler factory branch + trigger cast) — depends on S1 (seam) and S3 (SpillableAggregationSlice include);
    independent of S5. Can be built concurrently with S5.
  - S8 (store unit test) — written first per TDD but compiles only after S5; can be authored in parallel.
- **Group P3 (sequential, LAST):** S7 (lowering wire-in) — consumes S5 (store + factory), S9 (handler flag), and
  the S1 seam (extractor cast). Land S10 (routing-test rewrite) in the SAME change as S7 (the old test breaks by
  design). S11 optional, after the gate is green.

Within P2, S5 and S9 touch disjoint files → fully concurrent. S4 is a one-word edit; fold into S5's PR.

---

## 7. PR boundaries (for the per-PR review pass)

- **PR1 — D1 seam (atomic):** S1, S2, S3. Self-contained in nes-physical-operators; regression gate must stay
  green with zero behavior change (pure relocation + virtual lift). Reviewer focus: no duplicate symbols,
  `getNumberOfTuples` snapshot dispatch. Build+`ctest -LE 'Systest|DockerCompose'`.
- **PR2 — Store + budget + forced spill:** S4 (un-final), S5 (new store), S6 (CMake), S8 (store unit/integration
  test incl. InMemory + RocksDB-tempdir cases). The store is exercised in isolation — this PR proves the entire
  lifetime model (D3 unspill-on-read, D4 budget, forced spill, idempotency, emitted-not-spillable) on the gate.
  This is the highest-value PR.
- **PR3 — Wire-in:** S9 (handler factory branch + trigger cast), S7 (lowering construction + extractor cast +
  guard removal + O2 factory call), S10 (routing-test rewrite). Crosses into nes-query-compiler. Reviewer focus:
  no rocksdb linkage added to nes-query-compiler (factory keeps it in-target), handler `spillEnabled` plumbed.
- **PR4 (optional, out-of-gate) — Route-3a systest:** S11. Run manually; not part of the regression gate.

Rationale: PR1 is the load-bearing seam everything depends on and is zero-risk to merge first; PR2 proves the
spill path without touching the query engine; PR3 connects it. Each PR independently passes
`ctest -LE 'Systest|DockerCompose'`.

---

## 8. Resolution of OPEN items + flags

- **O1 — buffer provider stash — CONFIRMED.** `PipelineExecutionContext::getBufferManager()` returns
  `shared_ptr<AbstractBufferProvider>` (query-lifetime stable, verified CORE.md §2). Stash it via
  `SpillableTimeBasedSliceStore::setBufferProvider(...)` called from `setupSliceStoreProxy`
  (`DefaultTimeBasedSliceStoreRef.cpp:114-126`, which already holds `sliceStore` + `pipelineCtx` and derefs
  `getBufferManager()` at 124). **Seam decision (flag):** `setupSliceStoreProxy` holds a
  `DefaultTimeBasedSliceStore*` and calls non-virtual base methods. To call `setBufferProvider` without polluting
  the base, do `if (auto* spillable = dynamic_cast<SpillableTimeBasedSliceStore*>(sliceStore)) spillable->
  setBufferProvider(pipelineCtx->getBufferManager());` inside the proxy. This is ONE concrete `dynamic_cast`
  localized to the ref proxy (NOT scattered through handler/lowering, so Route B's intent holds). It requires
  EDITING `DefaultTimeBasedSliceStoreRef.cpp` (add the cast + include `<SliceStore/SpillableTimeBasedSliceStore.hpp>`)
  — **this is a file the SYNTHESIS does not list.** Alternative considered and rejected: add a `virtual
  setBufferProvider` no-op to the base store (cleaner OO, but contradicts D2's "delete one word, no other base
  change" and adds a vtable slot to the in-memory path). **My recommendation: the localized `dynamic_cast` in the
  proxy** — smallest blast radius, base store untouched beyond un-final. Flag for the synthesis: add
  `nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStoreRef.cpp` to the EDIT list (it is currently absent).
- **O2 — backend ownership/construction — CONFIRMED** (see §3): one `RocksDBSpillBackend` per store, built INSIDE
  nes-physical-operators via `SpillableTimeBasedSliceStore::create(...)` static factory so no rocksdb linkage
  reaches nes-query-compiler; per-operator subdir `<rocksdbPath>/op-<handlerId>`; errors via `SpillStoreFailure`.
- **O3 — slice factory — CONFIRMED:** in-place branch in `getCreateNewSlicesFunction` on a `bool spillEnabled`
  carried on the handler ctor (option (a), S9). Handler stays `final`. No store `dynamic_cast` in the factory.
- **O4 — DEFERRED to Increment B** (hard-threshold semantics: block vs aggressive-spill vs spill-then-error).
  `hardThresholdBytes` is stored and `residentBytes()` is computed for observability, but no enforcement action
  is wired in Increment A.

**Where I think the SYNTHESIS is incomplete / to flag to the synthesizer:**
1. **Missing EDIT file (O1 seam):** `DefaultTimeBasedSliceStoreRef.cpp` must be edited to stash the buffer
   provider (the localized `dynamic_cast` in `setupSliceStoreProxy`). SYNTHESIS lists the stash as happening "via
   setBufferProvider in setupSliceStoreProxy" but does NOT list the `.cpp` in its EDIT table (§Files affected) —
   add it, and decide cast-vs-virtual (I recommend the cast).
2. **D4 `residentBytes` semantics ambiguity:** SYNTHESIS/D4 says `residentBytes = Σ ⌈getNumberOfTuples()/
   entriesPerPage⌉×pageSize` read via the (virtual, snapshot-aware) `getNumberOfTuples()`. But a spilled slice's
   snapshot count would then yield NON-zero "resident" bytes for a slice with zero resident memory. For a *resident
   memory* budget the spilled slice must count 0. I resolved this by gating on `isResident()` and computing
   per-worker page math from `getHashMapPtr(w)->getNumberOfTuples()`. The virtual `getNumberOfTuples()` snapshot is
   still load-bearing for *snapshot reporting* / future store-wide reads, so D1's third method stays justified —
   but D4's formula as literally written would mis-budget spilled slices. Flag for the synthesizer to confirm
   "resident bytes = resident-only" (my reading).
3. **Routing-test weakening (S10):** with the guard deleted, `apply()` cannot be safely called on a dummy operator
   (the shape PRECONDITIONs `std::terminate`). The positive lowering proof therefore moves to the out-of-gate
   Route-3a systest (S11) + the store gate test (S8). SYNTHESIS implies the routing test can "assert successful
   spillable lowering" — that is not feasible in-gate without constructing a full logical aggregation operator.
   Flag: either accept the config-flow assertion (my choice) or budget for a real logical-operator fixture.
4. **GC backend-erase via tracked `spilledKeys` set** (R5/Q3): I avoid touching the base `private slices` map by
   tracking spilled keys in the spillable store and erasing post-base-GC when the slice is no longer retrievable.
   This is not exercised by the gate test (GC needs a full `windowSize` of extra watermark slack, hard to drive
   deterministically) → GC-erase correctness is an Increment-B test target. Flag as a known coverage gap for the
   spike.

**O4 explicitly marked DEFERRED.** No variable-sized keys, no join spilling, no automatic cold scan / live
eviction, no multi-thread D6 contention tests, no stale-SliceCache-invalidation test (all Increment B).
