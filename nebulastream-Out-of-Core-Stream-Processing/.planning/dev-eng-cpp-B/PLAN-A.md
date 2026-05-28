# Implementation Plan A — Increment B

> Architect A (Phase 3), dev-eng-orchestra-cpp. Branch `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b`
> (Increment A done, 805/805). Build/test ONLY in Docker `nebulastream/nes-development:local-rocksdb`,
> `cmake-build-debug`, ninja + `ctest -LE 'Systest|DockerCompose'`. Authoritative scope =
> `.planning/dev-eng-cpp-B/SYNTHESIS.md` + `.planning/dev-eng-phase4b/DESIGN_BRIEF_INCREMENT_B.md`.
> This plan honors every LOCKED item verbatim and writes ONLY this file.

## Summary

Increment B turns the Increment-A forced-spill spike into automatic, multi-thread-safe out-of-core
eviction for **tumbling** windowed aggregation. The `SpillableTimeBasedSliceStore` **self-tracks**
every created slice (`Synchronized<map<SliceEnd, weak_ptr<Slice>>> createdSlices`, recorded in a
`getSlicesOrCreate` override AFTER the base call returns) and **learns the build watermark** from the
trigger feeders (`lastSeenWatermark`, monotonic), so it can enumerate COLD candidates
(`sliceEnd < watermark`, resident, not emitted) and compute store-wide `residentBytes()` without ever
touching the private base maps (O-B1 = option a). A dedicated **store-owned background spiller
`std::jthread`** (started lazily once the buffer provider is stashed in `setupSliceStoreProxy`,
stop+joined in the store dtor / quiesced in `deleteState`) preemptively spills cold slices when
resident memory crosses the **soft** threshold (O-B-ownership). The **hard** threshold is enforced
**synchronously** on the worker path at the slice-creation cadence (inside the `getSlicesOrCreate`
override, never per-tuple on the JIT hot path — O-B2). All eviction and emitted-marking are serialized
by ONE `evictionMutex` plus a per-slice `spillInProgress` reservation set; the actual RocksDB I/O runs
**outside** the lock with the slice reserved (O-B3); a trigger that wants a mid-spill slice **waits**
on a condvar until the spill finishes, then unspills+marks emitted, so a triggered slice always ends
resident+emitted, never half-freed. O-B5: over-budget with no cold candidate → soft sleeps, hard
proceeds-and-logs; never spill a filling/emitted slice; even after spilling all cold the hard path
proceeds+logs (never errors, never blocks the worker forever).

## Resolved OPEN items

### O-B2 — Eviction trigger & cadence + wakeup mechanism

**Decision.**
- **Soft path / background-thread wakeup = condvar, signalled at the slice-creation cadence, with a
  bounded-poll safety net.** The background spiller waits on `std::condition_variable_any spillerCv`
  guarded by `evictionMutex`, woken (a) explicitly whenever the `getSlicesOrCreate` override observes
  `residentBytes() > softThresholdBytes`, and (b) by a bounded `wait_for(250ms)` timeout so a store
  that stops creating slices (e.g. between input bursts) still drains down to soft and so the loop
  re-checks its stop token promptly. On wake it scans `createdSlices` oldest-first and spills until
  `residentBytes() <= soft` or no cold candidate remains, then waits again.
- **Hard path = synchronous, checked inside the `getSlicesOrCreate` override**, immediately AFTER the
  base `getSlicesOrCreate` returns the (new or existing) slice and AFTER `createdSlices`/watermark are
  updated. If `residentBytes() > hardThresholdBytes`, the *worker thread itself* runs the same
  pick-cold→reserve→spill loop synchronously until back under hard or no cold candidate remains.
- **Soft check is also evaluated at this same point** (cheap compare), and if over soft it `notify`s
  the spiller condvar rather than spilling inline — keeping the worker path latency-bounded.

**Why this cadence (not per-tuple, not a separate GC hook).** The JIT build hot path is
`getDataStructureRef` → `SliceCache` → (on cache miss only) `defaultTimeBasedSliceStoreRefCacheMissProxy`
→ `getSlicesOrCreate` (`DefaultTimeBasedSliceStoreRef.cpp:59`). A cache *hit* (the common per-tuple
case) NEVER calls into the store, so hooking `getSlicesOrCreate` runs eviction only on a slice-cache
*miss* — i.e. roughly once per (slice, worker) window boundary, NOT per tuple. This is the coarsest
cadence that still reacts before the next slice's tuples accumulate, and it is exactly where the
synthesis says budget should be checked. `residentBytes()` (Σ over `createdSlices`) is therefore
computed at most once per cache miss + once per spiller wake — cheap relative to the I/O it gates.

**RQ tradeoff.** Background-thread soft spill keeps worker tail latency low under steady cold growth
(RQ2): workers never block on RocksDB writes while still under hard. The synchronous hard backstop
trades a bounded latency spike for OOM-avoidance (RQ1 correctness: "completes where it would OOM").
The condvar (vs pure poll) minimizes wasted wakeups and reacts within one cache-miss of crossing soft;
the 250 ms bounded `wait_for` caps worst-case drain latency when slice creation stalls.

### O-B3 — Exact locking model + reservation semantics

**The invariant.** ONE `std::mutex evictionMutex` is the OUTER serialiser. It is held across, and ONLY
across, these short critical sections (never across RocksDB I/O):
1. **Spiller pick** — read `lastSeenWatermark`, iterate `createdSlices` oldest-first, and for the first
   candidate that is cold (`sliceEnd < lastSeenWatermark`), resident (`isSliceResident`), not emitted
   (`!emittedKeys.contains`), and not already reserved (`!spillInProgress.contains`): insert it into
   `spillInProgress`. Then RELEASE `evictionMutex` and do `spill()` I/O.
2. **Spiller finalize** — re-acquire `evictionMutex`, `spilledKeys.insert(sliceEnd)`, `spillInProgress.erase(sliceEnd)`,
   `spillerCv.notify_all()` (wakes any trigger waiting on this exact slice).
3. **Emitted-marking** (`unspillAndMarkEmitted`, currently `SpillableTimeBasedSliceStore.cpp:112`) — now
   runs UNDER `evictionMutex`. For each returned slice: while `spillInProgress.contains(sliceEnd)`,
   `spillerCv.wait(lock)` (the trigger **waits**, see below); then if non-resident, unspill (I/O — see
   the carve-out below) and `spilledKeys.erase`; then `emittedKeys.insert(sliceEnd)`.

**`spilledKeys` / `emittedKeys` stay accessed one-at-a-time** (their `folly::Synchronized` wlock taken,
used, released) INSIDE the `evictionMutex` critical section — never nested with each other (H1
preserved). `evictionMutex` is the OUTER lock; a `Synchronized` set lock is always taken and dropped
within it, so the only lock-order rule is `evictionMutex` → (one set lock) → release. **Base
`slices`/`windows` are NEVER acquired while holding `evictionMutex`** (NH1): the spiller pick reads
ONLY `createdSlices`+`lastSeenWatermark`+the two sets; it gets the `shared_ptr<Slice>` by
`weak_ptr::lock()` from `createdSlices` (no base-map access). Emitted-marking operates on slices the
base feeder already returned (base lock already released, `getTriggerableWindowSlices` returns before
`unspillAndMarkEmitted` runs — see `SpillableTimeBasedSliceStore.cpp:120-122`).

**Reservation semantics — trigger vs mid-spill slice = WAIT (not skip).** When a trigger
(`unspillAndMarkEmitted`) wants a slice that is in `spillInProgress`, it `spillerCv.wait`s under
`evictionMutex` until the spiller's finalize step removes it from `spillInProgress` (then the slice is
spilled→`spilledKeys`). The trigger then sees it non-resident, unspills it, and marks it emitted. Net:
**a triggered slice mid-spill always ends resident + emitted, never half-freed.** Skipping instead of
waiting was rejected: skipping would return a non-resident slice to the probe (UAF when the probe reads
the raw `HashMap*`). The wait is bounded by one `spill()` I/O of one slice (NH2: I/O is outside the
lock, so other workers' build path is unaffected; only the concurrent trigger of that *same* slice
waits — a rare collision because the spiller targets COLD slices and the trigger marks them emitted in
the same `evictionMutex` region, so once a trigger wins the race the spiller's pick step will see it in
`emittedKeys` and skip it).

**The carve-out (unspill I/O under `evictionMutex`).** Unspill-on-read in `unspillAndMarkEmitted` does
RocksDB `get` I/O. To keep emitted-marking atomic vs the spiller, the trigger holds `evictionMutex`
across the unspill. This is acceptable because: (a) a slice can be EITHER mid-spill OR resident-spilled,
never being-built by a worker (cold ⇒ stopped filling), so no build path waits on it; (b) the only
contender for `evictionMutex` during a trigger's unspill is the background spiller, which we *want*
serialized; (c) triggers are at the window-trigger cadence, not the per-tuple cadence. We accept the
spiller stalling for one unspill I/O (it has cold work elsewhere on the next wake). If profiling later
shows this is a bottleneck, the refinement is a per-slice `unspillInProgress` reservation mirroring
`spillInProgress` — explicitly out of scope for SC2 ("don't gold-plate").

**Which of A's rules changes.** A's "never hold two store locks at once"
(`SpillableTimeBasedSliceStore.cpp:155-158` H1 note) is REFINED, not broken: the two
`folly::Synchronized` *set* locks (`spilledKeys`, `emittedKeys`) are still never nested with each other.
What changes is that emitted-marking now additionally participates in `evictionMutex`. `evictionMutex`
is a NEW, strictly-outer lock that did not exist in A; introducing it cannot create a cycle with the
existing rule because (i) it is always acquired first and (ii) base `slices`/`windows` are never taken
under it. Safe by construction.

### O-B5 — Eviction policy edge cases

- **Over budget, no COLD candidate (everything filling or emitted).**
  - *Soft (background thread):* the scan finds no candidate → log `NES_DEBUG` once per wake, then
    `wait_for` (sleep) and retry on the next wake. Never spills a filling/emitted slice.
  - *Hard (synchronous worker path):* the loop finds no candidate → **proceed over-budget and log
    `NES_WARNING`** (do NOT block the worker; blocking would deadlock — only this worker can close the
    filling slices that would later become cold). Never spills a filling/emitted slice (would violate
    INV-1 / async-probe-drain).
- **Hard still over budget after ALL cold slices spilled.** **Proceed + `NES_WARNING`, do not error.**
  Rationale: erroring would fail an otherwise-correct query for a transient budget overshoot that the
  background thread + GC will relieve as windows trigger and free; the hard threshold is a *backstop to
  avoid OOM*, and once every cold slice is on disk the only resident memory left is genuinely-needed
  filling/emitted state that we are forbidden to spill. A real OOM then is a query-sizing problem
  surfaced by the systest (MS3/MS4), not a spill-store bug. The `NES_WARNING` makes it diagnosable.

### Background-thread ownership

**Decision: STORE-OWNED** (`std::jthread` member of `SpillableTimeBasedSliceStore`).

**Rationale.** Fewest production edits (the synthesis "store-owned ⇒ the two store EDITs suffice; no
handler/proxy edit"), and RAII teardown: the thread is a member declared *last* so it is destroyed
*first*, and its `request_stop()`+`join()` run in `stopSpiller()` called from the store dtor BEFORE
`backend`/`bufferProvider` members are destroyed (member-destruction order is reverse-declaration; we
make `jthread` the last member so it joins before `backend`). Handler-ownership was rejected: it spreads
the feature across `AggregationOperatorHandler` + the store + the proxy wiring for no lifecycle benefit
the store cannot provide.

**Start point (lazy).** The store is constructed in the lowering rule before the BufferManager exists;
the provider is stashed later in `setupSliceStoreProxy` (`DefaultTimeBasedSliceStoreRef.cpp:131-133`).
So `setBufferProvider` calls `startSpiller()` exactly once (idempotent, guarded by the existing
"already stashed → return" branch at `SpillableTimeBasedSliceStore.cpp:80-84`, so a repeat
`setBufferProvider` does NOT start a second thread). The thread therefore never runs before the
provider is available (it needs the provider only for unspill, but starting it post-provider keeps the
invariant trivially true and avoids a started-but-unusable thread).

**Stop+join point.** `stopSpiller()` = `request_stop()` + `spillerCv.notify_all()` + (jthread auto-joins
on destruction; we also `join()` explicitly in `stopSpiller` for determinism). Called from: (1) the
store destructor, before `backend`/`bufferProvider` go away; (2) `deleteState()` FIRST (quiesce), so no
spiller wake races with the tracked-set clear. `deleteState()` after quiescing clears `createdSlices`
alongside `spilledKeys`/`emittedKeys`. On a unit-test store with no provider stashed, the thread was
never started, so `stopSpiller` is a no-op (guarded by a `spillerStarted` flag).

## Sections

> Header + its `.cpp` share one section (one agent owns the pair). Each section = exactly one
> file/file-pair, one agent.

### Section 1: `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`  (+ paired `.cpp`)
- **Agent assignment:** implementation-1
- **Changes (header).**
  - New includes: `<atomic>`, `<condition_variable>`, `<mutex>`, `<thread>` (`std::jthread`,
    `std::stop_token`).
  - Drop `[[maybe_unused]]` from `softThresholdBytes`/`hardThresholdBytes` (lines 111-112; now enforced).
  - New private members:
    - `folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices;` — self-tracked
      creation order (map sorted ascending by `SliceEnd` ⇒ oldest-first scan is `begin()`→`end()`).
    - `std::atomic<Timestamp::Underlying> lastSeenWatermark{Timestamp::INITIAL_VALUE};` — monotonic
      build watermark learned from feeders (raw `uint64_t` so it is lock-free; `Timestamp` is a thin
      wrapper, store `.getRawValue()`).
    - `std::mutex evictionMutex;` — the outer serialiser (O-B3).
    - `std::condition_variable_any spillerCv;` — spiller wakeup + trigger-wait (used with
      `std::unique_lock<std::mutex>` on `evictionMutex`; `_any` not needed if always `std::mutex` —
      use `std::condition_variable` with `std::unique_lock<std::mutex>`).
    - `std::set<SliceEnd> spillInProgress;` — per-slice reservation; ALWAYS accessed under
      `evictionMutex` (a plain `std::set`, not `Synchronized`, because `evictionMutex` already guards it).
    - `std::jthread spillerThread;` — declared **LAST** so it joins first on destruction.
    - `std::atomic<bool> spillerStarted{false};` — start-once guard.
  - New method decls:
    - `std::vector<std::shared_ptr<Slice>> getSlicesOrCreate(Timestamp, const std::function<...>&) override;`
    - `[[nodiscard]] uint64_t residentBytes() const;` — store-wide Σ.
    - `void startSpiller();` / `void stopSpiller();`
    - `void runSpillerLoop(std::stop_token);` — the background loop body.
    - `void evictUntilUnderHard();` — synchronous hard path (worker thread).
    - `std::optional<SliceEnd> pickAndReserveColdSlice();` — under `evictionMutex`; returns a reserved
      sliceEnd or nullopt (no cold candidate). Caller spills outside the lock then calls `finalizeSpill`.
    - `void finalizeSpill(SliceEnd, const std::shared_ptr<SpillableAggregationSlice>&);` — under
      `evictionMutex`: spilledKeys.insert, spillInProgress.erase, notify.
    - `~SpillableTimeBasedSliceStore() override;` — declared so it can call `stopSpiller()`
      (the base is `final`-removed; A's class is `final` — keep `final`, add explicit dtor).
  - Update the class doc comment: replace the "single-threaded spike / multi-thread hardening is
    Increment B / Not thread-safe" lines (currently lines 37-53) with the B model (background spiller,
    `evictionMutex` discipline, store-owned lifecycle).
- **Changes (`.cpp`).**
  - **`getSlicesOrCreate` override:** call `DefaultTimeBasedSliceStore::getSlicesOrCreate(timestamp, createNewSlice)`
    (base lock already released on return — `DefaultTimeBasedSliceStore.cpp:101`). For the single returned
    slice, `createdSlices.wlock()->insert_or_assign(slice->getSliceEnd(), std::weak_ptr<Slice>(slice));`
    Then **soft/hard check** (O-B2): compute `residentBytes()`; if `> hardThresholdBytes` call
    `evictUntilUnderHard()`; else if `> softThresholdBytes` `spillerCv.notify_one()`. Return the base result.
  - **Watermark learning:** in `getTriggerableWindowSlices` and `getAllNonTriggeredSlices` overrides,
    after the base call, monotonically bump `lastSeenWatermark` to `max(current, globalWatermark.getRawValue())`
    (CAS loop on the atomic). `getAllNonTriggeredSlices` has no watermark arg → on the terminate path set
    `lastSeenWatermark = INVALID_VALUE` semantics? NO — instead leave it: terminate triggers everything via
    the feeder anyway; bumping to a sentinel could over-select. Decision: on `getAllNonTriggeredSlices`
    set `lastSeenWatermark = max(current, Timestamp::INVALID_VALUE)` is unsafe; instead **do not change
    watermark there** — the terminate path is single-shot and the spiller is quiesced before deleteState,
    so cold-selection during terminate is moot. (Document this.)
  - **`residentBytes()`:** `uint64_t sum=0; createdSlices.withRLock([&](auto& m){ for (auto& [end, weak] : m) { if (auto s = weak.lock()) sum += residentBytesOf(*s); } });` Skips expired weak_ptrs (GC-dropped slices). `residentBytesOf` already returns 0 for spilled.
  - **`pickAndReserveColdSlice()`:** `std::unique_lock lock(evictionMutex);` snapshot
    `wm = lastSeenWatermark.load();` iterate `createdSlices` (rlock) oldest-first; for first sliceEnd with
    `sliceEnd.getRawValue() < wm && !spillInProgress.contains(sliceEnd) && isSliceResident(sliceEnd) &&
    !emittedKeys.rlock()->contains(sliceEnd)` and `weak.lock()` non-null and `dynamic_pointer_cast<SpillableAggregationSlice>` non-null and `isResident()`: `spillInProgress.insert(sliceEnd); return sliceEnd;` else `return std::nullopt;`. (NOTE: cannot hold createdSlices.rlock() AND call code that re-locks it — copy candidate sliceEnds into a local vector first, or iterate a copied snapshot of keys. Use: copy `(sliceEnd, weak_ptr)` pairs into a local vector under one rlock, release, then evaluate predicates — avoids nesting createdSlices with the set locks.)
  - **`finalizeSpill(end, slice)`:** `std::unique_lock lock(evictionMutex); spilledKeys.wlock()->insert(end); spillInProgress.erase(end); spillerCv.notify_all();`
  - **One spill step (shared by soft loop & hard loop):** `auto picked = pickAndReserveColdSlice(); if (!picked) return false; auto slice = lookupSpillable(*picked); slice->spill(*backend); /* outside evictionMutex */ finalizeSpill(*picked, slice); return true;` where `lookupSpillable` re-locks `createdSlices` to `weak.lock()`+cast. **Edge:** if between reserve and spill the weak_ptr expired (GC), unreserve under lock and skip.
  - **`evictUntilUnderHard()`:** `while (residentBytes() > hardThresholdBytes) { if (!spillOneColdSlice()) { NES_WARNING(...proceed over hard, no cold candidate...); break; } }` — O-B5.
  - **`runSpillerLoop(stop)`:** `while (!stop.stop_requested()) { std::unique_lock lock(evictionMutex); spillerCv.wait_for(lock, 250ms, [&]{ return stop.stop_requested() || residentBytes() > softThresholdBytes; }); lock.unlock(); if (stop.stop_requested()) break; while (residentBytes() > softThresholdBytes) { if (!spillOneColdSlice()) { NES_DEBUG(...over soft, no cold candidate, sleeping...); break; } } }` — NOTE `residentBytes()` inside the predicate must not deadlock: `residentBytes` locks `createdSlices` (a `Synchronized` set), NOT `evictionMutex`, so it is safe to call while holding `evictionMutex` (lock order: evictionMutex → createdSlices, consistent everywhere). Verify `spillOneColdSlice` does NOT hold `evictionMutex` when called from the loop body (it takes/releases internally).
  - **`startSpiller()`:** `bool expected=false; if (spillerStarted.compare_exchange_strong(expected,true)) { spillerThread = std::jthread([this](std::stop_token st){ runSpillerLoop(st); }); }`
  - **`stopSpiller()`:** `if (spillerThread.joinable()) { spillerThread.request_stop(); spillerCv.notify_all(); spillerThread.join(); }`
  - **`setBufferProvider`:** after the provider is stashed (inside the existing `withWLock`, success
    branch only), call `startSpiller()`. (Idempotent: the "already stashed → return" branch prevents a
    second start.)
  - **`~SpillableTimeBasedSliceStore()`:** call `stopSpiller()` first. (Even though `jthread` auto-joins,
    explicit stop guarantees the loop exits before `backend`/`bufferProvider` are destroyed.)
  - **`forceSpill`:** make it take `evictionMutex` for the emitted-check→spill→spilledKeys.insert so the
    M1 TOCTOU (`SpillableTimeBasedSliceStore.cpp:203-205` note) is closed; mirror the spillInProgress
    reservation. Keep its public contract (throws on emitted, no-op on unknown/already-spilled).
  - **`unspillAndMarkEmitted`:** wrap the per-slice body in `std::unique_lock lock(evictionMutex)`, add
    the `while (spillInProgress.contains(end)) spillerCv.wait(lock);` wait, then the existing
    unspill+`spilledKeys.erase`+`emittedKeys.insert` (now under `evictionMutex`).
  - **`garbageCollectSlicesAndWindows`:** after the base GC + existing spilledKeys/backend prune, also
    prune `createdSlices` of expired/GC-dropped ends (erase entries whose `weak.lock()` is null OR whose
    base lookup is now empty). Take `createdSlices.wlock()` independently (never nested with the set locks).
  - **`deleteState`:** call `stopSpiller()` FIRST (quiesce), THEN the existing erase/clear, THEN
    `createdSlices.wlock()->clear()`, THEN base `deleteState`.
- **Logging.**
  - `getSlicesOrCreate` (hard fire): `NES_DEBUG("Hard threshold crossed (resident={} > hard={}); synchronous spill", residentBytes(), hardThresholdBytes)`.
  - `getSlicesOrCreate` (soft notify): `NES_TRACE("Soft threshold crossed (resident={} > soft={}); waking spiller", ..., softThresholdBytes)`.
  - `pickAndReserveColdSlice` (reserve): `NES_DEBUG("Reserved cold slice {} for spill (resident={})", end, residentBytes())`.
  - `finalizeSpill`: `NES_DEBUG("Spilled cold slice {}; resident now {}", end, residentBytes())`.
  - `evictUntilUnderHard` (no cold): `NES_WARNING("Over hard threshold (resident={} > hard={}) but no COLD slice to evict; proceeding over-budget", residentBytes(), hardThresholdBytes)`.
  - `runSpillerLoop` (no cold): `NES_DEBUG("Over soft threshold but no COLD candidate; spiller sleeping")`.
  - `unspillAndMarkEmitted` (waited on mid-spill): `NES_DEBUG("Trigger waited for in-progress spill of slice {} before unspill", end)`.
  - `startSpiller`: `NES_DEBUG("Started background spiller thread for spillable slice store")`.
  - `stopSpiller`: `NES_DEBUG("Stopped+joined background spiller thread")`.
- **Tests.** Covered by Section 3 (single-thread budget/eviction in `SpillableTimeBasedSliceStoreTest.cpp`)
  and Section 4 (concurrency stress). No new wiring in this section's CMake.

### Section 2: `nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStoreRef.cpp`  (NO EDIT — confirmation only)
- **Agent assignment:** implementation-1 (same agent; trivial, may be folded into Section 1's verification)
- **Changes:** NONE. `setupSliceStoreProxy` (lines 115-135) already calls `setBufferProvider`
  (line 133), which now triggers `startSpiller()` internally. **Store-owned ⇒ no proxy edit needed.**
  This section exists only to record that the start point is reached without modifying this file.
  (If during implementation it turns out `setBufferProvider` cannot start the thread cleanly — e.g. a
  test path that never calls `setupSliceStoreProxy` but still wants eviction — the test calls
  `setBufferProvider` directly, which the existing fixture already does at `SpillableTimeBasedSliceStoreTest.cpp:108`.)

### Section 3: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`  (EDIT — deterministic single-thread B tests)
- **Agent assignment:** implementation-2
- **Changes.** Add deterministic, single-thread tests (no background thread — drive eviction by calling
  the synchronous entry points / by setting small thresholds and creating slices). To make eviction
  observable WITHOUT the background thread, add a test-only entry by lowering thresholds in a dedicated
  `makeStoreWithThresholds(soft, hard)` helper (extend the existing `makeStore`). Because the
  background thread is store-owned and only started on `setBufferProvider`, **a test that wants pure
  synchronous behavior should construct the store but drive the HARD path** (which runs on the worker
  via `getSlicesOrCreate`); for SOFT, the test sets a tiny soft threshold and asserts the background
  thread eventually spills (poll with a timeout). New cases:
  - `residentBytesIsStoreWideSum`: create 3 tumbling slices, populate, assert `residentBytes()` ==
    Σ `residentBytesOf`.
  - `hardThresholdSpillsOldestColdSliceSynchronously`: tiny `hard`; create slice A (ts 50), populate
    past hard, advance watermark via `getTriggerableWindowSlices` is NOT wanted (that emits it) — instead
    create A, then create B (ts 250) so A is cold relative to a learned watermark. To learn the
    watermark without emitting A, call `getTriggerableWindowSlices(Timestamp(0))` (no-op, but does it set
    wm? globalWatermark 0 ⇒ no). **Approach:** drive watermark by a trigger that emits an UNRELATED later
    window; simpler — expose learned watermark via creating slices then calling a trigger with a
    watermark between A.end and B.end so ONLY A's window would be emitted… but emitting A makes it
    non-spillable. **Resolution for the deterministic test:** seed `lastSeenWatermark` through a feeder
    call whose returned set is empty for A — not possible. Therefore the single-thread test asserts the
    hard path via a slice made cold by GC-free watermark advance: create A (ts 50) + B (ts 150) + C
    (ts 250); call `getTriggerableWindowSlices(Timestamp(120))` → emits ONLY window [0,100) (A) and
    learns wm=120; then A is emitted (not spillable) but wm is now 120, so B (end 200) is NOT yet cold.
    To get a cold-but-unemitted slice: create A,B,C; call `getTriggerableWindowSlices(Timestamp(250))`
    learns wm=250 and emits A,B (ends 100,200 < 250) but NOT C (end 300). Now create D (ts 350):
    `residentBytes` over A,B (resident, emitted), C (resident, cold: end 300<? wm 250 → NO, 300>250).
    Hmm — wm 250 makes C (end 300) not cold. **Final deterministic recipe:** create slices at ts
    50,150,250,350 (ends 100,200,300,400); call `getTriggerableWindowSlices(Timestamp(350))` learns
    wm=350, emits A,B,C (ends 100/200/300 < 350); D (end 400) resident+filling. Now NONE are cold AND
    unemitted (A,B,C emitted; D not cold). So instead use forceSpill semantics for the deterministic
    *budget* assertions and rely on the **concurrency test** for the live cold-spill path. **Cleanest
    deterministic test of the hard path:** add a friend-free, test-visible synchronous method
    `forceEvictUnderHard()` is over-engineering; instead the single-thread tests assert: (1)
    `residentBytes()` sum correctness, (2) that creating slices over the hard threshold with a
    pre-learned watermark (set by a prior feeder call that emits an *earlier disjoint* window) spills
    the oldest COLD unemitted slice. Implement by: create E1(ts 50)…E5(ts 450); call
    `getTriggerableWindowSlices(Timestamp(150))` → learns wm=150, emits ONLY E1(end 100). Now E2(end
    200)…E5 are resident, NOT emitted, but only E2..E5 with end<wm(150) are cold → none yet. So advance:
    second feeder `getTriggerableWindowSlices(Timestamp(250))` learns wm=250, emits E2(end 200). Now
    wm=250: E3(end 300)..E5 not cold. The pattern shows cold+unemitted requires wm to pass an end
    without the feeder emitting that window — which the base feeder always couples. **Therefore the
    honest deterministic single-thread coverage is: budget math + the synchronous hard loop exercised
    via a unit hook.** Add a `protected`/`public` test-only method **`spillColdSlicesUnderHardForTest()`**
    that runs `evictUntilUnderHard()` after the test manually seeds `lastSeenWatermark` via a new
    test-only setter `setLastSeenWatermarkForTest(Timestamp)`. This keeps the production cadence intact
    while making the loop deterministically testable. Cases:
    - `setLastSeenWatermarkForTest` + 3 cold slices over hard → `evictUntilUnderHard` spills
      oldest-first until `residentBytes() <= hard`; assert exact set spilled + backend records.
    - over hard, all slices emitted (call a feeder first) → `evictUntilUnderHard` spills none, logs,
      returns (assert no spill, `residentBytes` unchanged).
    - over hard, all cold spilled, still over hard → proceeds (no throw), `residentBytes` minimized.
  - `garbageCollectPrunesCreatedSlices`: create + GC past window+size, assert `createdSlices` entry gone
    (via a `createdSlicesSizeForTest()` accessor) and `residentBytes()` recomputes correctly.
  - `deleteStateQuiescesSpiller`: construct store (spiller started via setBufferProvider), call
    `deleteState()`, assert no crash + tracked sets + createdSlices cleared.
  > Add the two test-only accessors (`setLastSeenWatermarkForTest`, `createdSlicesSizeForTest`,
  > `spillColdSlicesUnderHardForTest`) to Section 1's header guarded by nothing (they are cheap, const
  > where possible) — they expose no new production behavior, only deterministic drive points. Document
  > them as "test hooks".
- **Logging.** N/A (test).
- **Tests / wiring.** Already registered (`CMakeLists.txt:28`); no wiring change.

### Section 4: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp`  (NEW — multi-thread stress)
- **Agent assignment:** implementation-3
- **Changes.** New file mirroring `ConcurrentSynchronizationTest.cpp` structure: `std::array<std::jthread,N>`
  builder threads + a trigger thread + the (store-owned) background spiller, `std::latch` for start
  barrier + completion, atomic work-claim, deterministic post-condition. Reuse the existing fixture's
  helpers by copying `makeArgs`/`populate`/`sortedEntries` patterns (or factoring a tiny shared header —
  but SC2 "don't gold-plate" ⇒ copy the few helpers into this TU). Scenario:
  - Construct a `SpillableTimeBasedSliceStore` with a tiny `soft` (forces the spiller to run) and a
    `hard` slightly above soft, `setBufferProvider(bufferManager)` (starts the spiller).
  - N=8 builder jthreads each repeatedly: pick a timestamp from a per-thread strictly-increasing stream,
    `getSlicesOrCreate(ts)`, `populate(slice, WorkerThreadId(threadIdx), k, offset)` — exercising the
    create cadence + concurrent residentBytes/eviction.
  - 1 trigger jthread repeatedly calls `getTriggerableWindowSlices(advancingWatermark)` and, for each
    returned slice, reads every worker `HashMapPtr` and folds a checksum over the entries (this is the
    "probe" — asserts the raw `HashMap*` is valid, i.e. NOT spilled out from under it ⇒ exercises the
    spill-vs-emit race / async-probe-drain).
  - Deterministic post-condition: after all builders join and a final `getAllNonTriggeredSlices()` drain,
    the **sum of all triggered checksums == the sum of all inserted (key,value) contributions** (every
    tuple is accounted for exactly once; nothing lost to a half-freed spill). Also assert: no crash, and
    `EXPECT_TRUE` that at least one spill occurred (a `spillCountForTest()` accessor) so the test
    actually exercised eviction.
  - **Spill-vs-trigger race:** because soft is tiny and the trigger advances the watermark, cold slices
    are simultaneously spill candidates and trigger targets; the checksum equality is the UAF/half-free
    detector. Run `numIterations` (e.g. 50) loops to raise Heisenbug-catch probability (mirrors the
    template's iteration loop).
- **Logging.** Test uses `NES_INFO` at SetUp/TearDown (template parity).
- **Tests / wiring.** Section 5 adds the `add_nes_physical_operator_test(...)` line.

### Section 5: `nes-physical-operators/tests/CMakeLists.txt`  (EDIT — register the new test)
- **Agent assignment:** implementation-3 (same agent as Section 4)
- **Changes.** Append after line 28:
  `add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest SpillableTimeBasedSliceStoreConcurrencyTest.cpp)`
  No label edits (`gtest_discover_tests` auto-registers; `-LE 'Systest|DockerCompose'` auto-includes it).

## Parallel groups

- **Sequential spine (data dependency):** Section 1 (store hpp/cpp incl. test hooks) MUST land before
  Sections 3 and 4 (they call its new methods/hooks). Section 2 is a no-op confirmation folded into
  Section 1.
- **Group P1 (after Section 1):** Section 3 (implementation-2) and Section 4+5 (implementation-3) run
  **in parallel** — different files, both depend only on Section 1's public surface + test hooks.
- So: `[Section 1]` → `{ [Section 3] || [Section 4 + Section 5] }`.

## PR boundaries

Map to SYNTHESIS PR-1/2/3, but COLLAPSE PR-1+PR-2 because store-ownership makes the background thread
and the synchronous path land in the same `.cpp` (splitting them would ship a half-wired store). Two PRs:

- **PR-1 (enumeration + budget + synchronous hard path + single-thread tests).** = SYNTHESIS PR-1 ∪ PR-2.
  Section 1's non-thread parts (`getSlicesOrCreate` override, `createdSlices`, `lastSeenWatermark`,
  `residentBytes`, `evictUntilUnderHard`, GC/deleteState pruning, test hooks) + Section 3. Gate: extended
  `SpillableTimeBasedSliceStoreTest` green, full suite ≥ 805.
- **PR-2 (concurrency: evictionMutex + reservation + trigger-wait + background jthread + lifecycle).** =
  SYNTHESIS PR-3 (highest risk). Section 1's threading parts (`evictionMutex`, `spillInProgress`,
  `spillerCv`, `runSpillerLoop`, `startSpiller`/`stopSpiller`, `setBufferProvider` start hook, dtor,
  `forceSpill`/`unspillAndMarkEmitted` joining the serialiser) + Section 4 + Section 5. Gate: the new
  stress test green over its iteration loop. **If concurrency cannot be made stress-clean, ESCALATE
  rather than ship** (per SYNTHESIS).

Each PR is reviewed by the per-PR cpp-review pass; the LOCKED items list (below) is the reviewer's
"do-not-flag" set.

## Test plan

**Deterministic single-thread (PR-1, Section 3) — concrete assertions:**
- `residentBytesIsStoreWideSum`: Σ over created slices == `residentBytes()`; spilling one drops it by
  that slice's `residentBytesOf`.
- `hardEvictsOldestColdFirst`: seed `setLastSeenWatermarkForTest`, fill 3 cold slices over `hard`,
  `spillColdSlicesUnderHardForTest()`; assert oldest sliceEnd spilled first, loop stops once
  `residentBytes() <= hard`, backend has exactly the spilled slices' records, `isSliceResident` false
  for them / true for the rest.
- `hardNoColdCandidateProceeds`: all slices emitted (drive a feeder), over hard →
  `spillColdSlicesUnderHardForTest()` spills nothing, no throw, `residentBytes` unchanged (O-B5 hard).
- `hardStillOverAfterAllColdSpilled`: every slice cold + still over hard after spilling all → no throw,
  `residentBytes` minimized (O-B5 terminal).
- `gcPrunesCreatedSlices` + `deleteStateClearsAndQuiesces`: lifecycle/bookkeeping.

**Concurrency stress (PR-2, Section 4) — new ctest `SpillableTimeBasedSliceStoreConcurrencyTest`:**
- **Post-condition:** Σ(triggered+drained checksums) == Σ(inserted contributions) over all iterations
  ⇒ no tuple lost to a half-freed/UAF spill. This is the **spill-vs-emit** detector: a slice spilled
  after the trigger snapshotted its `HashMap*` would corrupt/segfault the probe-fold; the equality fails
  or the test crashes.
- **spill-vs-trigger race:** tiny soft + advancing watermark ⇒ cold slices are simultaneously spill
  candidates and trigger targets; `spillInProgress`+`spillerCv` wait must make every triggered slice end
  resident+emitted. Asserted by the same checksum equality + `EXPECT_GT(store->spillCountForTest(), 0)`
  (eviction actually happened).
- **No-crash / no-deadlock:** the test must complete within ctest's timeout across `numIterations`
  (~50) — a deadlock (e.g. evictionMutex held over I/O) would hang and fail the gate.
- New ctest name auto-discovered: `SpillableTimeBasedSliceStoreConcurrencyTest.<TestName>`.

**Regression:** full `ctest -LE 'Systest|DockerCompose'` stays ≥ 805 (the baseline) on both PRs.

## Risks & mitigations

- **Teardown order (R2-lifetime, HIGH).** `spillerThread` declared LAST ⇒ destroyed FIRST; `stopSpiller()`
  in the dtor + as the FIRST step of `deleteState()` guarantees the loop exits before `backend`/
  `bufferProvider` are gone. *Mitigation test:* `deleteStateQuiescesSpiller`. *Risk if wrong:* UAF on
  shutdown — caught by the stress test's teardown across 50 iterations.
- **TOCTOU / UAF (R1/M1, ABSOLUTE).** Spiller pick→reserve and trigger emitted-marking serialized by
  `evictionMutex`; I/O outside the lock with `spillInProgress` reservation; trigger WAITS on a mid-spill
  slice. *Mitigation:* the checksum post-condition + `EXPECT_GT(spillCount,0)`. This is the single most
  important correctness property of B.
- **Heisenbug / no-TSan ceiling (HIGH, honest).** TSan is NOT runnable here (deps baked
  `sanitizer=none`). The stress test is probabilistic: it reduces but cannot eliminate race risk.
  *Documented ceiling:* final concurrency confidence comes from the out-of-gate thesis systest under
  Docker memory limits (MS3/MS4). **If R1/R2 cannot be made stress-clean, ESCALATE — do not ship
  unverified concurrency.**
- **NH2 stall (throughput/latency cliff).** Holding `evictionMutex` across RocksDB I/O would serialise
  all triggers behind disk. *Mitigation:* reserve-then-I/O-outside-lock; only the concurrent trigger of
  the *same* mid-spill slice waits (rare). The one accepted exception (unspill I/O under the lock,
  O-B3 carve-out) is bounded to one slice and contends only with the spiller, not the build path.
- **createdSlices lock nesting (NH1).** `residentBytes`/`pickAndReserveColdSlice` must snapshot
  `createdSlices` into a local vector under a single rlock, then evaluate the set-lock predicates AFTER
  releasing it — never nest `createdSlices` with `spilledKeys`/`emittedKeys`, and never take base
  `slices`/`windows` under `evictionMutex`. *Mitigation:* code review checklist item; the lock order
  `evictionMutex → (createdSlices | one set)` is the only legal order.
- **Watermark lag under-selects (safe-by-design).** A lagging `lastSeenWatermark` only ever
  under-selects cold slices ⇒ never spills something the engine still considers hot. No mitigation
  needed; documented.
- **Stale SliceCache entry (R3, contract-dependent).** A spilled cold slice's `HashMap*` cached in a
  worker's SliceCache would be a UAF IF a build tuple re-landed in its range — prevented by the
  watermark-monotonicity / no-late-data contract (INV-3). We RESPECT this (no cache change). *Mitigation:*
  the stress test never feeds a tuple into an already-cold/spilled range (per-thread strictly-increasing
  timestamps); the no-late-data precondition is documented in the PR.
- **ABI / blast radius.** Self-track + threading are subclass-private (option a). No base-header edit ⇒
  the 805 baseline regression surface is unchanged — the explicit win over the exploration's option c.
