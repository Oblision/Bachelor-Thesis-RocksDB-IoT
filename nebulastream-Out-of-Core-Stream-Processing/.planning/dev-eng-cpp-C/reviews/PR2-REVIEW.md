# Code Review — PR2+PR3: Lagged Emit Watermark + Late-Write Guard (Increment C)

**Commit:** e64e085a2523ac2e62cc919818e4e6ac0fe470b1
**Reviewer:** Claude Code (cpp-reviewer, super-reviewer concurrency pass)
**Date:** 2026-05-23
**Files reviewed:**
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`
- `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`

---

## Verdict: APPROVE WITH NITS

No blocking issues found after thorough concurrency analysis. One medium-severity gap in test coverage (T8 does not exercise pin teardown through the emit path), one low-severity observation about `forceSpill` not checking `pinnedKeys`, and a set of nits. All LOCKED constraints are respected. The core safety argument holds.

---

## Findings by Severity

### Critical
_None._

### High
_None._

### Medium

#### M1 — T8 does not cover pin teardown via the emit path
**File:** `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`, lines 893-933

T8 verifies that a pinned slice stays resident under re-eviction, which is the central pin correctness check. However, it does not follow through to emission: it never calls `getTriggerableWindowSlices` with a watermark that crosses `bandEnd`, so the `pinnedKeys.erase(sliceEnd)` path in `unspillAndMarkEmitted` (cpp:244) is never exercised in the late-write scenario. After the late write and pin, the slice should be emittable and the pin should drop cleanly. If `pinnedKeys.erase` were accidentally missing or conditional, T8 would pass silently while the pin leaked forever — causing unbounded `pinnedKeys` growth and incorrect spill suppression for slices that should be spill-eligible again after emit.

**Suggested fix:** extend T8 with a final `getTriggerableWindowSlices(Timestamp(enough))` that includes `bandEnd` in its emit window, then assert `isSliceResident(bandEnd)` (it is emitted, so guaranteed resident) and — via the `spillColdSlicesUnderHardForTest` hook — that the slice is no longer skipped for the reason "pinned" (i.e. that a subsequent attempt to identify it as a spill candidate would instead find it in `emittedKeys`). Since `pinnedKeys` has no test hook, the observable is: after emit, force-spilling the slice must throw `SpillStoreFailure` (emitted — already the emitted-slice protection). This indirectly proves the pin was dropped because `emittedKeys` is now the guard.

---

### Low

#### L1 — `forceSpill` does not check `pinnedKeys`; commit comment acknowledges this as intended but the rationale boundary needs a comment
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, line 519-541

`pickAndReserveColdSlice` skips `pinnedKeys` entries (line 622). `forceSpill` does NOT check `pinnedKeys` (confirmed: there is no `pinnedKeys.contains` in `forceSpill`'s evictionMutex block). The commit message explicitly acknowledges this: "forceSpill is the Increment-A manual driver, not on the production hot path, and pinnedKeys is only populated when emitLag>0". This is correct — `forceSpill` is test/manual-drive only, and a caller invoking it on a pinned slice would get a successful spill that dangerously invalidates the cached raw pointer. However, there is no in-code comment on the `forceSpill` evictionMutex block that says "pinnedKeys intentionally not checked — test-only caller, emitLag>0 discipline is caller's responsibility."

If `forceSpill` were ever wired into a production path in a future increment, this omission becomes a UAF. An `INVARIANT(!pinnedKeys.contains(sliceEnd), "forceSpill on a pinned slice is unsafe")` inside the mutex block would be a cheap guard.

**Priority:** Low for this increment (the manual-only characterization is accurate). Flag as a hardening item for any future increment that considers wiring `forceSpill` on a live path.

#### L2 — Late-write guard fires on every `getSlicesOrCreate` call that hits an existing cold slice, including the already-pinned case; unnecessary condition check on subsequent hits
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, lines 175-178

The condition `emitLag > 0 && sliceEnd < lastSeenWatermark` correctly identifies a cold late-write. On a second `getSlicesOrCreate` call for the same already-pinned slice (e.g. two late records arrive back-to-back), `pinAndUnspillForLateWrite` will be called again. Inside, `pinnedKeys.insert` is idempotent (plain `std::set::insert`) and `needsUnspill` will be false (slice is already resident), so the function correctly no-ops after locking and checking. No correctness problem. The cost is one `evictionMutex` acquisition per redundant late-record call. This is acceptable at current scale but worth noting.

**No fix required.** Could add `if (!pinnedKeys.contains(sliceEnd))` before `spillInProgress` check inside `pinAndUnspillForLateWrite` as a fast-exit, but that requires holding `evictionMutex` first anyway, so the gain is just avoiding the `spillInProgress.contains` check. Not worth the change.

---

### Nits

#### N1 — `pinAndUnspillForLateWrite` comment says "the pin stays (harmless — the slice remains spilled)" on the `unspill()` throw path; this is not entirely harmless
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, line 316-317

The comment states "the pin stays (harmless — the slice remains spilled, and a later emit/GC will retry/clean it)." The pin staying on a spilled slice prevents the background spiller from re-selecting it. Since the slice is already spilled, this has no memory-pressure effect. However, it also means the slice will never be re-spilled via `pickAndReserveColdSlice`, so `residentBytes()` will count it as 0 (it is non-resident and in `spilledKeys`), and the actual disk record remains until emit/GC. In practice, this is truly harmless. The comment could be more precise: "the pin stays; since the slice remains spilled, it costs nothing in memory. Emit will drop the pin via `pinnedKeys.erase` in `unspillAndMarkEmitted`; GC will drop it via `garbageCollectSlicesAndWindows`."

#### N2 — The `NES_TRACE` log line in `getTriggerableWindowSlices` formats `laggedEmitWatermark.getRawValue()` not `laggedEmitWatermark`; may miss custom `Timestamp` formatter if one exists
**File:** `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`, line 340

Minor style inconsistency: `NES_TRACE("...lagged emit wm={}", ..., laggedEmitWatermark.getRawValue())` extracts the raw value explicitly, whereas the comparable log line in the CAS loop reads `rawWatermark` (also a raw value). No behavior difference. If `Timestamp` has a registered `fmt` formatter, using the `Timestamp` object directly would be more consistent with how the rest of the codebase logs watermarks.

#### N3 — `T8` comment says "window end 100 is cold (100 < 400) but NOT emitted (100 is not < 100)"; the parenthetical is slightly confusing
**File:** `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`, line 911

The comment "(100 is not < 100)" refers to the lagged emit watermark `laggedEmitWatermark = 400 - 300 = 100`, so `windowEnd < 100` is false for `windowEnd == 100`. The comment reads ambiguously as if comparing `sliceEnd` to itself. Suggest: "(the lagged emit cutoff is 100; windowEnd 100 does not satisfy windowEnd < 100, so it is not emitted)."

---

## Focused Concurrency Scrutiny (Mandated by PLAN.md PR3 requirements)

### 1. The re-spill race the pin is meant to close

**Claim verified — CORRECT.**

(a) **Single chokepoint check:** `pinnedKeys.contains(end)` appears at exactly one place in the production path: `pickAndReserveColdSlice` (cpp:622). Both the background `spillerLoop` (which calls `spillOneColdSlice` → `pickAndReserveColdSlice`) and the synchronous hard-eviction path (`evictUntilUnderHard` → `spillOneColdSlice` → `pickAndReserveColdSlice`) go through this single function. There is no other `spillInProgress.insert` that doesn't also go through `pickAndReserveColdSlice` on the production path; `forceSpill` bypasses it (Low finding L1 above, acknowledged as intentional).

(b) **Pin before unspill, no window:** In `pinAndUnspillForLateWrite`, the sequence under `evictionMutex` is: (1) `pinnedKeys.insert` (pin), (2) wait for `spillInProgress` to drain, (3) read `isResident`, (4) optionally `spillInProgress.insert` (reserve). The mutex is not dropped between steps (1)-(4). After the mutex scope closes, the slice is either already resident (no unspill needed, pin is set) or reserved in `spillInProgress` (pin is set, spiller cannot pick it). There is no window where an unspilled-but-not-yet-pinned slice could be re-picked by the spiller. The pin is set atomically before the residence check. **The re-spill race is closed.**

(c) **`forceSpill` hole:** `forceSpill` does not check `pinnedKeys` and is not on the production code path (Increment A manual driver only). With `emitLag > 0`, a test caller invoking `forceSpill` on a pinned slice would succeed and invalidate the cached HashMap pointer. This is a latent hazard, classified as Low (L1) above. For the current increment, it does not affect production correctness.

### 2. Lock order in `pinAndUnspillForLateWrite`

**Verified — CORRECT. Lock order conforms to the declared invariant.**

Full sequence:
```
[evictionMutex]
  pinnedKeys.insert(sliceEnd)                  // plain set, under evictionMutex
  spillInProgress.contains / .wait             // condvar wait releases+reacquires evictionMutex
  spillable->isResident()                      // slice's own atomic flag, no lock needed
  spillInProgress.insert(sliceEnd)             // plain set, under evictionMutex
[/evictionMutex]

[no lock]
  bufferProvider.copy()                        // folly::Synchronized — one folly lock, alone
  spillable->unspill(*backend, *provider)      // I/O OUTSIDE evictionMutex (LOCKED #7)
  spilledKeys.wlock()->erase(sliceEnd)         // ONE folly lock, alone, no evictionMutex

[evictionMutex]
  spillInProgress.erase(sliceEnd)
[/evictionMutex]
spillerCv.notify_all()
unspillGuard.committed = true
```

Key verifications:
- No base map (`slices`, `windows`) access under `evictionMutex`. Pass.
- No two folly sets locked simultaneously (e.g. `spilledKeys` AND `emittedKeys`). Pass.
- `spilledKeys.wlock()` taken alone (evictionMutex is NOT held at line 320). Pass.
- `ReservationGuard` is constructed BEFORE `unspill()` (line 318) and covers the throw path: if `unspill()` throws, the guard's destructor calls `releaseReservation(sliceEnd)`, which takes `evictionMutex`, erases from `spillInProgress`, and calls `spillerCv.notify_all()`. The pin remains (correctly) because the guard does not touch `pinnedKeys`. Pass.
- `spillerCv.wait` predicate `[&] { return !spillInProgress.contains(sliceEnd); }` is correct: it waits while the in-flight spill is active, which is the necessary and sufficient condition for proceeding safely. Pass.
- The `bufferProvider.copy()` folly lock is taken OUTSIDE `evictionMutex`. Pass.

**Pre-existing nested lock pattern in `unspillAndMarkEmitted` and `finalizeSpill`:** These functions take `evictionMutex` and then call `emittedKeys.wlock()` / `spilledKeys.wlock()` while still holding `evictionMutex` (cpp:241, 642). This pattern was established in Increment B and is NOT introduced by this commit. The commit's new code in `pinAndUnspillForLateWrite` correctly does NOT repeat this pattern — it releases `evictionMutex` before taking any folly set lock. The pre-existing pattern is a technical debt item from Increment B outside this review's scope.

### 3. `pinnedKeys` lifecycle / unbounded growth

**Verified — CORRECT. No leak path identified.**

Every pin inserted by `pinAndUnspillForLateWrite` (cpp:294) is eventually removed by exactly one of three paths:
1. `unspillAndMarkEmitted` (cpp:244): the slice emits → `pinnedKeys.erase` under `evictionMutex`, at the same time `emittedKeys` takes over protection.
2. `garbageCollectSlicesAndWindows` (cpp:452): GC drops the slice → `pinnedKeys.erase` under `evictionMutex`, taken separately after the `spilledKeys`/`emittedKeys` folly locks are released.
3. `deleteState` (cpp:492): teardown → `pinnedKeys.clear()` under `evictionMutex`, after the spiller is quiesced.

A slice that is pinned but never emits and never GCs would be a leak. Under normal operation, every window eventually either emits (via `getTriggerableWindowSlices`) or flushes via `getAllNonTriggeredSlices` (EOS path). The EOS path calls `unspillAndMarkEmitted` on all non-triggered slices, which does `pinnedKeys.erase`. So a pinned slice that takes the EOS path is also cleaned. There is no scenario where a valid slice escapes all three paths.

**GC lock order:** In `garbageCollectSlicesAndWindows`, the `pinnedKeys.erase` block (cpp:448-453) takes `evictionMutex` as a separate scope AFTER the `spilledKeys.wlock()` and `emittedKeys.wlock()` scopes have been released. This is correct — it does not nest `evictionMutex` with any folly set lock and does not violate the H1 invariant (never hold two of our locks at once). Pass.

### 4. The clamp

**Verified — CORRECT.**

```cpp
const auto laggedEmitWatermark = Timestamp(rawWatermark >= emitLag ? rawWatermark - emitLag : 0);
```

`rawWatermark` and `emitLag` are both `uint64_t`. The ternary checks `rawWatermark >= emitLag` before subtracting, so `rawWatermark - emitLag` is only evaluated when the result is non-negative, preventing unsigned wrap. When `rawWatermark < emitLag`, the lagged watermark is `Timestamp(0)`, which is the correct saturating floor. `Timestamp(0)` passed to the base `getTriggerableWindowSlices` causes no windows to emit (since no `windowEnd < 0` can hold), which is the desired behavior. Covered by C-T7. With `emitLag == 0`: `rawWatermark >= 0` is always true, so `rawWatermark - 0 = rawWatermark` — exactly the pre-Increment-C identity. Pass.

### 5. `emitLag == 0` byte-identity

**Verified — CORRECT.**

With `emitLag == 0`:
- `getSlicesOrCreate` line 175: `emitLag > 0` is false → `pinAndUnspillForLateWrite` is never called. Zero new code runs. Pass.
- `getTriggerableWindowSlices`: `laggedEmitWatermark = Timestamp(rawWatermark - 0) = Timestamp(rawWatermark) = globalWatermark`. The base is called with the same watermark as before. Pass.
- All other new code (`pinnedKeys` erase in `unspillAndMarkEmitted`, `pinnedKeys` erase in GC, `pinnedKeys.clear` in `deleteState`) is dead code at L=0 because `pinnedKeys` is always empty. These are empty-set operations and do not affect behavior. Pass.
- Only one new branch is taken on the L=0 path: the saturating subtract comparison in `getTriggerableWindowSlices`, which is a single integer compare and subtract. This is the only overhead at L=0, and it is genuinely negligible. Pass.

### 6. Quiescence on TRUE watermark

**Verified — CORRECT. This is the central fix.**

`getTriggerableWindowSlices` (cpp:341-350):
```cpp
auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(laggedEmitWatermark);  // line 341: lagged
unspillAndMarkEmitted(result);                                                               // line 342
auto current = lastSeenWatermark.load(std::memory_order_relaxed);
const auto incoming = globalWatermark.getRawValue();                                         // line 347: TRUE wm
while (incoming > current && !lastSeenWatermark.compare_exchange_weak(current, incoming, ...))
{}
```

The CAS-max uses `globalWatermark.getRawValue()` (the parameter, the TRUE watermark), NOT `laggedEmitWatermark.getRawValue()`. `lastSeenWatermark` therefore tracks the true frontier, which `pickAndReserveColdSlice` reads at line 568 to determine coldness. Cold slices are those with `sliceEnd < lastSeenWatermark` (true frontier), while they are only emitted when `sliceEnd < laggedEmitWatermark` (true frontier - L). The retained band `[trueFrontier - L, trueFrontier)` is correctly cold-but-unemitted. Pass.

Using `laggedEmitWatermark` for the CAS would have partially re-broken the fix: `lastSeenWatermark` would lag behind reality, and some genuinely cold slices would appear hot, suppressing their eviction. The comment in the code (cpp:343-345) correctly explains this. Pass.

### 7. Test adequacy

**Overall: good coverage for a unit test suite, with one gap (M1).**

- **C-T1** (identity at L=0): exercises the boundary condition `windowEnd == watermark` and the basic identity. Adequate.
- **C-T2** (deferral): exercises the core deferral behavior with monotonically advancing watermarks. Adequate.
- **C-T3** (differential): exercises the headline correctness claim that values are identical; uses `getAllNonTriggeredSlices` for EOS flush. Adequate for correctness.
- **C-T4** (terminate flush, LOCKED #8): exercises EOS drain with a huge lag that suppresses all triggers. Adequate.
- **C-T5** (E0 → fixed): directly exercises that `spillCountForTest() >= 1` after a production trigger + hard eviction with L>0. This is the primary regression for the E0 finding. Adequate.
- **C-T6** (E0 regression, L=0): reproduces the E0 empty-set scenario as a named regression. Adequate.
- **C-T7** (clamp): exercises `rawWatermark < emitLag`. Adequate.
- **C-T8** (late-write guard): verifies no-terminate, unspill-before-write, and pin-survives-eviction. **Gap:** pin teardown via emit is not tested (M1 above). The test stops short of advancing the watermark past `bandEnd` to trigger emission and confirm the pin is dropped.

**Missing T9 (concurrent differential checksum):** PLAN.md marks this OPTIONAL and states the SPILL_LEAD harness in `SpillableTimeBasedSliceStoreConcurrencyTest.cpp` already exercises the cold-but-unemitted regime via `setLastSeenWatermarkForTest`. For a thesis, T8 + the existing concurrency test suite is sufficient. Noting for completeness.

**Unexercised path:** `unspill()` throw inside `pinAndUnspillForLateWrite` — the `ReservationGuard` drop on the exception path (cpp:318). This path is hard to exercise deterministically (requires a `SpillBackend` that throws on `unspill`). The analogous path in `unspillAndMarkEmitted` and `getSliceBySliceEnd` has the same gap. Pre-existing across Increment B; not introduced here.

---

## Cross-Cutting Concerns

### No violation of the async-probe-drain absolute (R3)
`unspillAndMarkEmitted` is called at line 342, AFTER the base emit call at line 341 and BEFORE the `lastSeenWatermark` CAS. The lag subtraction is applied only to the base call argument — it does not move past line 342. Any slice in the lagged-emit result goes through `unspillAndMarkEmitted` before the handler snapshots its raw `HashMap*`. A spilled slice in the retained band is NOT in the lagged-emit result (it is retained), so it never reaches the probe half-freed. The async-probe-drain invariant is preserved. Pass.

### `getAllNonTriggeredSlices` correctly untouched (LOCKED #8)
`getAllNonTriggeredSlices` (cpp:357-365) calls the base without any watermark argument, then `unspillAndMarkEmitted`. No lag subtraction is applied. The comment at cpp:361-363 explicitly notes this is intentional. The EOS path drains the full retained backlog. Pass.

### `getSlicesOrCreate` ordering: pin before hard-eviction
`pinAndUnspillForLateWrite` (line 177) is called BEFORE `recordCreatedSlice` (line 182) and BEFORE `evictUntilUnderHard` (line 187). This is the correct ordering: the pin is established before any eviction decision could see the now-resident slice as a spill candidate. If the ordering were reversed (pin after evictUntilUnderHard), there would be a window where `evictUntilUnderHard` → `pickAndReserveColdSlice` could pick the slice (it is cold, resident, not yet pinned) and re-spill it before the pin takes effect. The ordering as implemented is safe. Pass.

### No `emittedKeys` insertion in `pinAndUnspillForLateWrite`
Confirmed: `emittedKeys` does not appear in `pinAndUnspillForLateWrite` at any point. The slice remains in `WINDOW_FILLING` state and is not protected by `emittedKeys` until it actually emits. Pass.

---

## Compliments

The core insight — saturating subtract on the emit watermark while keeping the TRUE watermark for cold-pick — is minimal and elegant. The single-line change at cpp:214 + 341 achieves the decoupling with zero handler blast radius, exactly as designed.

The `pinnedKeys` mechanism is well-scoped. Using a plain `std::set<SliceEnd>` protected by the existing `evictionMutex` (which is already taken by `pickAndReserveColdSlice` for the entire predicate scan) is the right choice: it avoids any new lock and slots cleanly into the existing eviction serialization. The three removal sites (emit, GC, deleteState) are complete and correctly sequenced relative to the folly set locks.

The `ReservationGuard` usage in `pinAndUnspillForLateWrite` is careful: the guard is constructed with the `spillInProgress` reservation already inserted, covers the `unspill()` I/O, and `committed` is set only after the manual cleanup succeeds — matching the pattern established in `unspillAndMarkEmitted` and `getSliceBySliceEnd`. The comment on the throw path (cpp:315-317) is accurate.

The "pin stays on throw" design decision (N1) is intentional and actually the right call: an I/O failure during unspill does not invalidate the spilled record (it is still on disk), so leaving the pin in place (suppressing future re-spill of an already-spilled slice) is harmless and avoids a secondary failure path.

T3 (differential correctness) is a strong test: the multiset equality of `(sliceEnd → entries)` over the full drain of both an L=0 and L>0 run directly proves that the lag changes only timing, not values. T5's use of the production trigger (not `setLastSeenWatermarkForTest`) correctly validates the E0 fix via the actual code path.

---

## Summary Table

| ID | Severity | Location | Title |
|----|----------|----------|-------|
| M1 | Medium | Test cpp:893 | T8 does not verify pin teardown through emit |
| L1 | Low | Store cpp:519 | `forceSpill` skips `pinnedKeys` check; no in-code rationale comment |
| L2 | Low | Store cpp:175 | Redundant `pinAndUnspillForLateWrite` call on already-pinned slice; benign but observable under lock contention |
| N1 | Nit | Store cpp:316 | "pin stays (harmless)" comment imprecise |
| N2 | Nit | Store cpp:340 | Log formats `.getRawValue()` instead of `Timestamp` object |
| N3 | Nit | Test cpp:911 | Misleading "(100 is not < 100)" parenthetical |

