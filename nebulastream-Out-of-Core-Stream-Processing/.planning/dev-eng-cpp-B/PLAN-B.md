# Implementation Plan B — Increment B (background-spiller auto-eviction, tumbling agg)

Branch `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b` (Increment A complete, 805/805 green).
Build/test ONLY in Docker `nebulastream/nes-development:local-rocksdb`, `cmake-build-debug`,
`ninja` + `ctest -LE 'Systest|DockerCompose'`. Authoritative scope = `SYNTHESIS.md` +
`DESIGN_BRIEF_INCREMENT_B.md`. This plan is produced INDEPENDENTLY of Architect A.

---

## Summary

Increment B turns Increment A's manual `forceSpill` spike into an automatic, concurrency-safe
out-of-core feature for **tumbling** windowed aggregation. The `SpillableTimeBasedSliceStore`
self-tracks every created slice (override `getSlicesOrCreate` → record `sliceEnd` +
`weak_ptr<Slice>`), learns the build watermark from the trigger feeders, and computes store-wide
`residentBytes()` over that tracking map — all WITHOUT touching the private base maps. A dedicated
**store-owned `std::jthread`** preemptively spills the oldest COLD slices when resident memory
crosses the **soft** threshold (so workers never block on disk), while the **worker/allocating
path** runs a synchronous mandatory spill when it crosses the **hard** threshold (the OOM
backstop). All spill decisions are serialized by one `evictionMutex` against the trigger's
emitted-marking, with a per-slice `spillInProgress` reservation set so the actual RocksDB I/O runs
OUTSIDE the lock — closing A's M1 TOCTOU and avoiding the NH2 stall.
**O-B2:** wakeup = condvar signalled at the slice-creation / build-close cadence (never per-tuple),
plus a bounded safety poll; the hard check fires synchronously at the SAME cadence.
**O-B3:** `evictionMutex` makes pick-cold→confirm-not-emitted→reserve atomic vs emitted-marking; a
trigger that wants a mid-spill slice WAITS (reservation), never skips, so it always ends up
resident+emitted. **O-B5:** no-cold-candidate ⇒ soft sleeps/retries, hard proceeds-over-budget +
WARNING (never spill filling/emitted, never error). **Ownership:** store-owned, lazily started
after `setBufferProvider`, joined in the store dtor before the backend is destroyed.

---

## Resolved OPEN items

### O-B2 — Eviction trigger & cadence (wakeup mechanism + exact fire points)

**Decision.**
- **Soft wakeup = condvar, signalled at the slice-creation / build-close cadence, with a bounded
  safety poll.** The background spiller `jthread` blocks on a `std::condition_variable evictionCv`
  with a bounded `wait_for(softPollInterval = 50ms)`. It is woken (`notify_one`) by:
  1. the `getSlicesOrCreate` override, AFTER recording a newly *created* slice, IF a cheap
     `residentBytes() > softThresholdBytes` snapshot says we are over soft; and
  2. the trigger feeders (`getTriggerableWindowSlices` / `getAllNonTriggeredSlices`), after
     unspill-on-read and emitted-marking, because that is the cadence where `emittedKeys` and
     `lastSeenWatermark` are freshest (newly cold slices appear here).
  The bounded poll is a backstop so a store that stops creating slices but is still over soft
  eventually drains (covers the "watermark advanced via GC but no new build" corner).
- **Hard check = synchronous, fired at the SAME cadence on the worker path** — specifically inside
  the `getSlicesOrCreate` override AFTER the base create+record returns (the only worker entry the
  store controls that is NOT per-tuple). After recording, evaluate `residentBytes()` once; if
  `> hardThresholdBytes`, the worker thread itself runs the synchronous eviction loop until back
  under hard or no cold candidate remains.

**Why this cadence (not per-tuple).** The per-tuple JIT hot path is
`DefaultTimeBasedSliceStoreRef::getDataStructureRef` → `defaultTimeBasedSliceStoreRefCacheMissProxy`
(`DefaultTimeBasedSliceStoreRef.cpp:93-113`), which calls `sliceStore->getSlicesOrCreate` ONLY on a
SliceCache *miss* — i.e. once per (worker, new slice), NOT once per tuple. Hanging both the
soft-signal and the hard-check off `getSlicesOrCreate` therefore amortizes to once-per-slice-
creation, never on the hot in-cache tuple path. `residentBytes()` is a Σ over the (typically small)
`createdSlices` map and is only computed at this slice-granularity cadence, so it is cheap relative
to slice allocation.

**RQ tradeoff.** Condvar (vs pure poll) gives the best **tail-latency** story (RQ2): the spiller
reacts immediately when a worker observes over-soft, so cold state is evicted before the hard
threshold is ever reached, keeping worker threads off the synchronous spill path under normal load
(RQ1 throughput). The bounded 50ms poll caps worst-case eviction latency without a busy-wait. The
synchronous hard path is the only place a worker can block on disk; it fires rarely (only if the
background thread cannot keep up), which is exactly the OOM-avoidance regime where blocking is
acceptable (RQ3 memory-ceiling: completing > OOM-crashing).

**Rejected alternatives.** (a) Pure bounded poll: simpler but adds up-to-`softPollInterval` latency
to every eviction even under load → worse RQ2; kept only as a backstop. (c) Piggyback ONLY on
build-`close()` after trigger: build-close in this codebase is `checkAndTriggerWindows` /
`triggerAllWindows`, which already route through the trigger feeders — so this is subsumed by
signal-source (2). No new build-close hook is added (the brief warns against spreading the feature).

### O-B3 — Exact locking model (the precise invariant + reservation semantics)

**Decision — one outer serializer + a per-slice reservation.**

New state on the store:
- `std::mutex evictionMutex;` — the OUTER serializer.
- `std::condition_variable evictionCv;` paired with `evictionMutex` for spiller wakeup.
- `std::set<SliceEnd> spillInProgress;` — guarded BY `evictionMutex` (plain set, not Synchronized,
  because it is only ever touched while holding `evictionMutex`).

**The invariant.** `evictionMutex` is held across exactly these short critical sections, never
across I/O:
1. **Spill-decision (spiller OR synchronous-hard worker):** pick the oldest candidate from
   `createdSlices` that is `cold ∧ resident ∧ !emitted ∧ !spillInProgress`, INSERT it into
   `spillInProgress` (the *reservation*), then RELEASE `evictionMutex`. The candidate is now owned
   by this spiller turn; nobody else will pick or free it.
2. **Spill-finalize:** after `spillable->spill(*backend)` completes OUTSIDE the lock, RE-acquire
   `evictionMutex`, insert into `spilledKeys`, ERASE from `spillInProgress`, `notify` the cv (so a
   trigger that was waiting on this slice can proceed), release.
3. **Emitted-marking (trigger feeder, the ONE change to A's lock rule):** in
   `unspillAndMarkEmitted`, the `emittedKeys.insert(sliceEnd)` step now happens WHILE holding
   `evictionMutex` (see reservation semantics below).

**Atomicity claim.** Because the spill-decision (read `emittedKeys`, reserve) and the trigger's
emitted-marking are BOTH under `evictionMutex`, they are mutually exclusive: a slice cannot be
reserved-for-spill and marked-emitted in an interleaved order. This closes A's M1 TOCTOU
(`SpillableTimeBasedSliceStore.cpp:203-210`): once a slice is in `spillInProgress` the trigger sees
it under the lock and waits; once a slice is in `emittedKeys` the spiller's `!emitted` filter
rejects it. The async-probe-drain invariant (NEVER spill an `EMITTED_TO_PROBE` slice) is therefore
upheld even under concurrency.

**Reservation semantics — a trigger that wants a mid-spill slice WAITS (does not skip).** Rationale:
the trigger MUST hand the probe a *resident* HashMap*; if it skipped a mid-spill slice it would
either emit a half-freed map (UAF) or silently drop the window (wrong result). So in
`unspillAndMarkEmitted`, per slice:
```
lock(evictionMutex)
while spillInProgress.contains(sliceEnd):          // someone is mid-spill
    evictionCv.wait(lock)                            // wait for finalize → it lands in spilledKeys
// now the slice is either resident or fully spilled (in spilledKeys), never half-freed
emittedKeys.insert(sliceEnd)                         // claim it: spiller will now reject it forever
unlock(evictionMutex)
if !spillable->isResident():                         // it was spilled → unspill OUTSIDE the lock
    spillable->unspill(*backend, *provider)
    spilledKeys.wlock()->erase(sliceEnd)
```
Inserting into `emittedKeys` BEFORE releasing guarantees no NEW spill of this slice can start after
the wait. The unspill I/O itself runs outside `evictionMutex` (it cannot race a spiller because the
slice is now emitted). A triggered slice that was mid-spill thus ALWAYS ends up resident+emitted,
never half-freed.

**Which of A's rules changes, and why it is safe.** A's rule "never hold two store locks at once"
(`SpillableTimeBasedSliceStore.cpp:155-158`) is PRESERVED for the *folly::Synchronized* locks
(`spilledKeys`, `emittedKeys`, `createdSlices`) — they are still each acquired one-at-a-time and
never nested. What changes: `emittedKeys` modification now happens UNDER the new `evictionMutex`.
This is safe because `evictionMutex` is a NEW, dedicated lock that sits strictly ABOVE the folly
locks in a fixed order (`evictionMutex` → folly lock → release folly lock → … → release
`evictionMutex`); no folly lock is ever held while *acquiring* `evictionMutex`. **NH1 preserved:**
the base `slices`/`windows` locks are NEVER acquired while holding `evictionMutex` — the only base
call inside an eviction critical section is `weak_ptr::lock()` on already-tracked slices, which
touches no base map. `getSlicesOrCreate` calls the base FIRST (releasing base locks) and only THEN
takes `evictionMutex` to record/evaluate, so base-lock-then-evictionMutex never happens either.

**Per-tuple build path is untouched.** Build only writes resident FILLING slices (their `HashMap*`
came from the SliceCache / `getHashMapPtrOrCreate`), which are never eviction candidates
(`cold = sliceEnd < lastSeenWatermark` excludes filling slices). No `evictionMutex` on the hot path.

### O-B5 — Eviction policy edge cases

**Decision.**
- **Over soft, but no COLD candidate** (every tracked slice is filling, emitted, or already
  spilled): the spiller's scan returns no pick → it logs `NES_TRACE` once and goes back to waiting
  (`evictionCv.wait_for(softPollInterval)`). It will re-scan on the next signal/poll, by which time
  the watermark may have advanced and produced cold slices. Soft NEVER spills a filling/emitted
  slice and NEVER blocks a worker.
- **Over hard, but no COLD candidate:** the synchronous worker loop exits WITHOUT spilling, logs
  `NES_WARNING("over hard budget {} > {} but no cold slice to evict; proceeding over-budget",
  resident, hard)`, and **proceeds over-budget** (does NOT block, does NOT throw). Blocking a worker
  forever would deadlock the very pipeline that must advance the watermark to *create* cold slices.
- **Over hard even after ALL cold slices spilled** (resident still > hard because filling/emitted
  state alone exceeds it): **proceed + WARNING, do NOT error.** Rationale: a hard error here would
  abort a query that is making forward progress and whose memory is dominated by *un-spillable*
  live state (filling slices the workers are actively writing, emitted slices in flight to the
  probe). The honest contract is "spill what we safely can; if live state alone exceeds hard, the
  budget was set too low — surface it loudly, don't crash." This matches the thesis goal (complete
  where it would otherwise OOM) for the case where spilling *can* help, and degrades gracefully when
  it can't. (An out-of-gate systest with a realistically-sized budget validates the help-able case.)

### Background-thread ownership

**Decision: STORE-OWNED `std::jthread`.**

Rationale: fewest production edits (store hpp/cpp only — no handler/ref edits beyond the existing
`setBufferProvider` site), RAII teardown in the store dtor, and the spiller's two dependencies
(`backend`, `bufferProvider`) are store members, so member-declaration order alone enforces correct
destruction order. Handler-ownership was rejected: it spreads the feature across
`AggregationOperatorHandler` + a store eviction entry point + proxy wiring (the brief explicitly
warns against gold-plating / spreading), and gains nothing because the store already has the
lifecycle anchors it needs.

- **Start point (lazy):** the spiller cannot run before `bufferProvider` is stashed (unspill needs
  it, and computing residency needs the maps to exist). `bufferProvider` is set in
  `setBufferProvider` (called from `setupSliceStoreProxy`, `DefaultTimeBasedSliceStoreRef.cpp:131-
  133`, at first JIT build setup, AFTER handler `start()`). Therefore **start the spiller at the end
  of `setBufferProvider`**, exactly once (guard with the existing `stashed != nullptr` early-return
  so a repeat call does not start a second thread). The store unit test calls `setBufferProvider`
  directly, so the thread also starts under test.
- **Stop+join point:** request stop (`stopRequested = true; evictionCv.notify_all();`) and JOIN at
  the TOP of the store destructor, BEFORE the backend/bufferProvider members are destroyed. With
  `std::jthread spiller;` declared as the LAST member of `SpillableTimeBasedSliceStore` (after
  `backend`), reverse-destruction-order would auto-join it too late (after `backend`); so we make
  the join EXPLICIT in `~SpillableTimeBasedSliceStore()` (which we must add) before anything else
  runs. `std::jthread`'s stop_token drives the loop's exit condition.
- **`deleteState()` must quiesce first:** `deleteState` clears tracked state and erases backend
  records; if the spiller were mid-spill it could touch a just-erased record or a cleared
  `createdSlices`. So `deleteState` first sets a `quiesced` flag under `evictionMutex` and waits for
  `spillInProgress` to drain (the spiller checks `quiesced` and stops picking), THEN clears state.
  Since `deleteState` is also called from the base dtor, the explicit join in our dtor must run
  before `DefaultTimeBasedSliceStore::~DefaultTimeBasedSliceStore()` runs `deleteState` — the
  explicit dtor-top join guarantees this.

---

## Sections

Each section is assigned to exactly ONE implementation agent and (mostly) ONE file. The store
header and its `.cpp` share Section 1 because the member set and its implementation are one
inseparable unit (splitting them across agents would desync the member list).

### Section 1: `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` + `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`

- **Agent assignment:** implementation-1
- **Changes (header):**
  - Add includes: `<atomic>`, `<condition_variable>`, `<mutex>`, `<thread>` (`std::jthread`).
  - Drop `[[maybe_unused]]` from `softThresholdBytes`/`hardThresholdBytes` (lines 111-112).
  - New members (declaration order matters — `spiller` LAST):
    ```cpp
    folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices;   // O-B1 self-track
    std::atomic<Timestamp> lastSeenWatermark{Timestamp(0)};                        // learned watermark
    mutable std::mutex evictionMutex;                                              // O-B3 outer serializer
    std::condition_variable evictionCv;                                            // spiller wakeup (O-B2)
    std::set<SliceEnd> spillInProgress;                                            // guarded by evictionMutex
    bool quiesced = false;                                                         // guarded by evictionMutex
    std::jthread spiller;                                                          // store-owned (declared LAST)
    ```
  - New method decls:
    ```cpp
    ~SpillableTimeBasedSliceStore() override;                                      // explicit: join spiller first
    std::vector<std::shared_ptr<Slice>> getSlicesOrCreate(
        Timestamp timestamp,
        const std::function<std::vector<std::shared_ptr<Slice>>(SliceStart, SliceEnd)>& createNewSlice) override;
    [[nodiscard]] uint64_t residentBytes() const;                                 // store-wide Σ
    private:
    void recordCreatedSlice(const std::shared_ptr<Slice>& slice);
    void updateWatermarkFromTrigger(/* learns max windowEnd seen */);             // called in feeders
    std::optional<SliceEnd> pickColdCandidateLocked();                            // requires evictionMutex held
    void evictUntilUnderLocked(uint64_t targetBytes, const std::stop_token& st);  // soft loop helper (spiller)
    void synchronousHardSpill();                                                  // worker path (O-B2 hard)
    void startSpiller();                                                          // launches the jthread
    void spillerLoop(std::stop_token stopToken);                                  // the background loop body
    ```
- **Changes (.cpp) — algorithms in prose/pseudocode:**
  - **`getSlicesOrCreate` override:**
    1. `auto result = DefaultTimeBasedSliceStore::getSlicesOrCreate(timestamp, createNewSlice);`
       (base call returns having released `slices`/`windows` — NH1 safe).
    2. `recordCreatedSlice(result[0])` → `createdSlices.wlock()->emplace(sliceEnd, weak_ptr(slice))`
       (no-op if already present; tumbling ⇒ one slice).
    3. Hard check (O-B2): `if (residentBytes() > hardThresholdBytes) synchronousHardSpill();`
    4. Soft signal (O-B2): `if (residentBytes() > softThresholdBytes) evictionCv.notify_one();`
       (cheap: residentBytes already computed in step 3's path; compute once, reuse).
    5. `return result;`
  - **`residentBytes()`:** read-lock `createdSlices`; for each entry `lock()` the weak_ptr (skip
    expired — GC already dropped it); `bytes += residentBytesOf(*slice)` (existing helper, returns 0
    for spilled). Return Σ. Does NOT take `evictionMutex` (read-only snapshot; tolerates a slightly
    stale value because eviction only ever lowers resident bytes monotonically between ticks).
  - **`unspillAndMarkEmitted` (rewrite per O-B3):** per slice, take `evictionMutex`; `while
    (spillInProgress.contains(sliceEnd)) evictionCv.wait(lk);` then `emittedKeys.wlock()->insert(...)`;
    release `evictionMutex`; THEN if non-resident `unspill` outside the lock + erase from
    `spilledKeys`. (Replaces the current non-atomic body at `SpillableTimeBasedSliceStore.cpp:90-115`.)
  - **`getTriggerableWindowSlices` / `getAllNonTriggeredSlices`:** unchanged structure; after
    `unspillAndMarkEmitted(result)`, call `updateWatermarkFromTrigger` to bump `lastSeenWatermark`
    to the max `windowEnd` observed in `result` (monotonic via `std::atomic` CAS-max or
    `fetch_max`-style; a lagging value only under-selects cold → safe). Then `evictionCv.notify_one()`.
  - **`pickColdCandidateLocked()` (requires `evictionMutex` held):** if `quiesced` return nullopt;
    iterate `createdSlices` ascending (read-lock it briefly inside); first `sliceEnd` with
    `sliceEnd < lastSeenWatermark` (cold) ∧ `isSliceResident(sliceEnd)` ∧
    `!emittedKeys.rlock()->contains(sliceEnd)` ∧ `!spillInProgress.contains(sliceEnd)` →
    `spillInProgress.insert(sliceEnd)` (reserve) and return it; else nullopt.
  - **`evictUntilUnderLocked` / soft path (spiller):** loop while `residentBytes() > softThresholdBytes`
    and `!st.stop_requested()`: `{lock evictionMutex; auto pick = pickColdCandidateLocked();}` — if
    no pick, break (O-B5 soft no-cold). Lock the weak_ptr → `spillable`; release `evictionMutex`;
    `spillable->spill(*backend)` (I/O outside lock); re-lock `evictionMutex`:
    `spilledKeys.wlock()->insert(pick); spillInProgress.erase(pick); evictionCv.notify_all();`.
  - **`synchronousHardSpill()` (worker path):** same pick→reserve→spill-outside→finalize loop, run
    by the WORKER thread, while `residentBytes() > hardThresholdBytes`; on no-pick → WARNING +
    return (O-B5 hard no-cold / still-over-after-all-cold).
  - **`spillerLoop(stopToken)`:** `while (!stopToken.stop_requested()) { unique_lock lk(evictionMutex);
    evictionCv.wait_for(lk, softPollInterval, [&]{ return stopToken.stop_requested() ||
    residentBytes() > softThresholdBytes; }); lk.unlock(); if (stopToken.stop_requested()) break;
    evictUntilUnderLocked(softThresholdBytes, stopToken); }`. (`softPollInterval` =
    `std::chrono::milliseconds(50)` constexpr in the .cpp.)
  - **`startSpiller()`:** `spiller = std::jthread([this](std::stop_token st){ spillerLoop(st); });`
  - **`setBufferProvider`:** at the END of the existing `withWLock` success branch (after stashing),
    call `startSpiller()` (once — guarded by the existing `stashed != nullptr` early-return).
  - **`~SpillableTimeBasedSliceStore()`:** `{lock evictionMutex; quiesced = true;} evictionCv.notify_all();
    spiller.request_stop(); if (spiller.joinable()) spiller.join();` — THEN let base dtor run
    `deleteState`. (Explicit, at dtor top, so join precedes backend destruction.)
  - **`deleteState()`:** FIRST `{unique_lock lk(evictionMutex); quiesced = true; evictionCv.wait(lk,
    [&]{ return spillInProgress.empty(); });}` (drain in-flight spills), THEN the existing
    backend-erase + tracked-set-clear, THEN also `createdSlices.wlock()->clear()`, THEN base
    `deleteState`. Reset `quiesced=false` is NOT needed (deleteState is terminal).
  - **`garbageCollectSlicesAndWindows`:** unchanged body PLUS prune `createdSlices` of expired
    weak_ptrs (lock `createdSlices`, erase entries whose `weak_ptr.expired()`). Keeps the tracking
    map bounded; weak_ptr means the base GC's shared_ptr drop already freed the slice.
  - **`forceSpill`:** wrap the existing emitted-check + spill in `evictionMutex` + a `spillInProgress`
    reservation so the test-only forced path obeys the SAME discipline (so the stress test may use
    it interchangeably). Behavior otherwise unchanged (still throws `SpillStoreFailure` on emitted).
- **Logging (exact lines + function):**
  - ctor (existing `NES_DEBUG` at line 60) — extend message to note "auto-eviction enabled".
  - `setBufferProvider`, after `startSpiller()`: `NES_DEBUG("Started background spiller thread (soft={}B, hard={}B)", softThresholdBytes, hardThresholdBytes);`
  - `synchronousHardSpill`, on entry: `NES_DEBUG("Hard threshold crossed (resident={}B > hard={}B); synchronous spill on worker path", resident, hardThresholdBytes);`
  - `synchronousHardSpill`, no-cold exit: `NES_WARNING("Over hard budget ({}B > {}B) but no cold slice to evict; proceeding over-budget", resident, hardThresholdBytes);`
  - `evictUntilUnderLocked`, per spilled slice: `NES_DEBUG("Background spiller evicted cold slice {} ({}B); resident now {}B", sliceEnd, freed, residentBytes());`
  - `evictUntilUnderLocked`, no-cold: `NES_TRACE("Over soft ({}B > {}B) but no cold candidate; spiller sleeping", resident, softThresholdBytes);`
  - `unspillAndMarkEmitted`, when it waits on a mid-spill slice: `NES_TRACE("Trigger of slice {} waiting on in-flight spill", sliceEnd);`
  - dtor: `NES_DEBUG("Stopping and joining background spiller thread");`
  - `deleteState`, drain: `NES_TRACE("Quiescing spiller before deleteState ({} in-flight)", spillInProgress.size());`
- **Tests:** Section 4 (`SpillableTimeBasedSliceStoreTest.cpp`, deterministic) + Section 5
  (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp`, stress). No new `add_nes_..._test` wiring for
  this section (covered by existing + the new test in Section 6).

### Section 4: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (deterministic single-thread B-evict)

- **Agent assignment:** implementation-1 (same agent — co-evolves with the store API).
- **Changes:** add a `softThresholdBytes`/`hardThresholdBytes` knob to `makeStore` (overload taking
  explicit thresholds, defaulting to the current large values so existing tests are unaffected). Add
  cases (concrete assertions in Test plan): `residentBytesAggregatesAcrossSlices`,
  `softThresholdSpillsOldestColdSlice` (drive the spiller deterministically by lowering soft,
  populating > soft, advancing watermark via a trigger feeder, then polling `isSliceResident` with a
  bounded `waitFor` helper), `hardThresholdSynchronousSpill` (single-thread: creating a slice over
  hard triggers synchronous spill inside `getSlicesOrCreate`), `noColdCandidateProceedsOverBudget`
  (everything filling → no spill, no throw), `emittedNeverSpilledUnderEviction` (mark emitted via
  trigger, lower soft, assert it stays resident).
- **Logging:** n/a (test).
- **Tests:** these ARE the tests; wired by the existing line 28 in CMakeLists.

### Section 5 + 6: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` (NEW) + CMakeLists wiring

- **Agent assignment:** implementation-2
- **Changes (Section 5, the test):** new file modelled on `ConcurrentSynchronizationTest.cpp`
  (`std::array<std::jthread,N>` + `std::latch` + atomic work-claim + deterministic postcondition).
  Structure (full design in Test plan): N build jthreads each owning a disjoint `WorkerThreadId`
  populating slices via `getSlicesOrCreate`+`populate`; one trigger jthread looping
  `getTriggerableWindowSlices` with a monotonically advancing watermark (to mark slices emitted and
  exercise spill-vs-emit / spill-vs-trigger races); the store's own background spiller running under
  a tiny soft threshold so eviction is continuous. Postconditions: no crash/UAF; every triggered
  slice that is returned is resident at return; the per-key aggregate checksum equals the
  single-thread reference. Reuse the fixture helpers from `SpillableTimeBasedSliceStoreTest.cpp` by
  factoring them into a small shared test header OR duplicating the minimal `makeArgs`/`populate`
  (duplicate is acceptable per "don't gold-plate"; prefer a tiny shared header if trivial).
- **Changes (Section 6, CMakeLists):** append ONE line after line 28 of
  `nes-physical-operators/tests/CMakeLists.txt`:
  ```cmake
  add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest SpillableTimeBasedSliceStoreConcurrencyTest.cpp)
  ```
  No ctest-label edits (`gtest_discover_tests` auto-registers; `-LE 'Systest|DockerCompose'`
  auto-includes). No sanitizer/preset infra changes.
- **Logging:** n/a (test).
- **Tests:** this IS the stress test (Test plan §"concurrency stress test").

---

## Parallel groups

- **Group 1 (must land first, sequential anchor):** Section 1 (store hpp+cpp) + Section 4 (its
  deterministic test) — implementation-1. Everything depends on the store API + `createdSlices`
  enumeration + the eviction entry points existing.
- **Group 2 (after Group 1):** Section 5+6 (concurrency test + CMake) — implementation-2. Depends on
  the store's `evictionMutex`/reservation/background-thread being implemented in Group 1; the test
  exercises them.

Within Group 1, the header and .cpp are one agent (no internal parallelism — they are one unit).
Section 4 is written test-first against the API shape defined in Section 1 (RED → GREEN).

## PR boundaries

Map to SYNTHESIS PR-1/2/3 (the ordering constraint PR-1 → PR-2 → PR-3 is what matters; they MAY
collapse but the order is load-bearing):

- **PR-1 (enumeration + budget, single-thread):** `getSlicesOrCreate` override + `createdSlices` +
  `lastSeenWatermark` + `residentBytes()` + GC/deleteState pruning. Gate: the new deterministic
  `residentBytesAggregatesAcrossSlices` + GC-prune assertions in Section 4. NO eviction logic yet.
- **PR-2 (eviction policy, single-thread):** `synchronousHardSpill` + the soft `evictUntilUnder`
  helper + `pickColdCandidate` + O-B5 edge cases, driven SYNCHRONOUSLY from the test (call the hard
  path / a test-only `evictOnce()` shim — no background thread yet). Gate: `hardThresholdSynchronousSpill`,
  `noColdCandidateProceedsOverBudget`, `emittedNeverSpilled`. Depends on PR-1.
- **PR-3 (concurrency: mutex + reservation + background thread):** `evictionMutex`, `spillInProgress`,
  the trigger joining the serializer (rewrite `unspillAndMarkEmitted`), the background `jthread` +
  `startSpiller`/lifecycle + dtor join + `deleteState` quiesce. Gate: the NEW stress test (Section
  5). **Highest-risk PR; if its concurrency cannot be made stress-clean across the iteration count,
  ESCALATE rather than ship** (per SYNTHESIS + brief).

Because Section 1 is one file pair, PR-1/2/3 are implemented as STACKED commits on that file (each
commit adds the next layer + its gate), keeping the per-PR reviewer pass meaningful while not
artificially fragmenting the file across agents.

## Test plan

**Deterministic single-thread (Section 4, extends `SpillableTimeBasedSliceStoreTest.cpp`):**
- `residentBytesAggregatesAcrossSlices`: create 3 tumbling slices at ts 50/150/250, populate
  distinct counts; assert `store->residentBytes()` == Σ `residentBytesOf` of each; forceSpill the
  middle one; assert `residentBytes()` drops by exactly that slice's prior bytes (spilled ⇒ 0).
- `createdSlicesPrunedByGC`: create slices, advance probe watermark past them, call
  `garbageCollectSlicesAndWindows`; assert `residentBytes()` excludes the GC'd slices (weak_ptrs
  expired, pruned).
- `hardThresholdSynchronousSpill`: `makeStore` with a tiny `hardThresholdBytes`; create slice A at
  ts 50, populate over hard; advance `lastSeenWatermark` past A by calling `getTriggerableWindowSlices`
  on a LATER slice... — actually simpler: create A (cold via a prior trigger that set the watermark),
  then create B over hard; assert that creating B synchronously spills A (`isSliceResident(A)==false,
  isSliceResident(B)==true`) and `residentBytes() <= hard`.
- `softThresholdSpillsOldestColdSlice` (drives the live spiller deterministically): tiny soft;
  create A, B (A older, both cold via an advanced watermark); a bounded `waitFor(2s, []{ return
  !isSliceResident(A); })` helper polls until the background thread evicts A; assert A spilled BEFORE
  B (oldest-first), `residentBytes() <= soft` once drained.
- `noColdCandidateProceedsOverBudget`: tiny hard; create a single FILLING slice (watermark not
  advanced ⇒ not cold), populate over hard; assert `getSlicesOrCreate`/hard path does NOT spill it,
  does NOT throw, and logs the over-budget WARNING (assert via no-throw + state).
- `emittedSliceNeverSpilledUnderEviction`: trigger a slice (→ emitted), tiny soft, wait; assert it
  stays resident (spiller's `!emitted` filter holds).

**Concurrency stress test (Section 5, NEW, no sanitizer — template `ConcurrentSynchronizationTest`):**
- Fixture: `BufferManager::create()`; a `SpillableTimeBasedSliceStore` with a SMALL soft threshold
  (forces continuous eviction) and a moderate hard threshold; `setBufferProvider` (which starts the
  spiller).
- `numThreads` (e.g. 8) build jthreads: each claims a disjoint `WorkerThreadId` and a contiguous
  block of timestamps via an atomic counter; for each timestamp it calls
  `store->getSlicesOrCreate(ts, factory)` then `populate(slice, myWorkerId, k, ts)` with a
  DETERMINISTIC per-(worker,ts) payload so the global checksum is reproducible.
- 1 trigger jthread: loops, advancing a monotonically increasing watermark and calling
  `getTriggerableWindowSlices`; for every returned slice it ASSERTS `isSliceResident(sliceEnd)` (the
  spill-vs-emit / spill-vs-trigger race exposure point — a returned slice MUST be resident) and
  accumulates its per-key values into a thread-local sum.
- The store's background spiller runs concurrently the whole time (spill-vs-build, spill-vs-trigger).
- `std::latch` over all build threads; the trigger thread drains remaining windows via
  `getAllNonTriggeredSlices` after the latch.
- **Postconditions / concrete assertions:** (1) no crash, no UAF (the process completing IS the
  primary signal under no-TSan); (2) every triggered/returned slice was resident at return
  (assert inside the trigger loop); (3) the summed aggregate over all triggered slices EQUALS the
  precomputed single-thread reference checksum (proves no slice was lost to a spill/emit race and no
  double-count); (4) after teardown, `backend` has no orphaned records for GC'd slices.
- **Race exercise:** the small soft threshold guarantees the spiller is constantly picking cold
  slices WHILE the trigger thread marks slices emitted and WHILE build threads create new slices —
  so spill-vs-emit (closed by `evictionMutex`+emitted-marking) and spill-vs-trigger (closed by the
  `spillInProgress` wait) are continuously stressed. Iterate the whole scenario `numIterations`
  times (default a few; raise locally for soak) to widen the Heisenbug window.
- New ctest name (auto-discovered): `SpillableTimeBasedSliceStoreConcurrencyTest.*`.

**Regression:** full `ctest -LE 'Systest|DockerCompose'` stays green vs the 805 baseline (the new
test ADDS to the count; no existing test changes behavior).

## Risks & mitigations

- **Lifetime / teardown order (HIGH).** The spiller must not outlive `backend`/`bufferProvider`.
  *Mitigation:* explicit `request_stop()`+`join()` at the TOP of `~SpillableTimeBasedSliceStore`
  (before the base dtor's `deleteState`, before member destruction); `deleteState` drains
  `spillInProgress` under `evictionMutex` before clearing state. `spiller` declared LAST member so
  even the fallback (jthread auto-join in member destruction) cannot run before the explicit join.
- **TOCTOU / UAF spill-vs-emit (ABSOLUTE).** A slice spilled after its raw `HashMap*` was
  snapshotted for the async probe = UAF. *Mitigation:* `evictionMutex` makes spill-decision atomic
  vs emitted-marking; the `!emittedKeys` filter + `spillInProgress` reservation guarantee an emitted
  or in-flight slice is never spilled, and a triggered mid-spill slice WAITS then unspills (O-B3).
- **Heisenbug / no-TSan ceiling (HIGH, honest).** R1/R2 are data races; the only in-gate signal is
  the probabilistic stress test (TSan not buildable here — vcpkg deps baked `sanitizer=none`).
  *Mitigation + ceiling:* run the stress test with high iteration/thread counts; document in the PR
  that final concurrency confidence comes from the out-of-gate thesis systest under Docker memory
  limits (MS3/MS4). If the stress test is not reliably clean, ESCALATE — do not ship.
- **NH2 stall (throughput/latency cliff).** Holding `evictionMutex` across RocksDB I/O would
  serialize all triggers behind disk. *Mitigation:* reserve-then-release-then-I/O-outside-lock
  (the `spillInProgress` set is exactly the state that lets the lock be dropped during I/O).
- **Stale per-worker SliceCache entry (R3, contract-dependent).** A spilled cold slice's `HashMap*`
  cached in a worker's SliceCache would UAF IF a build tuple re-landed in that range. *Mitigation:*
  we RESPECT the watermark-monotonicity / no-late-data contract (a cold tumbling slice is past
  trigger; the cache won't re-look-it-up) per the LOCKED "no JIT/SliceCache change"; the stress test
  asserts no tuple lands in a spilled range. Document the no-late-data precondition.
- **`residentBytes()` staleness vs `evictionMutex`-free read.** Read outside the lock could be
  slightly stale. *Mitigation:* eviction only ever LOWERS resident bytes; a stale-high read causes
  at most one extra (safe) eviction pass, a stale-low read is corrected on the next cadence tick —
  never a correctness issue, only a tiny over/under-eviction that self-heals.
- **Spurious double-start of the spiller.** `setBufferProvider` could be called twice.
  *Mitigation:* the existing `stashed != nullptr` early-return already guards the start call.
