# CONTRACTS — Explorer 3 (Contracts, Interfaces & Tests)

Increment C: Emit-Decouple (Lagged Emit Watermark). Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`.
READ-ONLY exploration. All paths absolute. Findings keyed to file:line + verbatim signatures.

Repo root (abbreviated below as `…`):
`/Users/oblision/Desktop/Desktop/Uni_TI_BS/Bachelor Arbeit/Bachelor-Thesis/nebulastream-Out-of-Core-Stream-Processing`

Files of record:
- `…/nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp` (the abstract contract)
- `…/nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp` (base; NOT final; maps PRIVATE)
- `…/nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStore.cpp` (base impl)
- `…/nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` (subclass; `final`)
- `…/nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` (subclass impl)
- `…/nes-physical-operators/src/WindowBasedOperatorHandler.cpp` (the trigger call site)
- `…/nes-query-compiler/interface/SpillConfig.hpp` + `QueryExecutionConfiguration.hpp` (config)
- `…/nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` (store construction)
- `…/nes-common/include/ExceptionDefinitions.inc` (exceptions)
- `…/nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` + `…ConcurrencyTest.cpp` (test template)
- `…/nes-physical-operators/tests/CMakeLists.txt` (registration)

---

## 1. The virtual override set (DefaultTimeBasedSliceStore → SpillableTimeBasedSliceStore)

The polymorphism root is the pure-virtual interface `WindowSlicesStoreInterface`
(`WindowSlicesStoreInterface.hpp:63-101`). `DefaultTimeBasedSliceStore` `override`s those methods;
`SpillableTimeBasedSliceStore` (which is `final`, `.hpp:88`) re-`override`s a subset. The base is NOT
`final` (explicit comment at `DefaultTimeBasedSliceStore.hpp:55-57`), and its state maps are PRIVATE:

```cpp
// DefaultTimeBasedSliceStore.hpp:88-89  (PRIVATE — a subclass cannot enumerate them)
folly::Synchronized<std::map<WindowInfo, SlicesAndState>> windows;
folly::Synchronized<std::map<SliceEnd, std::shared_ptr<Slice>>> slices;
```
⇒ the subclass self-tracks via its own `createdSlices`
(`folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>>`, `SpillableTimeBasedSliceStore.hpp:222`)
— weak_ptr so the base GC's shared_ptr drop still frees the slice.

### Methods SpillableTimeBasedSliceStore OVERRIDES (exact signatures, all from the .hpp)

1. **Destructor** — `SpillableTimeBasedSliceStore.hpp:93`
   ```cpp
   ~SpillableTimeBasedSliceStore() override;
   ```
2. **getSlicesOrCreate** — `.hpp:113-114`
   ```cpp
   std::vector<std::shared_ptr<Slice>> getSlicesOrCreate(
       Timestamp timestamp,
       const std::function<std::vector<std::shared_ptr<Slice>>(SliceStart, SliceEnd)>& createNewSlice) override;
   ```
3. **getTriggerableWindowSlices** — `.hpp:118-119`
   ```cpp
   std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
   getTriggerableWindowSlices(Timestamp globalWatermark) override;
   ```
4. **getAllNonTriggeredSlices** — `.hpp:120`
   ```cpp
   std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>> getAllNonTriggeredSlices() override;
   ```
5. **getSliceBySliceEnd** — `.hpp:122`
   ```cpp
   std::optional<std::shared_ptr<Slice>> getSliceBySliceEnd(SliceEnd sliceEnd) override;
   ```
6. **garbageCollectSlicesAndWindows** — `.hpp:124`
   ```cpp
   void garbageCollectSlicesAndWindows(Timestamp newGlobalWaterMark) override;
   ```
7. **deleteState** — `.hpp:127`
   ```cpp
   void deleteState() override;
   ```

NOTE: none of these is `const` and none is `noexcept`. The interface declares them non-const/non-noexcept
(`WindowSlicesStoreInterface.hpp:68-92`); the only `const`-qualified interface method is
`getWindowSize() const` (`:100`), which the subclass does NOT re-override (inherits the base's
`getWindowSize() const override`, `DefaultTimeBasedSliceStore.hpp:73`). Likewise
`incrementNumberOfInputPipelines()` (`:97`) and the non-virtual helpers
(`allocateSpaceForSliceCache`, `createSliceStoreRef`) are NOT overridden.

### What would BREAK if a signature changed — confirm ADD, do NOT change

The override relationship is **signature-identity based** (C++ override matching: name + parameter types +
cv-qualifiers). The build is the typecheck and it is strict: `-Werror -Woverloaded-virtual` is ON (per
project memory, Phase 4b idioms). Consequences of touching an existing signature:

- **Drop `override` matching → silent name-hiding / overloaded-virtual.** If you change the parameter list
  of, e.g., `getTriggerableWindowSlices` ONLY on the subclass, it stops overriding the base virtual; the
  base version is then hidden by the new name, and `-Woverloaded-virtual` becomes a hard `-Werror`. The
  prior phase hit exactly this (HJSlice `getHashMapPtr` arity divergence) and had to add `using
  Base::method;`.
- **Change the base virtual's signature → every caller + the interface must change in lockstep.** The
  interface vtable slot is consumed by `WindowBasedOperatorHandler` (`WindowBasedOperatorHandler.cpp:87`
  and `:93`), the Nautilus build ref, all tests, and both stores. This is an ODR/ABI-wide ripple — the
  whole regression suite (820 baseline) recompiles and any missed call site fails to compile.
- **Add a parameter to the virtual (e.g. pass `emit_lag` per-call) → forces the handler + interface +
  base + both subclass overrides + every test call (≈10 call sites in the two test files) to change.**

**RECOMMENDATION (confirmed): ADD, do not change.** The increment needs NO new override and NO signature
change to any existing virtual. The emit-lag value is plumbed through the **constructor** (a new ctor
parameter on `SpillableTimeBasedSliceStore` only — see §2), and the override bodies already in place
(`getTriggerableWindowSlices`, `getAllNonTriggeredSlices`) get small body-only edits. Any NEW test hook
(e.g. a `setEmitLagForTest` if needed) is added as a brand-new non-virtual public method, mirroring the
existing `setLastSeenWatermarkForTest` / `spillColdSlicesUnderHardForTest` hooks. Zero blast radius on
the override set, the interface, or non-spill / join windowing.

---

## 2. The seam for the true watermark (OPEN #4 — setQuiescenceWatermark) — RESOLVED

### The single watermark already flows IN as a parameter

The handler computes ONE global watermark and passes it positionally:

```cpp
// WindowBasedOperatorHandler.cpp:76-87  (checkAndTriggerWindows)
const auto newGlobalWatermark
    = watermarkProcessorBuild->updateWatermark(bufferMetaData.watermarkTs, bufferMetaData.seqNumber, bufferMetaData.originId);
...
const auto slicesAndWindowInfo = sliceAndWindowStore->getTriggerableWindowSlices(newGlobalWatermark);
```

The base store uses that argument BOTH as the emit cutoff AND nothing else:

```cpp
// DefaultTimeBasedSliceStore.cpp:104-123  (getTriggerableWindowSlices)
... getTriggerableWindowSlices(const Timestamp globalWatermark) {
  ...
  for (auto& [windowInfo, windowSlicesAndState] : *windowsWriteLocked) {
      if (windowInfo.windowEnd >= globalWatermark) { break; }   // <-- THE EMIT CUTOFF (line 119)
      ...
      windowSlicesAndState.windowState = WindowInfoState::EMITTED_TO_PROBE;  // emit (line 130)
```

The subclass override receives the SAME `globalWatermark` and currently uses it for quiescence (cold
tracking) via CAS-max:

```cpp
// SpillableTimeBasedSliceStore.cpp:264-280  (override)
... getTriggerableWindowSlices(const Timestamp globalWatermark) {
  auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(globalWatermark);  // EMIT at true wm today
  unspillAndMarkEmitted(result);
  // O-B-watermark: learn the build watermark monotonically (CAS-max). Slices ending before it are cold.
  auto current = lastSeenWatermark.load(std::memory_order_relaxed);
  const auto incoming = globalWatermark.getRawValue();              // QUIESCENCE uses the SAME wm (line 272)
  while (incoming > current && !lastSeenWatermark.compare_exchange_weak(current, incoming, std::memory_order_relaxed)) {}
  spillerCv.notify_one();
  return result;
}
```

This is exactly the E0 root cause: the base `break` cutoff (line 119) and the subclass `lastSeenWatermark`
advance (line 272) BOTH use the identical `globalWatermark`, so `{cold AND not-emitted}` is empty in
steady state.

### Recommendation — NO new `setQuiescenceWatermark` setter; apply L inside the override

A separate setter (`setQuiescenceWatermark`) would introduce **redundant state**: the store would carry
both the lagged emit watermark (per-call argument) AND a stashed true watermark, and the handler would
have to call two methods per trigger with a documented happens-before ordering between them — extra
coupling and a new race surface, contradicting the brief's "avoid redundant state."

The clean seam (matches OPEN #1 leading candidate, "smallest blast radius"): keep ONE argument flowing in
(the TRUE `globalWatermark`, exactly as today, no handler change), store `emit_lag` as a **const member
set at construction**, and inside the override SPLIT the two uses:

- **Quiescence / cold-pick** uses the TRUE argument: keep the CAS-max into `lastSeenWatermark` on
  `globalWatermark` UNCHANGED (lines 271-275). This already drives `pickAndReserveColdSlice`
  (`SpillableTimeBasedSliceStore.cpp:478` reads `lastSeenWatermark`; `:498` `end >= wm ⇒ break`).
- **Emit** uses the lagged value: call the base with `globalWatermark − emit_lag` instead of the raw
  watermark, i.e. `DefaultTimeBasedSliceStore::getTriggerableWindowSlices(Timestamp(globalWatermark − L))`
  on line 267 (clamp at 0; if `globalWatermark < L`, pass `Timestamp(0)` ⇒ nothing emits yet).

Net effect: the base RETAINS windows with `windowEnd ∈ [globalWatermark − L, globalWatermark)` (they fail
the `windowEnd >= laggedWatermark` break and stay `WINDOW_FILLING`/not `EMITTED_TO_PROBE`), while
`lastSeenWatermark = globalWatermark` makes the cold-pick see exactly that retained band. The
`{cold AND not-emitted}` set becomes the retained band — non-empty — and Increment B's spiller engages
unchanged.

This needs:
- ONE new ctor parameter `uint64_t emitLag` (event-time duration, LOCKED #5) + one `const uint64_t
  emitLag;` member. Default path (`emit_lag = 0`) makes `globalWatermark − 0 == globalWatermark` ⇒
  byte-identical to today (LOCKED #6 regression net).
- ONE body edit in `getTriggerableWindowSlices` (line 267).
- NO interface change, NO `WindowBasedOperatorHandler` change, NO new virtual, NO new setter.

(If a test wants to vary L without rebuilding the store, add a non-virtual `setEmitLagForTest(uint64_t)`
hook mirroring `setLastSeenWatermarkForTest`, `SpillableTimeBasedSliceStore.hpp:155`. The const member is
the production path; the hook is test-only.)

---

## 3. Terminate / flush contract (LOCKED #8) — getAllNonTriggeredSlices

### Current EOS path (verbatim)

Subclass override (`SpillableTimeBasedSliceStore.cpp:282-290`):
```cpp
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
SpillableTimeBasedSliceStore::getAllNonTriggeredSlices() {
    auto result = DefaultTimeBasedSliceStore::getAllNonTriggeredSlices();
    unspillAndMarkEmitted(result);   // unspills + marks emitted every returned slice
    // Deliberately do NOT bump lastSeenWatermark here (terminate path; spiller quiesced before deleteState).
    return result;
}
```

Base (`DefaultTimeBasedSliceStore.cpp:150-210`): it iterates ALL windows and emits regardless of any
watermark argument — it takes NO watermark parameter. It emits every window in state `WINDOW_FILLING`
(once `numberOfActiveInputPipelines == 0`) or `WAITING_ON_TERMINATION`, skipping only those already
`EMITTED_TO_PROBE` (`:177-205`). The handler calls it from `triggerAllWindows`
(`WindowBasedOperatorHandler.cpp:91-95`).

### What determines which slices it returns / what must change

**Good news: the base `getAllNonTriggeredSlices` is already watermark-agnostic** — it returns EVERY
not-yet-emitted window (modulo the multi-pipeline termination gate), so it inherently flushes the WHOLE
retained backlog independent of `emit_lag`. The retained band `[wm − L, wm)` consists of windows still in
`WINDOW_FILLING` (they were never emitted because the lagged emit cutoff held them back) — and those are
exactly the windows this method emits. The subclass override already `unspillAndMarkEmitted`s every
returned slice (line 285), so spilled deferred windows are restored from RocksDB before being handed to
the probe.

**Therefore LOCKED #8 is satisfied with NO change to either `getAllNonTriggeredSlices` body**, PROVIDED
the lag is applied ONLY in `getTriggerableWindowSlices` (§2), never in this terminate path. The subclass
already (correctly) does NOT bump `lastSeenWatermark` here and does NOT subtract any lag. The one thing to
**guard against / verify**: do not "optimize" by adding an `emit_lag` cutoff into this path — a deferred
window that is still `WINDOW_FILLING` at EOS MUST emit here, or it is lost (a correctness bug per LOCKED
#8, not an optimization).

Risk to test (the brief's "what must change so nothing is lost"): the base only emits a `WINDOW_FILLING`
window once `numberOfActiveInputPipelines` has decremented to 0 (`DefaultTimeBasedSliceStore.cpp:179-205`).
With a large L the backlog can be large; the existing single-pipeline drain pattern
(`incrementNumberOfInputPipelines()` once, then one `getAllNonTriggeredSlices()` call) already covers this
in the concurrency test (`…ConcurrencyTest.cpp:241, 438`). A NEW unit test should assert that with
`emit_lag > 0`, the deferred backlog that `getTriggerableWindowSlices` did NOT emit is fully returned by
`getAllNonTriggeredSlices` (see Test plan T-DRAIN below).

---

## 4. The exception / error contract

ONE concrete exception type `NES::Exception`; named "exceptions" are factory functions carrying an
`ErrorCode` (project memory `nes_cpp_idioms`). The spill error is generated from:

```
// nes-common/include/ExceptionDefinitions.inc:89
EXCEPTION(SpillStoreFailure, 3014, "spill store failure")
```

Usage in the store (the ONLY throw of it): the async-probe-drain guard in `forceSpill`
(`SpillableTimeBasedSliceStore.cpp:437-438`):
```cpp
throw SpillStoreFailure(
    "refusing to spill slice {} that has been emitted to the probe (async-probe-drain invariant)", sliceEnd);
```
Tested via the macro (NOT `EXPECT_THROW` — that does not compile for factory-fn exceptions):
```cpp
// SpillableTimeBasedSliceStoreTest.cpp:309
ASSERT_EXCEPTION_ERRORCODE(store->forceSpill(sliceEnd), ErrorCode::SpillStoreFailure);
```

`PRECONDITION` / `INVARIANT` call `std::terminate()` (uncatchable) — used for "must never happen"
contracts (e.g. `INVARIANT(result.size() == 1, ...)`, `SpillableTimeBasedSliceStore.cpp:165;
`PRECONDITION(this->backend != nullptr, ...)`, `:112`). Eviction-path spill failures are SWALLOWED
(O-B5: never throw on the background/worker eviction path — `spillOneColdSlice` catches `std::exception`,
`:585-589`); only `forceSpill` re-throws.

**Relevance to Increment C: essentially NONE.** Emit-decouple introduces no new I/O and no new failure
mode — it only changes the watermark fed to the base emit cutoff. No new `EXCEPTION(...)` line is needed.
The existing `SpillStoreFailure` + the swallow-on-eviction discipline cover the (unchanged) spill paths.
The only new INVARIANT worth considering is a defensive `emit_lag` sanity clamp (negative/underflow when
`globalWatermark < L`), which should be a saturating subtract (clamp to 0), NOT a thrown error.

---

## 5. Existing test patterns (the template for new tests)

Both test files share the same shape; reuse it verbatim.

**Fixture base:** `class …Test : public NES::Testing::BaseUnitTest`
(`SpillableTimeBasedSliceStoreTest.cpp:93`; `…ConcurrencyTest.cpp:96`). `#include <BaseUnitTest.hpp>`
(from `nes-test-util`). `SetUpTestSuite()` calls `Logger::setupLogging(...)`; `SetUp()` calls
`BaseUnitTest::SetUp()` then `bufferManager = BufferManager::create();`
(`Test.cpp:96-106`).

**Store construction (the canonical builder):** `makeStoreWithThresholds(soft, hard, &backendOut)`
(`Test.cpp:146-162`):
```cpp
auto backend = std::make_unique<InMemorySpillBackend>();
*backendOut = backend.get();
auto store = std::make_unique<SpillableTimeBasedSliceStore>(
    WINDOW_SIZE, WINDOW_SLIDE, SliceCacheConfiguration{}, std::move(backend),
    entriesPerPage(), PAGE_SIZE, softThresholdBytes, hardThresholdBytes);
store->setBufferProvider(bufferManager);   // also starts the background spiller (PR-3 lifecycle)
return store;
```
Variants: `makeStore` (large thresholds ⇒ no auto-eviction, `:137-142`),
`makeStoreWithBackend` (caller-supplied backend, e.g. `ThrowingSpillBackend`, `:167-181`).
**For Increment C the ctor gains an `emitLag` parameter** — the builders just append it (default 0).

**Backends available:** `InMemorySpillBackend` (`SliceStore/InMemorySpillBackend.hpp`) with inspection
helpers `contains(sliceEnd, workerThreadId)`, `sliceCount()` (used at `Test.cpp:254-255, 421-424`);
`ThrowingSpillBackend` (test-local, `Test.cpp:57-88`, `failPut`/`failGet`/`putCount`/`getCount` atomics)
for I/O-failure injection; `ThreadSafeInMemorySpillBackend` (mutex-wrapped, `…ConcurrencyTest.cpp:59-83`)
for concurrency.

**createNewSlice callback:** `spillableSliceFactory()` builds a `SpillableAggregationSlice`
(`Test.cpp:131-135`):
```cpp
return [](SliceStart s, SliceEnd e) -> std::vector<std::shared_ptr<Slice>>
{ return {std::make_shared<SpillableAggregationSlice>(s, e, makeArgs(), NUMBER_OF_HASH_MAPS)}; };
```
`makeArgs()` builds `CreateNewHashMapSliceArgs` with a no-op Nautilus cleanup (`Test.cpp:123-128`).

**Inserting slices / tuples:**
- A slice: `store->getSlicesOrCreate(Timestamp(ts), spillableSliceFactory())[0]` — tumbling ⇒ exactly one.
  `Timestamp(50)` lands in window `[0,100)`, `Timestamp(150)` in `[100,200)`, etc.
  (`WINDOW_SIZE == WINDOW_SLIDE == 100`).
- Tuples into a slice's per-worker map: `populate(slice, WorkerThreadId(0), count, offset)`
  (`Test.cpp:183-198`) — `getHashMapPtrOrCreate` then `insertEntry(hash, bufferManager.get())` and memcpy
  an 8-byte key + 8-byte value into the payload. Footprint math: `entriesPerPage == PAGE_SIZE/entrySize ==
  256/32 == 8`; each populated map rounds up to whole pages × PAGE_SIZE.

**Driving triggering / spill (the test hooks — public, no production behavior):**
- `setLastSeenWatermarkForTest(Timestamp)` (`…Store.hpp:155`, impl `:673-677`) — seed the learned
  watermark directly so a slice can be COLD WITHOUT being emitted (the base feeders couple
  watermark-advance to emit; this is the only way to a cold-but-unemitted slice). This is the manual
  analog of the production emit-decouple.
- `forceSpill(SliceEnd)` (`…Store.hpp:136`) — explicit cold-slice spill.
- `spillColdSlicesUnderHardForTest()` (`…Store.hpp:160`, impl runs `evictUntilUnderHard`) — drive the
  synchronous hard eviction policy without the background thread.
- `getTriggerableWindowSlices(Timestamp(WINDOW_SIZE*2))` — drive the emit/trigger path directly.
- `getAllNonTriggeredSlices()` (after `incrementNumberOfInputPipelines()`) — the terminate/drain path.

**Asserting spill:**
- residency: `store->isSliceResident(sliceEnd)` (true unless spilled).
- footprint: `store->residentBytes()` (store-wide Σ) and `store->residentBytesOf(*slice)` (per slice; 0 if
  spilled).
- spill count: `store->spillCountForTest()` (monotonic ">= finalized spills"; assert `> 0`, never an exact
  equality tied to spilledKeys size — PR-2 L3 caveat, `Test.cpp:516-518`, `…ConcurrencyTest.cpp:452`).
- created-slice tracking: `store->createdSlicesSizeForTest()`.
- backend inspection: `backend->contains(sliceEnd, WorkerThreadId(0))`, `backend->sliceCount()`.

**Correctness vs liveness (concurrency idiom):** ALWAYS-ON correctness = checksum equality (UAF / lost-
update detector) + every-returned-slice-resident (async-probe-drain). NEVER hard-assert background-thread
LIVENESS (`EXPECT_GT(spillCount,0)` is starvable under `-j8`); make "work-happened" coverage deterministic
by driving the SYNCHRONOUS path (`spillColdSlicesUnderHardForTest`) on a non-starvable thread, not by
hoping the jthread runs (`…ConcurrencyTest.cpp:204-457`, project memory Increment B idioms).

**Checksum-equality drain template (directly reusable for the differential test):** the concurrency test
already proves "Σ(triggered + drained) == Σ(inserted)" with a deterministic per-(worker,key) payload
`valueFor(worker,key) = (worker+1)*1000003 + key` (`…ConcurrencyTest.cpp:157-201, 277-313, 438-443`). It
ALSO already simulates the lagged emit watermark by hand: a SPILL HORIZON leads via
`setLastSeenWatermarkForTest` while an EMIT HORIZON lags by `SPILL_LEAD` windows via
`getTriggerableWindowSlices` (`…ConcurrencyTest.cpp:379-425`). This is the manual analog of production
`emit_lag` and is the proof-of-concept that decoupling does not change the summed result.

**Registration:** `nes-physical-operators/tests/CMakeLists.txt:13-17` defines
`add_nes_physical_operator_test(TARGET_NAME source.cpp)` (links `nes-data-types nes-physical-operators
nes-memory-test-utils nes-test-util`). Existing entries at `:28-29`. New tests added to the SAME
`SpillableTimeBasedSliceStoreTest.cpp` need NO CMake change; a new file needs one new
`add_nes_physical_operator_test(...)` line.

---

## 6. The differential test design (OPEN #5) — emit_lag=0 vs >0 ⇒ identical result rows

**Goal:** prove deferring emit changes only TIMING, not VALUES. Smallest construction that proves it
WITHOUT the heavy Systest label: a pure unit test in `SpillableTimeBasedSliceStoreTest.cpp` that runs the
SAME deterministic insert stream through TWO stores (L=0 and L>0) and compares the aggregate captured at
emit time.

**Assertion approach — capture emitted slice contents, compare per-key aggregates.** The store's emit unit
is the trigger map `std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>` returned by
`getTriggerableWindowSlices` / `getAllNonTriggeredSlices` (this IS the `EmittedAggregationWindow` payload
upstream). For each emitted slice we fold its per-worker map into a per-key (or summed) checksum, EXACTLY
as `checksumOfMap`/`sortedEntries` already do. The differential invariant:

> For the identical insert stream, the MULTISET of emitted (windowEnd → per-key value) tuples is identical
> between L=0 and L>0. Only the watermark at which each window is emitted differs.

**Construction (concrete):**
1. Two stores from `makeStore` (large thresholds so spill is incidental to the comparison, OR small
   thresholds for the spill-engages variant — see T-DIFF-SPILL below): `storeE0` with `emitLag = 0`,
   `storeEL` with `emitLag = L` (e.g. `L = 3 * WINDOW_SIZE`).
2. Drive BOTH with the identical sequence: for window index `w = 0..N-1`, create the slice at
   `Timestamp((w+1)*WINDOW_SIZE - 1)` and `populate` it with the SAME deterministic key/value payload
   (reuse `valueFor`-style determinism). After each window, advance the watermark in BOTH stores to the
   same TRUE frontier `(w+1)*WINDOW_SIZE + 1` by calling `getTriggerableWindowSlices(Timestamp(frontier))`.
3. Capture every emitted slice's folded per-key aggregate into `emittedE0` / `emittedEL` maps keyed by
   `windowEnd`. At each step, `storeE0` will have emitted window `w` (its windowEnd < frontier), while
   `storeEL` will have emitted only windows with `windowEnd < frontier − L` (the lagged band stays
   retained).
4. Drain both at EOS: `incrementNumberOfInputPipelines()` once per store up front, then
   `getAllNonTriggeredSlices()` at the end; fold the drained slices into the same maps. The drain flushes
   `storeEL`'s deferred backlog (LOCKED #8) so both stores end having emitted ALL N windows.
5. **Assert `emittedE0 == emittedEL`** (same set of windowEnds, same per-key folded values).
   Optionally assert that AT A MID-STREAM CHECKPOINT, `storeEL` had emitted strictly FEWER windows than
   `storeE0` (proves the lag actually deferred — timing differs).

This needs only the unit-test machinery already present + the new `emitLag` ctor param. No Systest, no
real query, no Nautilus.

**Why this is sufficient:** the base store's correctness (which per-key value lands in which window) is
independent of the watermark — the watermark only gates WHEN a window is handed off. The folded aggregate
captured at emit/drain time is the exact payload the probe would receive. Equal multisets ⇒ identical
result rows (order/timing aside), which is the headline gate.

---

## 7. Test for "spilling now engages" (E0 empty-set vs EL non-empty)

**Goal:** prove that with `emit_lag > 0` the cold/spillable set becomes NON-EMPTY and the spiller acts,
versus the E0 case where `{cold AND not-emitted}` is provably empty.

**The cleanest proof uses the production override directly** (no `setLastSeenWatermarkForTest` seeding —
that hook BYPASSES the very coupling the increment fixes; using it would not prove the production decouple).
Construction:
1. `storeEL = makeStoreWithThresholds(soft, hard, &backend)` with `emitLag = L = k*WINDOW_SIZE` and tiny
   thresholds (e.g. soft = PAGE_SIZE, hard = 2*PAGE_SIZE).
2. Create + populate `N >> k` tumbling windows. Advance the TRUE watermark via
   `getTriggerableWindowSlices(Timestamp(frontier))` to a frontier `F` such that the band `[F−L, F)`
   contains several COMPLETED-but-unemitted windows.
3. After the trigger call, the override's body has: emitted only windows `< F − L` (lagged cutoff in the
   base call), but advanced `lastSeenWatermark = F` (true). So windows in `[F−L, F)` are
   COLD (`windowEnd < lastSeenWatermark`) AND NOT emitted ⇒ the spillable set is NON-EMPTY.
4. **Assert spilling engages:** drive the synchronous path `spillColdSlicesUnderHardForTest()` (non-
   starvable, deterministic) and assert `store->spillCountForTest() > 0`, `backend->sliceCount() > 0`, and
   that at least one window in the retained band is now non-resident
   (`!store->isSliceResident(bandWindowEnd)`).
5. **Contrast / regression (E0):** the SAME drive with `emitLag = 0` ⇒ the lagged cutoff equals the true
   frontier ⇒ every window `< F` is BOTH cold AND emitted ⇒ the eviction scan's `!emittedKeys` filter
   rejects them all ⇒ `spillCountForTest() == 0` and `backend->sliceCount() == 0`. This is the empty-set
   proof and doubles as the LOCKED #6 regression guard.

The eviction scan that this exercises is `pickAndReserveColdSlice` (`SpillableTimeBasedSliceStore.cpp:469-
537`): it skips `end >= lastSeenWatermark` (`:498`), in-flight (`:506`), expired (`:511`), non-resident
(`:523`), and emitted (`:527`). With `emit_lag > 0` the band `[F−L, F)` passes ALL of these; with
`emit_lag == 0` it fails `:527` (emitted). This test is the direct unit proof of the E0→EL transition the
whole increment exists to make.

---

## Test plan skeleton

All tests live in
`…/nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (registered by the EXISTING line
`add_nes_physical_operator_test(SpillableTimeBasedSliceStoreTest SpillableTimeBasedSliceStoreTest.cpp)` at
`nes-physical-operators/tests/CMakeLists.txt:28` — NO new CMake line needed). The differential test MAY
optionally go in a new `EmitDecoupleDifferentialTest.cpp` if isolation is preferred, which WOULD need a new
`add_nes_physical_operator_test(EmitDecoupleDifferentialTest EmitDecoupleDifferentialTest.cpp)` line at
`CMakeLists.txt:30`; default recommendation is to keep them in the existing file.

Prereq for all: the `SpillableTimeBasedSliceStore` ctor gains a trailing `uint64_t emitLag` parameter
(default-applied as 0 in the test builders `makeStore`/`makeStoreWithThresholds`/`makeStoreWithBackend`).

1. **`emitLagZeroEmitsIdenticallyToBaseline`** (LOCKED #6 regression net) — with `emitLag = 0`, the set
   of windowEnds emitted by `getTriggerableWindowSlices(F)` at every frontier F is byte-identical to a
   plain `DefaultTimeBasedSliceStore` (or to the pre-existing `unspillOnTriggerableReadRestoresEntries`
   behavior). Asserts: same emitted windowEnds, same folded per-key values, `spillCountForTest()==0` when
   thresholds are large. Registered by `CMakeLists.txt:28`.

2. **`emitLagDefersEmissionByLagBand`** (timing, OPEN #5 half 1) — with `emitLag = L`, after advancing the
   true watermark to F, `getTriggerableWindowSlices` emits ONLY windows with `windowEnd < F − L`; windows
   in `[F−L, F)` are NOT in the returned map (still retained). Asserts: the returned trigger map excludes
   the lagged band; `createdSlicesSizeForTest()` still counts them (retained, not GC'd). `CMakeLists.txt:28`.

3. **`emitLagDifferentialIdenticalResults`** (THE headline gate, OPEN #5 half 2) — two stores (L=0, L>0)
   driven by the identical deterministic insert+watermark stream; capture every emitted+drained slice's
   folded per-key aggregate. Asserts: `emittedE0 == emittedEL` (identical multiset of (windowEnd → per-key
   value)); plus a mid-stream checkpoint where `storeEL` has emitted strictly fewer windows than `storeE0`
   (proves only timing differs). `CMakeLists.txt:28` (or new line `:30` if split out).

4. **`emitLagBacklogFullyFlushedOnTerminate`** (LOCKED #8) — with `emitLag = L`, build N windows, advance
   the true watermark high but NOT call enough triggers to emit the lagged band; then
   `incrementNumberOfInputPipelines()` + `getAllNonTriggeredSlices()`. Asserts: the drain returns EVERY
   deferred window (the whole `[*, F)` band, none lost), each returned slice resident (unspilled), and the
   union of (triggered ∪ drained) windowEnds == all N created windows. `CMakeLists.txt:28`.

5. **`emitLagMakesColdSetNonEmptyAndSpiller Engages`** (§7, the E0→EL proof) — `emitLag = L`, tiny
   thresholds; after advancing the true watermark to F via the PRODUCTION `getTriggerableWindowSlices`
   (NO `setLastSeenWatermarkForTest`), the retained band `[F−L, F)` is cold-AND-unemitted. Asserts:
   `spillColdSlicesUnderHardForTest()` ⇒ `spillCountForTest() > 0`, `backend->sliceCount() > 0`, at least
   one band window non-resident. `CMakeLists.txt:28`.

6. **`emitLagZeroColdSetEmptyNoSpill`** (§7 contrast / E0 empty-set proof, LOCKED #6) — same drive with
   `emitLag = 0`: every window `< F` is cold AND emitted ⇒ the `!emittedKeys` filter rejects all ⇒
   `spillColdSlicesUnderHardForTest()` ⇒ `spillCountForTest() == 0`, `backend->sliceCount() == 0`. This is
   the direct unit reproduction of the E0 root cause and proves the fix is what makes spilling engage.
   `CMakeLists.txt:28`.

7. **`emitLagUnderflowClampsToZeroEmit`** (defensive boundary) — when the true watermark F < L (early in
   the stream), the lagged emit watermark must clamp to `Timestamp(0)` (saturating subtract), emitting
   NOTHING rather than underflowing. Asserts: `getTriggerableWindowSlices(Timestamp(small))` with large L
   returns an EMPTY map and throws/terminates nowhere. `CMakeLists.txt:28`.

8. **(Concurrency, optional escalation)** `emitLagConcurrentDifferentialChecksum` — add an `emitLag`-driven
   variant to `…ConcurrencyTest.cpp` that removes the manual `setLastSeenWatermarkForTest` horizon and
   instead lets the production `emitLag` create the cold band, reusing the existing
   Σ(triggered+drained)==Σ(inserted) checksum invariant (`…ConcurrencyTest.cpp:442`). Asserts: checksum
   equality (no tuple lost/double-counted under the production decouple) + `spillCountForTest() > 0`.
   Registered by the EXISTING `CMakeLists.txt:29`. Mark as the ESCALATE gate; keep the synchronous
   non-starvable drive for the deterministic spill-happened assertion (do NOT hard-assert jthread liveness).
