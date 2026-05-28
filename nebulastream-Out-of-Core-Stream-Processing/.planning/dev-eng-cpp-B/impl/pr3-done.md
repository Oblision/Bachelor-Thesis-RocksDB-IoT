# PR-3 done — background spiller thread + concurrency stress test (Phase 4b-B, the ESCALATE gate)

**Branch:** `feat/ooc-phase1-spill-skeleton`
**Base HEAD before PR-3:** `c2f41c6d8c` (PR-1 + PR-2 + PR-2 fixes; 805 baseline + new tests green)
**Result:** GREEN. Genuine, reproducible stress-clean. No ESCALATE.

Files touched (the four allowed, no other production file):
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` (NEW)
- `nes-physical-operators/tests/CMakeLists.txt` (one registration line)

---

## TDD evidence

### RED
- New stress test compiled against PR-2 production (it uses only the existing public API:
  `setBufferProvider`, `getSlicesOrCreate`, `getTriggerableWindowSlices`, `getAllNonTriggeredSlices`,
  `deleteState`, `spillCountForTest`, `isSliceResident`).
- Run against PR-2 production → **ABORT** (Subprocess aborted): `INVARIANT (resident): getHashMapPtrOrCreate
  called on a spilled slice` — the spill-vs-builder/spill-vs-read UAF the PR-3 work must close. This is the
  expected RED behavioral failure (no live spiller / no trigger-WAIT in PR-2).

### GREEN
- Implemented PR-3 production per PLAN §1 PR-3.
- Full `ninja` → exit 0 (no `-Werror` / `-Wunused-private-field` / `-Woverloaded-virtual` failures; the seam
  header is included widely, so a full build was required and is clean).
- `ctest -R SpillableTimeBasedSliceStoreConcurrencyTest` → **Passed (0.47–0.49 s)**, well under the 120 s timeout.
- Internal iterations: **50** per process run (numIterations = 50). Each iteration = 8 builder jthreads + 1
  trigger jthread + the store-owned background spiller, with checksum + residency post-conditions.

### Stress confidence (process-level repeats)
- `ctest -R ... --repeat until-fail:10` → **10/10 Passed** (= 500 full scenario runs total). No flake, no
  deadlock, no checksum mismatch.

### Same-SliceEnd WAIT path coverage (the binding requirement)
Ran the binary once at `LOG_TRACE` and counted log lines:
- `"Trigger of slice ... waiting on in-flight spill"` (the `unspillAndMarkEmitted` → `spillerCv.wait` path):
  **35 hits** across the 50 internal iterations.
- `"Spilled cold slice ..."` (finalized spills): **2759**; `"Reserved cold slice ..."`: **2759**.
This proves the `spillInProgress` / `spillerCv` WAIT path is genuinely exercised, not bypassed. The in-test
assertion `EXPECT_GT(store->spillCountForTest(), 0)` holds every iteration (asserted `> 0`, NOT an exact count,
per PR2-REVIEW L3 — the counter advances just before `finalizeSpill`).
Log level reverted to `LOG_ERROR` afterwards.

### Module regression
- `ctest -R 'SpillableTimeBasedSliceStore|SpillableAggregationSlice|SliceSerializer|SpillBackend|RocksDB|LowerToPhysicalWindowedAggregation'`
  → **60/60 Passed** (incl. all PR-1/PR-2 deterministic tests, which now also exercise the changed
  `residentBytes()`).
- Full suite `ctest -LE 'Systest|DockerCompose'` → **818/818 Passed** (805 baseline + new tests incl. the 1
  new concurrency test; the new test ADDS to the count, no existing test changed behavior). 1 pre-existing
  Disabled test did not run (unchanged).
- `clang-format-19 --dry-run --Werror` on all three source files → clean.

---

## How the stress test forces the collision (no-late-data honoured)

The same-SliceEnd spill-vs-trigger collision needs the spiller to be MID-spilling a slice the trigger then
emits. But the trigger feeder only marks a slice cold (advances the learned watermark) AFTER it has already
emitted it, so emission and coldness coincide and the spiller would always lose. The test DECOUPLES the two
cadences:
- a **spill horizon leads**: once all 8 builders finish window h (per-window `doneCount` == NUM_BUILDERS), the
  trigger seeds `lastSeenWatermark` past it via `setLastSeenWatermarkForTest` (the documented hook for "a
  cold-but-unemitted slice"). Windows in `[emitHorizon, spillHorizon)` are then COLD AND NOT YET EMITTED → the
  background spiller / hard path actively spill them.
- an **emit horizon lags** by `SPILL_LEAD = 4` windows: `getTriggerableWindowSlices` then emits those same
  windows, frequently catching a slice mid-spill → `unspillAndMarkEmitted` hits `spillInProgress` and WAITs.

No builder ever writes a cold window: the horizon only ever passes windows ALL builders have finished, and each
builder writes each window exactly once on a strictly-increasing per-thread timestamp stream (INV-3 respected).

UAF detector: Σ(triggered + drained per-worker checksums) == Σ(inserted contributions), computed up front and
deterministic; double-count guarded by a per-window `windowTriggered` flag. Each worker owns a distinct
`WorkerThreadId` and `NUMBER_OF_HASH_MAPS == NUM_BUILDERS == 8`, so two builders never write the same
ChainedHashMap (that would be a data race independent of the store).

---

## Production changes (per PLAN PR-3) + the deviations

Header: dropped `[[maybe_unused]]` from `softThresholdBytes`; added `spillerCv`, `quiesced`,
`spillerStarted`, and `std::jthread spiller` (declared LAST); added `~SpillableTimeBasedSliceStore()` and the
`startSpiller`/`stopSpiller`/`spillerLoop` declarations; updated the class doc to the PR-3 model. Includes
`<chrono> <condition_variable> <stop_token> <thread>`.

`.cpp`: file-local `constexpr std::chrono::milliseconds SPILLER_POLL_INTERVAL{100}`; dtor calls `stopSpiller()`
first; `setBufferProvider` ends with `startSpiller()`; `getSlicesOrCreate` adds the soft `spillerCv.notify_one()`
on the not-over-hard branch; `unspillAndMarkEmitted` rewritten to the WAIT→claim-emitted→unspill-outside form;
trigger feeder notifies after the watermark CAS-max; `pickAndReserveColdSlice` returns nullopt when `quiesced`;
`finalizeSpill` notifies; `spillerLoop`/`startSpiller`/`stopSpiller` added; `deleteState` quiesces first.

### Deviations / extra fixes (all within the 4-file scope, all justified)

1. **`residentBytes()` made concurrency-safe (REQUIRED — exposed by the stress test, not optional).**
   The PLAN's lock-free `residentBytes()` reads each slice's `hashMaps` via `residentBytesOf` →
   `getHashMapPtr`/`getNumberOfTuples`. Under concurrency a background spill/unspill mutates+frees those maps
   and flips `resident`, tripping the hard `INVARIANT(resident)` and racing the map free — a genuine data
   race, not mere staleness. Fix: `residentBytes()` now takes `evictionMutex` (lock order evictionMutex →
   createdSlices, the legal order), skips any slice currently in `spillInProgress` (footprint transitioning),
   and reads the stable, non-reserved slices' footprint UNDER the lock. An unreserved slice cannot transition
   into `spillInProgress` (and thus cannot begin a concurrent spill/unspill that frees its maps) while the lock
   is held, so the read is race-free. This is a strict superset of the M2 contention concern (review M2 only
   forbade the scan inside `finalizeSpill`; reading it from the dedicated `residentBytes()` accessor is fine).

2. **`spillerLoop` does NOT call `residentBytes()` under `evictionMutex`** (consequence of #1, else
   self-deadlock on the non-recursive mutex). The bounded `wait_for` predicate now only checks the stop token;
   the budget is evaluated after the lock is released. The condvar is still primary (soft signal +
   trigger-feeder notify wake it immediately); the poll is the safety net per reconciled divergence 2.

3. **Unspill-on-read paths reserve in `spillInProgress` for the I/O duration** (`unspillAndMarkEmitted` and the
   `getSliceBySliceEnd` override). The unspill mutates the slice's maps OUTSIDE the lock; reserving it makes a
   concurrent `residentBytes()` skip it (the footprint-reader fence) and lets the existing condvar wake
   waiters. `getSliceBySliceEnd` additionally WAITs out any in-flight spill first (it is not emitted, so a
   spiller could otherwise pick it) — it is the join-probe path, out of 4b scope, hardened for consistency.

4. **`releaseReservation` now notifies `spillerCv`.** A reservation can be released WITHOUT finalizing (spill()
   threw → O-B5 swallow; weak_ptr expired). On those paths `finalizeSpill` (the only other notifier) never
   runs, so a trigger WAITing on that sliceEnd would hang. Notifying in `releaseReservation` closes that
   liveness hole.

5. **Test backend is a `std::mutex`-guarded `InMemorySpillBackend` delegate** (defined in the test TU only —
   `InMemorySpillBackend.hpp` is out of scope). The uniform no-I/O-under-lock rule lets spill/unspill of
   different SliceEnds run concurrently; the shared `std::map` in `InMemorySpillBackend` (documented "Not
   thread-safe") would race on the tree — a confound in the *test backend*, not the store. The wrapper removes
   it so the test exercises only the store's concurrency.

6. **Test calls `incrementNumberOfInputPipelines()` once** before draining: the base `getAllNonTriggeredSlices`
   requires `numberOfActiveInputPipelines > 0` and only flushes all remaining windows once it decrements back
   to 0, so a single increment makes the post-latch drain flush every still-filling window in one call.

LOCKED items honoured: uniform no-I/O-under-evictionMutex (spill AND unspill outside); lock order
`evictionMutex → (createdSlices | one folly set)`, folly sets never nested, base slices/windows never under
evictionMutex; async-probe-drain absolute (emittedKeys claimed before releasing the mutex; emitted slices never
spilled); O-B5 (eviction never throws/blocks); spiller declared last, started in setBufferProvider, stop+join
before backend dies, deleteState quiesces first; `-Werror` clean; full ninja.

---

## Commit

`feat(slice-store): background spiller thread + concurrency stress test [Phase 4b-B PR3]`

(committed on branch `feat/ooc-phase1-spill-skeleton`, not pushed; no co-author trailer.)

---

## PR-3 review fixes (L1 + L2) — stacked fix commit

**Reviewer findings cleared:** both Low findings from `reviews/PR3-REVIEW.md` (0 Critical / 0 High / 0 Medium).
Touched only `src/SliceStore/SpillableTimeBasedSliceStore.cpp` and `tests/SpillableTimeBasedSliceStoreTest.cpp`
(no `.hpp` change needed — the fix reuses the existing `ReservationGuard`; no signature changed).

**L1 — latent deadlock-on-exception (fixed properly, same bug class as PR-2's High findings).**
Both unspill-on-read sites reserved the slice in `spillInProgress` (the footprint-reader fence) but, if
`spillable->unspill(...)` threw, left the reservation leaked → `residentBytes()` would permanently skip the slice AND a
future `spillerCv.wait(!spillInProgress.contains(sliceEnd))` on the same `SliceEnd` would hang forever. Fixed by
wrapping the `unspill()` + `spilledKeys.erase` in the existing PR-2 `ReservationGuard` at both sites:
- `unspillAndMarkEmitted` (trigger path; slice already emitted — leak was a stale lifetime entry).
- `getSliceBySliceEnd` (join-probe path; slice NOT emitted — the more reachable wait-forever case).
The guard releases the `spillInProgress` entry under `evictionMutex` via `releaseReservation` (which already notifies
`spillerCv`) on the throw path. On success the existing manual erase+notify runs and the guard is committed afterwards
(`unspillGuard.committed = true`) so there is no double-erase — identical discipline to `forceSpill`/`spillOneColdSlice`.
The RAII release only touches the in-memory reservation under the mutex; the unspill I/O stays OUTSIDE the mutex
(no-I/O-under-evictionMutex preserved).

**L2 — perf nit (no behavior change).** `spillerLoop` evaluated `residentBytes()` twice per inner iteration (once in the
`while` condition, once inside the `NES_TRACE`). `residentBytes()` takes `evictionMutex` and scans `createdSlices`, so
the duplicate doubled lock contention on the spiller path under trace logging. Now captured once into a local
`resident`, used in both the condition and the log, and re-read after each successful spill so the loop still drains down
to soft. Single authoritative read per iteration.

**Regression test (L1) — added (not over-engineered).** A `failGet` toggle (plus `getCount`) was added to the existing
`ThrowingSpillBackend` (a few lines, symmetric to the existing `failPut`) to drive the unspill-throws path
(`unspill()` calls `backend.get()`). Two deterministic single-threaded tests assert the reservation is RELEASED on the
throw — proven by a SUBSEQUENT operation on the SAME `sliceEnd` (with `get()` now succeeding) completing without hanging
and restoring residency; a leaked reservation would deadlock the second op's `spillerCv.wait`:
- `getSliceBySliceEndReleasesReservationWhenUnspillThrows` (the more-reachable, NOT-emitted site).
- `unspillAndMarkEmittedReleasesReservationWhenUnspillThrows` (the trigger/emitted site).
This goes beyond the "structural-only / covered by symmetric put-throw tests" fallback the FIX task allowed, because the
get-throw toggle turned out to be a trivial, faithful addition.

**Build/test (Docker only).**
- `ninja`: exit 0, `-Werror` clean (`-Woverloaded-virtual`, `-Wunused-private-field`), full link (51/51).
- Gate suite (`SpillableTimeBasedSliceStore|...Concurrency|SpillableAggregationSlice|SliceSerializer|SpillBackend|RocksDB|LowerToPhysicalWindowedAggregation`):
  **62/62 passed** (was 60 + the 2 new L1 tests).
- Concurrency stress `--repeat until-fail:5`: **5/5 green** (0.48–0.57 s/run). The change is on the exception path, not
  the hot path, so the stress profile is unchanged.

LOCKED items honoured: uniform no-I/O-under-evictionMutex (unspill I/O outside; RAII guard touches only the in-memory
reservation under the mutex on release); lock order `evictionMutex → (createdSlices | one folly set)`; base maps never
under evictionMutex; O-B5; async-probe-drain. No amend, no push, no `--no-verify` — stacked as a NEW fix commit.

## Fix Commit

`fix(slice-store): release unspill reservation on exception + dedup residentBytes [Phase 4b-B PR3 review]`

---

## Follow-up fix — concurrency test eviction guarantee made DETERMINISTIC under -j8 load (Phase 4b-B PR3 review)

**Base HEAD before this fix:** `e4cd3d0150`
**Fix commit:** `5e4fd3cd9f` — `fix(slice-store): make concurrency test eviction guarantee deterministic under load [Phase 4b-B PR3 review]`
**Scope:** test-only. Production `nes-physical-operators/src` and `include` UNCHANGED (verified `git diff --stat` empty). 1 file changed, +68/-4.

**Symptom (reproduced first):** `concurrentBuildTriggerSpillIsRaceFree` passed in isolation / `--repeat until-fail:5`
but FAILED under 8-parallel load (8 × `--gtest_repeat=15`). The ONLY failing assertion was
`EXPECT_GT(spillCountForTest(), 0U)` → `actual: 0`. All correctness assertions (Σ checksum equality = UAF/lost-update
detector; every returned slice `isSliceResident`; no crash/deadlock) PASSED under load. Confirmed a TEST liveness
defect, not a store race.

**Root cause (confirmed deeper than "background spiller starved"):** spilling requires a COLD slice
(`pickAndReserveColdSlice` rejects `sliceEnd >= lastSeenWatermark`). The test routed ALL watermark advancement
(coldness) through the trigger thread. Under -j8 saturation the trigger AND the background spiller AND watermark-advance
all starve together, so NO slice ever goes cold → `evictUntilUnderHard` / the spiller have nothing to evict → spillCount
stays 0. The builders (which do the CPU work) are the only non-starvable actors, and they only built HOT slices.

**Fix mechanism (deterministic, non-starvable):**
1. Threshold sizing: kept TINY soft (`PAGE_SIZE`, background spiller still enabled + exercised), lowered hard from
   `8*PAGE_SIZE` to `2*PAGE_SIZE` (footprint math verified: `entriesPerPage==6`, `TUPLES_PER_CELL==4` ⇒ 1 page/worker-map;
   so >=3 resident worker-maps ⇒ resident > hard).
2. The NON-STARVABLE BUILDER threads now drive coldness + eviction. After each builder finishes a window it calls
   `seedColdHorizonFromBuilders()` which (a) advances `lastSeenWatermark` via the documented `setLastSeenWatermarkForTest`
   hook over windows ALL builders have finished (gated `doneCount==NUM_BUILDERS`, keeping `SPILL_LAG=8` windows hot —
   identical safety to the trigger's own seeding, honours INV-3 / no-late-data), then (b) invokes the documented PR-2
   `spillColdSlicesUnderHardForTest()` drive hook, which runs the SAME production `evictUntilUnderHard` code. With cold-
   resident slices present and resident > hard, this spills >=1 cold slice on every invocation → `spillCount > 0`
   deterministically, independent of trigger/background-spiller scheduling.
3. ALL correctness assertions kept strict and always-on (unchanged). No assertion deleted or weakened.

**Same-SliceEnd spill-vs-trigger WAIT path coverage preserved:** the builder-driven hard eviction and the still-enabled
background spiller spill cold-but-unemitted windows that the trigger then emits (trigger emit horizon lags the spill
horizon by `SPILL_LEAD`), so `unspillAndMarkEmitted`'s `spillerCv.wait` collision is still driven concurrently; isolated
`--repeat` runs additionally exercise it. The test's PASS/FAIL no longer hinges on background-thread scheduling.

**Build/test (Docker only, `nebulastream/nes-development:local-rocksdb`).**
- `ninja SpillableTimeBasedSliceStoreConcurrencyTest`: exit 0, `-Werror` clean.
- LOAD REPRO (the gate, LOG_ERROR config) — 8 × `--gtest_repeat=15`, run 3× + final cleaned run: **all instances exit=0,
  "NONE - all green"** every time (~20–21 s wall/round; each instance well under the 120 s ctest timeout).
  Pre-fix baseline of the same command: all 8 instances exit=1 with `spillCountForTest()==0`.
- Full `-j8` regression (`ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`): **100% tests passed, 0 failed out of 820**
  (78 s).

No production change, no amend, no push. Stacked as a NEW fix commit on `e4cd3d0150`.
