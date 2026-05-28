# PR-3 Code Review — background spiller thread + concurrency stress test
**Commit:** `8a3a0366ac9abacc743a397a75f046f2a4e454ac`
**Reviewer:** Claude (code-reviewer agent), 2026-05-23
**Files reviewed:**
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` (NEW)
- `nes-physical-operators/tests/CMakeLists.txt`

**Context read:**
- `.planning/dev-eng-cpp-B/PLAN.md` §1 PR-3 + §3
- `.planning/dev-eng-cpp-B/impl/pr3-done.md`
- `.planning/dev-eng-cpp-B/reviews/PR2-REVIEW.md`

---

## Verdict

**APPROVE WITH NITS**

Must-fix count: **0 Critical / 0 High / 0 Medium**. Two Low findings; four Nits.

The `residentBytes()`-under-mutex + `spillerLoop`-outside-lock restructuring is **race-free and deadlock-free**: the decision to hold `evictionMutex` while scanning non-in-flight slices is the logically necessary fix for a real data race exposed by the stress test; the loop's reciprocal discipline (budget evaluated after lock release, no condvar predicate calling `residentBytes()`) closes the self-deadlock the plan's original predicate form would have introduced. The implementation is correct.

---

## Deviation Analysis (required per review scope)

### Deviation 1 — `residentBytes()` now takes `evictionMutex` + skips `spillInProgress` slices

**Verdict: CORRECT. Required fix, not scope creep.**

**Lock order.** Every call to `residentBytes()` is made with `evictionMutex` NOT held by the caller:

- `getSlicesOrCreate` (line 171): called after `recordCreatedSlice` returns; no prior `evictionMutex` acquisition in this call frame.
- `evictUntilUnderHard` (lines 588, 593): called from `getSlicesOrCreate` (no mutex) and from `spillColdSlicesUnderHardForTest` (the test hook, also unmutexed); `residentBytes()` is invoked directly in the `while` condition and in the `NES_WARNING` — both are outside any `evictionMutex` block.
- `spillerLoop` (lines 700, 704): the loop explicitly drops the lock before this code (`spillerCv.wait_for` releases on return; the `{}` block at lines 692–695 exits before line 700). Confirmed.
- `spillOneColdSlice` → `evictUntilUnderHard` chain: same as above.
- No test hook calls `residentBytes()` while holding `evictionMutex`.

Internal lock order inside `residentBytes()`: `evictionMutex` acquired first (line 638), then `createdSlices.withRLock` (line 641). This is `evictionMutex → createdSlices` — the single legal order per the plan and consistent with `pickAndReserveColdSlice`.

**In-flight skip correctness.** Skipping `spillInProgress` slices under-counts the current footprint during the I/O interval. This is safe by the same argument as PR-2's stale-high/stale-low analysis: skipping is always under-counting (the slice is being freed or rebuilt, so its footprint is between 0 and full); neither the hard check nor the soft signal are harmed by a transient under-count. A stale-low reading on the hard path may defer one eviction round, but the next `getSlicesOrCreate` call re-evaluates. On the soft path the spiller re-evaluates every SPILLER_POLL_INTERVAL. No permanent accounting hole: when the I/O completes the slice is either in `spilledKeys` (counted as 0 by `residentBytesOf`) or fully resident again (counted at full weight), so the next `residentBytes()` call reflects the correct post-I/O state. The skip cannot cause a permanent under-count that prevents reaching the hard threshold: a slice being unspilled is temporarily skipped, but it will be included in the next call once it leaves `spillInProgress`.

**Conclusion:** Deviation 1 is correctly implemented.

---

### Deviation 2 — `spillerLoop` evaluates budget OUTSIDE the lock; condvar predicate checks only `stop_requested()`

**Verdict: CORRECT. Necessarily different from the plan's predicate form.**

The PLAN's proposed predicate `[&]{ return stopToken.stop_requested() || residentBytes() > softThresholdBytes; }` would have called `residentBytes()` while holding `evictionMutex` (the predicate is evaluated with the mutex locked per `std::condition_variable::wait_for` semantics). Since `residentBytes()` also acquires `evictionMutex` (Deviation 1), the plan's form would have been a self-deadlock on the non-recursive `std::mutex`.

The actual implementation uses a **predicate-less `wait_for`** (woken by `notify_one`/`notify_all` or by timeout) and evaluates the budget only after releasing the lock. This is correct:

**No missed-wakeup.** A slice becoming cold occurs when: (a) `lastSeenWatermark` advances in `getTriggerableWindowSlices` (which then calls `spillerCv.notify_one()`), or (b) a new slice is created in `getSlicesOrCreate` over soft (which calls `spillerCv.notify_one()`). Both paths explicitly notify after the state change. The `SPILLER_POLL_INTERVAL` (100 ms) safety net closes the residual window where a store goes over-soft after stopping to create slices entirely.

**No busy-spin.** The `wait_for` will block for up to 100 ms if no notification arrives. The inner `while (residentBytes() > softThresholdBytes)` loop exits either by dropping below soft, by exhausting cold candidates, or by `stop_requested()`. All three exit conditions are bounded; none is a tight spin on the mutex.

**No lost notification between budget check and wait.** The classic lost-wakeup scenario is: thread checks predicate (false), notification fires, thread enters wait (misses it). Here: the outer `while (!stopToken.stop_requested())` evaluates without the lock; the `wait_for` then acquires the lock and enters the wait. A notification between the outer-while check and the `wait_for` lock acquisition is not lost — `wait_for` returns immediately if the notification arrives between the lock acquisition and the actual wait entry (this is the standard POSIX/C++ guarantee). The `SPILLER_POLL_INTERVAL` additionally provides a maximum sleep bound so even a truly lost notification is bounded to 100 ms.

**`SPILLER_POLL_INTERVAL` as safety net.** 100 ms is conservative for a background preemptive eviction use case where the primary wakeup path (condvar notify at each new slice / trigger) handles the common case. A store entirely quiescent at over-soft will be drained within one poll interval, which is acceptable given O-B5's "never block" constraint.

**Conclusion:** Deviation 2 is correctly implemented.

---

### Deviation 3 — Unspill-on-read paths reserve in `spillInProgress` to fence `residentBytes()`

**Verdict: CORRECT.**

**`unspillAndMarkEmitted`** (the trigger path):
1. Under `evictionMutex`: wait out any in-flight spill (`spillerCv.wait`), insert into `emittedKeys`, conditionally insert into `spillInProgress`.
2. Outside the mutex: `spillable->unspill(...)`, `spilledKeys.wlock()->erase(sliceEnd)`.
3. Re-acquire `evictionMutex`: erase from `spillInProgress`, then release, then `notify_all`.

Release completeness: step 3 always executes the inner `{}` block if `needsUnspill` is true, regardless of whether `unspill()` throws. However — `unspill()` is called without a guard. If `unspill()` throws, `spillInProgress` retains the entry for this `sliceEnd`, which would hang any future waiter on that slice. This is a **Low** finding (see below).

No deadlock against the spiller: the spiller cannot pick this slice (it is `emittedKeys`), so no spiller will be waiting on it; only a second trigger of the same slice could wait, which is structurally impossible in the tumbling-window single-emit model. `getSliceBySliceEnd` also takes `evictionMutex` but it checks `spillInProgress` and waits, then takes its own reservation. No cycle.

**`getSliceBySliceEnd`** (join-probe path):
Identical structure: wait → reserve → I/O outside → release. Not emitted, so a concurrent spiller could pick it concurrently if the wait-and-reserve window were open — the implementation correctly closes this by doing the wait and reserve atomically under `evictionMutex` before releasing it.

The same Low-severity `unspill()` exception path without a RAII guard applies here too (see Low finding L1).

**Conclusion:** Deviation 3 is correctly implemented, modulo the Low `unspill()` exception path.

---

### Deviation 4 — `releaseReservation` notifies `spillerCv`

**Verdict: CORRECT. Closes a real liveness hole.**

Without the notify, the O-B5 exception-swallow path in `spillOneColdSlice` (which calls `releaseReservation` via `ReservationGuard` when `spill()` throws) would release the `spillInProgress` entry but never fire a `notify_all`. Any `unspillAndMarkEmitted` caller waiting on `spillerCv.wait(lock, [&]{ return !spillInProgress.contains(sliceEnd); })` would be waiting on a predicate that is now trivially true — but would only wake on the next unrelated notify or the spiller's own `wait_for` timeout (up to 100 ms latency). With the notify in `releaseReservation`, the waiter wakes immediately.

Spurious-wakeup correctness: `spillerCv.notify_all()` in `releaseReservation` can also wake the spiller itself. The spiller's wait predicate is `stopToken.stop_requested()` — the notify leaves it false, so the spiller re-enters `wait_for` immediately without any erroneous re-spill. The trigger waiter's predicate `!spillInProgress.contains(sliceEnd)` is now true (the reservation was just erased), so it proceeds correctly. No incorrect transitions result from the spurious-wakeup.

**Conclusion:** Deviation 4 is correctly implemented.

---

## Core PR-3 Correctness

### Async-probe-drain absolute invariant

**Is the guarantee airtight?** Trace for `unspillAndMarkEmitted`:
1. Thread T (trigger) acquires `evictionMutex`.
2. T checks `spillInProgress.contains(sliceEnd)`. If true, T waits on `spillerCv` — `evictionMutex` is released during the wait (standard `condition_variable::wait` semantics).
3. Once the wait predicate `!spillInProgress.contains(sliceEnd)` is true (i.e., the spiller finalized or released), T reacquires `evictionMutex` atomically before returning from `wait`.
4. T inserts `sliceEnd` into `emittedKeys.wlock()` — still under `evictionMutex`.
5. T releases `evictionMutex`.

Is there a window between step 3's predicate becoming true and step 4's insert where the spiller could re-pick this slice? No: steps 3 and 4 both happen under the held `evictionMutex`. `pickAndReserveColdSlice` also holds `evictionMutex` while checking `emittedKeys`. There is no window between the wait returning and the emitted-insert where the spiller can hold `evictionMutex` — only one thread holds it at a time. The emitted-insert is atomic with respect to the spiller's pick.

**Conclusion:** The async-probe-drain invariant is airtight.

### Deadlock freedom

Enumeration of all locks and their acquisition sites:

| Lock | Owner |
|------|-------|
| `evictionMutex` | `std::unique_lock` in: `releaseReservation`, `unspillAndMarkEmitted` (inner section + release section), `getSliceBySliceEnd` (inner + release), `forceSpill` (emitted/reserve), `pickAndReserveColdSlice`, `finalizeSpill`, `residentBytes`, `spillerLoop` (wait block), `stopSpiller` (quiesce) |
| `createdSlices` (folly rlock/wlock) | `recordCreatedSlice`, `pickAndReserveColdSlice` (snapshot via `withRLock`), `residentBytes` (via `withRLock`), `garbageCollectSlicesAndWindows` (wlock), `deleteState` (wlock), `createdSlicesSizeForTest` (rlock) |
| `spilledKeys` (folly rlock/wlock) | `isSliceResident`, `finalizeSpill` (wlock under `evictionMutex`), `unspillAndMarkEmitted` (erase OUTSIDE), `getSliceBySliceEnd` (erase OUTSIDE), `garbageCollectSlicesAndWindows` (wlock), `deleteState` (wlock) |
| `emittedKeys` (folly rlock/wlock) | `unspillAndMarkEmitted` (wlock INSIDE `evictionMutex`), `forceSpill` (rlock INSIDE), `pickAndReserveColdSlice` (rlock INSIDE), `garbageCollectSlicesAndWindows` (wlock OUTSIDE), `deleteState` (wlock OUTSIDE) |
| `bufferProvider` (folly rlock/wlock) | `setBufferProvider` (wlock), `unspillAndMarkEmitted` (copy), `getSliceBySliceEnd` (copy) |

**Base maps (`slices`, `windows`):** The base `DefaultTimeBasedSliceStore::getSliceBySliceEnd` (and `getSlicesOrCreate`, `getTriggerableWindowSlices`, etc.) acquires base-internal locks. All calls to these are made BEFORE acquiring `evictionMutex` in every path (`forceSpill` was verified in PR-2 re-review; `spillOneColdSlice` uses `getSliceBySliceEnd` after `pickAndReserveColdSlice` exits the `evictionMutex` block at line 521). NH1 is preserved.

**The single global order:** `evictionMutex` → (`createdSlices` | `emittedKeys` | `spilledKeys`) → never nested with each other. The folly Synchronized locks (`spilledKeys`, `emittedKeys`) are taken one at a time; the wlock of `emittedKeys` inside `evictionMutex` (in `unspillAndMarkEmitted`, `forceSpill`, `pickAndReserveColdSlice`) follows the declared order.

**`spillerCv.wait` releases `evictionMutex`:** Confirmed — `std::condition_variable::wait` mandates this.

**No lock cycle found.**

### Lifecycle / lifetime

**`spiller` declared LAST:** Confirmed in header (line 255). Reverse-order destruction joins it before `spilledKeys`, `emittedKeys`, `createdSlices`, `bufferProvider`, and `backend` are destroyed.

**`stopSpiller()` called FIRST in dtor:** Line 128 — confirmed. The dtor body is only `stopSpiller()`.

**`stopSpiller()` called FIRST in `deleteState()`:** Line 379 — confirmed. `backend->erase()` calls at line 390 happen after join.

**Idempotent `stopSpiller()`:** First call: sets `quiesced = true`, calls `spiller.request_stop()`, `notify_all`, `spiller.join()`. After `join()`, `spiller.joinable()` returns `false`. Second call: sets `quiesced = true` again (idempotent on a `bool`), `joinable()` is false → the `if` body is skipped. Safe.

**`spiller` accessing dead state after join:** Not possible — join blocks until `spillerLoop` returns. The loop's exit conditions are `stopToken.stop_requested()` (checked at top of outer `while` and inside the inner drain loop). After `request_stop()` + `notify_all`, the loop wakes from `wait_for`, re-checks `stopToken.stop_requested()` (true), and exits. The only state the loop touches after wakeup is `spillOneColdSlice` (guarded by `quiesced` → `pickAndReserveColdSlice` returns nullopt immediately). The loop cannot access `backend` or `bufferProvider` after `stopSpiller` joins it.

**`startSpiller()` start-once:** CAS on `spillerStarted` with `compare_exchange_strong(expected=false, true)`. A second call leaves `expected = true`, CAS fails, body skipped. The "already stashed" branch in `setBufferProvider` also prevents a second `startSpiller()` call reaching the CAS. Belt-and-braces is correct.

### `std::jthread` / `stop_token`

`request_stop()` + `notify_all()` + `join()` sequence in `stopSpiller` is correct. `spillerLoop`'s predicate on `wait_for` checks `stopToken.stop_requested()`, so a stop request during a `wait_for` call wakes immediately. The outer `while (!stopToken.stop_requested())` and inner `if (stopToken.stop_requested()) break` ensure prompt exit after a stop is requested mid-drain.

---

## Low

### L1 — `unspill()` call in `unspillAndMarkEmitted` (and `getSliceBySliceEnd`) lacks RAII guard for the `spillInProgress` footprint-reader fence

**File:** `SpillableTimeBasedSliceStore.cpp`
- `unspillAndMarkEmitted`, lines 243–250
- `getSliceBySliceEnd`, lines 312–319

In both paths, if `spillable->unspill(*backend, *provider)` throws, control leaves the `if (needsUnspill)` block before reaching the inner `std::unique_lock` block that erases `sliceEnd` from `spillInProgress`. The entry remains in `spillInProgress` permanently.

For `unspillAndMarkEmitted`: the slice was already inserted into `emittedKeys` (step 4 above), so no spiller can re-pick it. However, the leaked `spillInProgress` entry means `residentBytes()` permanently skips this slice (its footprint is invisible). For a triggered+emitted slice this is moot — emitted slices are not eviction candidates and the probe is about to drain them — but it leaves a stale entry in `spillInProgress` for the lifetime of the store. If a second trigger of the same sliceEnd is somehow attempted (unusual in tumbling-window model but possible in the terminate path via `getAllNonTriggeredSlices`), it would block forever on `spillerCv.wait`.

For `getSliceBySliceEnd`: the slice is NOT emitted. A leaked reservation means any future access to this sliceEnd via `unspillAndMarkEmitted` or `getSliceBySliceEnd` will wait forever.

**Severity:** Low. The tumbling-window + single-emit model makes the `unspillAndMarkEmitted` scenario unlikely in production (a slice is emitted once). However `getSliceBySliceEnd` serving the join-probe path is more exposed. The fix is straightforward:

```cpp
// In unspillAndMarkEmitted, replace the inner needsUnspill block:
if (needsUnspill)
{
    INVARIANT(provider != nullptr, ...);
    NES_DEBUG("Unspilling slice {} on trigger read", sliceEnd);
    // ReservationGuard releases spillInProgress on any exception path
    ReservationGuard unspillGuard{*this, sliceEnd};
    spillable->unspill(*backend, *provider);
    spilledKeys.wlock()->erase(sliceEnd);
    unspillGuard.committed = true;  // no release via guard; manual erase below
    {
        std::unique_lock lock(evictionMutex);
        spillInProgress.erase(sliceEnd);
    }
    spillerCv.notify_all();
}
```
Or equivalently wrap in `try/catch` that calls `releaseReservation` before rethrowing. The same pattern applies to `getSliceBySliceEnd`.

---

### L2 — `spillerLoop` calls `residentBytes()` twice per inner loop iteration, one of which is inside the `NES_TRACE` log call

**File:** `SpillableTimeBasedSliceStore.cpp`, lines 700–705

```cpp
while (residentBytes() > softThresholdBytes)
{
    if (!spillOneColdSlice())
    {
        NES_TRACE("Over soft ({} > {}) but no cold candidate; spiller sleeping", residentBytes(), softThresholdBytes);
        break;
    }
```

The `NES_TRACE` call invokes `residentBytes()` a second time to format the log message. Since `NES_TRACE` is compiled out at non-trace log levels this is a production no-op, but it makes the code misleading: a reader sees two `residentBytes()` calls and may wonder if one is observing a different state. The first call in the `while` condition is the authoritative check; the second in the trace message is purely diagnostic.

**Fix (Nit-level):** Capture the value once: `const auto resident = residentBytes();` before the `while`, use `resident` in both the condition and the log — or simply use a static `"No cold candidate; spiller sleeping"` message without the resident count (since the TRACE level is only enabled for debugging).

This is Low rather than Nit only because it slightly increases per-iteration lock contention on the spiller path when trace logging is enabled.

---

## Nits

**N1 — `getSlicesOrCreate` uses the stale `resident` from before `evictUntilUnderHard()` for the soft check**
`SpillableTimeBasedSliceStore.cpp`, lines 171–182. The `resident` variable is captured before the hard-eviction call; the `else if (resident > softThresholdBytes)` branch is unreachable if the hard branch fires (correct — `else if`), but the comment "NOT already over hard (the hard branch already spilled inline)" is slightly misleading: if we are over hard we are by definition also over soft, so the `else if` correctly skips the soft notify. The actual concern is that `resident` is stale after `evictUntilUnderHard()` — the soft check uses the pre-eviction value. This is benign (at worst sends a spurious notify), but worth a comment noting "resident is captured once; stale after evictUntilUnderHard, but harmless."

**N2 — `stopSpiller` acquires `evictionMutex` just to set `quiesced = true`, but `quiesced` is checked only inside `evictionMutex` in `pickAndReserveColdSlice`**
`SpillableTimeBasedSliceStore.cpp`, lines 725–739. The lock is needed to make `quiesced = true` visible to `pickAndReserveColdSlice`, which reads it under the same mutex. This is correct. However the comment "Idempotent: callable from BOTH the dtor and deleteState" could note that the idempotency of the `quiesced = true` write (it is just a bool assignment, not a CAS) is correct here because `evictionMutex` ensures it is not a data race, and the `joinable()` guard prevents double-join. Minor comment improvement only.

**N3 — `finalizeSpill` log message dropped the `"; resident now {}"` suffix that the PLAN specified**
`PLAN.md` line 319: `NES_DEBUG("Spilled cold slice {}; resident now {}", end, residentBytes())`. PR-2-REVIEW M2 recommended moving `residentBytes()` out of the lock — the implementer correctly resolved M2 by dropping the suffix entirely rather than moving it, simplifying the code. This is the right call and was approved in the PR-2 re-review. Noting for completeness that the PLAN's exact log string was not carried verbatim — no action needed.

**N4 — Test `windowTriggered` check uses `idx <= NUM_WINDOWS` but `windowEnd / WINDOW_SIZE` for window `w` produces `idx = w + 1` in range `[1, NUM_WINDOWS]`, so `idx == 0` is never valid**
`SpillableTimeBasedSliceStoreConcurrencyTest.cpp`, lines 280–288. The guard `idx <= NUM_WINDOWS` protects against out-of-bounds access on `windowTriggered` (which has size `NUM_WINDOWS + 1`). Index 0 (`windowEnd == 0`) is never generated by the test's key scheme (windowEnd = (w+1)*WINDOW_SIZE ≥ 100), so the `<= NUM_WINDOWS` bound correctly encompasses all valid indices. No logic error; minor comment clarification ("idx is always in [1, NUM_WINDOWS]") would help a reader.

---

## Test quality assessment

### Does the stress test genuinely exercise the WAIT path?

Yes. The SPILL_LEAD=4 decoupling is the critical design choice: the trigger seeds `lastSeenWatermark` 4 windows ahead of emission, so a window that is fully built, cold, and not yet emitted is visible to the background spiller (which runs continuously at tiny soft=1 page). The 35 WAIT-path log hits across 50 iterations (reported in `pr3-done.md`) are direct evidence. The ASSERT in `foldTriggered` (`ASSERT_TRUE(store->isSliceResident(sliceEnd))`) fires immediately on the trigger thread if a slice is handed back non-resident — it is a real UAF detector, not a tautology.

### Is the checksum post-condition non-tautological?

Yes. The expected checksum is computed up-front from the deterministic `valueFor(worker, key)` formula before any I/O occurs. The observed checksum is accumulated from the actual raw `HashMap*` pointers returned by the trigger and drain paths. Any UAF (reading from a freed map) would produce garbage values and almost certainly break the equality. Any lost tuple (a window silently dropped due to a missed trigger or double-spill) would produce a deficit. The `windowTriggered` flag prevents a window from contributing twice to `observedChecksum` across repeated trigger sweeps. The post-condition is sound.

### `ThreadSafeInMemorySpillBackend` — isolation or masking?

Legitimate isolation. The review question is whether the mutex wrapper accidentally hides a store-level race that the production RocksDB backend would also suffer. It does not: the production `RocksDBSpillBackend` is independently thread-safe (RocksDB's `DB::Put`/`Get` are documented thread-safe on different keys). The `ThreadSafeInMemorySpillBackend` wrapper brings the test backend to the same safety level as the production one. Any race in the store's own serialization (the `spillInProgress` reservation, the `emittedKeys` claim, the condvar wait) would still be visible because those paths run entirely within the store before any backend call is made. The wrapper does not mask store-level races.

### `EXPECT_GT(spillCountForTest(), 0U)` — not an exact count

Correct per PR-2 L3: `spillCount` is incremented before `finalizeSpill`, so it momentarily reads "one ahead." The stress test correctly uses `> 0`, not an equality. Given the tiny soft threshold (1 page) and 8 builders producing 64 windows each, the actual spill count per iteration is in the thousands (2759 per pr3-done.md), so `> 0` is a very conservative bound that cannot be accidentally satisfied by a broken implementation.

### Iterations and timeout

50 iterations × 500 total runs (`--repeat until-fail:10`) with no flake, completing in 0.47–0.49 s per run, is well within the 120 s ctest timeout. The iteration count is meaningful: at 8 builders × 64 windows, each iteration exercises O(512) slice lifetimes, making false-negative probability for a race in the WAIT path extremely low given the SPILL_LEAD forcing mechanism.

### Assertion weakening

No assertion has been weakened to make the test pass. The residency assertion is `ASSERT_TRUE` (not `EXPECT_TRUE`) so a failure aborts the test immediately rather than silently continuing. The checksum is `EXPECT_EQ`, not `EXPECT_GE`. The `EXPECT_GT(spillCount, 0)` is the intended lower-bound assertion, documented in L3.

---

## PR-2 invariant regression check

- **H1/H2 (ReservationGuard) preserved:** Both `spillOneColdSlice` and `forceSpill` still use `ReservationGuard` correctly; the guard's `releaseReservation` now additionally fires `spillerCv.notify_all()` (Deviation 4), which is the correct extension.
- **M1 (break on watermark):** Preserved — `pickAndReserveColdSlice` still uses `break` for the watermark predicate and `continue` for all others.
- **M2 (residentBytes out of finalizeSpill):** Preserved — `finalizeSpill` only holds `evictionMutex` for `spilledKeys.insert` + `spillInProgress.erase`.
- **M3 (unspillAndMarkEmitted obligation):** Closed — the PR-3 rewrite is complete and correct.
- **L3 (spillCount ordering contract):** The comment documenting the `>= finalized spills` semantics is preserved in both `spillOneColdSlice` (line 575) and `forceSpill` (line 448).

---

## Compliments

**The deadlock-avoidance restructuring is intellectually honest and technically precise.** The decision to drop the plan's `residentBytes()` predicate from `wait_for`, document the exact reason (self-deadlock on non-recursive mutex), and evaluate the budget outside the lock is the kind of reasoned deviation that improves on the plan. The comment block at lines 685–688 explaining the constraint is exactly the level of clarity this code requires.

**The async-probe-drain guarantee is airtight.** The wait-then-claim-under-mutex sequence in `unspillAndMarkEmitted` has no window for the spiller to re-pick the slice between the condvar wakeup and the emittedKeys insert. This is the single most important correctness property of the increment and it is correctly implemented.

**`releaseReservation` liveness fix is clean.** Adding `notify_all` there closes the liveness hole without introducing any spurious behaviour — the spiller's predicate-less `wait_for` ignores extra notifications, and the trigger's predicate correctly guards against false-positive wakeups.

**Teardown ordering is belt-and-suspenders.** `spiller` declared last (member destruction order) + explicit `stopSpiller()` at the top of both `~SpillableTimeBasedSliceStore()` and `deleteState()` + `quiesced` stopping the picker before join is triple coverage. Any one of the three is sufficient; all three together make the invariant easy to verify and hard to violate.

**The stress test design is excellent.** The SPILL_LEAD decoupling to force the spill-vs-trigger same-SliceEnd collision is non-obvious and directly addresses the binding requirement from reconciled divergence 1. The checksum post-condition is a genuine UAF detector, not a tautology. The 35 confirmed WAIT-path hits per 50 iterations prove the collision path is exercised, not bypassed. This is the highest-quality concurrent stress test design one can achieve in a no-TSan environment.

**Full module regression (818/818) with the new test adding to the count** is exactly the right integration signal. The TDD evidence (RED: ABORT on PR-2 production; GREEN: 50-iteration clean) is credible.

