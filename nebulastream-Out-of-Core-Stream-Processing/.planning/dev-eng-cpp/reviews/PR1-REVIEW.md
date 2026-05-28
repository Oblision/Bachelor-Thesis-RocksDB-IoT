# Code Review: Phase 1 (L1) — Paced Terminal Flush, One Window at a Time

**Diff:** `.planning/dev-eng-cpp/reviews/PHASE1.diff`
**Reviewer:** Claude (cpp-reviewer agent)
**Date:** 2026-05-23
**Build status:** GREEN — 835 pass / 0 fail (831 baseline + 4 new)

---

## Verdict: APPROVE WITH NITS

No CRITICAL or HIGH issues. Two MEDIUM findings (one a stale test comment/assertion
that does not affect correctness; one a minor per-window map construction cost). Four
LOW/NIT items. The thread-safety extraction is exact; the async-probe-drain invariant
is preserved; include hygiene is fine; const-correctness is sound.

---

## Findings

### CRITICAL — None

### HIGH — None

### MEDIUM

**M1 — C-T4 (`emitLagBacklogFullyFlushedOnTerminate`) not updated per the PLAN**

File: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`, lines 862–876

The PLAN.md (Section 4) explicitly states: "UPDATE: C-T4 emitLagBacklogFullyFlushedOnTerminate
(~846) + C-T3 emitLagDifferentialIdenticalResults (~801): ensure per window before
residency/content reads."

C-T3 (`emitLagDifferentialIdenticalResults`, line 834) calls `getAllNonTriggeredSlices()`
and passes results to `collect()`, which only reads `getSliceEnd()` — NOT a HashMap
dereference — so it is safe as written. No update needed there.

C-T4 (`emitLagBacklogFullyFlushedOnTerminate`, line 863), however, calls
`getAllNonTriggeredSlices()` and then asserts `EXPECT_TRUE(store->isSliceResident(...))` on
the returned slices (line 870), while the comment on line 861 still says "unspilled-to-
resident on the way out". Because C-T4 uses large thresholds and never calls `forceSpill`,
slices happen to be resident at the time of the drain, so the assertion passes — but for
the **wrong reason**: under the new lazy contract the assertion `isSliceResident` can only
be guaranteed true *after* `ensureWindowSlicesResident` is called, not after
`getAllNonTriggeredSlices()` alone.

This is a latent correctness issue in the test: if someone later adds a `forceSpill`
in the setup (to verify EOS flush with spilled slices), the test's existing assertion will
silently fail to exercise the correct flow. The stale comment actively misleads future
readers.

**Required fix:** add `ensureWindowSlicesResident` per window before reading residency,
and update the comment:

```cpp
store->incrementNumberOfInputPipelines();
const auto drained = store->getAllNonTriggeredSlices();
std::vector<SliceEnd> drainedEnds;
for (const auto& [windowInfo, slices] : drained)
{
    store->ensureWindowSlicesResident(slices); // Phase 1 L1: unspill before reading
    for (const auto& slice : slices)
    {
        drainedEnds.push_back(slice->getSliceEnd());
        EXPECT_TRUE(store->isSliceResident(slice->getSliceEnd()));
    }
}
```

**M2 — Per-window `std::map` construction allocates on every iteration in `triggerAllWindows`**

File: `nes-physical-operators/src/WindowBasedOperatorHandler.cpp`, line 103

```cpp
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>> oneWindow{{windowInfo, slices}};
triggerSlices(oneWindow, pipelineCtx);
```

A new `std::map` is heap-allocated, keyed, and destroyed per window. For the terminal
flush across a large retained band this is N heap allocations in a tight loop at a point
where allocator pressure may already be elevated.

This is the terminal path (not the hot steady-state), and the allocation is bounded by one
map per window rather than one per slice, so the impact is modest. However, if
`triggerSlices` is ever given an overload that takes a single `(WindowInfoAndSequenceNumber,
vector<shared_ptr<Slice>>)` pair directly, the construction can be eliminated entirely.
For now, this is acceptable; documenting it as a known micro-cost.

No immediate fix required; flag as a known follow-up if `triggerSlices` is ever refactored.

---

### LOW

**L1 — `NES_DEBUG` inside `ensureWindowSlicesResident` fires once per window on the terminal
flush; may be too chatty at `DEBUG` level for large retained bands**

File: `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, line 251

```cpp
NES_DEBUG("ensureWindowSlicesResident: ensuring {} slice(s) resident for one window", windowSlices.size());
```

A 20-window band produces 20 `DEBUG` log lines. The per-slice "Unspilling slice {} on trigger
read" at line 287 is also `DEBUG`. Depending on the NES log-level convention for hot paths,
this may be worth moving the window-level summary to `NES_TRACE`. Not a correctness issue;
leave or lower as the author sees fit.

**L2 — `getAllNonTriggeredSlices` comment in C-T3 is now slightly misleading**

File: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`, line 832–834

```cpp
/// EOS: flush whatever is still retained (L>0 holds back a tail; L=0 already emitted everything).
store->incrementNumberOfInputPipelines();
collect(store->getAllNonTriggeredSlices());
```

The comment does not mention that the caller (`collect`) only reads `getSliceEnd()`, NOT
`getHashMapPtr`. A future maintainer adding a `sortedEntries` call here (reading the
HashMap) would get a contract violation without obvious indication. Consider adding a brief
note: `// collect only reads slice metadata (getSliceEnd), not the HashMap — safe without ensure`.

**L3 — `unspillAndMarkEmitted` private comment references the original "outer-loop" shape that
no longer exists**

File: `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, lines 228–239

The batch wrapper still carries the old PR-3 trigger-WAIT comment block at the original
location. After the refactor the authoritative comment is on `ensureWindowSlicesResident`;
the `unspillAndMarkEmitted` body is now a one-liner loop. A short JSDoc-style "delegates to
ensureWindowSlicesResident, see that method's comment for the eviction-mutex protocol" would
be cleaner than the current implicit delegation.

**L4 — `oneWindow` copy of `slices` vector in `triggerAllWindows` copies `shared_ptr`s**

File: `nes-physical-operators/src/WindowBasedOperatorHandler.cpp`, line 103

The structured-binding `slices` in `for (auto& [windowInfo, slices] : slicesAndWindowInfo)`
is a reference into the map. The `oneWindow{{windowInfo, slices}}` initialiser copies the
vector. For a tumbling window the vector has exactly one element (one `shared_ptr`), so the
copy is one reference-count increment and one pointer copy — negligible. For sliding windows
the vector can be longer; if Phase 1 is ever applied to sliding windows this is worth
revisiting with `std::move` (after confirming `slicesAndWindowInfo` is not iterated again).
Not a bug; document as a note.

---

### NITS

**N1 — `(void)windowSlices;` suppression idiom in the interface default**

File: `nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp`, line 96

C++23 `[[maybe_unused]]` on the parameter is idiomatic over `(void)` suppression in C++.
Either is fine; `(void)` is the existing codebase convention, so no change needed.

**N2 — `windows` local vector in P1-T2 test copies the slice vectors unnecessarily**

File: `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`, lines 928–932

```cpp
std::vector<std::vector<std::shared_ptr<Slice>>> windows;
for (auto& [windowInfo, slices] : drained)
{
    windows.push_back(slices);   // copies three shared_ptr vectors
}
```

The copies are safe and the test is single-threaded, but `windows.push_back(slices)` could
be `windows.emplace_back(slices)` or, if a reference into `drained` is acceptable,
`windows.push_back(std::ref(slices))` (or structured bindings plus direct indexing).
No correctness impact; style nit only.

---

## Thread-Safety Audit (per the review brief)

Checked against the original `unspillAndMarkEmitted` inner body:

| Invariant | Original | Refactored `ensureWindowSlicesResident` | Status |
|---|---|---|---|
| `emittedKeys.insert` under `evictionMutex` BEFORE HashMap exposure | Yes (line 117 of diff) | Yes (line 270 of .cpp) | PRESERVED |
| RocksDB `unspill()` OUTSIDE `evictionMutex` | Yes | Yes (line 296) | PRESERVED |
| `spillInProgress.insert` under lock BEFORE I/O; `ReservationGuard` on throw | Yes | Yes (lines 280–294) | PRESERVED |
| `spillerCv.wait` while `spillInProgress.contains(sliceEnd)` | Yes | Yes (lines 264–268) | PRESERVED |
| `spillInProgress.erase` under lock + `spillerCv.notify_all` on success | Yes | Yes (lines 302–307) | PRESERVED |
| `recordUnspillSample` called OUTSIDE `evictionMutex` (latency vector has own lock) | Yes | Yes (line 299) | PRESERVED |
| `pinnedKeys.erase` under `evictionMutex` | Yes | Yes (line 273) | PRESERVED |
| No new lock nesting introduced | N/A | Verified: same lock sequence | CLEAN |

**Order of operations in `triggerAllWindows` for async-probe-drain invariant:**

```
ensureWindowSlicesResident(slices)   → inserts emittedKeys BEFORE returning
triggerSlices(oneWindow, pipelineCtx)  → dereferences HashMap*
```

`emittedKeys` is inserted under `evictionMutex` at line 270 in `ensureWindowSlicesResident`,
before the function returns. `triggerSlices` is called only after `ensureWindowSlicesResident`
completes. The per-window ordering in the handler loop guarantees this: there is no
inter-window parallelism in `triggerAllWindows`. The invariant is preserved.

---

## Async-Probe-Drain Invariant Audit

`ensureWindowSlicesResident` → `emittedKeys.insert` (under mutex, line 270 .cpp)
→ `unspill()` (outside mutex, line 296)
→ returns

`triggerSlices(oneWindow, ...)` is called after the above returns. HashMap* is first
dereferenced inside `triggerSlices`. The mark-emitted step therefore provably precedes
any HashMap* dereference. A slice that `ensureWindowSlicesResident` marks emitted cannot
be subsequently picked by `pickAndReserveColdSlice` (it checks `emittedKeys` under
`evictionMutex`). Confirmed by P1-T4 (`ensureWindowSlicesResidentMarksEmittedAndPreventsRespill`).

---

## Correctness: Per-Window Emit Equivalence

`triggerSlices` is called once per window with a single-entry map rather than once with
the whole map. The base `getAllNonTriggeredSlices` returns a `std::map<WindowInfoAndSequenceNumber,
...>` sorted by `WindowInfo::operator<` (ascending windowEnd). The per-window loop iterates
in that order, so emit sequence is identical to the pre-Phase-1 whole-map call. Sequence
numbers are baked into the keys by the base implementation before the loop begins; no
per-call mutation of sequence state exists in `triggerSlices`. Empty-map case (no untriggered
slices): the loop body never executes — correct no-op.

---

## Const-Correctness Audit

`ensureWindowSlicesResident(const std::vector<std::shared_ptr<Slice>>& windowSlices)`:

- The `const&` refers to the vector — the vector is not mutated (no push/pop).
- `std::dynamic_pointer_cast<SpillableAggregationSlice>(slice)` returns a new `shared_ptr`
  (not a reference into the const vector), so calling non-const methods on the result is
  legal — the `const` qualification on the parameter refers to the vector container, not to
  the pointed-to slice objects.
- `spillable->unspill(...)`, `spillable->isResident()` operate on the slice via the new
  `shared_ptr` — no UB, no `const_cast`.

Sound.

---

## Contract-Change Blast-Radius Audit

Production callers of `getAllNonTriggeredSlices` (grep result):
- `WindowBasedOperatorHandler::triggerAllWindows` — updated to per-window ensure-resident.
- `DefaultTimeBasedSliceStore` — the base implementation; returns in-memory slices, always
  resident. No update needed.
- No other production callers found.

Test callers:
- C-T3 (`emitLagDifferentialIdenticalResults`, line 834): reads only `getSliceEnd()` (metadata)
  via `collect` — safe without ensure because no HashMap is dereferenced. No spills in setup.
- C-T4 (`emitLagBacklogFullyFlushedOnTerminate`, line 863): reads `isSliceResident()` and
  asserts true — passes today because no spills in setup, but test comment/assertion is stale
  against the new lazy contract. **See M1.**
- New P1-T1 through P1-T4: all correctly call `ensureWindowSlicesResident` before reading
  resident slices.
- Concurrency test drain: correctly updated to per-window ensure-resident (lines 441–444).

---

## Include / ODR / CMake

`WindowBasedOperatorHandler.cpp` includes `<map>` (line 18 of the full file) and
`<SliceStore/WindowSlicesStoreInterface.hpp>` (line 24) — `std::map` and the virtual
`ensureWindowSlicesResident` declaration are both visible at the `oneWindow` construction
site. No missing includes. No CMakeLists change required (SYNTHESIS.md confirms all files
were already registered). No ODR risk: `ensureWindowSlicesResident` has a defaulted inline
body on the interface, one override in the .cpp TU — no duplicate definition.

---

## Logging Audit

| Site | Level | Content | Assessment |
|---|---|---|---|
| `ensureWindowSlicesResident` entry | `NES_DEBUG` | window slice count | Slightly chatty for N-window terminal flush; see L1 |
| "Waiting on in-flight spill" | `NES_TRACE` | sliceEnd (numeric ID) | Appropriate |
| "Unspilling slice {} on trigger read" | `NES_DEBUG` | sliceEnd (numeric ID) | Appropriate |
| `getAllNonTriggeredSlices` return | `NES_TRACE` | window count | Appropriate |
| Handler: "Triggering N windows..." | `NES_TRACE` | count + originId | Appropriate |

No PII. No secrets. All format strings use structured `{}` placeholders (no printf-style).

---

## Test Coverage

The four new tests cover the core mechanism well:

| Test | What it proves |
|---|---|
| P1-T1 `terminalFlushReturnsSlicesStillSpilled` | `getAllNonTriggeredSlices` is now lazy — spilled slices stay spilled |
| P1-T2 `ensureWindowSlicesResidentUnspillsOnlyThatWindow` | Unspill is bounded: only the passed window unspills |
| P1-T3 `pacedTerminalFlushFullDrainCorrectAndComplete` | Full paced drain is correct and complete (content preserved) |
| P1-T4 `ensureWindowSlicesResidentMarksEmittedAndPreventsRespill` | mark-emitted is preserved in the hook |

**Coverage gaps:**

1. **No handler-level test for `triggerAllWindows` per-window loop.** SYNTHESIS.md calls this
   "nice-to-have" and documents that the store-level mechanism tests + e2e harness cover it.
   Agreed — the handler loop is three lines and the store method is the load-bearing piece.
   Acceptable gap for Phase 1.

2. **C-T4 does not test the case where slices ARE spilled before the terminal drain.** This is
   the main scenario Phase 1 exists to optimise, yet no unit test drives it from the old
   C-T4 angle (the new P1-T3 covers it from a different angle). Not blocking — P1-T3 provides
   the coverage — but a future refactor of C-T4 to include spills (after fixing M1) would
   close this documentation gap.

3. **`ensureWindowSlicesResident` called on an empty vector** (zero-slice window, possible in
   multi-input deferred-flush): the loop body simply does not execute — no coverage for this
   edge case in the new tests. Behaviour is trivially correct, but an explicit test would
   future-proof against a non-trivial refactor of the preamble.

4. **Unspill-throws path in `ensureWindowSlicesResident`** (the `ReservationGuard` on the
   terminal path): PR-3 has `unspillThrowsReleasesReservation` for the steady-state
   `getSliceBySliceEnd` path. The `ensureWindowSlicesResident` equivalent (calling the method
   with a `ThrowingSpillBackend` that has `failGet=true`) is not tested. Given that the code
   path is structurally identical to the existing tested paths, the risk is low, but for
   completeness a `P1-T5_ensureWindowSlicesResidentUnspillThrowReleasesReservation` would be
   the right follow-up.

---

## Compliments

- The extraction is surgical and exact: every line of the original inner body (including the
  subtle `pinnedKeys.erase`, the `spillInProgress` footprint-reader fence, the `ReservationGuard`,
  and the `spilledKeys.wlock()` OUTSIDE the mutex) is preserved without reordering. The
  eviction-mutex discipline is not degraded.

- Making `unspillAndMarkEmitted` a thin delegation loop is the right call: it ensures the
  steady-state and terminal paths share one code path for the concurrency-sensitive logic,
  not two separately maintained copies.

- The defaulted no-op on the interface is the minimal, correct seam: in-memory stores
  (`DefaultTimeBasedSliceStore`, joins) get exactly zero overhead.

- The four new RED-first tests are well-scoped: each tests one specific aspect of the
  mechanism, with minimal setup, and each would have caught a regression independently.

- The concurrency test drain update mirrors the handler loop exactly — no drift from the
  production code path.

- Comments throughout are detailed and accurate. The PR-3 eviction-mutex invariant comment
  is preserved verbatim at the extraction site, which makes future audits straightforward.
