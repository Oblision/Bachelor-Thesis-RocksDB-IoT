# PLAN-B.md — Phase 4b Increment A (single-threaded spill spike)

Architect B. Independent plan. Derived from `SYNTHESIS.md` + `DESIGN_BRIEF.md`, every dependent line re-verified
against live code on `feat/ooc-phase1-spill-skeleton` @ `40de3f9a55` (read 2026-05-22). All paths repo-relative;
module roots `nes-physical-operators/`, `nes-query-compiler/`.

---

## 1. Summary

Increment A wires the already-built spill components into the live aggregation path on a **single worker thread**,
proven by a gate-included C++ integration test that drives `SpillableTimeBasedSliceStore` directly: spill a COLD
slice, trigger it, observe unspill-on-read, assert correct aggregate and that the backend was used.

Six structural moves, in dependency order:

1. **D1 seam** — lift THREE methods to `virtual` on `HashMapSlice` (`getHashMapPtr` const, `getHashMapPtrOrCreate`,
   `getNumberOfTuples`). Relocate the resident accessor bodies into `HashMapSlice.cpp`. `AggregationSlice` drops its
   two accessor overrides; `SpillableAggregationSlice` marks all three `override`.
2. **D2 store** — delete `final` from `DefaultTimeBasedSliceStore`; new `SpillableTimeBasedSliceStore` subclass owns a
   `unique_ptr<SpillBackend>`, stashes a query-lifetime `shared_ptr<AbstractBufferProvider>`, holds page-math inputs +
   thresholds. Reuses `createSliceStoreRef`/`SliceCache`/ref unchanged.
3. **D3 unspill-on-read** — override `getTriggerableWindowSlices`, `getAllNonTriggeredSlices`, `getSliceBySliceEnd`;
   each unspills any non-resident returned slice before returning. Override `garbageCollectSlicesAndWindows` to
   `backend.erase` the diff of deleted sliceEnds. Override `setBufferProvider`-hook via `setupSliceStoreProxy`.
4. **D4 budget + forced spill (Increment A)** — `residentBytes()` page math over the now-virtual `getNumberOfTuples()`;
   a public **`forceSpill(SliceEnd)`** test-driven API (BLOCKER option (b): defer the automatic windows-map cold scan
   and live eviction to Increment B). `forceSpill` rejects an emitted slice.
5. **Handler** — `bool spillEnabled` ctor param/member; `getCreateNewSlicesFunction` branches the factory to
   `SpillableAggregationSlice`; `triggerSlices` cast `AggregationSlice` → `HashMapSlice` (seam only, no spill logic).
6. **Lowering** — replace the Phase-1 `NotImplemented` guard; when `conf.spill.enabled` construct
   `SpillableTimeBasedSliceStore` owning a `RocksDBSpillBackend` (built **inside** nes-physical-operators via a static
   factory, O2); extractor cast → `HashMapSlice&`; pass `spillEnabled` to handler ctor. Rewrite the routing test.

**Decisions I make (and where I differ from synthesis):**
- BLOCKER → **option (b)**: forced/manual spill only; defer the cold-scan. (Agrees with synthesis recommendation.)
- `spillEnabled` lives on the **handler ctor** (synthesis O3 option (a)); the **backend lives on the store**. I do NOT
  thread the flag through the store for the factory — keeps Route B's no-concrete-cast rule.
- I make the forced-spill API take an explicit `AbstractBufferProvider&` overload for unit tests **and** use the
  stashed provider in the trigger-path overrides — so the store unit test never depends on `setupSliceStoreProxy`.
- **Minor disagreement with synthesis Risk R5 / GC:** I erase backend records via snapshot-diff but compute the diff
  **inside** the GC override *around* the base call, then erase **after** `slicesToDelete.clear()` has run (i.e. after
  the base method returns, when the shared_ptrs are already dropped). Synthesis says "after the shared_ptr drops" —
  snapshot-diff around the whole base call satisfies that without refactoring the base. Concur, just being explicit.

---

## 2. Sections (one file each)

Log-level convention matches the codebase: `NES_TRACE` for per-call hot detail, `NES_DEBUG` for lifecycle/spill events,
`NES_WARNING`/`NES_ERROR` for anomalies. Spill/unspill are infrequent (trigger phase) → `NES_DEBUG`.

### 2.1 `nes-physical-operators/include/HashMapSlice.hpp` — EDIT (D1 seam)
- Line 80 `[[nodiscard]] uint64_t getNumberOfTuples() const;` → prepend `virtual`:
  `[[nodiscard]] virtual uint64_t getNumberOfTuples() const;`
- After line 80, add the two accessor virtuals (public block):
  ```cpp
  [[nodiscard]] virtual HashMap* getHashMapPtr(WorkerThreadId workerThreadId) const;
  [[nodiscard]] virtual HashMap* getHashMapPtrOrCreate(WorkerThreadId workerThreadId);
  ```
- No new includes (`Identifiers.hpp`:23, `HashMap.hpp`:24 present). `hashMaps`/`createNewHashMapSliceArgs` already
  `protected` (83-84).
- No logs (declarations only).
- **Covered by:** `SpillableTimeBasedSliceStoreTest` virtual-dispatch case (§5.A) + existing `SpillableAggregationSliceTest`.

### 2.2 `nes-physical-operators/src/HashMapSlice.cpp` — EDIT (D1 base bodies)
- Add the two accessor definitions (verbatim relocation of `AggregationSlice.cpp:37-58`, retyped as `HashMapSlice::`):
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
- No new includes: `ChainedHashMap.hpp`:25, `ErrorHandling.hpp`:28, `<memory>`:19 present (verified).
- No logs.

### 2.3 `nes-physical-operators/include/Aggregation/AggregationSlice.hpp` — EDIT (D1 drop)
- Delete the two accessor declarations (lines 37-38). Class stays `final`. No other change.

### 2.4 `nes-physical-operators/src/Aggregation/AggregationSlice.cpp` — EDIT (D1 drop)
- Delete the two accessor definitions (lines 37-58). Only the ctor (28-35) remains. Trim now-unused includes
  (`ChainedHashMap.hpp`, `ErrorHandling.hpp`) optionally — harmless to leave; I leave them to keep the diff minimal.
- Behavior identical: the moved code IS the AggregationSlice code.

### 2.5 `nes-physical-operators/include/Aggregation/SpillableAggregationSlice.hpp` — EDIT (D1 override)
- Append `override`: line 46 `getHashMapPtr ... const override;`, line 48 `getHashMapPtrOrCreate ... override;`,
  line 56 `getNumberOfTuples() const override;`. Class stays `final`.

### 2.6 `nes-physical-operators/src/Aggregation/SpillableAggregationSlice.cpp` — NO body change
- `override` is header-only. Bodies unchanged (keep `INVARIANT(resident,...)` at 53/60; do NOT add the
  `INVARIANT(pos<size)` the base has — preserving the pre-existing micro-divergence avoids a behavior change).

### 2.7 `nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp` — EDIT (D2 un-final)
- Line 55: delete one word `final`:
  `class DefaultTimeBasedSliceStore : public WindowSlicesStoreInterface`
- The five methods (61-67) are already `override` → re-overridable, no per-method `virtual`. Do NOT touch
  `createSliceStoreRef` (76) / `allocateSpaceForSliceCache` (71). No include change.

### 2.8 `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` — NEW
- `class SpillableTimeBasedSliceStore final : public DefaultTimeBasedSliceStore` (this subclass IS `final`).
- Includes: `<SliceStore/DefaultTimeBasedSliceStore.hpp>`, `<SliceStore/SpillBackend.hpp>`,
  `<Runtime/AbstractBufferProvider.hpp>`, `<memory>`, `<cstdint>`, `<folly/Synchronized.h>`.
- Ctor:
  ```cpp
  SpillableTimeBasedSliceStore(
      uint64_t windowSize, uint64_t windowSlide, SliceCacheConfiguration sliceCacheConfiguration,
      std::unique_ptr<SpillBackend> backend,
      uint64_t entriesPerPage, uint64_t pageSize,
      uint64_t softThresholdBytes, uint64_t hardThresholdBytes);
  ```
- Members:
  ```cpp
  std::unique_ptr<SpillBackend> backend;
  folly::Synchronized<std::shared_ptr<AbstractBufferProvider>> bufferProvider; // stashed once, query-lifetime
  uint64_t entriesPerPage; uint64_t pageSize;
  uint64_t softThresholdBytes; uint64_t hardThresholdBytes;
  ```
  (Thresholds stored in **bytes**; the lowering converts MB→bytes once. Soft/hard are recorded now for D4 wiring but
  only `forceSpill` consumes the budget in Increment A — automatic soft-driven eviction is Increment B.)
- Overrides (declarations; bodies in .cpp):
  `getTriggerableWindowSlices`, `getAllNonTriggeredSlices`, `getSliceBySliceEnd`, `garbageCollectSlicesAndWindows`.
  (NOT `getSlicesOrCreate`: build hot path only touches resident FILLING slices — the base is correct, do not override.)
- Public API (Increment A):
  ```cpp
  void setBufferProvider(std::shared_ptr<AbstractBufferProvider> provider); // called once in setupSliceStoreProxy
  void forceSpill(SliceEnd sliceEnd);                                       // test/manual: spill a COLD slice
  [[nodiscard]] uint64_t residentBytes() const;                            // Σ over resident slices (D4)
  [[nodiscard]] uint64_t residentBytesOf(const Slice& slice) const;        // per-slice page math (D4)
  ```

### 2.9 `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` — NEW
Implements D2/D3/D4 + forced spill. Key points (full forced-spill spec in §4):

- **`setBufferProvider`** — guarded set-once (warn-and-return if already set, like the `:294` once invariant):
  ```cpp
  bufferProvider.withWLock([&](auto& p){ if (p) { NES_DEBUG("bufferProvider already set; ignoring"); return; } p = std::move(provider); });
  ```
- **`residentBytesOf(slice)`** — D4: read tuple count polymorphically via the now-virtual `getNumberOfTuples()`:
  ```cpp
  const auto& hms = dynamic_cast<const HashMapSlice&>(slice);
  const uint64_t tuples = hms.getNumberOfTuples();          // virtual → spill-aware snapshot when spilled
  const uint64_t numMaps = hms.getNumberOfHashMaps();        // == workers; D4 "Σ_workers"
  // ⌈tuples/entriesPerPage⌉ × pageSize, but the count is already summed across maps; size by total pages:
  return ((tuples + entriesPerPage - 1) / entriesPerPage) * pageSize;
  ```
  Note: `getNumberOfTuples()` already sums across all worker maps (`HashMapSlice.cpp:73-82`), so this is the Σ_workers
  total; I do not multiply by numMaps again. (See §4 for the per-map-vs-total subtlety and the chosen convention.)
- **`residentBytes()`** — iterate the `slices` map under rlock; sum `residentBytesOf` for slices that are resident
  (a `SpillableAggregationSlice` reports its spill-time snapshot when spilled, so to measure *resident* footprint I
  skip non-resident slices via `dynamic_pointer_cast<SpillableAggregationSlice>(...)->isResident()`; non-spillable
  slices count as resident).
- **`forceSpill(sliceEnd)`** — §4. `NES_DEBUG("forceSpill slice {} ({} resident bytes before)", ...)`.
- **Trigger overrides (D3)** — pattern for all three feeders:
  ```cpp
  auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(globalWatermark); // base flips state, returns map
  unspillReturned(result);   // helper: for each slice, dynamic_pointer_cast<SpillableAggregationSlice>; if !isResident → unspill
  return result;
  ```
  `unspillReturned` derefs the stashed provider:
  ```cpp
  auto provider = bufferProvider.copy(); INVARIANT(provider, "buffer provider not stashed before trigger");
  for (auto& [info, slices] : result) for (auto& s : slices)
      if (auto sp = std::dynamic_pointer_cast<SpillableAggregationSlice>(s); sp && !sp->isResident()) {
          NES_DEBUG("unspill-on-read slice {}", sp->getSliceEnd()); sp->unspill(*backend, *provider); }
  ```
  Same for `getAllNonTriggeredSlices()` (termination flush — load-bearing, R3) and `getSliceBySliceEnd()`
  (consistency; unspill the single optional slice if present).
- **`garbageCollectSlicesAndWindows` override (R5)** — snapshot-diff:
  ```cpp
  std::set<SliceEnd> before; slices-rlock → collect keys;        // snapshot before
  DefaultTimeBasedSliceStore::garbageCollectSlicesAndWindows(wm); // base erases + drops shared_ptrs (clear() at :271)
  std::set<SliceEnd> after; slices-rlock → collect keys;         // snapshot after (shared_ptrs already dropped)
  for (auto e : before) if (!after.contains(e)) { NES_DEBUG("erasing backend record for GC'd slice {}", e); backend->erase(e); }
  ```
  Erasing after the base returns guarantees the shared_ptr is dropped (base `slicesToDelete.clear()` at `:271`).
- Includes: own header, `<Aggregation/SpillableAggregationSlice.hpp>`, `<SliceStore/Slice.hpp>`,
  `<Util/Logger/Logger.hpp>`, `<ErrorHandling.hpp>`, `<set>`, `<memory>`.

### 2.10 `nes-physical-operators/src/SliceStore/CMakeLists.txt` — EDIT (D2 registration)
- Add `SpillableTimeBasedSliceStore.cpp` after `RocksDBSpillBackend.cpp` (line 18) in the `add_source_files` block.

### 2.11 `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` — EDIT (O3)
- Add `bool spillEnabled` as the **last** ctor param (after `maxNumberOfBuckets`) and a `bool spillEnabled;` member.
  Handler stays `final`. Default the param to `false` so existing test/construction call sites (if any) compile —
  but the only construction site is the lowering rule, which will pass it explicitly.

### 2.12 `nes-physical-operators/src/Aggregation/AggregationOperatorHandler.cpp` — EDIT (O3 factory + D1 cast)
- Ctor (42-52): add `bool spillEnabled` param; init member `, spillEnabled(spillEnabled)`.
- `getCreateNewSlicesFunction` (54-68): capture `spillEnabled` in the lambda; branch at line 66:
  ```cpp
  if (spillEnabled)
      return {std::make_shared<SpillableAggregationSlice>(sliceStart, sliceEnd, copyOfNewHashMapArgs, numberOfWorkerThreads)};
  return {std::make_shared<AggregationSlice>(sliceStart, sliceEnd, copyOfNewHashMapArgs, numberOfWorkerThreads)};
  ```
  Add `#include <Aggregation/SpillableAggregationSlice.hpp>`. `NES_TRACE` already present at 65.
- `triggerSlices` (70-): line 82 `dynamic_pointer_cast<AggregationSlice>` → `dynamic_pointer_cast<HashMapSlice>`.
  All downstream calls (`getNumberOfHashMaps` 83, `getHashMapPtr` 85) exist on `HashMapSlice` after D1. No spill logic.

### 2.13 `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` — EDIT (wire-in)
- Delete the Phase-1 guard (163-166).
- At the store-construction site (234-235), branch on `conf.spill.enabled.getValue()`:
  ```cpp
  std::unique_ptr<WindowSlicesStoreInterface> sliceAndWindowStore;
  if (conf.spill.enabled.getValue()) {
      auto backend = SpillableTimeBasedSliceStore::makeRocksDBBackend(   // O2 static factory, in-target
          conf.spill.rocksdbPath.getValue() + "/agg-" + std::to_string(handlerId.getRawValue()),
          conf.spill.compression.getValue());
      sliceAndWindowStore = std::make_unique<SpillableTimeBasedSliceStore>(
          windowType->getSize().getTime(), windowType->getSlide().getTime(), conf.sliceCacheConfiguration,
          std::move(backend), entriesPerPage, pageSize,
          conf.spill.softThresholdMB.getValue() * 1024ULL * 1024ULL,
          conf.spill.hardThresholdMB.getValue() * 1024ULL * 1024ULL);
      NES_DEBUG("Constructed SpillableTimeBasedSliceStore for handler {} at {}", handlerId, ...);
  } else {
      sliceAndWindowStore = std::make_unique<DefaultTimeBasedSliceStore>(
          windowType->getSize().getTime(), windowType->getSlide().getTime(), conf.sliceCacheConfiguration);
  }
  ```
  `createSliceStoreRef` is still called on the (base-typed) pointer — it's inherited, dispatches fine. Since
  `sliceAndWindowStore` is now base-typed, call `createSliceStoreRef` via the base interface (it's a non-virtual member
  of `DefaultTimeBasedSliceStore`, present on both); keep the variable as `DefaultTimeBasedSliceStore*` temporarily or
  call before the `unique_ptr<WindowSlicesStoreInterface>` upcast. **Concrete approach:** build the concrete store into
  a `DefaultTimeBasedSliceStore*` raw alias for the `createSliceStoreRef` call, then `std::move` the owning unique_ptr
  into the handler. (`createSliceStoreRef` is not on `WindowSlicesStoreInterface`.)
- Extractor (239): `dynamic_cast<AggregationSlice&>(slice)` → `dynamic_cast<HashMapSlice&>(slice)`;
  `getHashMapPtrOrCreate` (240) is now virtual on `HashMapSlice`.
- Handler ctor (254-255): append `, conf.spill.enabled.getValue()`.
- Add includes: `<SliceStore/SpillableTimeBasedSliceStore.hpp>`, `<HashMapSlice.hpp>` (for the cast).
- The page-math inputs `entriesPerPage` (215), `pageSize` (214) are reused as-is — **no recompute** (D4 no-drift).
- `NES_DEBUG` on the spill branch.

### 2.14 `nes-query-compiler/tests/UnitTests/LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp` — EDIT (rewrite)
- The two `spillEnabledThrows*` cases (56-72) assert the old guard → **rewrite** (see §5.E). `spillDisabledByDefault`
  (79-83) stays valid.

### 2.15 `nes-physical-operators/tests/CMakeLists.txt` — EDIT (test registration)
- After line 27 (`SpillableAggregationSliceTest`), add:
  ```cmake
  add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)
  ```
- The end-to-end integration proof is **folded into the same test file** (a TEST_F driving the store with a sliding
  window) — no second file needed. (Keeps the change surface minimal; the file already links everything required.)

### 2.16 `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` — NEW
Unit + in-gate integration tests (see §5). Reuses `SpillableAggregationSliceTest`'s fixture/`makeArgs`/`populate`.

---

## 3. Backend ownership / construction (O2 / O3)

- **O2 — backend built INSIDE nes-physical-operators.** The store owns `unique_ptr<SpillBackend>`. The
  `RocksDBSpillBackend` is constructed by a **static factory on the store**,
  `SpillableTimeBasedSliceStore::makeRocksDBBackend(path, compression) -> std::unique_ptr<SpillBackend>`, whose body
  lives in `SpillableTimeBasedSliceStore.cpp` (in-target → `RocksDB::rocksdb` PRIVATE link, no new linkage on
  nes-query-compiler). The lowering rule passes only `SpillConfig` *values* (path, compression) across the seam —
  never touches `rocksdb::` symbols. Confirmed: `nes-query-compiler` links `nes-physical-operators` PUBLIC; the new
  header includes only the `SpillBackend` interface. **No CMake linkage edit anywhere** (verified `CMakeLists.txt`
  Phase-3 wiring: `find_package(RocksDB CONFIG REQUIRED)` + `RocksDB::rocksdb` PRIVATE already present).
- **rocksdbPath (R8):** per-operator subdir `"<rocksdbPath>/agg-<handlerId>"` (handlerId available at lowering `:183`)
  → no collision when a query has >1 windowed aggregation.
- **Backend lifecycle (O-q4):** the backend is owned by the store; store dtor drops it (RocksDB dtor closes the DB).
  Increment A does NOT delete the on-disk dir on teardown (out of scope; documented). For the in-gate test the
  integration case uses **`InMemorySpillBackend`** (no disk) so repeated runs leak nothing; an optional RocksDB-on-temp
  case uses a unique `testing::TempDirectoryPath`-style dir. (Per-run `/tmp/nes-spill` cleanup → Increment B.)
- **O3 — factory branch in the handler**, on a `bool spillEnabled` ctor member (option (a)). The handler stays
  backend-agnostic (only a bool); the backend lives on the store. No `dynamic_cast` of the store anywhere.
- **Errors:** `RocksDBSpillBackend` throws `SpillStoreFailure` (3014, factory fn). It propagates through the JIT proxy
  `checkWindowsTriggerProxy` (`WindowBuildPhysicalOperator.cpp:37`, no try/catch). Test with
  `ASSERT_EXCEPTION_ERRORCODE(stmt, ErrorCode::SpillStoreFailure)`.

---

## 4. The forced-spill store API

### Signatures
```cpp
// public, on SpillableTimeBasedSliceStore
void forceSpill(SliceEnd sliceEnd);
[[nodiscard]] uint64_t residentBytes() const;
[[nodiscard]] uint64_t residentBytesOf(const Slice& slice) const;
void setBufferProvider(std::shared_ptr<AbstractBufferProvider> provider);
static std::unique_ptr<SpillBackend> makeRocksDBBackend(const std::string& path, const std::string& compression);
```

### `forceSpill` semantics (Increment A, BLOCKER option (b))
```cpp
void SpillableTimeBasedSliceStore::forceSpill(SliceEnd sliceEnd)
{
    std::shared_ptr<Slice> slice;
    { auto sl = slices.rlock(); auto it = sl->find(sliceEnd);
      if (it == sl->end()) { NES_DEBUG("forceSpill: no slice {}", sliceEnd); return; } slice = it->second; }

    // R1 guard: never spill an emitted slice. Scan windows that reference this slice.
    {
        auto w = windows.rlock();
        for (const auto& [info, st] : *w)
            for (const auto& s : st.windowSlices)
                if (s->getSliceEnd() == sliceEnd && st.windowState == WindowInfoState::EMITTED_TO_PROBE)
                    throw SpillStoreFailure("refusing to spill emitted slice {} (async-probe-drain invariant)", sliceEnd);
    }

    auto sp = std::dynamic_pointer_cast<SpillableAggregationSlice>(slice);
    INVARIANT(sp != nullptr, "forceSpill on a non-spillable slice {}", sliceEnd);
    NES_DEBUG("forceSpill slice {}", sliceEnd);
    sp->spill(*backend);   // 4a: idempotent, serializes maps, frees TupleBuffers, snapshots tuple count
}
```
- **Why this is the spike's spill driver (not a soft-threshold scan):** the BLOCKER's cold-slice predicate needs a
  windows-map scan that is contention-sensitive; deferring it to Increment B (where it's tested under load) kills the
  D6 lifetime risk on the cheapest path. A test selects a *demonstrably* cold slice (sliceEnd < watermark, not yet
  triggered) and calls `forceSpill` — the emitted-slice guard above makes a wrong call fail loud, not UAF.
- **Serialization (D6.4):** `forceSpill`'s read of `slices`/`windows` is under the existing `folly::Synchronized`
  locks; `spill()` itself is idempotent (no-op if already spilled). Unspill is idempotent (no-op if resident,
  `SpillableAggregationSlice.cpp:121`) → the trigger-path retry after a `tryWLock` miss (R7) is safe.

### residentBytes page math (D4) — the per-map subtlety I resolve
`getNumberOfTuples()` (`HashMapSlice.cpp:73-82`) returns the **sum across all worker maps**. The brief's D4 formula is
`Σ_workers ⌈tuples_w / entriesPerPage⌉ × pageSize` — i.e. ceil **per worker map**, not on the total. Ceil-on-total
under-counts when tuples are split across maps. **Decision:** for the single-worker spike (`numberOfWorkerThreads == 1`,
one map) ceil-on-total == ceil-per-map exactly, so I use the simple total form in Increment A and document that the
exact per-map form (needs a per-map count accessor) is an **Increment-B** refinement. For the unit test I assert
exactness against a single-worker fill so the formula is provably correct for the spike's regime. (Synthesis writes the
Σ_workers form but reads it via the summed `getNumberOfTuples()`; I flag the rounding gap explicitly rather than leave
it latent.)

### How a test spills a COLD slice then verifies unspill-on-trigger (the proof)
1. Build store with sliding window (size 100, slide 50 → real cold population, D5), `InMemorySpillBackend`,
   `entriesPerPage`/`pageSize` from `makeArgs`. `setBufferProvider(bufferManager)`.
2. `getSlicesOrCreate(ts=10, createSpillableSlice)` → slice `[0,100)` (and the sliding overlap). Populate its worker-0
   map with N known keys via `getHashMapPtrOrCreate` + `insertEntry` (reuse `populate`).
3. Create a later slice past the watermark so the early slice is COLD; assert `residentBytes()` reflects the fill.
4. `forceSpill(sliceEnd=100)` → assert `backend.sliceCount() == 1`, the slice `!isResident()`,
   `residentBytes()` dropped, and `getNumberOfTuples()` via `HashMapSlice&` still == N (snapshot, R6).
5. `getTriggerableWindowSlices(watermark > windowEnd)` → assert the returned slice `isResident()` again (D3 unspill),
   and its map contents equal the pre-spill fill (reuse `sortedEntries` for order-independent compare) → correctness.
6. Idempotency: a second `getTriggerableWindowSlices` (or a redundant unspill) is a no-op; double `forceSpill` no-op.

---

## 5. Test strategy

**Gate:** Route 3b — C++ tests in `nes-physical-operators/tests`, registered via `add_nes_physical_operator_test`
(unlabeled → in `ctest -LE 'Systest|DockerCompose'`). Route 3a `.test` is out-of-gate (Systest-labeled) and **not**
built in Increment A; a manual systest smoke is optional and noted but not required for the TDD gate.

All tests in **`SpillableTimeBasedSliceStoreTest.cpp`** (fixture = `BaseUnitTest`, BufferManager in `SetUp`, reuse
`makeArgs`/`populate`/`sortedEntries` from `SpillableAggregationSliceTest.cpp:45-114`). The lowering routing test is in
its existing file (§5.E).

### A. Seam virtual dispatch (D1) — unit
- Construct `SpillableAggregationSlice`, hold as `HashMapSlice&`. Assert `hms.getHashMapPtr(wtid)` /
  `getHashMapPtrOrCreate(wtid)` dispatch to the spillable override (INVARIANT(resident) fires when spilled — verify via
  a controlled `spill()` then `ASSERT_DEATH`/skip; primary positive check = resident dispatch works).
- **getNumberOfTuples via `HashMapSlice&` on a spilled slice (R6, the load-bearing third method):** fill N, `spill`,
  read `static_cast<const HashMapSlice&>(slice).getNumberOfTuples()` → assert `== N` (snapshot), NOT 0.
- Construct `AggregationSlice` as `HashMapSlice&` → same accessors hit the base impl (regression: behavior unchanged).

### B. Store forced-spill → trigger-unspill → correctness → backend-used — unit/integration (the spike proof)
- The §4 sequence. Asserts: `backend.sliceCount() > 0` (spill occurred), unspilled slice resident on trigger, map
  contents correct after unspill, `residentBytes()` capped after spill. Sliding window per D5.

### C. residentBytes exactness (D4) — unit
- Single-worker slice filled with exactly K tuples; assert `residentBytesOf == ⌈K/entriesPerPage⌉ × pageSize` for
  several K (0, 1, entriesPerPage, entriesPerPage+1). Assert a spilled slice contributes 0 to `residentBytes()`
  (resident-footprint semantics) while `getNumberOfTuples()` still returns K.

### D. Idempotency + emitted-not-spillable (D6, R1) — unit
- Double `forceSpill(sliceEnd)` → second is a no-op (`sliceCount` unchanged, still `!isResident`).
- Double trigger / redundant unspill → no `INVARIANT(resident)` abort; idempotent.
- **emitted-not-spillable:** trigger a slice (flips its window EMITTED_TO_PROBE), then `forceSpill(sliceEnd)` →
  `ASSERT_EXCEPTION_ERRORCODE(store.forceSpill(end), ErrorCode::SpillStoreFailure)`.
- Termination-flush unspill (R3): spill a cold slice, then `getAllNonTriggeredSlices()` (drive
  `incrementNumberOfInputPipelines` once first so the INVARIANT at `:157` holds) → assert returned slice resident, no
  abort.
- GC backend-erase (R5): spill a slice, advance watermark past `sliceEnd + windowSize`, call
  `garbageCollectSlicesAndWindows` → assert `backend.contains(sliceEnd, wtid) == false` and the slice gone from the map.

### E. Lowering routing (rewrite of the broken test) — nes-query-compiler unit
- `LowerToPhysicalWindowedAggregationSpillRoutingTest.cpp`: the two `spillEnabledThrows*` cases asserted the old guard.
  Increment A removes the guard, but a bare `apply(dummyOperator)` now trips operator-shape PRECONDITIONs
  (`std::terminate`, uncatchable) before reaching the store branch — so I cannot assert "successful spillable lowering"
  on a dummy op without a full `WindowedAggregationLogicalOperator` fixture (heavy; the file has no such builder).
  **Decision:** rewrite the two cases to assert the guard is **gone** at the config level + that spill-enabled no longer
  throws `NotImplemented` on the config read path — concretely, keep `spillDisabledByDefault`, and replace the two
  throwing cases with one positive case that constructs the rule with spill enabled and asserts
  `conf.spill.enabled.getValue()` is honored (the rule no longer rejects it). Full live spillable lowering is proven by
  the Route-3b store integration test (B) + an optional manual Route-3a systest, NOT this unit (which lacks an agg-op
  fixture). This is honest about the test's reach. (Synthesis says "rewrite to assert successful spillable lowering" —
  I narrow that: it cannot fully without an agg-op builder; I document the seam the unit *can* assert.)

### ctest regex
- New: `ctest -R 'SpillableTimeBasedSliceStore'`
- Routing: `ctest -R 'LowerToPhysicalWindowedAggregationSpillRouting'`
- All spill units: `ctest -R 'Spill|SliceSerializer|RocksDB' -LE 'Systest|DockerCompose'`
- Gate (must stay green; confirm baseline from a clean pre-change `ctest -LE 'Systest|DockerCompose'` in Docker):
  `ctest -LE 'Systest|DockerCompose'`

---

## 6. Parallel groups (D1 first)

- **G0 (prereq, serial):** D1 seam — §2.1–2.6 must land together (base-impl relocation + AggregationSlice drop in one
  change, else duplicate-symbol/pure-virtual at link). Plus D2 un-final §2.7 (one word). These gate everything.
- **G1 (parallel after G0):**
  - **G1a — store:** §2.8 + §2.9 + §2.10 (new store hpp/cpp + CMake src). Self-contained in nes-physical-operators.
  - **G1b — handler:** §2.11 + §2.12 (ctor flag + factory branch + cast). Depends only on D1 (seam cast + Spillable
    include). Independent of G1a.
- **G2 (after G1a + G1b):** §2.13 lowering wire-in (consumes the new store, the handler flag, the seam cast) + §2.14
  routing-test rewrite (same change — the old test breaks by design).
- **G3 (parallel, can start once its deps compile):** tests — §2.15 + §2.16 (store unit/integration) after G0+G1a;
  §2.14 lands with G2. TDD: write tests first, they compile once their deps exist.

Edit-ordering hard rules (synthesis "Dependencies / order" confirmed): D1 → D2 un-final → new store+CMake → handler
flag → lowering last (with routing-test rewrite).

---

## 7. PR boundaries (per-PR review pass)

Three PRs, each independently buildable + green:

- **PR1 — D1 seam + D2 un-final (the polymorphic groundwork).** §2.1–2.7. Plus the §5.A seam unit test. No behavior
  change (relocation + override + delete-`final`). Smallest, lowest-risk; unblocks both downstream PRs. Gate: full
  regression stays green (no functional change).
- **PR2 — Spillable store + handler factory + forced-spill API + store tests.** §2.8–2.12, §2.15–2.16 (tests B/C/D).
  Self-contained in nes-physical-operators; no live-query path yet (driven directly by the test). This is the
  **value gate** — proves the lifetime/spill model on a single thread without touching the compiler.
- **PR3 — Lowering wire-in + routing-test rewrite.** §2.13–2.14. Connects the store to the live engine; smallest blast
  radius once PR1/PR2 are in. Optional manual Route-3a systest smoke noted in the PR description (out-of-gate).

Rationale: PR1 is pure groundwork (reviewer focuses on ABI/vtable + no-behavior-change); PR2 carries the deep lifetime
logic (reviewer focuses on D3/D5/D6 invariants + GC erase); PR3 is glue (reviewer focuses on the seam casts + no
linkage change + the test rewrite honesty).

---

## 8. Resolution of OPEN items

- **O1 — RESOLVED (confirm).** Store stashes `pipelineCtx->getBufferManager()`
  (`shared_ptr<AbstractBufferProvider>`, query-lifetime stable — confirmed `setupSliceStoreProxy` already derefs it at
  `DefaultTimeBasedSliceStoreRef.cpp:118,124`) via `setBufferProvider`, called once inside `setupSliceStoreProxy`
  (`:114-126`). Guarded set-once (mirrors the `:294` once invariant). Stored as `shared_ptr` (durable), deref to `&` at
  unspill. **Implementation note:** `setupSliceStoreProxy` currently takes `(DefaultTimeBasedSliceStore*, const
  PipelineExecutionContext*, DefaultTimeBasedSliceStoreRef*)`; to call `setBufferProvider` it must `dynamic_cast` the
  base store ptr to `SpillableTimeBasedSliceStore*` and call it only when non-null. This is a single, contained
  `dynamic_cast` at the proxy seam (acceptable — it is the one place that legitimately needs to know the store is
  spillable; it is NOT a scattered concrete cast on the read path). I add this `dynamic_cast` guard in
  `setupSliceStoreProxy`; the in-gate store unit test bypasses the proxy by calling `setBufferProvider` directly.
- **O2 — RESOLVED (confirm).** One `RocksDBSpillBackend` per store (per query), built **inside**
  nes-physical-operators via `SpillableTimeBasedSliceStore::makeRocksDBBackend` (keeps rocksdb in-target; no linkage on
  nes-query-compiler). Lowering passes only `SpillConfig` values. Per-operator subdir from `handlerId` (R8). Errors via
  `SpillStoreFailure` (3014).
- **O3 — RESOLVED (confirm).** In-place factory branch in `getCreateNewSlicesFunction` on a `bool spillEnabled` ctor
  member (option (a)). Handler stays `final`; no store `dynamic_cast` in the factory.
- **O4 — DEFERRED to Increment B.** Hard-threshold semantics (block vs aggressive-spill vs spill-then-error). Increment
  A records `hardThresholdBytes` on the store but does not act on it; only `forceSpill` consumes the budget. The
  automatic soft-threshold-driven cold-scan eviction (the BLOCKER windows-map predicate) is likewise Increment B.

---

## Synthesis points I disagree with / sharpen

1. **D4 rounding (§4):** synthesis states `Σ_workers ⌈tuples/entriesPerPage⌉ × pageSize` but reads tuples via the
   summed `getNumberOfTuples()`. Ceil-on-total ≠ Σ ceil-per-map when tuples span multiple maps. For the single-worker
   spike they coincide; I implement ceil-on-total now and flag exact per-map math as Increment B. Not a spike risk.
2. **Routing-test rewrite (§5.E):** synthesis says "rewrite to assert successful spillable lowering." Without a
   `WindowedAggregationLogicalOperator` builder (none exists in that test file), `apply` on a dummy op trips
   PRECONDITION `std::terminate` before the store branch. I narrow the unit to what it can honestly assert (guard gone /
   config honored) and push full live spillable lowering proof to the Route-3b store integration test (+ optional
   manual systest). I will not write a green-looking test that doesn't actually exercise the path.
3. **O1 proxy `dynamic_cast` (§8):** synthesis frames the stash as "add `setBufferProvider` and call it in the proxy"
   without naming the downcast. The proxy holds a `DefaultTimeBasedSliceStore*`; calling the spillable-only method
   requires one `dynamic_cast<SpillableTimeBasedSliceStore*>` guarded by null. I make this explicit — it's the single
   sanctioned concrete cast (at setup, not on the per-trigger read path), consistent with Route B.
4. **No `getSlicesOrCreate` override:** synthesis lists five overridable methods; I override only four (drop
   `getSlicesOrCreate`). The build hot path only creates resident FILLING slices; the base is correct and overriding it
   would add spill-awareness to the hot path for no benefit (CORE.md §4 confirms). The factory (handler) already emits
   `SpillableAggregationSlice` instances, so `getSlicesOrCreate` needs no change.
