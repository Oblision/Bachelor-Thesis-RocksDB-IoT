# SUPER-REVIEW — Increment B (OOC spill), cross-cutting consolidation audit (Phase 6b)

**Scope:** cumulative final state of the Increment-B commit stack on `feat/ooc-phase1-spill-skeleton`, reviewed as one integrated subsystem (PR-1 + PR-2 + PR-2-fix + PR-3 + PR-3-fix). READ-ONLY.
**Baseline:** `6a6ec7301b` (Increment A, 805/805).
**HEAD reviewed:** `e4cd3d0150`.
**Reviewer:** Claude (super-reviewer), 2026-05-23.

Commit stack verified present and in order:
- `a00d9d65d7` PR-1 (enumeration + budget)
- `dec5f95d62` PR-2 (eviction policy) + `c2f41c6d8c` (PR-2 review fixes)
- `8a3a0366ac` PR-3 (background spiller + stress test) + `e4cd3d0150` (PR-3 review fixes)

**Production blast radius (verified by `git diff 6a6ec7301b..HEAD --stat`):** only
`nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` (+155/−…) and
`.../src/SliceStore/SpillableTimeBasedSliceStore.cpp` (+571/−39). The base
`DefaultTimeBasedSliceStore.{hpp,cpp}`, `WindowSlicesStoreInterface.hpp`, the lowering rule, and the
handler were **NOT touched in Increment B** (the lowering/handler wiring already shipped in Increment A). Plus
two test files. LOCKED "no base-class edit" / "blast radius = subclass only" confirmed.

---

## FINAL VERDICT: **SHIP AS-IS**

No new cross-cutting must-fix findings. Every per-PR finding is verifiably closed in the cumulative state. The
async-probe-drain absolute invariant holds end-to-end across the integrated PR-1+2+3 state machine. The one
genuine subtlety a single-PR reviewer could not see — the *base-GC* free-while-probe-holds-`HashMap*` window —
is a **pre-existing base-store property that Increment B neither introduces nor regresses** (the subclass adds
only backend-erase to GC; it does not change the GC gating). See Cross-cutting finding 1.

**NEW must-fix findings: 0 Critical / 0 High / 0 Medium / 0 Low.**

---

## Top priority fixes

None. Two optional, non-blocking observations are recorded in Cross-cutting findings 1 and 8 (both are
pre-existing-base / out-of-gate items, explicitly deferred to the thesis systest MS3/MS4 — not Increment-B
defects).

---

## Aggregate stats

| PR | Per-PR verdict | Findings raised | Status in cumulative state |
|----|----------------|-----------------|----------------------------|
| PR-1 (`a00d9d65d7`) | APPROVE WITH NITS | 0C / 0H / 2M / 4L + 3 coverage gaps | All cleared in PR-2 (`recordCreatedSlice` bool gate, INVARIANT comment, `<algorithm>` removed, memory-order rationale, hook doc, coverage tests added) |
| PR-2 (`dec5f95d62`) | REQUEST CHANGES | 0C / 2H / 3M | All cleared by `c2f41c6d8c` (ReservationGuard for H1/H2; break-on-watermark M1; finalizeSpill scope M2; unspill obligation comment M3) — re-review verdict APPROVE |
| PR-2-fix (`c2f41c6d8c`) | APPROVE | 0 remaining | ReservationGuard verified: no double-erase, no self-deadlock, noexcept dtor |
| PR-3 (`8a3a0366ac`) | APPROVE WITH NITS | 0C / 0H / 0M / 2L + 4 nits | Both Low cleared by `e4cd3d0150` |
| PR-3-fix (`e4cd3d0150`) | (this fix commit) | L1 unspill RAII guard + L2 residentBytes dedup | Verified present in final `.cpp` |

**Total findings cleared by severity across the increment:** 2 High (PR-2 H1/H2), 5 Medium (PR-1 M1/M2;
PR-2 M1/M2/M3), 8 Low + ~12 nits + ~10 coverage gaps. **0 open at HEAD.**

**Regression delta vs PREFLIGHT (baseline 805):**
- Unit test `SpillableTimeBasedSliceStoreTest.cpp`: **6 → 20 TEST_F** (verified by `git show 6a6ec7301b:` vs HEAD). Breakdown matches the brief's projection exactly: PR-1 +2, PR-2 +8, PR-2-fix +2, PR-3-fix +2 = +14.
- New concurrency test file `SpillableTimeBasedSliceStoreConcurrencyTest.cpp`: **+1** ctest case (`concurrentBuildTriggerSpillIsRaceFree`, 50 internal iterations), registered in `tests/CMakeLists.txt` line 29.
- **No existing test changed behavior** — the +14 are net-new TEST_F; the base store and all other suites are untouched. pr3-done.md reports full suite `818/818` at `8a3a0366ac` and gate suite `62/62` after `e4cd3d0150`, consistent with `805 baseline + new tests`. Final expected ≈ 820 (805 + 14 unit + 1 concurrency, minus the ctest-vs-gtest counting granularity the per-PR docs already account for). **Consistent; no regression.**
- (Full ctest run is happening separately per the brief; this is reasoned from the per-PR counts + git verification, not re-run here.)

---

## Cross-cutting findings (dimensions 1–9)

### 1. End-to-end concurrency state machine of one cold slice — VERIFIED (with one pre-existing-base note)

Full lifecycle traced across the integrated code:

`getSlicesOrCreate` (base creates+inserts under base lock, returns; subclass `recordCreatedSlice` self-tracks
in `createdSlices` AFTER the base lock is released — `.cpp:162,169`) → watermark advances in the trigger feeder
(`lastSeenWatermark` CAS-max, `.cpp:271-275`) making `sliceEnd < wm` → background `spillerLoop` /
`evictUntilUnderHard` calls `spillOneColdSlice` → `pickAndReserveColdSlice` (under `evictionMutex`: cold +
not-reserved + live + resident + **not in `emittedKeys`**, reserve, `.cpp:496-534`) → `spill()` OUTSIDE the
mutex → `finalizeSpill` (insert `spilledKeys`, erase reservation, notify, `.cpp:539-554`) → trigger:
`getTriggerableWindowSlices` → base marks window `EMITTED_TO_PROBE` and returns slices → `unspillAndMarkEmitted`
(under `evictionMutex`: WAIT out in-flight spill on `spillerCv`, **claim `emittedKeys` BEFORE releasing the
mutex**, then unspill OUTSIDE the mutex, `.cpp:197-262`) → caller `triggerSlices` snapshots raw `HashMap*` and
`emitBuffer`s to the async probe (`AggregationOperatorHandler.cpp:80-156`).

**No window where an `EMITTED_TO_PROBE` slice can be spilled (ABSOLUTE invariant): CONFIRMED airtight.** The
only potential gap a per-PR reviewer might miss is the interval between the *base* setting `EMITTED_TO_PROBE` (in
`DefaultTimeBasedSliceStore::getTriggerableWindowSlices`) and the *subclass* inserting into `emittedKeys`. I
verified this is safe: in that interval the spiller may indeed pick+spill the slice (it keys off `emittedKeys`,
not the base window state), but `unspillAndMarkEmitted` runs synchronously next, WAITs the spill out, claims
`emittedKeys` under `evictionMutex` (so no *new* spill can start — `pickAndReserveColdSlice` holds the same mutex
and rejects emitted), and unspills if needed. The slice **always ends resident+emitted before
`getTriggerableWindowSlices` returns**, which is strictly before the caller hands any `HashMap*` to the probe
(call order verified in `WindowBasedOperatorHandler.cpp:87-88`). The async probe never sees a spilled or
mid-spill map.

**No window where a slice is freed while a worker/probe holds its `HashMap*`:** the *spill* free-path is fully
fenced (above + the `spillInProgress` footprint-reader fence on `residentBytes()`). The remaining free-path is
the **base GC** (`DefaultTimeBasedSliceStore::garbageCollectSlicesAndWindows`, `.cpp:212-272`), which frees a
slice only when `sliceEnd + windowSize < newGlobalWaterMark` AND the window is `EMITTED_TO_PROBE`. The async
probe holds raw `HashMap*` into that slice. **This GC-vs-async-probe lifetime gating is identical to the
in-memory base store and is NOT changed by Increment B** — the subclass override only *adds* backend-erase +
`createdSlices`/`emittedKeys` pruning after delegating to the base (`.cpp:340-387`), using the non-unspilling
base lookup so it never resurrects the slice it is dropping. The weak_ptr self-track guarantees the subclass
never extends slice lifetime. So Increment B introduces **no new** use-after-free on the GC path; the existing
base contract (one-full-windowSize watermark margin past emit before GC) is what the async probe relies on, as
it did pre-B. **Note (pre-existing base, out-of-B-scope):** the absolute timing safety of base-GC-vs-async-probe
is a base-store property the thesis systest (MS3/MS4) should confirm under real query load; it is explicitly
outside this gate and is not an Increment-B finding.

### 2. Global lock-order & deadlock freedom (whole class) — VERIFIED, single consistent order, no cycle

Locks enumerated: `evictionMutex` (plain, mutable); folly `Synchronized` for `createdSlices`, `spilledKeys`,
`emittedKeys`, `bufferProvider`; the base `slices`/`windows` locks (inside base methods only); `spillerCv`
(always paired with `evictionMutex`); `spillInProgress` + `quiesced` (plain members, only ever touched under
`evictionMutex`).

**The one global order: `evictionMutex → (createdSlices | exactly one of spilledKeys/emittedKeys)`.** Verified
exhaustively:
- The two folly *sets* are never nested with each other: `garbageCollectSlicesAndWindows` and `deleteState`
  release `spilledKeys` before touching `emittedKeys`/`backend` (`.cpp:348-369, 397-409`); the three-phase
  structure is the H1 pattern, correctly preserved.
- `createdSlices` is snapshotted under its own rlock then released before the folly-set predicates in
  `pickAndReserveColdSlice` (`.cpp:485-494`) and `residentBytes` (`.cpp:655-669`) — never nested with the sets.
- **Base maps are NEVER taken under `evictionMutex`:** every `DefaultTimeBasedSliceStore::getSliceBySliceEnd`
  call (which takes the base `slices` rlock) is made *before* acquiring `evictionMutex` (`forceSpill` `.cpp:420`
  before the mutex block at 434; `spillOneColdSlice` `.cpp:568` after `pickAndReserveColdSlice` has released the
  mutex; `garbageCollectSlicesAndWindows` `.cpp:353` outside any `evictionMutex`). The shared_ptr from the
  lookup keeps the slice alive across the critical section, so no re-lookup under the mutex is needed. NH1 holds.
- **No RocksDB I/O under `evictionMutex` anywhere:** every `spill()` (`.cpp:460, 583`) and `unspill()`
  (`.cpp:250, 326`) executes OUTSIDE the mutex, protected by the `spillInProgress` reservation. `backend->erase`
  (`.cpp:367, 406`) runs outside too. Uniform no-I/O-under-lock confirmed.
- `spillerCv.wait`/`wait_for` release `evictionMutex` while blocked (standard semantics), so a waiting trigger
  cannot hold the mutex against the spiller.

**Every reservation released on every path (success / throw / expire) at ALL call sites — VERIFIED via the
`ReservationGuard` RAII:**
- `spillOneColdSlice` (`.cpp:565`): guard covers the expired-weak_ptr `return true`, the `spill()` throw
  (caught, swallowed, `return false`), and success (`finalizeSpill` erases, then `committed=true`).
- `forceSpill` (`.cpp:458`): guard covers `spill()` throw (re-thrown after release) and success. The pre-reserve
  `SpillStoreFailure` for emitted slices throws *before* the guard is constructed — correctly unguarded.
- `unspillAndMarkEmitted` (`.cpp:249`) and `getSliceBySliceEnd` (`.cpp:325`): the PR-3-fix added the guard to
  both unspill sites, so an `unspill()` throw releases the footprint-reader fence (under `evictionMutex`, with a
  `spillerCv` notify) instead of leaking it. Verified in the final `.cpp`.
- `releaseReservation` (`.cpp:84-94`) erases under `evictionMutex` AND `notify_all`s — so a non-finalize release
  (throw/expire) cannot hang a `spillerCv` waiter (PR-3 Deviation 4). This closes the liveness hole that the
  guard alone would have left.

No lock cycle exists. Deadlock-free.

### 3. Lifecycle / lifetime integration — VERIFIED end-to-end into a real query

Wiring chain confirmed in source (this is how the spiller reaches a real query, the brief's open question):
`WindowBuildPhysicalOperator::setup` (`.cpp:91`) → `sliceStoreRef->setupSliceStore` →
`setupSliceStoreProxy` (`DefaultTimeBasedSliceStoreRef.cpp:115-135`) which `dynamic_cast`s to
`SpillableTimeBasedSliceStore` and calls `setBufferProvider(pipelineCtx->getBufferManager())` → which stashes
the provider **then** `startSpiller()` (`SpillableTimeBasedSliceStore.cpp:137-155`). The store itself is
constructed in the lowering rule `LowerToPhysicalWindowedAggregation.cpp:236-244` when `conf.spill.enabled`.
**So a real spill-enabled query DOES get a running spiller** — `setup` runs once per build pipeline launch, and
`setBufferProvider` is idempotent (the "already stashed → return" branch + `startSpiller`'s `spillerStarted` CAS
guard, so multiple build pipelines on the same handler cannot double-start).

- **Spiller cannot start before the provider/backend exist:** `startSpiller` is only ever called at the END of
  `setBufferProvider` (after the provider is stashed); the backend is a ctor PRECONDITION-non-null. Confirmed.
- **Spiller cannot outlive backend/provider:** `stopSpiller()` is called FIRST in both `~SpillableTimeBasedSliceStore`
  (`.cpp:123-129`, dtor body is *only* `stopSpiller()`) and `deleteState` (`.cpp:389-394`, before any
  `backend->erase` or base clear). Plus `spiller` is the LAST member, so even without the explicit calls,
  reverse-order destruction joins it before backend/provider/sets die. Triple coverage (explicit dtor call +
  explicit deleteState call + member order). `stopSpiller` is idempotent (sets `quiesced`, then `joinable()`
  guards the request_stop/join — second call is a no-op).
- **Handler→store ownership:** the store is owned by the `AggregationOperatorHandler` (via the base
  `WindowSlicesStoreInterface` unique_ptr, `LowerToPhysicalWindowedAggregation.cpp:275-276`). The handler
  destroying the store triggers the dtor → `stopSpiller` → join, all before the handler's own
  BufferManager/backend references are torn down. No teardown path lets the joined thread touch a destroyed
  BufferManager/backend.
- **`deleteState` then restart:** `deleteState` quiesces+joins the spiller and sets `spillerStarted` is **not**
  reset — so a subsequent `setBufferProvider` would NOT restart the spiller (CAS sees `true`). This is the
  correct conservative behavior for the tumbling single-shot lifecycle here: `deleteState` is terminal in the
  query teardown path (the base dtor also calls it). There is **no path where deleteState runs, restarts work,
  and the spiller is gone or double-started** within the current operator lifecycle: `setup`→`setBufferProvider`
  happens once at pipeline launch (start), and `deleteState`/dtor happen at teardown (stop); they do not
  interleave to leave a half-torn-down store doing work. Verified clean.

### 4. Member declaration order — VERIFIED

Header `.hpp:207-255`: declared order is `backend`, `bufferProvider`, `entriesPerPage`, `pageSize`,
`softThresholdBytes`, `hardThresholdBytes`, `spilledKeys`, `emittedKeys`, `createdSlices`, `lastSeenWatermark`,
`evictionMutex`, `spillerCv`, `spillInProgress`, `quiesced`, `spillerStarted`, `spillCount`, **`std::jthread
spiller` LAST**. So at destruction (reverse order) the jthread joins FIRST, while `backend`, `bufferProvider`,
`createdSlices`, `spilledKeys`, `emittedKeys`, `evictionMutex`, `spillerCv`, `spillInProgress` all still exist —
exactly the state the joined loop could touch. Load-bearing order correct. `spiller` is genuinely the last
member. Confirmed.

### 5. ABI / ODR / override integrity — VERIFIED

- All six base virtuals are overridden with **exactly matching signatures** (verified against
  `WindowSlicesStoreInterface.hpp:69-92` and `DefaultTimeBasedSliceStore.hpp:64-71`): `getSlicesOrCreate`,
  `getTriggerableWindowSlices`, `getAllNonTriggeredSlices`, `getSliceBySliceEnd`,
  `garbageCollectSlicesAndWindows`, `deleteState` — same return types, same by-value `Timestamp`/`SliceEnd`/
  `SliceStart` parameters. Each is marked `override`; the dtor is `override`; the class is `final`.
- **No `-Woverloaded-virtual` hiding:** every override covers the full base signature (no partial overload set
  that would hide a base overload). pr3-done.md and pr3-review confirm the full `ninja` build is `-Werror` clean
  including `-Woverloaded-virtual` and `-Wunused-private-field`.
- **No base header changed** (verified by git diff — Cross-cutting summary above): blast radius is the subclass
  TU only. RocksDB stays a private dependency (the backend factory `makeRocksDBBackend` is defined in this TU;
  the lowering rule passes only config values and never links rocksdb — `LowerToPhysicalWindowedAggregation.cpp`
  comment + `.hpp:105-108`).
- Includes are self-contained: the `.hpp` includes `<atomic> <condition_variable> <stop_token> <thread> <mutex>
  <map> <set> <memory>` etc. for every member type; the `.cpp` adds `<exception> <chrono> <utility>` for its
  uses. No transitive-include reliance for the new members.

### 6. Regression delta vs PREFLIGHT — VERIFIED (see Aggregate stats)

6→20 unit TEST_F (+14, matching PR-1 +2 / PR-2 +8 / PR-2-fix +2 / PR-3-fix +2) + 1 new concurrency case. No
existing test changed behavior (base + other suites untouched). Consistent with pr3-done's 818/818 and 62/62
gate. ≈820 final, reasoned from per-PR counts + git verification. No regression.

### 7. Logging contract — VERIFIED

`NES_WARNING` on both over-budget paths (`.cpp:587` spill failure; `.cpp:607` no-cold-candidate over hard) —
correct severity for a degraded-but-proceeding condition (O-B5). All other new logs are `NES_DEBUG`/`NES_TRACE`
(construction, reserve, spill, unspill, wait). The `NES_INFO` in the lowering rule logs only `handlerId` and the
rocksdb *path* (a config value, not a secret). No PII, no secrets, no credentials in any log line. New functions
all have NES_* coverage at the appropriate level.

### 8. Test coverage of the cross-PR path — VERIFIED, with the spiller-started reachability confirmed

The stress test (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp`) genuinely covers the integrated path:
8 builder jthreads + 1 trigger jthread + the store-owned background spiller, with `setBufferProvider` actually
**starting the real spiller** (`.cpp:221`), a tiny soft threshold (1 page) forcing continuous eviction, and the
SPILL_LEAD=4 decoupling that forces the **same-SliceEnd spill-vs-trigger collision** (35 confirmed
`spillerCv.wait` hits / 50 iterations per pr3-done). The checksum is a genuine UAF detector (deterministic
`valueFor`, per-window double-count guard, `ASSERT_TRUE(isSliceResident)` at the exposure point), not a
tautology. It exercises build+trigger+spill+unspill concurrently, same-SliceEnd collision, and checksum
equality — all of dimension 8's requirements.

**Spiller-started-in-a-non-test path: CONFIRMED** (the brief's specific integration-gap question). The
`setup`→`setupSliceStore`→`setupSliceStoreProxy`→`setBufferProvider`→`startSpiller` chain (finding 3) means a
real spill-enabled query gets a running spiller — the wiring guarantees `setBufferProvider` is called at build
pipeline launch.

**Remaining integration gap (out-of-gate, not an Increment-B defect):** the **base-GC-vs-async-probe** lifetime
window (finding 1) is unit-testable only indirectly; the stress test's `getAllNonTriggeredSlices` drain + the
residency assertion cover the spill free-path, but the *base GC* free-path under a real async probe pipeline is
not exercised by these unit/stress tests (it is a base-store property). This is exactly what the LOCKED honest
ceiling defers to the thesis systest. No action for this gate.

### 9. Consistency of the PR-3 deviations with the rest of the class — VERIFIED

- **residentBytes()-under-mutex (Dev 1):** every caller invokes it WITHOUT holding `evictionMutex` —
  `getSlicesOrCreate` (`.cpp:171`), `evictUntilUnderHard` (`.cpp:603,608`), `spillerLoop` (`.cpp:719,734`, lock
  released before each call). The CALLER CONTRACT is documented at `.cpp:649-652`. No remaining caller assumes
  the old lock-free `residentBytes`. The in-flight-skip is consistent with the reservation fence used by the
  unspill paths. Consistent.
- **spillerLoop-budget-outside-lock (Dev 2):** the `wait_for` predicate checks ONLY `stop_requested()`
  (`.cpp:708`); the budget is evaluated after the lock is released (`.cpp:719`). No code anywhere uses the plan's
  original predicate-form `residentBytes() > soft` inside a `wait`/`wait_for` (which would self-deadlock on the
  non-recursive mutex now that `residentBytes` takes it). Verified no such residual call exists. Consistent.
- **unspill reservation fence (Dev 3):** both unspill sites reserve+release symmetrically with the spill path,
  and the PR-3-fix RAII guard makes the throw path consistent with `forceSpill`/`spillOneColdSlice`. Consistent.
- **releaseReservation notify (Dev 4):** matches `finalizeSpill`'s `notify_all`; all reservation-clearing paths
  now notify, so no `spillerCv` waiter can hang regardless of how the reservation was cleared. Consistent.

No place in the class still assumes the old lock-free `residentBytes` or the plan's predicate-form wait.

---

## Integration / teardown sequence (verified lifecycle order)

**Startup (per build-pipeline launch):**
1. `WindowBuildPhysicalOperator::setup` → `registerActivePipeline` (increments active input pipelines) →
   `sliceStoreRef->setupSliceStore`.
2. `setupSliceStoreProxy`: allocate slice-cache space, then `dynamic_cast` to `SpillableTimeBasedSliceStore` and
   `setBufferProvider(getBufferManager())`.
3. `setBufferProvider`: stash provider (idempotent) → `startSpiller()` (CAS-guarded, once) → background jthread
   begins blocking on `spillerCv`/`SPILLER_POLL_INTERVAL`.

**Steady state:** builders `getSlicesOrCreate` (self-track + soft/hard signal); spiller preemptively spills cold
slices to soft; hard backstop spills inline on the worker path; triggers `getTriggerableWindowSlices` →
unspill+claim-emitted → async probe; GC erases backend records for base-dropped slices.

**Teardown (whichever comes first):**
- `deleteState`: `stopSpiller()` (quiesce → request_stop → notify → join) FIRST → erase backend records →
  clear `emittedKeys`/`createdSlices` → base `deleteState` clears base maps.
- `~SpillableTimeBasedSliceStore`: `stopSpiller()` (idempotent) → then base `~DefaultTimeBasedSliceStore`
  (which calls `deleteState` again, also idempotent) → member destruction reverse order joins `spiller` FIRST
  (already joined), then backend/provider/sets last.

The spiller is provably stopped+joined before backend, bufferProvider, or any tracked set is destroyed, on every
teardown path.

---

## Honest validation ceiling (restated)

- **No ThreadSanitizer available** in this environment. The concurrency stress test
  (`concurrentBuildTriggerSpillIsRaceFree`, 50 internal iterations × `--repeat until-fail:10` = 500 scenario
  runs, 35 confirmed WAIT-path hits per 50 iterations, clean) is the **accepted validation ceiling** for this
  gate. It is a probabilistic UAF/race detector forcing the same-SliceEnd collision, not a proof.
- **What the stress test does cover:** the spill-vs-trigger / spill-vs-emit collision on the `spillInProgress` +
  `spillerCv` WAIT path; the async-probe-drain spill invariant (residency assertion + checksum); concurrent
  build+trigger+spill+unspill; teardown join.
- **What the systest (MS3/MS4, out-of-gate) must still confirm:**
  1. The **base-GC-vs-async-probe** lifetime window under a real multi-pipeline query with a genuinely
     asynchronous probe consuming the emitted `HashMap*` buffers (the one lifetime property Increment B relies on
     from the base store but does not itself test). This is pre-existing-base behavior, not an Increment-B
     change, but final confidence belongs to the systest.
  2. Real RocksDB backend under load (the stress test uses a mutex-wrapped in-memory backend to isolate
     store-level concurrency from the test backend's own non-thread-safety — a legitimate isolation, verified not
     to mask store races since all store serialization runs before any backend call).
  3. End-to-end correctness of a spill-enabled windowed aggregation query result (the lowering→handler→store→
     probe path) under memory pressure.

LOCKED items (tumbling-only; no base edit; no JIT/SliceCache change; uniform no-I/O-under-evictionMutex; O-B5
proceed+log; store-owned jthread; TSan-unavailable ceiling) were respected throughout and were not re-raised.
