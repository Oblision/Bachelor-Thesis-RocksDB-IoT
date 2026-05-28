# PR-2 Code Review — cold-slice eviction policy + synchronous hard spill
**Commit:** `dec5f95d62752a92972ad5010a151290e5b8d922`
**Reviewer:** Claude (code-reviewer agent), 2026-05-23
**Files reviewed:**
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`

---

## Verdict

**REQUEST CHANGES**

One High finding (reservation leak on exception in `spillOneColdSlice` — will deadlock PR-3's condvar
wait) and one matching High in `forceSpill`. Three Medium findings. No Critical. Several Low/Nit
observations.

Must-fix count: **0 Critical / 2 High / 3 Medium**.

---

## Critical

None.

---

## High

### H1 — Reservation leak on `spill()` exception in `spillOneColdSlice`
**File:** `SpillableTimeBasedSliceStore.cpp` — `spillOneColdSlice`, lines ~392–394

Trace:
1. `pickAndReserveColdSlice()` inserts `*picked` into `spillInProgress` under `evictionMutex`.
2. `spillOneColdSlice` falls through to `spillable->spill(*backend);`.
3. If `spill()` throws (RocksDB I/O error, bad_alloc, etc.), the exception propagates out of
   `spillOneColdSlice` — `++spillCount` and `finalizeSpill(*picked)` are never reached.
4. The `SliceEnd` therefore stays in `spillInProgress` permanently (until process exit or `deleteState`).

In PR-2, single-threaded, no condvar: the leaked reservation blocks future eviction of that slice
(it looks "in-flight" forever) and bloats `spillInProgress`, but does not deadlock and does not cause
incorrect results. However the PLAN's PR-3 `unspillAndMarkEmitted` rewrite will do:
```
while (spillInProgress.contains(sliceEnd)) spillerCv.wait(lock);
```
A leaked reservation will make any trigger of that slice wait forever — a guaranteed deadlock in PR-3.
This must be fixed in PR-2 so the PR-3 author does not inherit a trap.

Additionally `spillCount` will be wrong (under-counted on a throw path), and `spilledKeys` will not
contain the slice, so `isSliceResident` will falsely report it resident while its data is actually
partially or fully on disk (depending on whether RocksDB wrote before throwing).

**Fix:** wrap the I/O + finalize in a try/catch that erases the reservation on any exception path, then
re-throws. A RAII guard is cleaner:

```cpp
// In spillOneColdSlice, replace the bare spill+finalize block:
struct ReservationGuard {
    SpillableTimeBasedSliceStore& store;
    SliceEnd end;
    bool committed = false;
    ~ReservationGuard() {
        if (!committed) {
            std::unique_lock lk(store.evictionMutex);
            store.spillInProgress.erase(end);
        }
    }
};
ReservationGuard guard{*this, *picked};
spillable->spill(*backend);
++spillCount;
finalizeSpill(*picked);   // inserts spilledKeys, erases spillInProgress
guard.committed = true;   // finalizeSpill already erased it — don't double-erase
return true;
```

Or equivalently, a simple try/catch:
```cpp
try {
    spillable->spill(*backend);
} catch (...) {
    std::unique_lock lk(evictionMutex);
    spillInProgress.erase(*picked);
    throw;  // or swallow + return false depending on desired semantics
}
++spillCount;
finalizeSpill(*picked);
```

Note: the PLAN's O-B5 rule says "never throw, never block" for the eviction path. That would
justify swallowing the exception and returning `false` (treat I/O failure as "no candidate found").
Either convention is acceptable, but the reservation MUST be erased before returning or re-throwing.

---

### H2 — Reservation leak on `spill()` exception in `forceSpill`
**File:** `SpillableTimeBasedSliceStore.cpp` — `forceSpill`, lines ~303–307

Same structural issue as H1. `forceSpill` inserts `sliceEnd` into `spillInProgress` at line 301
(inside the `evictionMutex` block), then the lock is released, and `spillable->spill(*backend)` is
called. If `spill()` throws, `finalizeSpill` is never reached. The `SliceEnd` stays reserved forever.

`forceSpill` is a public API that unit tests and the stress test call directly. The stress test
(PR-3 Section 3) uses `forceSpill` interchangeably with the eviction path. A leaked reservation there
will cause the same condvar deadlock described in H1.

`forceSpill` is also the only spill path that throws (`SpillStoreFailure` on emitted slice). A caller
who catches `SpillStoreFailure` and retries — or who calls `forceSpill` from the stress test and gets
an I/O failure — will see the slice stuck reserved.

**Fix:** same as H1 — wrap the I/O in a try/catch or RAII guard that erases the reservation.
The `SpillStoreFailure` throw (on emitted slice) happens BEFORE `spillInProgress.insert`, so that path
is already safe. Only the post-reservation I/O path needs the guard.

---

## Medium

### M1 — `pickAndReserveColdSlice` uses `continue` where `break` is correct, with a self-contradicting comment
**File:** `SpillableTimeBasedSliceStore.cpp` — `pickAndReserveColdSlice`, lines ~330–333

```cpp
if (end.getRawValue() >= wm)
{
    /// Not cold yet … Ascending order ⇒ no later entry is cold either, but
    /// keep scanning for clarity — the predicate short-circuits cheaply.
    continue;   // <-- should be break
}
```

The comment correctly observes that ascending order means no later entry can be cold once this
condition fires. Despite the comment, the code `continue`s rather than `break`ing. This means the
loop always scans the entire snapshot, including all hot slices that will never satisfy the cold
predicate, even when they make up 99% of the map. At large slice counts under memory pressure this
is a measurable regression: `pickAndReserveColdSlice` is called in a tight eviction loop by the
worker thread (on the `getSlicesOrCreate` path).

The "for clarity" justification in the comment is incorrect — continuing past non-cold entries is
less clear than breaking, not more. The comment should say `break` and the code should match.

**Fix:** change `continue;` to `break;`. Update the comment to: "Ascending order ⇒ no later entry
is cold; stop scanning."

---

### M2 — `finalizeSpill` calls `residentBytes()` (which locks `createdSlices`) while holding `evictionMutex`, embedded in a debug log
**File:** `SpillableTimeBasedSliceStore.cpp` — `finalizeSpill`, lines ~363–368

```cpp
void SpillableTimeBasedSliceStore::finalizeSpill(const SliceEnd end)
{
    std::unique_lock lock(evictionMutex);
    spilledKeys.wlock()->insert(end);
    spillInProgress.erase(end);
    NES_DEBUG("Spilled cold slice {}; resident now {}", end, residentBytes());
}
```

`residentBytes()` acquires `createdSlices.rlock()`. The lock order here is
`evictionMutex → createdSlices` — this is the LEGAL order per the PLAN and so it is not a deadlock
risk now.

However: the `residentBytes()` call is a non-trivial scan (iterates the entire tracked slice map,
calls `weak_ptr::lock()` and `residentBytesOf()` for each) and it happens while `evictionMutex` is
held. In PR-3, `evictionMutex` will be contended by both the worker thread (eviction) and the
background spiller; holding it across a full `createdSlices` scan during every `finalizeSpill` call
measurably increases contention on the lock that must not stall workers.

The plan's stated invariant is "evictionMutex covers ONLY in-memory decisions" — a diagnostic
`residentBytes()` inside the mutex is scope creep.

**Fix (two options):**

Option A (preferred): move the `residentBytes()` call outside the mutex. `finalizeSpill` already
knows it just erased one reservation, so the log line can either be dropped or computed after
`lock` goes out of scope:
```cpp
void SpillableTimeBasedSliceStore::finalizeSpill(const SliceEnd end)
{
    {
        std::unique_lock lock(evictionMutex);
        spilledKeys.wlock()->insert(end);
        spillInProgress.erase(end);
    }
    NES_DEBUG("Spilled cold slice {}; resident now {}", end, residentBytes());
}
```

Option B: if the log line must be accurate under the lock (so the logged value reflects the
finalized state), document explicitly why `residentBytes()` is called inside the mutex and add a
comment noting the lock-order safety (`evictionMutex → createdSlices`).

This is Medium because it does not deadlock and the lock order is correct, but it will cause
measurable contention in PR-3 and conflicts with the "no scan under lock" discipline.

---

### M3 — `unspillAndMarkEmitted` in PR-2 has no `evictionMutex` — the TOCTOU it closes in PR-3 is still open at this layer
**File:** `SpillableTimeBasedSliceStore.cpp` — `unspillAndMarkEmitted`, lines ~128–152

**Scope note:** the PLAN explicitly states that `unspillAndMarkEmitted` is rewritten in PR-3 to
close the spill-vs-emit TOCTOU under `evictionMutex`. This finding is NOT flagging the absence of
PR-3 work. It is flagging that the PR-2 form of `forceSpill` tightened the emitted-check + reserve
atomicity, but `unspillAndMarkEmitted` does NOT take `evictionMutex` before inserting into
`emittedKeys`. This means the PR-2 model is still not safe if called concurrently:

- `forceSpill` acquires `evictionMutex`, checks `emittedKeys.rlock()`, reserves — safe.
- `unspillAndMarkEmitted` calls `emittedKeys.wlock()->insert()` WITHOUT `evictionMutex` — an
  eviction running concurrently can pick the slice between the residency check and the
  `emittedKeys.wlock()->insert()`, racing with the atomic serialization `forceSpill` now provides.

The PR-2 class-doc comment says "this layer is still single-threaded beyond the base's per-map
folly::Synchronized guarantees" — so the unsafe `unspillAndMarkEmitted` is not a runtime bug yet.
But the asymmetry means a PR-3 implementer must remember that only `forceSpill` was hardened and
`unspillAndMarkEmitted` was not, increasing the risk of an incomplete PR-3 rewrite.

**Fix for PR-2:** add a comment directly on `unspillAndMarkEmitted` stating: "PR-3 MUST take
`evictionMutex` before inserting into `emittedKeys` (see PLAN PR-3 unspillAndMarkEmitted rewrite).
The emitted-check in forceSpill/pickAndReserveColdSlice is already serialized; this function's
insert is not yet — this is safe only under the current single-threaded PR-2 contract." This makes
the PR-3 obligation explicit in code, not just in planning docs.

---

## Low

### L1 — `pickAndReserveColdSlice` checks both `spillable->isResident()` and `isSliceResident(end)` redundantly
**File:** `SpillableTimeBasedSliceStore.cpp` — `pickAndReserveColdSlice`, lines ~345–348

```cpp
if (spillable == nullptr || !spillable->isResident() || !isSliceResident(end))
{
    continue;
}
```

`isSliceResident(end)` checks `!spilledKeys.rlock()->contains(end)`. `spillable->isResident()`
checks the object's own internal flag. These should be equivalent for a correctly-tracked slice.
Checking both acquires `spilledKeys.rlock()` on every candidate, adding lock overhead.

The double-check is not wrong (it defends against any divergence between the two residency tracking
mechanisms), but it is worth a comment explaining why both are needed, or alternatively removing
one if they are guaranteed to be in sync (i.e. every `spilledKeys.insert` is paired with the
slice's `isResident` flag going false).

---

### L2 — `continue` comment in `pickAndReserveColdSlice` says "keep scanning for clarity" but the loop DOES find the right answer by accident
**File:** `SpillableTimeBasedSliceStore.cpp` — line ~332

Tied to M1. The comment "keep scanning for clarity" is factually wrong: clarity would be served
by a `break`. The comment as written will mislead a future reader into thinking this is an
intentional full-scan when it is an unintentional inefficiency. Fix with M1.

---

### L3 — `spillCount` incremented BEFORE `finalizeSpill` in both `spillOneColdSlice` and `forceSpill` — leaves a window where count > reality
**File:** `SpillableTimeBasedSliceStore.cpp` — lines ~393, ~306

```cpp
spillable->spill(*backend);
++spillCount;           // bumped here
finalizeSpill(*picked); // spilledKeys not yet updated
```

Between `++spillCount` and the `spilledKeys.wlock()->insert` inside `finalizeSpill`, a test reading
`spillCountForTest()` would see a count that is one ahead of the actual number of completed spills
(where "completed" means the slice is in `spilledKeys`). In the current deterministic single-thread
tests this window is not observable. In PR-3's concurrent stress test, a test asserting
`EXPECT_EQ(store->spillCountForTest(), N)` immediately after triggering eviction could be racy.

**Fix:** either bump `spillCount` inside `finalizeSpill` (after `spilledKeys.insert`) so the count
and `spilledKeys` are updated atomically under `evictionMutex`, or document the ordering contract
so the stress test knows not to assert the exact count mid-flight.

---

### L4 — `hardStillOverAfterAllColdSpilled` test uses `hard=0` but `getSlicesOrCreate` triggers eviction inline on a new insert
**File:** `SpillableTimeBasedSliceStoreTest.cpp` — `hardStillOverAfterAllColdSpilled`, lines ~468–487

With `hardThresholdBytes=0`, `getSlicesOrCreate` will fire the hard check after EACH true insert
(when `recordCreatedSlice` returns true). At the point of the check the slice has just been created
but not yet populated, so `residentBytes() == 0 > 0` is false — no eviction fires inline. Then
`populate` is called, then `setLastSeenWatermarkForTest`, then `spillColdSlicesUnderHardForTest`.

This ordering is correct and the test passes for the right reasons. However the test comment does
not explain why the inline hard check doesn't fire during `getSlicesOrCreate`. A future reader
maintaining the test could change the order (populate before, then getSlicesOrCreate) and produce
a confusing result. A short comment clarifying "eviction does not fire during getSlicesOrCreate
because the slices are unpopulated at that point (residentBytes==0)" would improve testability.

---

## Nits

- **hpp — declaration order for new PR-2 members:** `evictionMutex` is declared `mutable` (needed
  for `const` methods). The comment says "mutable: residentBytes()/dtor may need it." `residentBytes()`
  does NOT take `evictionMutex` (it only locks `createdSlices`); only the dtor (PR-3) will need it.
  The comment should say "mutable: dtor and stopSpiller (PR-3) will take it from a const context."

- **cpp — `pickAndReserveColdSlice` holds `evictionMutex` across the entire `createdSlices.withRLock`
  snapshot:** Lock order is `evictionMutex → createdSlices` — correct and legal. The PLAN explicitly
  mandates this snapshot-under-lock pattern. Worth a brief code comment confirming this is intentional
  (not a mistake), e.g. "evictionMutex held across the snapshot so a concurrent emitted-mark or
  reservation cannot race the predicate evaluation."

- **cpp — `forceSpill` calls `DefaultTimeBasedSliceStore::getSliceBySliceEnd(sliceEnd)` while holding
  `evictionMutex`:** This is a base-class read through the store's `slices` map. The PLAN says "base
  `slices`/`windows` are NEVER taken under evictionMutex." If `getSliceBySliceEnd` acquires the base
  `slices` lock internally, this violates the stated lock-order invariant. This needs verification in
  the base implementation. If the base `slices` lock is taken, this is a HIGH issue (lock-order
  inversion once the background thread arrives). Mark as a pre-PR-3 verification item.

- **cpp — `emittedKeys.rlock()` is held (as a temporary) across `spillInProgress.insert(end)` inside
  `pickAndReserveColdSlice` (lines ~351–355):** The temporary `rlock()` handle from
  `emittedKeys.rlock()->contains(end)` is released at the semicolon, before `spillInProgress.insert`.
  So the lock is NOT nested across the insert. This is correct. No action needed; confirming for
  clarity.

- **test — `hardEvictsOldestColdFirst` test asserts `EXPECT_LE` then `EXPECT_EQ` on the same value
  (lines ~428–429):** The `EXPECT_LE` is redundant since `EXPECT_EQ` is strictly stronger. The `LE`
  was presumably left from an earlier draft. Remove the `EXPECT_LE` to avoid the impression that
  "approximately equal" is acceptable.

- **test — `hardNoColdCandidateProceeds`: does not drive a trigger feeder to emit slices (the comment
  says "every slice is still filling — none is past the watermark"). The O-B5 spec says "no cold
  candidate" (here: watermark=0 so nothing is cold, not because they're emitted but because the
  watermark hasn't advanced). This correctly tests the case, but a second variant testing the
  emitted-blocking-cold scenario would close the gap between "no-cold-because-hot" and
  "no-cold-because-emitted" more completely.** (Low priority — the emitted case is partially covered
  by `emittedSliceNeverSpilledUnderEviction`.)

---

## Test-coverage gaps (PR-2 scope)

1. **`spillOneColdSlice` exception path** (directly tied to H1): there is no test that verifies the
   reservation is cleaned up when `spill()` throws. An ideal test would inject a failing `SpillBackend`
   and assert that `spillInProgress` is empty after the throw. Without this test H1 could regress
   silently.

2. **`forceSpill` concurrent-with-eviction via `spillInProgress` guard** (tied to H2): `doubleForceSpillCountsOnce`
   tests the already-spilled idempotent path, but no test covers the case where `forceSpill` is
   called while an eviction has reserved the same slice (the `spillInProgress.contains(sliceEnd)` branch
   in `forceSpill`). This branch exists and is correct, but is exercised only by the PR-3 stress test.
   A minimal single-thread substitute: call `pickAndReserveColdSlice()` directly (it's private, but
   `spillColdSlicesUnderHardForTest` drives it), set up the store so one cold slice is being "spilled"
   via the eviction path, and from the test call `forceSpill` in sequence to confirm it returns without
   a double-write. This requires making `spillInProgress.size()` observable (a one-line test hook) or
   relying on `spillCountForTest() == 1`.

3. **`continue` vs. `break` in the pick loop** (tied to M1): no test observes scanning behaviour past
   the watermark. Adding a test where watermark < oldest slice end (no cold candidates) and asserting
   `spillCount==0` would catch a `break`-vs-`continue` regression. `hardNoColdCandidateProceeds`
   partially covers this, but it passes regardless of whether the loop uses `continue` or `break`.

4. **`deleteStateClearsCreatedSlices` calls `residentBytes()` after `deleteState`:** The test correctly
   asserts `residentBytes() == 0` post-delete. It does not assert that `spillInProgress` is empty
   (i.e. any in-flight reservation would be gone). Not critical in PR-2, but worth adding a
   `spillCountForTest()`/`isSliceResident` check.

---

## PR-1 findings — confirmed cleared

All five items from PR1-REVIEW (M1, M2, L1, L2, L3, coverage gaps 1–3) are verifiably cleared:

- **M1:** `recordCreatedSlice` returns `bool`; `getSlicesOrCreate` gates eviction on `if (inserted)`. Direct test: `recordCreatedSliceIdempotent`. ✓
- **M2:** INVARIANT comment updated to "new or existing — also runs on a SliceCache hit". ✓
- **L1:** `<algorithm>` removed from `.cpp` includes. ✓
- **L2:** Memory-order rationale comment added to `lastSeenWatermark`. ✓
- **L3:** Header comment on `setLastSeenWatermarkForTest` names the PR-2 tests that exercise it. ✓
- **Coverage gaps 1–3:** `deleteStateClearsCreatedSlices`, `recordCreatedSliceIdempotent`, `residentBytesEmptyStoreIsZero` all present and exercising the specified paths. ✓

---

## Compliments

- **The lock discipline is excellent everywhere it lands.** `pickAndReserveColdSlice` snapshots
  `createdSlices` under a single `rlock`, releases it, then evaluates the folly-set predicates
  one-at-a-time — exactly the NH1 pattern. The `evictionMutex → (createdSlices | one folly set)`
  order is consistently respected throughout the new code.

- **The no-I/O-under-lock rule is perfectly implemented.** Every `spill()` call in both
  `spillOneColdSlice` and `forceSpill` happens after the `evictionMutex` block is exited, protected
  only by the `spillInProgress` reservation. This is the uniform discipline the PLAN requires to avoid
  the NH2 throughput cliff.

- **M1 from PR-1 is thoroughly closed.** `recordCreatedSlice`'s `bool` return from
  `insert_or_assign`'s `.second` is minimal, correct, and well-commented. The gate in
  `getSlicesOrCreate` is tight and readable.

- **`forceSpill`'s serialization upgrade is correct.** The emitted-check, in-flight-check, and reserve
  now happen as one atomic critical section under `evictionMutex`, eliminating the TOCTOU that existed
  in Increment A. The public contract (throws on emitted, no-op on unknown/already-spilled) is
  preserved exactly.

- **RAII test fixture design is clean.** `makeStoreWithThresholds` is a well-scoped overload that keeps
  existing tests untouched while enabling deterministic eviction testing. The threshold-as-unit
  (`PAGE_SIZE`) approach in `hardEvictsOldestColdFirst` makes the math transparent.

- **`hardEvictsOldestColdFirst` is a strong test.** It asserts oldest spilled first, loop stops at
  threshold, backend has EXACTLY the spilled record (`EXPECT_FALSE` for B and C), and
  `spillCountForTest() == 1`. This covers all four dimensions of the policy spec in one focused test.

- **The TDD evidence (pr2-done.md) is credible.** RED phase produced the exact expected compile errors;
  GREEN phase passed 16/16 including unchanged PR-1 behaviour; module regression 57/57. The
  scope-adherence log (no PR-3 members added, exact deviations documented) is exactly the kind of
  implementation discipline that makes stacked PRs reviewable.

---

## Re-review of c2f41c6d8c

**Commit:** `c2f41c6d8c794252034bece1e923c025b56ea3e1`
**Reviewer:** Claude (code-reviewer agent), 2026-05-23
**Scope:** verify that the five findings from the original PR-2 review (H1, H2, M1, M2, M3) are correctly and completely resolved, and that the fix introduces no new defects. LOCKED items not re-raised.

**Verdict: APPROVE**
**Remaining must-fix count: 0 Critical / 0 High / 0 Medium**

---

### H1 — spillOneColdSlice: reservation leak on spill() exception — CLEARED

The fix introduces `ReservationGuard guard{*this, *picked}` immediately after `pickAndReserveColdSlice()` returns, wraps `spillable->spill(*backend)` in a `try/catch(const std::exception&)`, emits `NES_WARNING`, and returns `false` from the catch block (O-B5 swallow). The guard destructs on the `return false` path and calls `releaseReservation`, which takes `evictionMutex` and erases the entry. On the success path, `finalizeSpill(*picked)` erases the reservation, then `guard.committed = true` is set so the destructor becomes a no-op. No double-erase is possible: `committed` is set only after `finalizeSpill` has already erased. Correctly resolved.

### H2 — forceSpill: reservation leak on spill() exception — CLEARED

`ReservationGuard guard{*this, sliceEnd}` is created after the eviction-mutex block exits (i.e. after `spillInProgress.insert(sliceEnd)`). `spillable->spill(*backend)` is called without a try/catch — the guard destructs on any exception path, calling `releaseReservation`, and the exception then propagates (preserving the throwing contract). On success, `finalizeSpill(sliceEnd)` erases the reservation, then `guard.committed = true`. Note: the `SpillStoreFailure` throw (emitted slice) happens BEFORE `spillInProgress.insert`, so the guard is not yet constructed at that point — this path is correctly unaffected. Correctly resolved.

### ReservationGuard correctness — the highest-risk part

The guard is a non-copyable, non-movable struct defined in the `.cpp` with store-ref + SliceEnd + bool. Critical correctness points:

**No double-erase:** `finalizeSpill` calls `spilledKeys.wlock()->insert(end); spillInProgress.erase(end)` under `evictionMutex`, then returns. The caller immediately sets `guard.committed = true`. Between `finalizeSpill`'s internal erase and the `committed = true` assignment there is no observable window where the guard could fire, because no exception is possible on a plain `bool` assignment and there is no code between the two statements that could throw.

**No self-deadlock in releaseReservation:** `releaseReservation` unconditionally takes `std::unique_lock lock(evictionMutex)`. The guard destructs in exactly two contexts: (a) the exception path of `spill()` — `evictionMutex` was released before `spill()` was called, so the mutex is free; (b) the expired-weak-ptr path in `spillOneColdSlice` (`return true` immediately after the null check) — `evictionMutex` was also released before that `return`. In `forceSpill`, the mutex block ends at line 345's closing brace, and the guard is constructed at line 351. All guard-destruction sites are confirmed mutex-free.

**Destructor exception safety:** `releaseReservation` takes a `std::unique_lock` (which does not throw on construction for a non-recursive `std::mutex`) and calls `spillInProgress.erase(end)` (`std::set::erase` by value is `noexcept`). The destructor is therefore effectively `noexcept` and safe to call during stack unwinding.

**Idempotency:** `spillInProgress.erase(end)` on a `std::set` is a no-op if the key is absent. If, hypothetically, `finalizeSpill` already erased the entry and `committed` was somehow not set (impossible in the current code but worth checking), a second erase would be safe. The guard is correct.

### Lock-order fix — CLEARED

`DefaultTimeBasedSliceStore::getSliceBySliceEnd` acquires the base `slices` rlock. The commit moves this call to BEFORE the `evictionMutex` block (lines 313–320), so the base lock is never held under `evictionMutex`. The `shared_ptr` returned by the lookup keeps the slice alive across the critical section. Inside the mutex, only `emittedKeys.rlock()`, `spillInProgress.contains()`, `spillable->isResident()` (flag on the slice object, no lock), and `spillInProgress.insert()` are performed — no base-map lock. The lock-order invariant (evictionMutex → (createdSlices | one folly set); base slices/windows NEVER under evictionMutex) is now respected by `forceSpill`. Correctly resolved.

A secondary question: `pickAndReserveColdSlice` calls `DefaultTimeBasedSliceStore::getSliceBySliceEnd` AFTER `evictionMutex` is released (line 451, post-mutex). This was already correct before the commit and remains correct after.

### M1 — pickAndReserveColdSlice: break only on coldness condition — CLEARED

The `continue` on the `end.getRawValue() >= wm` branch is now `break`, and the comment has been rewritten to explain that this is the ONLY break in the loop ("single failure that monotonically rules out all remaining candidates"). Every other predicate — `spillInProgress.contains`, null weak_ptr, non-spillable/non-resident, `emittedKeys.contains` — now uses `continue`, so a cold-but-skipped-for-one-reason entry does not prematurely terminate scanning of later cold candidates. Correctly resolved.

### M2 — finalizeSpill: residentBytes() scan under evictionMutex — CLEARED

`finalizeSpill` now wraps only `spilledKeys.wlock()->insert(end)` and `spillInProgress.erase(end)` inside the `evictionMutex` block. The `NES_DEBUG` call (which previously embedded `residentBytes()`) has been simplified to `NES_DEBUG("Spilled cold slice {}", end)` with no scan at all — cleaner than the original Option A from the review recommendation. The mutex scope is now minimal. Correctly resolved.

### M3 — unspillAndMarkEmitted PR-3 obligation comment — CLEARED

A block comment has been added directly to `unspillAndMarkEmitted` in the `.cpp` (lines 162–168) stating the exact obligation: "PR-3 MUST wrap the per-slice body in evictionMutex and, while spillInProgress.contains(sliceEnd), WAIT on spillerCv before inserting into emittedKeys + unspilling." The comment explicitly flags that this is safe only under the current single-threaded PR-2 contract and that no behavior change occurs in PR-2. Correctly resolved.

### Regression tests — evaluated for non-tautology

**`forceSpillReleasesReservationWhenSpillThrows`:**
- First `forceSpill` call: `EXPECT_ANY_THROW` confirms the exception propagates (H2 throwing contract).
- `spillCountForTest() == 0` confirms `++spillCount` was not reached.
- `isSliceResident(sliceEnd) == true` confirms the spill did not complete.
- `backend->failPut.store(false)`, then second `forceSpill`: `EXPECT_NO_THROW` succeeds, `spillCountForTest() == 1`, `isSliceResident == false`, `putCount == 2`.

The critical non-tautology assertion is the second `forceSpill` succeeding rather than being a no-op. If the reservation had leaked, `forceSpill` would have returned early at the `spillInProgress.contains(sliceEnd)` check — the test would then fail at `spillCountForTest() == 1` (it would remain 0) and `isSliceResident == false` (it would remain true). The test is structurally sound.

**`evictionReleasesReservationAndSwallowsWhenSpillThrows`:**
- `hard=0` guarantees `evictUntilUnderHard` enters the loop.
- `EXPECT_NO_THROW` on `spillColdSlicesUnderHardForTest()` confirms O-B5 swallow.
- `spillCountForTest() == 0` and `isSliceResident == true` confirm no partial finalization.
- `putCount > 0` proves the spill was attempted (not short-circuited before I/O).
- Subsequent `forceSpill` succeeds, proving the reservation was released. Same non-tautology argument: a leaked reservation would cause `forceSpill` to silently no-op, failing `spillCountForTest() == 1`.

Both tests are non-tautological and correctly exercise the fix.

### One nit (no must-fix)

In `forceSpill`, `++spillCount` is incremented between `spill()` and `finalizeSpill()`, leaving a narrow window where `spillCountForTest()` reads one ahead of `spilledKeys`. This was documented in the original review as L3 with the recommendation to add an ordering comment — the commit adds exactly that comment ("PR-3's stress test must assert spillCountForTest() > 0, not an exact equality"). The ordering is documented; no behavioral change is required for PR-2. Not a must-fix.

### No new defects introduced

The new `releaseReservation` helper is private, called only by `ReservationGuard::~ReservationGuard()`, with no other callers. The `ReservationGuard` struct has all special members deleted except the explicit constructor; there is no implicit default construction path. The `<exception>` include for `std::exception` in the catch clause is correctly added. `ThrowingSpillBackend` in the test file correctly derives from `SpillBackend`, overrides all three virtual methods, and is owned by the test fixture — no resource leak. The `atomic<bool>` and `atomic<size_t>` members are default-initialized at construction. No new include-order issues, no new raw owning pointers, no new global state.

---

**Summary:** All five PR-2 findings (H1, H2, M1, M2, M3) are correctly and completely resolved. The `ReservationGuard` is the concurrency-critical component and is sound: no double-erase, no self-deadlock, effectively `noexcept` destructor. The regression tests are non-tautological. The code is safe to use as the concurrency foundation for PR-3.
