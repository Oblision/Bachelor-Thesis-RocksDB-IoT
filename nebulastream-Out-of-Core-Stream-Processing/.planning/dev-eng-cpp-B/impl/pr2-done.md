# PR-2 done — cold-slice eviction policy + synchronous hard spill

**Commit:** `dec5f95d62752a92972ad5010a151290e5b8d922`
**Branch:** `feat/ooc-phase1-spill-skeleton` (parent `a00d9d65d7`, PR-1)
**Files (only these three):**
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`

Build/test: Docker `nebulastream/nes-development:local-rocksdb`, `cmake-build-debug` only.

---

## TDD: RED

Wrote the PR-2 test cases + `makeStoreWithThresholds(soft, hard, backendOut)` helper FIRST, then built
`SpillableTimeBasedSliceStoreTest`. Compile FAILED as expected (eviction test hooks absent):

```
error: no member named 'spillColdSlicesUnderHardForTest' in 'NES::SpillableTimeBasedSliceStore'
error: no member named 'spillCountForTest' in 'NES::SpillableTimeBasedSliceStore'
9 errors generated.
ninja: build stopped: subcommand failed.
```

## TDD: GREEN

Implemented PR-2 production (members + 4 eviction methods + 2 test hooks + forceSpill serializer) and the
M1/M2/L2/L3 + coverage fixes. Target compiled clean under `-Werror`
(`-Woverloaded-virtual`, `-Wunused-private-field` ON), then:

```
ctest -R 'SpillableTimeBasedSliceStoreTest' --output-on-failure
100% tests passed, 0 tests failed out of 16
```

16 = 8 PR-1 (unchanged behavior) + 8 new:
- PR-2 eviction (per PLAN Section 2): `hardEvictsOldestColdFirst`, `hardNoColdCandidateProceeds`,
  `hardStillOverAfterAllColdSpilled`, `emittedSliceNeverSpilledUnderEviction`.
- Double-spill idempotent assertion: `doubleForceSpillCountsOnce` (forceSpill twice → `spillCount == 1`).
- Coverage gaps (PR-1 review): `deleteStateClearsCreatedSlices`, `recordCreatedSliceIdempotent`
  (two getSlicesOrCreate same ts → `createdSlicesSizeForTest() == 1`), `residentBytesEmptyStoreIsZero`.

## Module regression

- Full `ninja` → exit 0 (46/46 link/build steps).
- `ctest -R 'SpillableTimeBasedSliceStore|SpillableAggregationSlice|SliceSerializer|SpillBackend|RocksDB|LowerToPhysicalWindowedAggregation'`
  → **100% passed, 0 failed out of 57**.

## PR-1 review findings cleared

- **M1 (load-bearing):** `recordCreatedSlice` now returns `bool` (true only on a true `insert_or_assign`
  insert via `.second`). `getSlicesOrCreate` gates the new `residentBytes()`/`evictUntilUnderHard()` check
  inside `if (recordCreatedSlice(...))`, so eviction never runs on a SliceCache-hit / insert-race
  re-entry. The `NES_TRACE` moved inside the `if (inserted)` branch (nit cpp:107 also cleared).
  Direct test: `recordCreatedSliceIdempotent`.
- **M2:** `INVARIANT(result.size()==1)` comment tightened to "tumbling window ⇒ always exactly one slice
  per timestamp (new or existing — also runs on a SliceCache hit), got {}".
- **L2:** added the memory-order rationale comment on `lastSeenWatermark` ("lagging value only
  under-selects cold slices → safe; relaxed intentional, do NOT strengthen to seq_cst").
- **L3:** `setLastSeenWatermarkForTest` header comment now names the PR-2 eviction tests that exercise it.
- **L1 (opportunistic):** removed the unused `<algorithm>` include from the `.cpp` (no `std::` algorithm
  used in PR-1 or PR-2). Added `<mutex>` to both header and `.cpp` for `evictionMutex`.
- **Coverage gaps:** all three added (`deleteStateClearsCreatedSlices`, `recordCreatedSliceIdempotent`,
  `residentBytesEmptyStoreIsZero`).
- L4 / remaining nits: not addressed (non-blocking; `gcPrunesCreatedSlices` is a PR-1 test untouched).

## Scope adherence / deviations

- Implemented EXACTLY PR-2: added ONLY members PR-2 reads — `evictionMutex`, `spillInProgress`,
  `spillCount`. Deliberately did NOT add the PR-3-only members (`spillerCv`, `quiesced`,
  `spillerStarted`, `std::jthread spiller`) because `-Wunused-private-field` is ON and they are unread
  this PR. No thread, no condvar, no trigger-wait, no soft-signal, no lifecycle — all deferred to PR-3.
- `finalizeSpill` does NOT call `spillerCv.notify_all()` (the PLAN's PR-3 form); PR-2 form only inserts
  into `spilledKeys` and clears the reservation, per the task spec.
- `getSlicesOrCreate` adds ONLY the hard check; the `else if (soft)` notify is left for PR-3.
- **lockSpillable inlined:** the PLAN pseudocode references a `lockSpillable(*picked)` helper that does
  not exist in the file. Inlined the equivalent (base `getSliceBySliceEnd` + `dynamic_pointer_cast`) in
  `spillOneColdSlice`; on an expired/missing slice between reserve and lookup it takes `evictionMutex`,
  erases the reservation, and returns true (skip-as-progress) exactly as specified. Justification: avoids
  adding an unused helper and keeps the I/O-outside-lock contract.
- **forceSpill:** now wraps emitted-check → in-flight-check → reserve under `evictionMutex`, spills
  OUTSIDE the lock, then `finalizeSpill` (closes Increment A's M1 TOCTOU). Public contract preserved
  exactly (throws `SpillStoreFailure` on emitted; no-op on unknown/already-spilled). Increments
  `spillCount` on a real spill only. Added an in-flight-reservation guard so a concurrent eviction +
  forceSpill of the same slice cannot double-write (covered structurally; PR-3 stress test will exercise).

## LOCKED invariants honored

- All RocksDB I/O (`spill()`) OUTSIDE `evictionMutex`; the mutex covers only in-memory decisions.
- Lock order `evictionMutex → (createdSlices | one folly set)`; `pickAndReserveColdSlice` snapshots
  `createdSlices` under one rlock, releases it, THEN evaluates the folly-set predicates (no nesting); no
  base map taken under `evictionMutex`.
- O-B5: over-hard-no-cold and hard-still-over-after-all-cold → proceed + `NES_WARNING`, never throw,
  never block. Tested by `hardNoColdCandidateProceeds` and `hardStillOverAfterAllColdSpilled`.
- Tumbling only, no base-class edit, no thread/condvar/lifecycle.

---

## PR-2 review fix iteration 1 (Phase 6c)

Cleared ALL findings from `reviews/PR2-REVIEW.md` in a NEW stacked fix commit (no amend/push). Touched only
the store hpp/cpp + the deterministic test file.

- **H1** (reservation leak on `spill()` throw in `spillOneColdSlice`): wrapped the I/O in a try/catch +
  an RAII `ReservationGuard` (private nested type, defined in the .cpp; releases the reservation under
  `evictionMutex` via the new private `releaseReservation` unless `committed`). On throw: `NES_WARNING`,
  reservation released, `return false` (O-B5 swallow — eviction never propagates). Success path unchanged
  (`++spillCount; finalizeSpill; committed=true`).
- **H2** (same leak in `forceSpill`): same `ReservationGuard`, but PRESERVES the throwing contract — the
  guard releases the reservation and the exception RE-THROWS. The pre-insert `SpillStoreFailure` for
  emitted slices is untouched (throws before reserving).
- **M1** (`continue`→`break`): in `pickAndReserveColdSlice` the coldness failure (`end >= wm`, ascending)
  now `break`s; every OTHER predicate (in-flight / expired / non-spillable / non-resident / emitted) still
  `continue`s. Comment rewritten to state the distinction precisely.
- **M2** (`residentBytes()` under lock in `finalizeSpill`): scoped `evictionMutex` to the in-memory
  mutation only; log is now `NES_DEBUG("Spilled cold slice {}", end)` outside the lock.
- **M3**: added the PR-3-obligation comment on `unspillAndMarkEmitted` (evictionMutex wrap + spill-in-progress
  WAIT is PR-3's job; PR-2 hardened only `forceSpill`). No behavior change.
- **Lock-order nit (VERIFIED + FIXED)**: `DefaultTimeBasedSliceStore::getSliceBySliceEnd`
  (`DefaultTimeBasedSliceStore.cpp:143`) DOES acquire the base `slices.rlock()`. `forceSpill` previously
  called it UNDER `evictionMutex` → violated "base slices/windows NEVER under evictionMutex". FIX: the slice
  lookup now happens BEFORE acquiring `evictionMutex`; the mutex covers only the emitted-check + in-flight-check
  + reserve (the returned `shared_ptr` keeps the slice alive across the critical section). `spillOneColdSlice`
  already called `getSliceBySliceEnd` outside the mutex (after the pick releases it) — left as-is.
- **L3**: added comments in both `spillOneColdSlice` and `forceSpill` that `++spillCount` advances just
  before `finalizeSpill`, so PR-3 must assert `spillCountForTest() > 0`, not exact equality.
- **Nits**: dropped the redundant `EXPECT_LE` in `hardEvictsOldestColdFirst`; added the L1 dual-residency
  justification comment + the snapshot-under-lock confirming comment in `pickAndReserveColdSlice`; fixed the
  `mutable evictionMutex` comment in the header.

**Regression tests added** (in `SpillableTimeBasedSliceStoreTest.cpp`): a minimal `ThrowingSpillBackend`
(`put()` throws while `failPut` set; toggle is clean). `forceSpillReleasesReservationWhenSpillThrows`
(H2: forceSpill rethrows, then a retry succeeds → reservation was released, `putCount==2`) and
`evictionReleasesReservationAndSwallowsWhenSpillThrows` (H1: eviction does NOT throw, `spillCount==0`,
slice still resident, then forceSpill succeeds → reservation released).

**Build/test (Docker `nebulastream/nes-development:local-rocksdb`, `cmake-build-debug`):** `ninja` clean
(exit 0, `-Werror`). Gate suite
`SpillableTimeBasedSliceStore|SpillableAggregationSlice|SliceSerializer|SpillBackend|RocksDB|LowerToPhysicalWindowedAggregation`
= **59/59 passed** (≥57 prior; +2 new regression tests). `SpillableTimeBasedSliceStoreTest` itself 18/18 (was 16).
