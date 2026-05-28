# PR-1 done — enumeration + budget (single-thread, no eviction)

Increment B, Section 1 **PR-1** (`SpillableTimeBasedSliceStore` self-tracking + store-wide
`residentBytes()`) + Section 2's two PR-1 test cases. Strict TDD, Docker-only build/test.

- **Branch:** `feat/ooc-phase1-spill-skeleton`
- **Baseline commit:** `6a6ec7301b385b22af3276e9e19d77fb290a64a9`
- **PR-1 commit:** `a00d9d65d750f9e954041c4e782a3ad8d6621c4f`
- **Files touched (only these three):**
  - `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
  - `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
  - `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`
- **Image / build dir:** `nebulastream/nes-development:local-rocksdb` / `cmake-build-debug`
  (verified healthy before starting: image present, ninja configured,
  `RocksDB_DIR=/vcpkg/installed/arm64-linux-none-local/share/rocksdb`).

## Scope implemented (exactly PLAN.md §1 PR-1 + §2 PR-1 cases)
Production:
- New members: `folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices;` and
  `std::atomic<Timestamp::Underlying> lastSeenWatermark{Timestamp(0).getRawValue()};`. (Mutex/condvar/
  jthread/spillInProgress/quiesced/spillerStarted deliberately NOT added — PR-2/PR-3.) Added includes
  `<atomic>`, `<cstddef>`, `<functional>` to the header; `<algorithm>`, `<atomic>`, `<cstddef>`,
  `<functional>` to the `.cpp`. (`<map>`/`<memory>` already present.)
- `getSlicesOrCreate` override — signature matches the base exactly; calls base, `INVARIANT(result.size()
  == 1)` (tumbling), `recordCreatedSlice(result[0])`, returns `result`. No budget/eviction (PR-2).
- `recordCreatedSlice` — `createdSlices.wlock()->insert_or_assign(slice->getSliceEnd(),
  std::weak_ptr<Slice>(slice));` plus an `NES_TRACE`.
- Watermark learning — in `getTriggerableWindowSlices`, after `unspillAndMarkEmitted`, CAS-max
  `lastSeenWatermark` to `max(current, globalWatermark.getRawValue())`. `getAllNonTriggeredSlices` left
  unchanged with a documenting comment (terminate path; cold-selection moot).
- `residentBytes()` (public, `[[nodiscard]]`) — Σ over `createdSlices` (rlock, `weak.lock()`, skip
  expired) of the existing `residentBytesOf(*s)`. No `evictionMutex` (does not exist this PR).
- `garbageCollectSlicesAndWindows` — existing behavior kept; then a separate `createdSlices.wlock()`
  (NOT nested with the spilled/emitted set locks, taken after they are released) erases expired weak_ptr
  entries.
- `deleteState` — existing behavior kept; then `createdSlices.wlock()->clear()` (no spiller-quiesce yet).
- Test hooks (public, marked "PR-1 test hooks"): `setLastSeenWatermarkForTest(Timestamp)`,
  `[[nodiscard]] std::size_t createdSlicesSizeForTest() const`. (spillColdSlicesUnderHardForTest /
  spillCountForTest NOT added — PR-2/PR-3.)
- Class doc comment updated to describe the PR-1 self-tracking layer (full B model is PR-3).

Tests added to `SpillableTimeBasedSliceStoreTest.cpp` (PLAN.md §2 PR-1 cases only):
- `residentBytesIsStoreWideSum`
- `gcPrunesCreatedSlices`
No `makeStoreWithThresholds` overload was needed — both cases reuse the existing `makeStore` fixture and
helpers, so it was not added (avoiding an unused helper / dead code).

## RED evidence
Wrote the two test cases first, then built the test target:
`DRUN "set -o pipefail; cd cmake-build-debug && ninja SpillableTimeBasedSliceStoreTest 2>&1 | tail -40"`
→ **Exit 1**, 6 compile errors (production methods absent), e.g.:
```
SpillableTimeBasedSliceStoreTest.cpp:315:22: error: no member named 'createdSlicesSizeForTest' in 'NES::SpillableTimeBasedSliceStore'
SpillableTimeBasedSliceStoreTest.cpp:316:22: error: no member named 'residentBytes' in 'NES::SpillableTimeBasedSliceStore'
```
(6 errors = the missing `createdSlicesSizeForTest`/`residentBytes` references across the two methods.)
Tests were not weakened to compile — they assert the plan's exact behavior.

## GREEN evidence
After implementing the production code, rebuilt the test target → compiled clean under `-Werror`
(`-Woverloaded-virtual`, `-Wunused-private-field` on). Then:
`DRUN "cd cmake-build-debug && ctest -R 'SpillableTimeBasedSliceStoreTest' --output-on-failure"`
→ **100% tests passed, 0 failed out of 8** (6 pre-existing + the 2 new cases):
```
SpillableTimeBasedSliceStoreTest.residentBytesIsStoreWideSum ... Passed
SpillableTimeBasedSliceStoreTest.gcPrunesCreatedSlices ......... Passed
```

## Module regression
1. Full `DRUN "set -o pipefail; cd cmake-build-debug && ninja 2>&1 | tail -20"` → **exit 0** (46/46
   targets, includes the lowering rule + systest binaries).
2. `DRUN "cd cmake-build-debug && ctest -R 'SpillableTimeBasedSliceStore|SpillableAggregationSlice|SliceSerializer|SpillBackend|RocksDB|LowerToPhysicalWindowedAggregation' --output-on-failure"`
   → **100% tests passed, 0 failed out of 49**. No regressions.

## Deviations from PLAN.md (with justification)
- **Test hooks placed in the `public:` section** (plan says "private or public as needed for the test").
  The test fixture is not a `friend` of the store and calls `createdSlicesSizeForTest()` directly, so the
  hooks must be public — mirroring how the test already drives `setBufferProvider`/`forceSpill`. Clearly
  commented as PR-1 test hooks.
- **`makeStoreWithThresholds` overload not added.** PLAN.md gates it on "ONLY if needed for these two
  cases". Neither `residentBytesIsStoreWideSum` nor `gcPrunesCreatedSlices` needs custom thresholds
  (no eviction in PR-1), so adding it would be an unused helper (avoided per SC2 "don't gold-plate").
- **`setLastSeenWatermarkForTest` is unused by the two PR-1 tests.** It is required by the PR-1 hook list
  and will be exercised by PR-2 eviction tests. Unused *methods* do not trip `-Wunused-private-field`
  (that warns on fields only); the `lastSeenWatermark` *field* is read by the watermark CAS-max and the
  hook, so the build stays clean under `-Werror`.
- No other deviations. No core base-class edit; no lock nesting of `createdSlices` with
  `spilledKeys`/`emittedKeys`; no base `slices`/`windows` map touched under any new lock.
