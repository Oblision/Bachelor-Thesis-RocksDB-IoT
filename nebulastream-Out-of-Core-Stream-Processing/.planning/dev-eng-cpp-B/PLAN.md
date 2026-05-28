# Implementation Plan — Increment B (background-spiller auto-eviction, tumbling agg)

> Canonical reconciliation of `PLAN-A.md` + `PLAN-B.md` (Phase 3b). Branch
> `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b` (Increment A complete, suite 805/805 green,
> clean tree). Build/test ONLY in Docker `nebulastream/nes-development:local-rocksdb`,
> `cmake-build-debug`, `ninja` + `ctest -LE 'Systest|DockerCompose'`. Authoritative scope =
> `SYNTHESIS.md` + `DESIGN_BRIEF_INCREMENT_B.md`. The two architects AGREE on every major decision;
> this document merges them and applies the orchestrator's resolution of the three divergences.
> Every LOCKED item in SYNTHESIS §"Locked decisions" is carried verbatim and is the reviewer's
> do-NOT-flag set.

---

## Summary

Increment B turns the Increment-A forced-spill spike into automatic, multi-thread-safe out-of-core
eviction for **tumbling** windowed aggregation. The `SpillableTimeBasedSliceStore`:

- **Self-tracks** every created slice in `folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>>
  createdSlices`, recorded in a `getSlicesOrCreate` override AFTER the base call returns (weak_ptr so
  the base GC's shared_ptr drop still frees the slice; the eviction scan locks each weak_ptr and skips
  expired entries). No core base-class edit (O-B1 = option a self-track).
- **Learns the build watermark** monotonically (`lastSeenWatermark`) in the trigger feeders
  (`getTriggerableWindowSlices`, `getAllNonTriggeredSlices`) — a lagging value only ever
  *under*-selects cold slices (safe).
- Computes store-wide `residentBytes()` = Σ over `createdSlices` of the existing `residentBytesOf`
  (0 for spilled), touching no private base map.
- Runs a **store-owned `std::jthread`** background spiller that preemptively spills the oldest COLD
  slices when resident memory crosses the **soft** threshold, so worker threads keep processing
  (best tail-latency story, RQ2).
- Enforces the **hard** threshold **synchronously** on the worker path at the slice-creation cadence
  (inside the `getSlicesOrCreate` override) — the OOM backstop (RQ1: completes where it would OOM).
  Eviction NEVER runs on the per-tuple JIT hot path.
- Serializes all eviction decisions and emitted-marking under ONE outer `evictionMutex` plus a
  per-slice `spillInProgress` reservation set; ALL RocksDB I/O (both `spill()` and `unspill()`) runs
  OUTSIDE the mutex; a trigger that wants a mid-spill slice WAITS on a condvar until the spiller
  finalizes, then unspills + marks emitted — so a triggered slice always ends resident+emitted, never
  half-freed. This closes A's M1 TOCTOU and the spill-vs-emit / spill-vs-trigger UAF.

Validation ceiling: no TSan in this environment; the in-gate signal is a no-sanitizer multi-threaded
stress test (~50+ iterations, checksum/post-condition as the UAF detector). Final concurrency
confidence comes from the out-of-gate thesis systest (MS3/MS4). If the concurrency PR cannot be made
stress-clean → **ESCALATE**, do not ship.

---

## Resolved decisions

All decisions below are SETTLED — the two architects agree; reviewers must NOT re-open them.

### O-B1 — Cold-slice enumeration = SELF-TRACK (option a)
`folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices`, recorded in a
`getSlicesOrCreate` override AFTER the base call returns. `weak_ptr` so the base GC still frees the
slice (the scan locks each weak_ptr and skips expired entries). Pruned in
`garbageCollectSlicesAndWindows` / `deleteState`. No core base-class edit (base `slices`/`windows`
stay private). Tumbling ⇒ one slice == one window ⇒ `cold = sliceEnd < lastSeenWatermark` is decidable
from self-tracking alone.

### O-B-watermark — learn the build watermark in the trigger feeders
`lastSeenWatermark` is monotonically bumped to `max(current, globalWatermark)` in
`getTriggerableWindowSlices` (and on `getAllNonTriggeredSlices`, the terminate path, where coldness is
moot because the spiller is quiesced before `deleteState`). A lagging value only under-selects cold
slices → safe (never spills something the engine still considers hot).

### O-B-residentBytes — store-wide Σ
`residentBytes()` = Σ over `createdSlices` (read-lock the map, `lock()` each weak_ptr, skip expired) of
the existing `residentBytesOf(*slice)` (returns 0 for spilled). Does NOT take `evictionMutex` (a
read-only snapshot; tolerates a slightly stale value because eviction only ever LOWERS resident bytes
between ticks — a stale-high read causes at most one extra safe eviction pass; a stale-low read
self-heals on the next cadence tick).

### O-B2 — Eviction trigger, cadence & wakeup
- **Soft path / background-thread wakeup = condvar (PRIMARY), with a bounded `wait_for` poll as a
  safety net.** The spiller blocks on `spillerCv` (paired with `evictionMutex`). It is woken
  explicitly (`notify_one`) at two cadences where the budget / `emittedKeys` / watermark are freshest:
  1. the `getSlicesOrCreate` override (runs ONLY on a SliceCache MISS — i.e. once per (worker, new
     slice) window boundary, NEVER per tuple) when `residentBytes() > softThresholdBytes`; and
  2. the trigger feeders (after unspill-on-read + emitted-marking), where newly cold slices appear.
  The bounded `wait_for(SPILLER_POLL_INTERVAL)` safety net drains a store that stopped creating slices
  but is still over soft, and re-checks the stop token promptly.
- **Hard path = SYNCHRONOUS, on the worker thread, at the SAME slice-creation cadence** — inside the
  `getSlicesOrCreate` override, immediately AFTER the base call returns and AFTER `createdSlices` is
  updated. If `residentBytes() > hardThresholdBytes`, the worker thread itself runs the
  pick-cold→reserve→spill loop until back under hard or no cold candidate remains.
- The SOFT check at the same point is a cheap compare that SIGNALS the condvar (does not spill inline),
  keeping the worker path latency-bounded. Eviction NEVER runs on the per-tuple JIT hot path
  (`getDataStructureRef` → SliceCache hit, the common case, never enters the store).

### O-B3 — Locking model + reservation semantics
ONE `std::mutex evictionMutex` (strictly OUTER, above the existing folly Synchronized locks; base
`slices`/`windows` are NEVER acquired under it). A per-slice `std::set<SliceEnd> spillInProgress`
reservation set guarded by `evictionMutex` (a plain set — `evictionMutex` already guards it).

**The eviction-mutex invariant (verbatim — state crisply in code comments and the PR):**

> *evictionMutex covers ONLY in-memory decisions — pick-cold, confirm-not-emitted, reserve (insert
> spillInProgress), mark-emitted, clear-reservation. ALL RocksDB I/O — both `spill()` and `unspill()`
> — happens OUTSIDE evictionMutex, protected by the per-slice spillInProgress reservation; the rare
> spill-vs-trigger-same-slice collision is handled by a condvar wait.*

A trigger that wants a mid-spill slice **WAITS** (condvar) under `evictionMutex` until the spiller
finalizes (the slice lands in `spilledKeys`, leaves `spillInProgress`), then marks it emitted and
unspills it OUTSIDE the mutex — a triggered slice always ends resident+emitted, never half-freed.
**Skipping was rejected**: skipping would hand a non-resident slice to the async probe = UAF on the
raw `HashMap*`. The ONE change to A's "never hold two store locks (folly)" rule is that
emitted-marking now also participates in `evictionMutex`; the folly set locks (`spilledKeys`,
`emittedKeys`, `createdSlices`) are still each taken one-at-a-time and never nested with each other
(H1 preserved). `evictionMutex` is always acquired first; the only legal lock order is
`evictionMutex → (createdSlices | one folly set) → release`. NH1 preserved: no base map under
`evictionMutex` — the only base touch inside a critical section is `weak_ptr::lock()` on an
already-tracked slice.

### O-B5 — Eviction policy edge cases
- **Over budget, no COLD candidate** (everything filling or emitted):
  - *Soft (background thread):* log once (`NES_TRACE`), go back to `wait_for` (sleep), re-scan on the
    next wake. NEVER spills a filling/emitted slice, NEVER blocks a worker.
  - *Hard (synchronous worker path):* proceed over-budget + `NES_WARNING`; do NOT block (blocking
    would stall the very pipeline that must advance the watermark to *create* cold slices), do NOT
    throw, NEVER spill a filling/emitted slice.
- **Hard still over budget after ALL cold slices spilled:** proceed + `NES_WARNING`, do NOT error.
  The hard threshold is a backstop to avoid OOM; once every cold slice is on disk the only resident
  memory left is genuinely-needed filling/emitted state we are forbidden to spill. A real OOM then is
  a query-sizing problem surfaced by the systest (MS3/MS4), not a spill-store bug; the WARNING makes
  it diagnosable.

### Ownership — STORE-OWNED `std::jthread`
`std::jthread spiller` declared as the LAST member of `SpillableTimeBasedSliceStore` (so reverse-order
member destruction joins it first). Started LAZILY at the end of `setBufferProvider` (idempotent via
the existing "already stashed → return" guard at `SpillableTimeBasedSliceStore.cpp:80-84`; reached
from `setupSliceStoreProxy`, `DefaultTimeBasedSliceStoreRef.cpp:131-133`). Stopped (`request_stop()` +
`spillerCv.notify_all()`) and JOINED:
1. as the FIRST step of `~SpillableTimeBasedSliceStore()` (before `backend`/`bufferProvider` are
   destroyed and before the base dtor runs `deleteState`); AND
2. as the FIRST step of `deleteState()` (quiesce before clearing tracked state).
No handler/proxy edits needed beyond the existing `setBufferProvider` site (the store unit test calls
`setBufferProvider` directly, so the spiller also starts under test). Handler-ownership rejected:
spreads the feature across `AggregationOperatorHandler` + the store + proxy wiring for no lifecycle
benefit the store cannot provide.

### Reconciled divergence 1 — Unspill-on-read I/O location = UNIFORM (adopt B; reject A's carve-out)
ALL RocksDB I/O — both `spill()` and `unspill()` — happens OUTSIDE `evictionMutex`, protected by the
per-slice `spillInProgress` reservation; the rare spill-vs-trigger-same-slice collision is handled by
the condvar wait (see the invariant above). *Rationale:* SYNTHESIS flagged NH2 (I/O under lock =
throughput/tail-latency cliff, directly harms RQ2); a single uniform "no disk under the mutex" rule is
easier to review than A's asymmetric one. **CONSEQUENCE (binding on the stress test):** the
concurrency stress test MUST force the spiller and a trigger onto the SAME `SliceEnd` to exercise the
cv-wait path — otherwise that path is untested.

### Reconciled divergence 2 — Poll interval = 100ms, named constexpr
`constexpr std::chrono::milliseconds SPILLER_POLL_INTERVAL{100};` in the `.cpp`. It is ONLY the safety
net behind the condvar (which is primary), so it never adds latency on the loaded path.

### Reconciled divergence 3 — Section/PR granularity = ONE store-implementation section, 3-commit progression
The store header + its `.cpp` are ONE section owned by a single implementation agent (a file pair
cannot be split across parallel agents without trampling the member list / its implementation). It is
built as a 3-commit logical progression that doubles as the per-PR review boundaries (PR-1 → PR-2 →
PR-3; order is load-bearing). Tests are their OWN sections (separate files, parallelizable in the RED
phase): the deterministic single-thread tests extend `SpillableTimeBasedSliceStoreTest.cpp`; the NEW
concurrency stress test is `SpillableTimeBasedSliceStoreConcurrencyTest.cpp` + a one-line CMake
registration. *Rationale:* honors "one file pair = one agent" while keeping the SYNTHESIS PR-1/2/3
review boundaries meaningful and not artificially fragmenting the file.

---

## Sections

### Section 1: `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` + `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`

- **Agent assignment:** implementation-1 (owns the header + its `.cpp` as one inseparable unit).
- **Internal build order:** strictly sequential PR-1 → PR-2 → PR-3 (stacked commits on this file
  pair). Each PR adds the next layer + makes its gate green.

#### New members (header) — exact types, declaration order matters (`spiller` LAST)
```cpp
// O-B1 self-track: map sorted ascending by SliceEnd ⇒ oldest-first scan is begin()→end().
folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>> createdSlices;
// Learned build watermark (monotonic). Store the raw underlying value so the atomic is lock-free.
std::atomic<Timestamp::Underlying> lastSeenWatermark{Timestamp(0).getRawValue()};
mutable std::mutex evictionMutex;            // O-B3 OUTER serializer (mutable: residentBytes()/dtor)
std::condition_variable spillerCv;           // spiller wakeup + trigger-wait (paired with evictionMutex)
std::set<SliceEnd> spillInProgress;          // per-slice reservation; ALWAYS under evictionMutex
bool quiesced = false;                        // set under evictionMutex; stops the spiller picking
std::atomic<bool> spillerStarted{false};      // start-once guard (belt-and-braces with setBufferProvider)
std::jthread spiller;                         // store-owned; declared LAST so it joins first
```
Also: drop `[[maybe_unused]]` from `softThresholdBytes`/`hardThresholdBytes` (currently lines
111-112; now enforced). Add includes: `<atomic>`, `<condition_variable>`, `<mutex>`, `<thread>`,
`<chrono>`. Update the class doc comment (currently lines 37-53) — replace the "single-threaded spike /
Not thread-safe / multi-thread hardening is Increment B" lines with the B model (background spiller,
`evictionMutex` discipline, store-owned lifecycle, uniform no-I/O-under-lock).

#### New method signatures (header)
```cpp
~SpillableTimeBasedSliceStore() override;                                 // join spiller FIRST
std::vector<std::shared_ptr<Slice>> getSlicesOrCreate(
    Timestamp timestamp,
    const std::function<std::vector<std::shared_ptr<Slice>>(SliceStart, SliceEnd)>& createNewSlice) override;
[[nodiscard]] uint64_t residentBytes() const;                            // store-wide Σ
private:
void recordCreatedSlice(const std::shared_ptr<Slice>& slice);
std::optional<SliceEnd> pickAndReserveColdSlice();                       // takes evictionMutex internally
void finalizeSpill(SliceEnd end);                                        // takes evictionMutex internally
bool spillOneColdSlice();                                                // pick→reserve→spill(I/O outside)→finalize
void evictUntilUnderHard();                                             // synchronous worker path (O-B2 hard)
void startSpiller();
void stopSpiller();
void spillerLoop(std::stop_token stopToken);                            // background loop body
// PR-1 test hooks (documented "test hooks"; expose only deterministic drive points, no new behavior):
void setLastSeenWatermarkForTest(Timestamp wm);
[[nodiscard]] std::size_t createdSlicesSizeForTest() const;
void spillColdSlicesUnderHardForTest();                                  // runs evictUntilUnderHard() directly
[[nodiscard]] std::size_t spillCountForTest() const;                     // monotonic spill counter for the stress test
```

#### Algorithm per layer (PR-1/2/3)

**PR-1 — enumeration + budget (single-thread, NO eviction yet):**
- **`getSlicesOrCreate` override:** `auto result = DefaultTimeBasedSliceStore::getSlicesOrCreate(timestamp,
  createNewSlice);` (base lock already released on return — `DefaultTimeBasedSliceStore.cpp:101`,
  reached via the cache-miss proxy at `DefaultTimeBasedSliceStoreRef.cpp:59`). `INVARIANT(result.size()
  == 1)` (tumbling). `recordCreatedSlice(result[0])`; return `result`. (Budget checks added in PR-2.)
- **`recordCreatedSlice`:** `createdSlices.wlock()->insert_or_assign(slice->getSliceEnd(),
  std::weak_ptr<Slice>(slice));`
- **Watermark learning:** in `getTriggerableWindowSlices`, after `unspillAndMarkEmitted(result)`,
  CAS-max `lastSeenWatermark` to `max(current, globalWatermark.getRawValue())`. In
  `getAllNonTriggeredSlices`, leave `lastSeenWatermark` unchanged (terminate path; spiller quiesced
  before `deleteState`, so cold-selection there is moot — document this).
- **`residentBytes()`:** `uint64_t sum = 0; createdSlices.withRLock([&](auto& m){ for (auto& [end, weak]
  : m) { if (auto s = weak.lock()) sum += residentBytesOf(*s); } }); return sum;` (no `evictionMutex`).
- **`garbageCollectSlicesAndWindows`:** keep the existing base GC + spilledKeys/backend/emittedKeys
  prune (`SpillableTimeBasedSliceStore.cpp:151-181`), then ALSO take `createdSlices.wlock()` (never
  nested with the set locks) and erase entries whose `weak.lock()` is null (GC-dropped).
- **`deleteState`:** (PR-1 form) after the existing erase/clear (`SpillableTimeBasedSliceStore.cpp:183-199`),
  also `createdSlices.wlock()->clear()`. (The spiller-quiesce step is added in PR-3.)

**PR-2 — eviction policy (single-thread, eviction entry points callable from tests, NO thread yet):**
- **`pickAndReserveColdSlice()`:** `std::unique_lock lock(evictionMutex); if (quiesced) return
  std::nullopt;` snapshot `wm = lastSeenWatermark.load();` Copy `(end, weak)` pairs into a local vector
  under ONE `createdSlices.rlock()`, RELEASE it (never nest `createdSlices` with the set locks), then
  evaluate predicates: first `end` (ascending) with `end.getRawValue() < wm` (cold) ∧ `weak.lock()`
  non-null ∧ `dynamic_pointer_cast<SpillableAggregationSlice>` non-null ∧ `isResident()` ∧
  `isSliceResident(end)` ∧ `!emittedKeys.rlock()->contains(end)` ∧ `!spillInProgress.contains(end)` →
  `spillInProgress.insert(end); return end;` else `return std::nullopt;`.
- **`finalizeSpill(end)`:** `std::unique_lock lock(evictionMutex); spilledKeys.wlock()->insert(end);
  spillInProgress.erase(end); spillerCv.notify_all();` (notifies any trigger waiting on this slice).
- **`spillOneColdSlice()`:** `auto picked = pickAndReserveColdSlice(); if (!picked) return false; auto
  spillable = lockSpillable(*picked);` — if the weak_ptr expired between reserve and lookup, take
  `evictionMutex`, `spillInProgress.erase(*picked)`, return true (skip; treat as progress).
  `spillable->spill(*backend);` /* I/O OUTSIDE evictionMutex */ `++spillCount; finalizeSpill(*picked);
  return true;`.
- **`evictUntilUnderHard()`:** `while (residentBytes() > hardThresholdBytes) { if (!spillOneColdSlice())
  { NES_WARNING(...); break; } }` — O-B5 (proceed over-budget, never throw, never block).
- **`getSlicesOrCreate` override (extend PR-1):** after `recordCreatedSlice`, compute `auto resident =
  residentBytes();` then: `if (resident > hardThresholdBytes) evictUntilUnderHard();` (synchronous,
  worker thread). The soft *signal* is wired in PR-3 (it needs the condvar/thread).
- **`forceSpill`:** wrap the emitted-check → spill → `spilledKeys.insert` in `evictionMutex` + a
  `spillInProgress` reservation (closes the M1 TOCTOU at `SpillableTimeBasedSliceStore.cpp:203-210`)
  with the I/O outside the lock; keep its public contract (throws `SpillStoreFailure` on emitted,
  no-op on unknown/already-spilled) so the stress test may use it interchangeably.

**PR-3 — concurrency (evictionMutex live + reservation + trigger-waits + jthread + lifecycle). THIS IS
THE ESCALATE-IF-NOT-STRESS-CLEAN GATE.**
- **`unspillAndMarkEmitted` (rewrite — currently `SpillableTimeBasedSliceStore.cpp:90-115`):** per
  returned spillable slice, with `sliceEnd = spillable->getSliceEnd()`:
  ```
  { std::unique_lock lock(evictionMutex);
    while (spillInProgress.contains(sliceEnd)) spillerCv.wait(lock);   // WAIT for the spiller to finalize
    emittedKeys.wlock()->insert(sliceEnd); }                           // claim it: spiller rejects forever
  if (!spillable->isResident()) {                                      // was spilled → unspill OUTSIDE the mutex
      INVARIANT(provider != nullptr, "buffer provider must be stashed before an unspill-on-read");
      spillable->unspill(*backend, *provider);
      spilledKeys.wlock()->erase(sliceEnd); }
  ```
  Inserting into `emittedKeys` BEFORE releasing `evictionMutex` guarantees no NEW spill of this slice
  can start after the wait. The unspill I/O runs outside the mutex (it cannot race a spiller because
  the slice is now emitted) — uniform no-I/O-under-lock (reconciled divergence 1).
- **`getSlicesOrCreate` override (extend PR-2):** after the hard check, add the soft signal:
  `else if (resident > softThresholdBytes) spillerCv.notify_one();`.
- **Trigger feeders:** after the watermark CAS-max, `spillerCv.notify_one()` (newly cold slices may
  have appeared at the trigger cadence).
- **`spillerLoop(stopToken)`:**
  ```
  while (!stopToken.stop_requested()) {
      std::unique_lock lock(evictionMutex);
      spillerCv.wait_for(lock, SPILLER_POLL_INTERVAL,
          [&]{ return stopToken.stop_requested() || residentBytes() > softThresholdBytes; });
      lock.unlock();
      if (stopToken.stop_requested()) break;
      while (residentBytes() > softThresholdBytes) {
          if (!spillOneColdSlice()) { NES_TRACE(...over soft, no cold candidate, sleeping...); break; }
      }
  }
  ```
  (`residentBytes()` in the predicate locks `createdSlices`, not `evictionMutex` — lock order
  `evictionMutex → createdSlices` is consistent everywhere, no deadlock. `spillOneColdSlice` takes/
  releases `evictionMutex` internally, so it is called with the loop NOT holding it.)
- **`startSpiller()`:** `bool expected = false; if (spillerStarted.compare_exchange_strong(expected,
  true)) { spiller = std::jthread([this](std::stop_token st){ spillerLoop(st); }); }`
- **`stopSpiller()`:** `{ std::unique_lock lock(evictionMutex); quiesced = true; } if
  (spiller.joinable()) { spiller.request_stop(); spillerCv.notify_all(); spiller.join(); }`
- **`setBufferProvider`:** at the END of the existing `withWLock` success branch (after stashing,
  inside `SpillableTimeBasedSliceStore.cpp:74-88`), call `startSpiller()`. Idempotent: the existing
  "already stashed → return" branch prevents a second start.
- **`~SpillableTimeBasedSliceStore()`:** call `stopSpiller()` FIRST — guarantees the loop exits before
  `backend`/`bufferProvider` are destroyed and before the base dtor runs `deleteState`.
- **`deleteState()` (extend PR-1 form):** FIRST `stopSpiller()` (quiesce + join), THEN the existing
  erase/clear, THEN `createdSlices.wlock()->clear()`, THEN base `deleteState`.

#### The eviction-mutex invariant (verbatim, place as a code comment on `evictionMutex` + in the PR-3 body)
> *evictionMutex covers ONLY in-memory decisions — pick-cold, confirm-not-emitted, reserve (insert
> spillInProgress), mark-emitted, clear-reservation. ALL RocksDB I/O — both `spill()` and `unspill()`
> — happens OUTSIDE evictionMutex, protected by the per-slice spillInProgress reservation; the rare
> spill-vs-trigger-same-slice collision is handled by a condvar wait.*

#### Exact `NES_*` log lines + their functions
- `getSlicesOrCreate` (hard fire): `NES_DEBUG("Hard threshold crossed (resident={} > hard={}); synchronous spill on worker path", resident, hardThresholdBytes)`.
- `getSlicesOrCreate` (soft notify): `NES_TRACE("Soft threshold crossed (resident={} > soft={}); waking spiller", resident, softThresholdBytes)`.
- `pickAndReserveColdSlice` (reserve): `NES_DEBUG("Reserved cold slice {} for spill", end)`.
- `finalizeSpill`: `NES_DEBUG("Spilled cold slice {}; resident now {}", end, residentBytes())`.
- `evictUntilUnderHard` (no cold / still over): `NES_WARNING("Over hard budget ({} > {}) but no COLD slice to evict; proceeding over-budget", residentBytes(), hardThresholdBytes)`.
- `spillerLoop` (no cold): `NES_TRACE("Over soft ({} > {}) but no cold candidate; spiller sleeping", residentBytes(), softThresholdBytes)`.
- `unspillAndMarkEmitted` (waited mid-spill): `NES_TRACE("Trigger of slice {} waiting on in-flight spill", sliceEnd)`.
- `startSpiller`: `NES_DEBUG("Started background spiller thread (soft={}B, hard={}B)", softThresholdBytes, hardThresholdBytes)`.
- `stopSpiller`: `NES_DEBUG("Stopping and joining background spiller thread")`.
- `deleteState` (quiesce): `NES_TRACE("Quiescing spiller before deleteState")`.

### Section 2: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` (EDIT — deterministic single-thread)

- **Agent assignment:** implementation-2 (RED-phase, parallel with Section 3 after Section 1's public
  surface + test hooks exist).
- **Changes.** Add `makeStoreWithThresholds(soft, hard, InMemorySpillBackend** backendOut)` (overload
  the existing `makeStore` fixture at lines 95-110, defaulting to the current large thresholds so
  existing tests are unaffected). Use the PR-1 test hooks
  (`setLastSeenWatermarkForTest`/`createdSlicesSizeForTest`/`spillColdSlicesUnderHardForTest`/
  `spillCountForTest`) so the synchronous hard loop is deterministically drivable WITHOUT the
  background thread (the base trigger feeders couple watermark-advance to emit, so a cold-but-unemitted
  slice is only producible by seeding the watermark via the hook). Reuse the existing
  `makeArgs`/`spillableSliceFactory`/`populate`/`residentBytesOf` helpers (lines 81-127).
- **Cases (concrete assertions in the Test plan):** `residentBytesIsStoreWideSum`,
  `hardEvictsOldestColdFirst`, `hardNoColdCandidateProceeds`, `hardStillOverAfterAllColdSpilled`,
  `emittedSliceNeverSpilledUnderEviction`, `gcPrunesCreatedSlices`, `deleteStateClearsAndQuiesces`.
- **Wiring:** already registered (`CMakeLists.txt:28`); no change.

### Section 3: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` (NEW) + `nes-physical-operators/tests/CMakeLists.txt`

- **Agent assignment:** implementation-3 (RED-phase; depends on Section 1 **PR-3** — it exercises the
  live `evictionMutex` / reservation / background thread).
- **Changes (test).** New file modelled on
  `nes-input-formatters/.../ConcurrentSynchronizationTest.cpp` (`std::array<std::jthread, N>` + a
  trigger thread + the store-owned spiller + `std::latch` start/finish barrier + atomic work-claim +
  deterministic post-condition). Copy the minimal `makeArgs`/`populate`/`sortedEntries` helpers into
  this TU (SC2 "don't gold-plate" — a tiny shared header only if trivial). Scenario:
  - Construct the store with a TINY `soft` (forces continuous eviction) and a moderate `hard`;
    `setBufferProvider(bufferManager)` (starts the spiller).
  - N=8 builder jthreads, each owning a disjoint `WorkerThreadId` and a per-thread strictly-increasing
    timestamp stream: `getSlicesOrCreate(ts, spillableSliceFactory())` then `populate(slice,
    myWorkerId, k, deterministic-offset)` with a reproducible per-(worker,ts) payload.
  - 1 trigger jthread: loops, advancing a monotonic watermark, calls `getTriggerableWindowSlices`; for
    every returned slice ASSERTS `isSliceResident(sliceEnd)` (the spill-vs-emit / spill-vs-trigger
    exposure point — a returned slice MUST be resident) and folds every worker `HashMapPtr`'s entries
    into a checksum (the "probe" — asserts the raw `HashMap*` is valid, i.e. not spilled out from
    under it).
  - After the latch, the trigger thread drains via `getAllNonTriggeredSlices()`.
  - **FORCE the same-SliceEnd spill-vs-trigger collision** (binding consequence of reconciled
    divergence 1): include a phase where the trigger advances its watermark to overlap the spiller's
    current cold-pick range so the spiller and the trigger race the SAME `SliceEnd`, exercising the
    `spillInProgress`/`spillerCv` WAIT path. Assert via `EXPECT_GT(store->spillCountForTest(), 0)`
    that eviction actually happened.
  - Iterate the whole scenario `numIterations` (≥50) to widen the Heisenbug window.
- **Post-condition (the UAF detector):** Σ(all triggered + drained checksums) == Σ(all inserted
  (key,value) contributions) over all iterations ⇒ no tuple lost to a half-freed/UAF spill and no
  double-count. The process completing without crash is the primary no-TSan signal.
- **Changes (CMakeLists).** Append after `nes-physical-operators/tests/CMakeLists.txt:28`:
  ```cmake
  add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest SpillableTimeBasedSliceStoreConcurrencyTest.cpp)
  ```
  No label edits (`gtest_discover_tests` auto-registers; `-LE 'Systest|DockerCompose'` auto-includes).

---

## Parallel groups

- **Sequential spine:** Section 1 (store hpp+cpp) is the anchor; internally STRICTLY sequential
  PR-1 → PR-2 → PR-3 (stacked commits on the file pair — one agent, no internal parallelism).
- **Group P1 (RED-phase tests):**
  - Section 2 (deterministic single-thread) depends only on Section 1 **PR-1 hooks + PR-2 eviction
    entry points** — can be written test-first against that public surface.
  - Section 3 (concurrency stress) depends on Section 1 **PR-3** (live mutex/reservation/thread).
  - Sections 2 and 3 are different files → run **in parallel** once their respective Section-1
    layers exist: `[S1 PR-1] → [S1 PR-2 ‖ S2] → [S1 PR-3] → [S3]`, with S2 and S3 parallel to each
    other from the moment their dependencies land.

---

## PR boundaries

The 3 commits on Section 1's file pair ARE the PR boundaries (order load-bearing, PR-1 → PR-2 → PR-3):

- **commit/PR-1 — enumeration + budget (single-thread, no eviction).** `getSlicesOrCreate` override,
  `createdSlices`, `recordCreatedSlice`, `lastSeenWatermark` learning, store-wide `residentBytes()`,
  GC/`deleteState` createdSlices pruning, the PR-1 test hooks. + Section 2's `residentBytesIsStoreWideSum`
  / `gcPrunesCreatedSlices`. **Reviewer focus:** weak_ptr lifetime + GC prune correctness; no base map
  touched; watermark monotonicity. Gate: those Section 2 cases green, full suite ≥ 805.
- **commit/PR-2 — eviction policy (single-thread).** `pickAndReserveColdSlice`, `finalizeSpill`,
  `spillOneColdSlice`, `evictUntilUnderHard`, the synchronous hard check in `getSlicesOrCreate`,
  `forceSpill` joining the serializer, O-B5 edges. + Section 2's `hardEvictsOldestColdFirst` /
  `hardNoColdCandidateProceeds` / `hardStillOverAfterAllColdSpilled` / `emittedSliceNeverSpilledUnderEviction`.
  **Reviewer focus:** oldest-first pick; no-I/O-under-lock already holds here; O-B5 proceed+log (never
  throw); the createdSlices-snapshot-then-evaluate pattern (no folly-lock nesting). Gate: those cases
  green, full suite ≥ 805.
- **commit/PR-3 — concurrency (ESCALATE GATE).** `evictionMutex` live, `spillInProgress`, the trigger
  WAIT (rewrite `unspillAndMarkEmitted`), the soft signal, `spillerLoop` + `SPILLER_POLL_INTERVAL`,
  `startSpiller`/`stopSpiller`, the `std::jthread`, dtor join + `deleteState` quiesce. + Section 3
  (concurrency stress + CMake). **Reviewer focus:** the eviction-mutex invariant verbatim; lock order
  `evictionMutex → folly set`; no base map under `evictionMutex`; teardown order (jthread last,
  stop+join before backend); the forced same-SliceEnd collision is actually exercised. Gate: the new
  stress test green over ≥50 iterations. **If concurrency cannot be made stress-clean → ESCALATE, do
  not ship.**

---

## Test plan

**Deterministic single-thread (PR-1/PR-2, Section 2) — every concrete assertion:**
- `residentBytesIsStoreWideSum`: create 3 tumbling slices (ts 50/150/250), populate distinct counts;
  assert `store->residentBytes()` == Σ `residentBytesOf` of each; `forceSpill` the middle; assert
  `residentBytes()` drops by exactly that slice's prior bytes (spilled ⇒ 0).
- `hardEvictsOldestColdFirst`: `makeStoreWithThresholds` tiny `hard`; create 3 cold slices over hard;
  `setLastSeenWatermarkForTest` past all three; `spillColdSlicesUnderHardForTest()`; assert oldest
  `SliceEnd` spilled FIRST, the loop stops once `residentBytes() <= hard`, backend has exactly the
  spilled slices' records, `isSliceResident` false for them / true for the rest.
- `hardNoColdCandidateProceeds`: over hard, all slices emitted (drive a trigger feeder first) →
  `spillColdSlicesUnderHardForTest()` spills none, no throw, `residentBytes()` unchanged (O-B5 hard).
- `hardStillOverAfterAllColdSpilled`: every slice cold + still over hard after spilling all → no throw,
  `residentBytes()` minimized (O-B5 terminal).
- `emittedSliceNeverSpilledUnderEviction`: mark a slice emitted via a trigger, seed a tiny soft +
  `setLastSeenWatermarkForTest` past it; assert `spillColdSlicesUnderHardForTest()` (or a soft scan)
  leaves it resident (the `!emittedKeys` filter holds).
- `gcPrunesCreatedSlices`: create slices, advance probe watermark past them, call
  `garbageCollectSlicesAndWindows`; assert `createdSlicesSizeForTest()` dropped and `residentBytes()`
  recomputes correctly (weak_ptrs expired, pruned).
- `deleteStateClearsAndQuiesces`: construct store (spiller started via `setBufferProvider`), call
  `deleteState()`; assert no crash, tracked sets + `createdSlices` cleared, spiller joined.
- **Double-spill idempotent:** `forceSpill` a slice twice → second call is a no-op (no second backend
  write, no throw); after a `pickAndReserveColdSlice` reserves a slice, a concurrent `forceSpill` of
  the same slice does not double-write (covered structurally by the reservation; assert via spill
  count == 1).

**Concurrency stress (PR-3, Section 3) — new ctest `SpillableTimeBasedSliceStoreConcurrencyTest.*`:**
- N=8 build jthreads + 1 trigger jthread + the store-owned background spiller; `std::latch` barriers;
  per-thread strictly-increasing timestamps; deterministic per-(worker,ts) payloads.
- **Checksum equality:** Σ(triggered + drained checksums) == Σ(inserted contributions) over all
  iterations ⇒ no tuple lost to a half-freed/UAF spill, no double-count.
- **Every returned slice resident at return** (asserted inside the trigger loop) — the spill-vs-emit
  detector.
- **FORCE same-SliceEnd spill-vs-trigger collision** so the `spillInProgress`/`spillerCv` WAIT path is
  exercised (binding consequence of reconciled divergence 1); `EXPECT_GT(store->spillCountForTest(),
  0)` proves eviction happened.
- **No crash / no deadlock:** completes within ctest's timeout across ≥50 iterations (a deadlock —
  e.g. I/O under `evictionMutex` — would hang and fail the gate).
- ctest names auto-discovered: `SpillableTimeBasedSliceStoreConcurrencyTest.<TestName>`.

**Regression:** full `ctest -LE 'Systest|DockerCompose'` stays ≥ 805 on every PR (the new test ADDS
to the count; no existing test changes behavior).

---

## Risks & mitigations

- **Teardown order / lifetime (HIGH).** `spiller` declared LAST ⇒ destroyed first; `stopSpiller()` as
  the FIRST step of the dtor AND of `deleteState()` guarantees the loop exits before
  `backend`/`bufferProvider` are gone and before the base dtor runs `deleteState`. *Mitigation test:*
  `deleteStateClearsAndQuiesces` + the stress test's teardown across ≥50 iterations. *If wrong:* UAF
  on shutdown.
- **TOCTOU / UAF spill-vs-emit (ABSOLUTE).** Spiller pick→reserve and trigger emitted-marking
  serialized by `evictionMutex`; I/O outside the lock with the `spillInProgress` reservation; the
  trigger WAITS on a mid-spill slice. *Mitigation:* the checksum post-condition +
  `EXPECT_GT(spillCount, 0)` + every-returned-slice-resident assertion. The single most important
  correctness property of B.
- **Heisenbug / no-TSan ceiling (HIGH, honest).** TSan is NOT runnable here (deps baked
  `sanitizer=none`). The stress test is probabilistic — it reduces but cannot eliminate race risk.
  *Documented ceiling:* final concurrency confidence comes from the out-of-gate thesis systest under
  Docker memory limits (MS3/MS4). **If PR-3 cannot be made stress-clean → ESCALATE, do not ship.**
- **NH2 stall (throughput/latency cliff) — avoided by no-I/O-under-lock.** Holding `evictionMutex`
  across RocksDB I/O would serialize all triggers behind disk. *Mitigation:* the UNIFORM
  reserve-then-I/O-outside-lock rule (reconciled divergence 1) for BOTH spill and unspill; only the
  concurrent trigger of the SAME mid-spill slice waits (rare, bounded by one `spill()` of one slice).
- **createdSlices lock nesting (NH1).** `residentBytes`/`pickAndReserveColdSlice` MUST snapshot
  `createdSlices` into a local vector under a single rlock, release it, THEN evaluate the set-lock
  predicates — never nest `createdSlices` with `spilledKeys`/`emittedKeys`, never take base
  `slices`/`windows` under `evictionMutex`. Lock order `evictionMutex → (createdSlices | one folly
  set)` is the only legal order. *Mitigation:* code-review checklist item.
- **Watermark lag under-selects (safe-by-design).** A lagging `lastSeenWatermark` only ever
  under-selects cold slices ⇒ never spills something the engine still considers hot. Documented; no
  mitigation needed.
- **Stale per-worker SliceCache entry (R3, contract-dependent).** A spilled cold slice's `HashMap*`
  cached in a worker's SliceCache would be a UAF IF a build tuple re-landed in its range — prevented
  by the watermark-monotonicity / no-late-data contract (INV-3, RESPECTED, no cache change).
  *Mitigation:* the stress test feeds per-thread strictly-increasing timestamps (no tuple lands in an
  already-cold/spilled range); the no-late-data precondition is documented in the PR.
- **`residentBytes()` staleness (read outside `evictionMutex`).** Eviction only ever LOWERS resident
  bytes; a stale-high read causes at most one extra (safe) eviction pass, a stale-low read self-heals
  on the next cadence tick. Never a correctness issue.
- **Spurious double-start of the spiller.** `setBufferProvider` could be called twice. *Mitigation:*
  the existing "already stashed → return" branch + the `spillerStarted` CAS guard.
- **ABI / blast radius.** Self-track + threading are subclass-private (option a). No base-header edit ⇒
  the 805 baseline regression surface is unchanged.

---

## Logging summary (all `NES_*` lines added, by file)

**`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`:**
- `getSlicesOrCreate` — `NES_DEBUG` (hard fire), `NES_TRACE` (soft notify).
- `pickAndReserveColdSlice` — `NES_DEBUG` (reserved cold slice).
- `finalizeSpill` — `NES_DEBUG` (spilled cold slice; resident now …).
- `evictUntilUnderHard` — `NES_WARNING` (over hard, no cold / still over → proceed over-budget).
- `spillerLoop` — `NES_TRACE` (over soft, no cold candidate, sleeping).
- `unspillAndMarkEmitted` — `NES_TRACE` (trigger waiting on in-flight spill).
- `startSpiller` — `NES_DEBUG` (started background spiller).
- `stopSpiller` — `NES_DEBUG` (stopping and joining background spiller).
- `deleteState` — `NES_TRACE` (quiescing spiller before deleteState).
- (Existing ctor `NES_DEBUG`, `setBufferProvider`/`unspill`/GC log lines retained unchanged.)

**Tests (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp`):** `NES_INFO` at SetUp/TearDown (template
parity). No production logging in tests.
