# PR2 Code Review — SpillableTimeBasedSliceStore [Phase 4b-A Increment A]

**Commit:** 28cf82e475  
**Branch:** feat/ooc-phase1-spill-skeleton  
**Depends on:** PR1 (748390c4ea, virtual seam)  
**Reviewer:** Claude Sonnet 4.6 (code-reviewer agent)  
**Date:** 2026-05-23  

---

## Verdict

**APPROVE WITH CONDITIONS** — no CRITICAL defects found. One HIGH concurrency issue (lock-order inversion in GC) must be fixed before Increment B goes multi-threaded, and is documented below as a pre-requisite. Increment A (single-threaded) is unaffected at runtime. Three MEDIUM issues should be addressed. The core design — ownership model, invariant guards, unspill-on-read, and the async-probe-drain guard — is sound.

---

## CRITICAL — None

No critical defects found. Memory ownership, the async-probe-drain invariant, and residency gating in `residentBytesOf` are all correct.

---

## HIGH

### H1 — Lock-Order Inversion in `garbageCollectSlicesAndWindows`

**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, lines 156–164

**Description:**  
`garbageCollectSlicesAndWindows` holds `spilledKeys.wlock()` (acquired at line 156) and then acquires `emittedKeys.wlock()` (line 163) while inside the outer lock:

```cpp
auto keys = spilledKeys.wlock();          // (A) outer lock
for (auto it = keys->begin(); ...) {
    ...
    emittedKeys.wlock()->erase(*it);      // (B) inner lock acquired while (A) held
    it = keys->erase(it);
}
```

In `forceSpill`, the acquisition order is the reverse — `emittedKeys.rlock()` first (line 175), then `spilledKeys.wlock()` later (line 190), though they are sequential (not nested) in that function. In `unspillAndMarkEmitted`, the pattern is also sequential (`spilledKeys.wlock()` line 109 released before `emittedKeys.wlock()` line 112).

The inversion is specifically the GC nesting. If a future Increment B thread calls `forceSpill` while a GC thread holds `spilledKeys.wlock()` and waits for `emittedKeys`, and a trigger thread simultaneously holds `emittedKeys` waiting for something `forceSpill` holds, a deadlock cycle is possible.

**Severity:** HIGH — safe in Increment A (single-threaded), but this is a latent deadlock for Increment B. Must be fixed before Increment B.

**Fix:** In the GC loop, collect the keys to erase from `emittedKeys` into a local `std::vector<SliceEnd>`, release `spilledKeys.wlock()`, then acquire `emittedKeys.wlock()` separately:

```cpp
void SpillableTimeBasedSliceStore::garbageCollectSlicesAndWindows(Timestamp wm)
{
    DefaultTimeBasedSliceStore::garbageCollectSlicesAndWindows(wm);
    std::vector<SliceEnd> droppedKeys;
    {
        auto keys = spilledKeys.wlock();
        for (auto it = keys->begin(); it != keys->end();) {
            if (!DefaultTimeBasedSliceStore::getSliceBySliceEnd(*it).has_value()) {
                backend->erase(*it);
                droppedKeys.push_back(*it);
                it = keys->erase(it);
            } else {
                ++it;
            }
        }
    } // spilledKeys released before taking emittedKeys
    if (!droppedKeys.empty()) {
        auto emitted = emittedKeys.wlock();
        for (auto k : droppedKeys) emitted->erase(k);
    }
}
```

---

## MEDIUM

### M1 — TOCTOU on `emittedKeys` Guard in `forceSpill`

**File:** `SpillableTimeBasedSliceStore.cpp`, lines 175–190

**Description:**  
The emitted check (line 175, `emittedKeys.rlock()`) and the spill action (line 190, `spillable->spill()` + `spilledKeys.wlock()->insert()`) are not atomic. A concurrent `getTriggerableWindowSlices` call could add the sliceEnd to `emittedKeys` between the check and the spill, causing a slice to be spilled while its `HashMap*` is already snapshotted for the async probe — exactly the UAF the guard is supposed to prevent.

**Severity:** MEDIUM in Increment A (documented single-threaded). Must be addressed in Increment B. Document this explicitly as a known Increment B gap in the code comment above `forceSpill`.

**Suggested comment addition:**
```cpp
/// NOTE (Increment B): the emitted-check and the spill are not atomic. In multi-threaded
/// operation, a concurrent getTriggerableWindowSlices can race between the check and the spill.
/// Increment B must hold a combined lock or use a compare-and-swap state machine on the slice.
```

### M2 — `deleteState` Not Overridden: Backend Records Leak on Explicit `deleteState` Call

**File:** `SpillableTimeBasedSliceStore.hpp` / `.cpp`

**Description:**  
`deleteState()` from the base clears the `slices` and `windows` maps but is not overridden in `SpillableTimeBasedSliceStore`. If `deleteState()` is called externally (today it is only called from `~DefaultTimeBasedSliceStore`, which is safe due to destructor ordering), the backend records for spilled slices would remain, and `spilledKeys`/`emittedKeys` would be inconsistent with the now-empty base maps.

Currently harmless because `deleteState()` has no external callers, but the lack of an override is a maintenance trap — a new caller would silently create a dangling backend state.

**Fix:** Add an override that clears `spilledKeys`, `emittedKeys`, and calls `backend->erase` for all spilled keys before delegating to the base:

```cpp
void SpillableTimeBasedSliceStore::deleteState() override
{
    { auto k = spilledKeys.wlock(); /* erase from backend */ k->clear(); }
    emittedKeys.wlock()->clear();
    DefaultTimeBasedSliceStore::deleteState();
}
```

### M3 — Misleading `residentBytesOf` Log in `forceSpill` When Already Spilled

**File:** `SpillableTimeBasedSliceStore.cpp`, line 188

**Description:**  
```cpp
NES_DEBUG("Spilling slice {} ({} bytes resident)", sliceEnd, residentBytesOf(*spillable));
spillable->spill(*backend);
```
When `forceSpill` is called on an already-spilled slice, `residentBytesOf()` returns 0 and the debug log says "0 bytes resident." `spill()` then exits early as a no-op. The log is misleading — it implies a new spill is about to happen. Adding an explicit resident check before the log makes the control flow self-documenting and avoids logging noise:

```cpp
if (!spillable->isResident()) {
    NES_DEBUG("forceSpill: slice {} already spilled, no-op", sliceEnd);
    spilledKeys.wlock()->insert(sliceEnd); // idempotent set membership
    return;
}
NES_DEBUG("Spilling slice {} ({} bytes resident)", sliceEnd, residentBytesOf(*spillable));
spillable->spill(*backend);
spilledKeys.wlock()->insert(sliceEnd);
```

---

## LOW

### L1 — `residentBytesOf` Loop: `uint64_t` Worker Index Cast to `uint32_t` WorkerThreadId

**File:** `SpillableTimeBasedSliceStore.cpp`, line 211

```cpp
for (uint64_t worker = 0; worker < hashMapSlice->getNumberOfHashMaps(); ++worker)
{
    if (const auto* map = hashMapSlice->getHashMapPtr(
            WorkerThreadId(static_cast<WorkerThreadId::Underlying>(worker)));
```

`WorkerThreadId::Underlying` is `uint32_t`. If `getNumberOfHashMaps()` ever exceeds `UINT32_MAX`, the cast silently truncates. The existing `INVARIANT` in `SpillableAggregationSlice::spill()` guards against this at spill time but not here at read time. A static assert or a runtime INVARIANT on `getNumberOfHashMaps() <= std::numeric_limits<uint32_t>::max()` at the top of the function would be defensive.

### L2 — `getSliceBySliceEnd` Does Not Mark Emitted: Potential Uncontrolled Spill-Unspill Cycle

**File:** `SpillableTimeBasedSliceStore.cpp`, lines 133–149

By design, `getSliceBySliceEnd` unspills but does not add to `emittedKeys`. This is correct for the join-probe path. However, the consequence is that a caller who repeatedly calls `getSliceBySliceEnd` on a slice, and another caller repeatedly calls `forceSpill` on the same slice, will create an unbounded serialize/deserialize cycle. The behavior is correct but the comment on `getSliceBySliceEnd` in the header should note this explicitly:

```
/// NOTE: does not mark the slice as emitted (see comment on emittedKeys). A slice
/// unspilled through this path can be re-spilled by forceSpill. This is intentional
/// for the join-probe use case, which does not hold a long-lived snapshot.
```

### L3 — Missing `[[nodiscard]]` on `getAllNonTriggeredSlices` and `getTriggerableWindowSlices` Overrides

**File:** `SpillableTimeBasedSliceStore.hpp`, lines 65–69

Both overrides return maps. The base interface declarations in `WindowSlicesStoreInterface.hpp` lack `[[nodiscard]]`, but since the overrides involve a side-effectful unspill step, discarding the return value (and thus the unspilled data) would be a silent logic error. Adding `[[nodiscard]]` to the override declarations provides a compiler nudge.

---

## NITS

- **N1** (`SpillableTimeBasedSliceStore.cpp`, line 93): The local variable is named `provider`, shadowing the member field `bufferProvider`. Rename to e.g. `localProvider` or `providerSnapshot` to avoid confusion.
- **N2** (`SpillableTimeBasedSliceStore.cpp`, line 94 inner loop): The inner loop variable `slices` shadows the `SpillableTimeBasedSliceStore::` namespace's `slices` member path. In context it only shadows the range variable, not the member (which is in the base), but renaming the loop variable to `windowSliceVec` avoids future confusion.
- **N3** (`SpillableTimeBasedSliceStore.hpp`, line 53): The class-level doc says "Not thread-safe beyond the base's per-map folly::Synchronized guarantees." The folly::Synchronized members (`spilledKeys`, `emittedKeys`, `bufferProvider`) ARE individually thread-safe, but the compound operations (check-then-spill, unspill-then-mark) are NOT. Clarify: "Individual map operations are synchronized; compound operations are not (Increment B)."

---

## Test Coverage Gaps

### Gap 1 — GC Backend-Erase Path Not Tested (Missing)
There is no test that:
1. Spills a slice
2. Advances watermark past the GC threshold to drop the slice from the base store
3. Calls `garbageCollectSlicesAndWindows`
4. Asserts `backend->sliceCount()` decremented and `isSliceResident()` is not affected (slice gone from store entirely)

This is the most important untested path given the new GC override logic.

### Gap 2 — Multi-Worker `residentBytesOf` (Missing)
`residentBytesExactness` only populates `WorkerThreadId(0)`. The per-worker sum formula `Σ_w ⌈tuples_w/entriesPerPage⌉ × pageSize` is not exercised with `NUMBER_OF_HASH_MAPS=4` workers all populated. A test with 4 workers at different fill levels would validate the summation and confirm worker-local page rounding.

### Gap 3 — `setBufferProvider` Set-Once Idempotency (Partial)
The "already stashed" debug log path in `setBufferProvider` is exercised only implicitly when `makeStore` calls it. There is no explicit test that calls `setBufferProvider` twice and verifies the second provider is ignored (and the first remains active for unspill).

### Gap 4 — `getSliceBySliceEnd` Unspill Path Not Tested
There are no tests for the `getSliceBySliceEnd` override's unspill branch. A test should spill a slice, call `getSliceBySliceEnd`, and verify residency is restored and data is intact.

### Gap 5 — `getAllNonTriggeredSlices` Unspill Path Not Tested
Similar to Gap 4 but for the termination-flush path.

### Gap 6 — Non-`SpillableAggregationSlice` in Store (Heterogeneous Slice Type)
`unspillAndMarkEmitted` silently skips non-`SpillableAggregationSlice` entries (`if (spillable == nullptr) continue`). There is no test exercising a store that contains a mix of spillable and non-spillable slice types, verifying the non-spillable slices pass through unmodified while spillable ones are unspilled.

---

## Compliments

- **Ownership design is clean.** The `backend` is owned via `unique_ptr`, `bufferProvider` is a `shared_ptr` copy from query lifetime, and the slice `shared_ptr` from the base store is re-used directly. No raw owning pointers.
- **Async-probe-drain invariant is robustly modeled.** The `emittedKeys` set as a guard for `forceSpill`, the explicit `SpillStoreFailure` throw with a clear message, and the test `emittedSliceIsNotSpillable` covering it are all correct.
- **Non-unspilling GC lookup is exactly right.** Using `DefaultTimeBasedSliceStore::getSliceBySliceEnd` (qualified, non-virtual) inside the GC loop avoids rebuilding a map that is about to be dropped — a subtle and correct choice.
- **Residency-gating before `getHashMapPtr` in `residentBytesOf`.** The early `return 0` when `!isResident()` correctly prevents the `INVARIANT(resident, ...)` trip inside `SpillableAggregationSlice::getHashMapPtr`. The gating is correct and the ordering is tight.
- **`setBufferProvider` set-once pattern with `folly::Synchronized` wlock.** Clean and safe for the single-setup-call lifecycle.
- **Static `makeRocksDBBackend` factory correctly isolates RocksDB linkage.** The factory is defined in the `nes-physical-operators` translation unit, keeping the RocksDB dependency private and preventing linkage leakage to `nes-query-compiler`.
- **`spill()` idempotency via `if (!resident) return` in `SpillableAggregationSlice`** means double-`forceSpill` is safe even though `forceSpill` itself doesn't check residency. The double-spill test confirms this.
- **The single `dynamic_cast` in `setupSliceStoreProxy` is well-placed and well-documented.** The O1 seam comment explains the sanctioned-cast rationale clearly.

---

## Summary Table

| ID  | Severity | Area                   | Status for Increment A | Must fix before Increment B |
|-----|----------|------------------------|------------------------|-----------------------------|
| H1  | HIGH     | Lock-order GC          | Safe (single-thread)   | YES                         |
| M1  | MEDIUM   | TOCTOU emitted guard   | Safe (single-thread)   | YES                         |
| M2  | MEDIUM   | deleteState not overridden | No current caller   | YES (maintenance hazard)    |
| M3  | MEDIUM   | Misleading spill log   | Cosmetic               | Recommended                 |
| L1  | LOW      | uint64→uint32 cast     | Theoretical only       | Recommended                 |
| L2  | LOW      | Spill-cycle via getSliceBySliceEnd | Cosmetic/doc | Add comment                 |
| L3  | LOW      | Missing [[nodiscard]]  | No runtime impact      | Optional                    |
