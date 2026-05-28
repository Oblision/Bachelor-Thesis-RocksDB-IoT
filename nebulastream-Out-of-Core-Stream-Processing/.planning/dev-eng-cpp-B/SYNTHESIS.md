# SYNTHESIS — Increment B (background-spiller auto-eviction, tumbling agg)

Phase 2 of the dev-eng-orchestra-cpp run. Branch `feat/ooc-phase1-spill-skeleton` @ `6a6ec7301b`
(Increment A complete, 805/805 green, clean tree). Build/test ONLY in Docker
`nebulastream/nes-development:local-rocksdb`, `cmake-build-debug`, ninja + ctest.

This document supersedes the three exploration reports (`exploration/CORE.md`, `BUILD.md`,
`CONTRACTS.md`) wherever they conflict with it. The exploration was written BEFORE the brief's
"SCOPE CORRECTION (2026-05-23)" and "DECISION (2026-05-23, user)"; it assumed an over-scoped plan
(sliding windows, variable-sized serialization, a core base-class edit, TSan-as-gate). Those are
DROPPED here. Facts that survive the down-scope are carried forward and cited; everything the
correction drops is listed explicitly under **Scope → DROPPED**.

---

## Scope (corrected)

This increment turns the Increment-A forced-spill spike into a real, automatic out-of-core feature for
**tumbling windowed aggregation only**: a dedicated **background spiller thread** preemptively spills
COLD slices (`sliceEnd < watermark`, stopped filling, not emitted) when resident memory crosses a
**soft** threshold so worker threads keep processing; and a **synchronous mandatory spill** on the
allocating/worker path when resident memory crosses a **hard** threshold to prevent OOM. The
existing `forceSpill`/`isSliceResident`/`residentBytesOf` API and the fixed-size `SliceSerializer`
stay unchanged; B adds the automatic path on top, with the concurrency hardening needed to make the
spiller atomic against the trigger marking a slice emitted (closes A's M1 TOCTOU).

In scope:
- **Store self-tracking of created slices** (override `getSlicesOrCreate` to record `sliceEnd` + a
  `weak_ptr<Slice>`), so the subclass can enumerate candidates and compute store-wide
  `residentBytes()` WITHOUT touching the private base maps. Tumbling ⇒ slice ≡ window ⇒
  `cold = sliceEnd < watermark` is decidable from self-tracking alone.
- **Learn the build watermark from the trigger feeders.** The store sees the build watermark only in
  `getTriggerableWindowSlices(globalWatermark)` (`WindowBasedOperatorHandler.cpp:87`); cache it as a
  monotonic `lastSeenWatermark`. A lagging watermark only ever *under*-selects cold slices → safe.
- **Soft threshold → background spiller thread:** owns a loop that wakes (condvar/poll — O-B2), scans
  oldest-first, confirms each candidate not-emitted under the eviction mutex, reserves it, spills I/O
  OUTSIDE the lock, finalizes residency, until back under soft.
- **Hard threshold → synchronous spill** on the worker/allocating path (checked at the eviction
  cadence, see Mechanism) so an over-soft-but-still-growing store cannot OOM before the background
  thread catches up.
- **Eviction-mutex discipline** making pick-cold→confirm-not-emitted→spill atomic vs a trigger
  marking emitted (R1/M1).
- **Background-thread lifecycle**: start/stop/join tied to store (or handler) lifetime; must not
  outlive the BufferManager/backend (O-B-ownership).
- Tests: deterministic single-thread budget/eviction tests (extend
  `SpillableTimeBasedSliceStoreTest.cpp`) + a NEW multi-threaded stress test (no sanitizer,
  template = `ConcurrentSynchronizationTest.cpp`).

DROPPED (vs the ORIGINAL brief / the exploration's recommendations):
- **B-varsized — entirely.** No variable-sized key/value serialization. `SliceSerializer` stays
  fixed-size (V1, no VERSION bump, no `CreateNewHashMapSliceArgs` descriptor field, no
  `ChainedHashMap::getVarSizedBytes()`). CONTRACTS.md §3/§4 and its V2 format proposal are DISCARDED.
- **Sliding windows — entirely.** Only tumbling. CONTRACTS.md's per-window-state predicate (§2) and
  its `getColdSlicesSlidingWindowSafety` test are DISCARDED.
- **Core base-class edit — DISCARDED.** Do NOT add `getColdSlices`/`forEachSliceAccumulate` protected
  helpers to `DefaultTimeBasedSliceStore`, do NOT make `slices`/`windows` protected. CONTRACTS.md's
  option (c) recommendation is OVERRULED by the brief: O-B1 is RESOLVED as option (a) self-track.
  (CONTRACTS.md rejected (a) *only* because of sliding windows — which are now out of scope, so (a)
  is sound for tumbling.)
- **TSan-as-gate — DISCARDED.** TSan is not runnable here (vcpkg deps baked `sanitizer=none`; needs a
  multi-hour from-scratch tsan+rocksdb dep-image build that doesn't exist remotely — BUILD.md §1).
  Validation = a no-sanitizer stress test + the thesis systest under Docker memory limits
  (MS3/MS4, out of THIS run's gate). The honest validation ceiling is documented under Risks.

---

## Files affected

Production (EDIT):
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` — add the self-tracking
  map (`Synchronized<map<SliceEnd, weak_ptr<Slice>>> createdSlices`), `lastSeenWatermark`, the
  `evictionMutex`, a per-slice spill-in-progress reservation set, the background-thread member
  (`std::jthread` + condvar + stop flag), and method decls: `getSlicesOrCreate` override,
  store-wide `residentBytes()`, the eviction entry points (soft scan + synchronous hard spill),
  `start/stopSpiller`. Drop `[[maybe_unused]]` from `softThresholdBytes`/`hardThresholdBytes`
  (currently lines 111-112 — now enforced).
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` — implement the above:
  override `getSlicesOrCreate` (call base, record created slice), update `lastSeenWatermark` in the
  trigger feeders, store-wide `residentBytes()` over `createdSlices` (Σ `residentBytesOf`, weak_ptr
  lock()+skip-expired), the eviction scan (oldest-first, confirm not in `emittedKeys`, reserve,
  spill outside lock, record `spilledKeys`), the synchronous hard path, the background spiller loop,
  lifecycle start/join. Make the trigger path's emitted-marking participate in `evictionMutex` (the
  one change to A's lock rule). Extend `garbageCollectSlicesAndWindows`/`deleteState` to prune
  `createdSlices` the same way they already prune `spilledKeys`/`emittedKeys`.

Production (EDIT, plumbing — pick ONE per the ownership open question, see Open Questions):
- If the spiller is store-owned: the two EDITs above suffice; lifecycle is RAII in the store
  ctor/dtor. No further production edits.
- If the spiller is handler-owned: also EDIT
  `nes-physical-operators/include/Aggregation/AggregationOperatorHandler.hpp` +
  `.../src/Aggregation/AggregationOperatorHandler.cpp` to own the thread and call the store's
  eviction entry point, and EDIT
  `nes-physical-operators/src/SliceStore/DefaultTimeBasedSliceStoreRef.cpp` (`setupSliceStoreProxy`,
  lines 115-133, already the `setBufferProvider` wiring site) to start it after the provider is
  stashed. (Recommendation: store-owned — fewest edits, RAII teardown; but it is a genuine open
  question, see below.)

UNCHANGED (explicitly NOT touched — reverses exploration recommendations):
- `nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp` / `.cpp` — NO protected
  accessors, maps stay private (O-B1 = self-track).
- `nes-physical-operators/include/SliceStore/SliceSerializer.hpp` / `.cpp` — fixed-size V1 stays.
- `nes-nautilus/.../ChainedHashMap.{hpp,cpp}` — no `getVarSizedBytes()`.
- `nes-query-compiler/.../LowerToPhysicalWindowedAggregation.cpp` — store construction
  (lines 236-244) already passes soft/hard thresholds; no signature change needed.
- The JIT build hot path / `SliceCache` / `DefaultTimeBasedSliceStoreRef` build proxy — untouched
  (respect the watermark-monotonicity contract; do not modify the cache).

Test (EDIT):
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` — add deterministic
  single-thread B-evict cases (see Test strategy). The fixture already plumbs
  `softThresholdBytes`/`hardThresholdBytes` (lines 106-107) and has `populate`/`residentBytesOf`/
  `triggeredContains` helpers, all tumbling (`WINDOW_SIZE==WINDOW_SLIDE==100`, lines 73-74).

Test (NEW):
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` — the multi-threaded
  stress test (N build jthreads + a trigger thread + the eviction path), mirroring
  `ConcurrentSynchronizationTest.cpp` (`std::array<std::jthread,N>` + `std::latch` + atomic work
  claim + deterministic post-condition).

Build (EDIT):
- `nes-physical-operators/tests/CMakeLists.txt` — append ONE line after line 28:
  `add_nes_physical_operator_test(SpillableTimeBasedSliceStoreConcurrencyTest SpillableTimeBasedSliceStoreConcurrencyTest.cpp)`.
  No ctest-label edits needed (`gtest_discover_tests` auto-registers; the `-LE 'Systest|DockerCompose'`
  gate auto-includes it). No sanitizer/CMake/preset infra changes.

Count: **EDIT 4 production+test files minimum** (store hpp+cpp, store test, test CMakeLists) — **+2**
if handler-owned spiller (handler hpp/cpp) and **+1** wiring (`DefaultTimeBasedSliceStoreRef.cpp`);
**NEW 1** (the concurrency test).

---

## Mechanism sketch

### Cold-slice self-tracking (O-B1 = option a)
- The base `slices`/`windows` maps are PRIVATE (`DefaultTimeBasedSliceStore.hpp:88-89`), so the
  subclass cannot scan them. Override `getSlicesOrCreate(timestamp, createNewSlice)`: call
  `DefaultTimeBasedSliceStore::getSlicesOrCreate(...)` (unchanged base behavior — double-checked
  insert under `acquireLocked(slices, windows)`, `DefaultTimeBasedSliceStore.cpp:58-102`), then
  record the returned slice into a new `folly::Synchronized<std::map<SliceEnd, std::weak_ptr<Slice>>>
  createdSlices`. Use `weak_ptr` so the base GC's `shared_ptr` drop still frees the slice; the
  eviction scan `lock()`s each weak_ptr and skips expired entries (those the GC already dropped).
- This override does NOT hold any base lock when it records (base call has already returned and
  released `slices`/`windows`) → no new lock-order vs `acquireLocked`.
- For **tumbling**, one slice == one window, so coldness is fully `sliceEnd < lastSeenWatermark`
  AND `!emittedKeys.contains(sliceEnd)` AND `isSliceResident(sliceEnd)` — no per-window state needed.
  This is exactly why self-tracking suffices here where it failed for sliding (CONTRACTS.md §1).

### Learning the watermark
- The store currently never receives the *build* watermark (R5): `getSlicesOrCreate` has no watermark
  arg, and the only place the build watermark enters the store is
  `getTriggerableWindowSlices(globalWatermark)`, called from `checkAndTriggerWindows`
  (`WindowBasedOperatorHandler.cpp:77,87`). In the subclass override
  (`SpillableTimeBasedSliceStore.cpp:117-123`), after calling base, monotonically update a
  `lastSeenWatermark` (`std::atomic<Timestamp>` or under the eviction mutex). The same applies on
  `getAllNonTriggeredSlices` (terminate path). A lagging value only under-selects cold slices →
  always safe (never spills something the engine still considers hot).

### Store-wide residentBytes()
- `residentBytes()` = Σ over `createdSlices` (lock weak_ptr, skip expired) of `residentBytesOf(*slice)`
  — `residentBytesOf` already exists (`SpillableTimeBasedSliceStore.cpp:234-255`) and returns 0 for
  spilled slices (Option-1 page math). Recomputed each eviction tick; at the once-per-buffer / wakeup
  cadence this is cheap. No base change.

### Soft threshold → background spiller thread
- Owner runs a `std::jthread` loop. On wake (condvar signalled when a build/trigger observes
  `residentBytes() > soft`, OR a bounded poll — O-B2): take `evictionMutex`, scan `createdSlices`
  oldest-first (the map is sorted ascending by `SliceEnd`), pick the first slice that is cold
  (`sliceEnd < lastSeenWatermark`), resident (`isSliceResident`), and not emitted
  (`!emittedKeys.contains`). Atomically **reserve** it (insert into a `spillInProgress` set so a
  concurrent trigger of the same slice skips/waits — closes R2), **release** `evictionMutex`, do the
  `spillable->spill(*backend)` I/O OUTSIDE the lock, then re-take `evictionMutex` briefly to insert
  into `spilledKeys` and clear the reservation. Loop until `residentBytes() <= soft` or no cold
  candidate remains (then sleep — O-B5 over-budget-no-cold case).

### Hard threshold → synchronous mandatory spill
- Checked at the eviction cadence on the worker/allocating path (after `getSlicesOrCreate` creates a
  slice / at build-`close()` after trigger — O-B2). If `residentBytes() > hard`, the WORKER thread
  itself runs the same pick-cold→reserve→spill loop synchronously (under the same `evictionMutex`
  discipline) until back under hard (or no cold candidate → proceed over-budget + log, O-B5). This is
  the OOM backstop independent of the background thread's progress. It must NOT run on the per-tuple
  JIT path (`getDataStructureRef`, `AggregationBuildPhysicalOperator.cpp:93`) — only at the slice-
  creation / build-close cadence.

### Eviction-mutex discipline
- ONE dedicated `std::mutex evictionMutex` is the OUTER serialiser. Held across: the
  pick-cold→confirm-not-emitted→reserve decision, and the trigger path's emitted-marking
  (`emittedKeys.insert` in `unspillAndMarkEmitted`, `SpillableTimeBasedSliceStore.cpp:112`) — this is
  the ONE change to A's "never hold two store locks" rule: emitted-marking now participates in the
  eviction serialiser so it cannot interleave with an in-flight spill of the same slice. NOT held:
  the actual `spill()`/`unspill()` RocksDB I/O (done outside, with the slice reserved), and the
  per-tuple build path (build only ever writes resident FILLING slices, never eviction candidates;
  per-worker slot disjointness already protects those). `spilledKeys`/`emittedKeys` stay accessed
  one-at-a-time INSIDE the eviction critical section (H1 no-nesting preserved). NEVER acquire base
  `slices`/`windows` while holding `evictionMutex` (NH1).

### Background-thread lifecycle
- Start: after the BufferManager/backend are available — i.e. after `setBufferProvider`
  (`setupSliceStoreProxy`, `DefaultTimeBasedSliceStoreRef.cpp:131-133`) if store-owned, or in the
  handler's `start()` if handler-owned. Stop: request stop + notify the condvar, then `join()` in the
  store dtor / handler teardown (`std::jthread` auto-joins, but use an explicit stop token so the loop
  exits promptly). MUST stop+join BEFORE the backend/BufferManager it uses are destroyed — order is
  load-bearing (R2-lifetime). `deleteState()` must also quiesce the spiller before clearing tracked
  state.

---

## Dependencies & order

Include/link order: all touched store sources compile into the single `nes-physical-operators`
library (BUILD.md §3); a change to the store .hpp/.cpp rebuilds that one lib + relinks dependents.
The self-track + watermark-learning are header-private to the subclass → 0 base-header blast radius
(narrowest O-B1 path).

Landing order (suggested PR boundaries):
1. **PR-1 (enumeration + budget, single-thread):** `getSlicesOrCreate` override + `createdSlices` +
   `lastSeenWatermark` + store-wide `residentBytes()` + GC/deleteState pruning. Gate: extend
   `SpillableTimeBasedSliceStoreTest.cpp` (deterministic, no threads). Must land first — everything
   else depends on enumeration.
2. **PR-2 (eviction policy, single-thread):** soft-scan + synchronous hard spill + O-B5 edge cases,
   driven directly (no background thread yet — call the eviction entry point from the test). Gate:
   single-thread budget tests. Depends on PR-1.
3. **PR-3 (concurrency: mutex + reservation + background thread):** `evictionMutex`, the
   spill-in-progress reservation, the trigger path joining the serialiser (closes R1/M1, R2), the
   background `jthread` + lifecycle. Gate: the NEW stress test. Depends on PR-2. This is the
   highest-risk PR; if its concurrency cannot be made stress-clean, ESCALATE rather than ship.

(These may collapse into fewer PRs given SC2 "complete + correct + defensible, don't gold-plate" — the
ordering constraint PR-1 → PR-2 → PR-3 is what matters.)

---

## Risks

- **Lifetime (R2-lifetime, HIGH):** the background spiller must NOT outlive the BufferManager/backend.
  Stop+join the thread BEFORE those are torn down. `deleteState()` must quiesce the spiller first.
  Getting teardown order wrong → use-after-free on shutdown. Drives the store-vs-handler ownership
  question.
- **Thread-safety / TOCTOU (R1/M1, ABSOLUTE):** the spiller's pick-cold→confirm-not-emitted→spill must
  be atomic vs a trigger marking the slice emitted, else a slice gets spilled after its raw `HashMap*`
  was snapshotted for the async probe → UAF in the probe. Today's `forceSpill` check+spill is
  explicitly non-atomic (`SpillableTimeBasedSliceStore.cpp:203-210`, A's M1 note). Closed by the
  `evictionMutex` serialising spill-decision against emitted-marking + the spill-in-progress
  reservation (R2). This is the single most important correctness requirement of B.
- **Heisenbug / no-TSan validation ceiling (HIGH, honest):** R1/R2 are data races; the only in-gate
  signal is a probabilistic multi-threaded stress test (no sanitizer). TSan is NOT runnable here
  (BUILD.md §1: deps baked `sanitizer=none`, no remote tsan+rocksdb image, multi-hour from-scratch
  build). The stress test reduces but cannot eliminate Heisenbug risk; final concurrency confidence
  comes from the out-of-gate thesis systest under Docker memory limits (MS3/MS4). Document this
  ceiling in the PR; if R1/R2 cannot be made stress-clean, ESCALATE.
- **Stale per-worker SliceCache entry (R3, contract-dependent):** a spilled slice's `HashMap*` cached
  in a worker's SliceCache partition would be a UAF IF a build tuple ever re-lands in that slice's
  range. Safe ONLY by the watermark-monotonicity + no-late-data contract (INV-3); the cache is
  per-worker process memory the store cannot invalidate. We RESPECT this contract (no cache change per
  brief). It is invisible to all current gate tests (store tests bypass the cache, CORE.md §2.4).
  Document the no-late-data precondition; the stress test asserts no tuple lands in a spilled range.
- **ABI / encapsulation:** self-track is subclass-private → ABI-additive, 805 baseline regression
  surface unchanged (no base-header edit — this is the explicit win of choosing option a over the
  exploration's option c).
- **O-B5 over-budget-but-no-cold-slice:** if over budget but every slice is FILLING or EMITTED (no
  cold candidate), the store CANNOT evict → proceed over-budget and LOG; NEVER spill a filling or
  emitted slice (would break INV-1 / per-worker isolation). Both soft (sleep) and hard (proceed+log)
  paths must handle this terminal case.
- **NH2 (stall):** holding `evictionMutex` across RocksDB I/O would serialise all triggers behind
  disk → throughput/latency cliff. Mitigated by the reserve-then-I/O-outside-lock pattern; needs the
  per-slice spill-in-progress state A does not have today.

---

## Locked decisions (carry verbatim — reviewers must NOT flag these as bugs)

From Increment A (inherited):
- **Route-B seam:** consumers dispatch via `HashMapSlice&` (`getHashMapPtr`/`OrCreate`/
  `getNumberOfTuples` are virtual).
- `SpillableTimeBasedSliceStore` subclasses un-`final`'d `DefaultTimeBasedSliceStore`, owns the
  `SpillBackend`, reuses `createSliceStoreRef`/`SliceCache`/the Nautilus ref UNCHANGED. The JIT build
  hot path only ever touches resident WINDOW_FILLING slices.
- **async-probe-drain invariant (ABSOLUTE):** `AggregationOperatorHandler::triggerSlices` snapshots
  each slice's raw `HashMap*` into an `EmittedAggregationWindow` and emits it ASYNC to the probe; the
  probe reads those raw pointers later → a slice in `EMITTED_TO_PROBE` must NEVER be spilled
  (use-after-free). Spill target = COLD slices (`sliceEnd < watermark`, stopped filling, not yet
  emitted). (Verified: `AggregationOperatorHandler.cpp:90-148`; maps freed only at GC.)
- Unspill-on-read happens in the store's trigger feeders (`getTriggerableWindowSlices`,
  `getAllNonTriggeredSlices`) — `SpillableTimeBasedSliceStore.cpp:117-131`.
- Budget = per-store resident page math (Option 1): `Σ_workers ⌈tuples_w/entriesPerPage⌉ × pageSize`,
  0 when spilled. Pool occupancy rejected (aggregation memory is unpooled).
- The existing `forceSpill`/`isSliceResident`/`residentBytesOf` API + `SliceSerializer` fixed-size
  format STAY. `forceSpill` stays useful for tests; B adds the AUTOMATIC path on top. Existing tracked
  sets: `folly::Synchronized<set<SliceEnd>> spilledKeys, emittedKeys`.

From the SCOPE CORRECTION (SUPERSEDE the original B scope + exploration):
- **Tumbling windows ONLY.** DROP B-varsized entirely (keep `SliceSerializer` fixed-size). NO
  sliding-window work.
- **NO core base-class change** — do NOT add protected accessors/maps to
  `DefaultTimeBasedSliceStore`. O-B1 is RESOLVED as option (a): the store SELF-TRACKS created slices
  (override `getSlicesOrCreate` to record created slice ends + state) and learns the watermark from
  the trigger feeders. Tumbling ⇒ slice ≡ window ⇒ `cold = sliceEnd < watermark`.
- **NO JIT / SliceCache modification.** The watermark-monotonicity contract already guarantees a cold
  tumbling slice won't be re-looked-up by the build cache — a contract to RESPECT, not code to change.

From the DECISION (chosen design):
- **SOFT threshold → a dedicated BACKGROUND SPILLER THREAD** preemptively spills COLD slices so worker
  threads keep processing.
- **HARD threshold → synchronous mandatory spill** on the allocating/worker path to prevent OOM.
- Concurrency invariants to preserve: async-probe-drain, emittedKeys guard, idempotent spill/unspill.
  The spiller's pick-cold→confirm-not-emitted→spill must be ATOMIC vs a worker/trigger marking the
  slice emitted (closes A's M1 TOCTOU) — likely a dedicated eviction mutex + I/O done OUTSIDE the lock.
- Background-thread lifecycle: start/stop tied to store (or operator handler) lifetime; clean join on
  teardown; must NOT outlive the BufferManager/backend it uses.
- **TSan is NOT runnable here.** Validation = a multi-threaded stress test (no sanitizer) + the thesis
  systest under Docker memory limits (out of THIS run's gate). Document the honest validation ceiling.

---

## Open questions for the architects (resolve WITH code visibility — do NOT pre-decide)

- **O-B2 — Eviction trigger & cadence.** Where does the soft check fire and how is the background
  thread woken? Candidates (CORE.md §3): (a) condvar signalled when a build cache-miss/`getSlicesOrCreate`
  or build-`close()` observes `residentBytes() > soft`; (b) bounded poll (sleep N ms, re-scan); (c)
  piggyback build-`close()` after trigger (the cadence where `emittedKeys`/watermark are freshest, no
  per-tuple cost). For the HARD path: is it checked at slice-creation, at build-close, or both? It must
  never run per-tuple on the JIT hot path. Resolve the wakeup mechanism (condvar vs poll) and the exact
  fire points, with the latency/throughput tradeoff for the 3 RQs in mind.
- **O-B3 — Exact locking model.** Does A's "never hold two store locks at once"
  (`SpillableTimeBasedSliceStore.cpp:155-158`) survive? The synthesis proposes ONE outer
  `evictionMutex` serialising spill-decision against emitted-marking + a per-slice spill-in-progress
  reservation, with `spilledKeys`/`emittedKeys` still un-nested inside, and base `slices`/`windows`
  NEVER acquired under `evictionMutex` (NH1). Architects must: define the precise invariant (what is
  atomic w.r.t. what), confirm the reservation set semantics (does a trigger that wants a reserved
  slice wait or skip? — a triggered slice that is mid-spill must end up resident+emitted, not
  half-freed), and define how the stress test exercises the spill-vs-emit and spill-vs-trigger races
  deterministically enough to be meaningful.
- **O-B5 — Eviction policy edge cases.** Over budget but no COLD slice (everything filling/emitted):
  confirm "cannot evict → proceed over-budget + log; never spill filling/emitted" for BOTH soft (sleep
  and retry on next wake) and hard (proceed + log, do not block forever) paths. Also: hard-threshold
  behavior when even all-cold-spilled is still over hard — proceed+log vs error? Define precisely and
  test.
- **Background-thread ownership (store vs operator handler).** Store-owned = fewest edits + RAII
  teardown in the store dtor, but the store is constructed in the lowering rule before the
  BufferManager is stashed (provider set later in `setupSliceStoreProxy`,
  `DefaultTimeBasedSliceStoreRef.cpp:131-133`), so the thread must start lazily after
  `setBufferProvider`. Handler-owned = cleaner lifecycle hook (`start()`/teardown) but spreads the
  feature across `AggregationOperatorHandler` + a store eviction entry point + the proxy wiring.
  Decide ownership and the precise start point (must be after the provider is available) and the
  stop+join point (must be before backend/BufferManager destruction).
