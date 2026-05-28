# CORE — Explorer 1 (Core, Runtime & Data Structures) — Increment C: Emit-Decouple

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. READ-ONLY audit; every claim is `file:line`
with a quote. All paths absolute from
`nebulastream-Out-of-Core-Stream-Processing/`.

**One-line task:** decouple EMIT from QUIESCENCE via an event-time `emit_lag L`: drive emit with
`watermark − L`, but feed the spillable store the TRUE `watermark` so its existing cold-pick targets
the now-retained band `[watermark−L, watermark)` and the spiller finally has cold slices to act on.
`emit_lag` default 0 = today's exact behavior.

---

## 0. The data flow in one picture (so the seams below make sense)

Build path (per output buffer, JIT-driven):
`WindowBuildPhysicalOperator::close` (`nes-physical-operators/src/WindowBuildPhysicalOperator.cpp:71-84`)
→ `invoke(checkWindowsTriggerProxy, …, executionCtx.watermarkTs, …)` →
`checkWindowsTriggerProxy` (`:37-52`) `dynamic_cast<WindowBasedOperatorHandler*>` →
`opHandler->checkAndTriggerWindows(bufferMetaData, pipelineCtx)` (`:51`).

Handler (`nes-physical-operators/src/WindowBasedOperatorHandler.cpp:73-89`):
`newGlobalWatermark = watermarkProcessorBuild->updateWatermark(...)` (`:76-77`) →
`getTriggerableWindowSlices(newGlobalWatermark)` (`:87`) → `triggerSlices(...)` (`:88`).

Spillable store override (`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:264-280`):
calls base trigger with the SAME watermark, marks the returned slices emitted, AND advances
`lastSeenWatermark` to that same watermark. **This is the coupling Increment C must break.**

Probe-side GC is a SEPARATE path on a SEPARATE watermark processor:
`WindowProbePhysicalOperator::close` (`nes-physical-operators/src/WindowProbePhysicalOperator.cpp:84-99`)
→ `garbageCollectSlicesProxy` (`:37-51`) → `opHandler->garbageCollectSlicesAndWindows(bufferMetaData)` →
handler (`WindowBasedOperatorHandler.cpp:59-71`) drives `watermarkProcessorProbe` (`:61-62`).

---

## 1. The E0 root-cause site — `SpillableTimeBasedSliceStore::getTriggerableWindowSlices`

`nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp:264-280` (verbatim):

```cpp
std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>>
SpillableTimeBasedSliceStore::getTriggerableWindowSlices(const Timestamp globalWatermark)
{
    auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(globalWatermark);   // 267
    unspillAndMarkEmitted(result);                                                            // 268
    /// O-B-watermark: learn the build watermark monotonically (CAS-max). Slices ending before it are cold...
    auto current = lastSeenWatermark.load(std::memory_order_relaxed);                         // 271
    const auto incoming = globalWatermark.getRawValue();                                      // 272
    while (incoming > current && !lastSeenWatermark.compare_exchange_weak(current, incoming, std::memory_order_relaxed))
    {                                                                                         // 273-275
    }
    spillerCv.notify_one();                                                                   // 278
    return result;                                                                            // 279
}
```

**How it emits AND advances coldness in one call:**
- Line 267 asks the base for every window with `windowEnd < globalWatermark` (base loop breaks at
  `windowInfo.windowEnd >= globalWatermark`, `DefaultTimeBasedSliceStore.cpp:119`) and the base sets
  each such window's state to `EMITTED_TO_PROBE` (`DefaultTimeBasedSliceStore.cpp:130`).
- Line 268 `unspillAndMarkEmitted(result)` inserts every returned slice's `SliceEnd` into
  `emittedKeys` (`SpillableTimeBasedSliceStore.cpp:228`).
- Lines 271-275 then CAS-advance `lastSeenWatermark` up to **the same** `globalWatermark`.

**Why cold ≡ emitted ⇒ empty spillable set.** The spiller's cold predicate is
`pickAndReserveColdSlice` (`SpillableTimeBasedSliceStore.cpp:498`): `if (end.getRawValue() >= wm) break;`
where `wm = lastSeenWatermark` (`:478`), AND it rejects emitted slices at `:527-531`
(`if (emittedKeys.rlock()->contains(end)) continue;`). A slice is "cold" precisely when
`sliceEnd < lastSeenWatermark`. But the only writer of `lastSeenWatermark` is this same call (lines
271-275), and at the instant it makes `sliceEnd < lastSeenWatermark` true (cold), line 268 has already
put that `sliceEnd` into `emittedKeys`. So `{cold ∧ ¬emitted}` is empty in steady state — the spiller
scans, finds every cold slice already emitted, and `evictUntilUnderHard` logs
"no COLD slice to evict; proceeding over-budget" (`:607-608`). This is exactly E0
(`E0-SMOKE-RESULTS.md:103-127`) and `EXPLORE-WINDOW-LIFECYCLE.md:160-192`.

**The fix lever is right here.** Lines 271-275 already maintain a watermark variable distinct from the
*emit* decision in line 267. Today both use the same `globalWatermark`. Increment C makes line 267 use
`globalWatermark − L` while lines 271-275 keep the TRUE `globalWatermark`. Then the cold-pick band
becomes `[globalWatermark−L, globalWatermark)`: those windows are NOT yet emitted (base wasn't asked
to trigger them — `windowEnd >= globalWatermark−L`) but ARE cold (`sliceEnd < lastSeenWatermark =
globalWatermark`). Non-empty ⇒ the spiller acts.

---

## 2. The base class — `DefaultTimeBasedSliceStore`

Header `nes-physical-operators/include/SliceStore/DefaultTimeBasedSliceStore.hpp`. **It is NOT `final`
(`:55-58` comment confirms), and `windows`/`slices` are PRIVATE (`:88-89`)** — a subclass cannot
enumerate window state (this is *why* the spillable store self-tracks in `createdSlices`).

### 2a. `WindowInfoState` enum (`Slice.hpp:40-45`)
```cpp
enum class WindowInfoState : uint8_t
{
    WINDOW_FILLING,
    WAITING_ON_TERMINATION,
    EMITTED_TO_PROBE
};
```
State is per-WINDOW, on `SlicesAndState` (`DefaultTimeBasedSliceStore.hpp:44-53`), stored in private
`folly::Synchronized<std::map<WindowInfo, SlicesAndState>> windows` (`:88`). `WAITING_ON_TERMINATION`
is reached ONLY on multi-pipeline-join partial termination (`DefaultTimeBasedSliceStore.cpp:179-189`,
gated `numberOfActiveInputPipelines > 0`); for single-input tumbling aggregation it is NEVER entered
(`EXPLORE-WINDOW-LIFECYCLE.md:99-127`). Not a usable dwell state — irrelevant to Increment C.

### 2b. `getTriggerableWindowSlices` — the emit gate (signature + watermark flow)
`DefaultTimeBasedSliceStore.cpp:104-139`. Signature:
`std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>> getTriggerableWindowSlices(Timestamp globalWatermark)`.
The watermark enters ONLY at the emit gate (`:119-123`):
```cpp
for (auto& [windowInfo, windowSlicesAndState] : *windowsWriteLocked) {
    if (windowInfo.windowEnd >= globalWatermark) { break; }                  // 119  EMIT GATE
    if (windowSlicesAndState.windowState == WindowInfoState::EMITTED_TO_PROBE) { continue; }  // 124
    windowSlicesAndState.windowState = WindowInfoState::EMITTED_TO_PROBE;    // 130
    const auto newSequenceNumber = SequenceNumber(sequenceNumber++);        // 132
    for (auto& slice : windowSlicesAndState.windowSlices)
        windowsToSlices[{windowInfo, newSequenceNumber}].emplace_back(slice);  // 135
}
```
**Critical for L=0 safety:** the gate is `windowEnd >= globalWatermark` (strict `<` to be triggerable).
Sequence numbers are minted only for windows that actually pass the gate (`:132`), in window-end order
(map is sorted). A smaller watermark (`globalWatermark − L`) simply triggers FEWER windows this pass;
the windows it skips keep `WINDOW_FILLING` and are picked up by a later pass once the lagged frontier
reaches them — same windows, same values, same sequence ordering, just later. **This is the linchpin
that makes the lag values-identical: the base is purely a function of the watermark argument and is
otherwise untouched.**

### 2c. `getAllNonTriggeredSlices` — terminate/EOS path (LOCKED #8)
`DefaultTimeBasedSliceStore.cpp:150-210`. Signature: `…getAllNonTriggeredSlices()` (NO watermark
argument). On the last input-pipeline termination (`numberOfActiveInputPipelines == 0` after the
decrement at `:158`) it emits EVERY `WINDOW_FILLING` window unconditionally via
`addAllSlicesToReturnMap` (`:190`), regardless of any watermark. **This is the natural full-backlog
flush** the design requires: because it ignores the watermark entirely, it already drains the entire
retained band the lag created — no `> globalWatermark−L` filter exists here to leak deferred windows.
The spillable override `getAllNonTriggeredSlices` (`SpillableTimeBasedSliceStore.cpp:282-290`) wraps
it with `unspillAndMarkEmitted` and *deliberately does NOT bump `lastSeenWatermark`* (`:286-288`). So
LOCKED #8 is **satisfied with no code change** IF the lag is applied only inside the trigger override
(seam (a)) and NOT inside `getAllNonTriggeredSlices` — see §4 risk. (If the lag were applied in the
handler's `checkAndTriggerWindows` only, the terminate path goes through `triggerAllWindows` →
`getAllNonTriggeredSlices` which never sees the lag, so it is also safe there — but verify the handler
seam doesn't accidentally also filter the terminate path.)

### 2d. The late-tuple refusal (~`:94-96`)
`getSlicesOrCreate` (`:58-102`), on a genuinely-new slice for a window already triggered:
```cpp
INVARIANT(
    it->second.windowState != WindowInfoState::EMITTED_TO_PROBE,
    "We should not add slices to a window that has already been triggered.");   // 94-96
```
PRECONDITION/INVARIANT terminate the process (`nes_cpp_idioms`). Note the early-return path at
`:64-69` (existing `sliceEnd` in `slices` map) does NOT hit this INVARIANT — it silently returns the
existing (possibly-emitted) slice. This asymmetry matters for OPEN #3 (§7).

### 2e. `garbageCollectSlicesAndWindows` — the probe-side GC (OPEN #2 anchor)
`DefaultTimeBasedSliceStore.cpp:212-272`. Signature: `…garbageCollectSlicesAndWindows(Timestamp newGlobalWaterMark)`.
Window erase condition (`:227`):
`windowInfo.windowEnd < newGlobalWaterMark and windowSlicesAndState.windowState == WindowInfoState::EMITTED_TO_PROBE`.
Slice erase condition (`:252`): `sliceEnd + sliceAssigner.getWindowSize() < newGlobalWaterMark`.
**The window-erase is gated on `EMITTED_TO_PROBE`** — a deferred (still-`WINDOW_FILLING`) window in the
retained band CANNOT be GC'd by the window loop even if `newGlobalWaterMark` overtakes it. But the
slice loop (`:252`) is NOT state-gated; see §5.

---

## 3. The handler — `WindowBasedOperatorHandler::checkAndTriggerWindows`

`nes-physical-operators/src/WindowBasedOperatorHandler.cpp:73-89` (verbatim core):
```cpp
void WindowBasedOperatorHandler::checkAndTriggerWindows(const BufferMetaData& bufferMetaData, PipelineExecutionContext* pipelineCtx)
{
    const auto newGlobalWatermark
        = watermarkProcessorBuild->updateWatermark(bufferMetaData.watermarkTs, bufferMetaData.seqNumber, bufferMetaData.originId);  // 76-77
    const auto slicesAndWindowInfo = sliceAndWindowStore->getTriggerableWindowSlices(newGlobalWatermark);  // 87
    triggerSlices(slicesAndWindowInfo, pipelineCtx);                                                        // 88
}
```

- **Where the global watermark comes from:** `watermarkProcessorBuild->updateWatermark(...)`, a
  `MultiOriginWatermarkProcessor` over `inputOrigins` (`WindowBasedOperatorHandler.cpp:37`). Input is
  `bufferMetaData.watermarkTs` from the JIT build close (`WindowBuildPhysicalOperator.cpp:79`,
  `executionCtx.watermarkTs`).
- **Is it event-time?** Yes for an event-time window: the build operator derives `watermarkTs` from the
  record's event-time field (the `EventTimeFunction` lowered at
  `LowerToPhysicalWindowedAggregation.cpp:104-109`), so the watermark is in the same event-time unit
  (ms) as the window definition. This is exactly what makes LOCKED #5 (`emit_lag` an event-time
  DURATION in the same unit) directly subtractable: `newGlobalWatermark − L` is well-defined.
- **Does the handler also drive joins (shared-code blast radius)?** YES. `checkAndTriggerWindows` is a
  `virtual` method ON `WindowBasedOperatorHandler` (`WindowBasedOperatorHandler.hpp:77`) and is NOT
  overridden by `StreamJoinOperatorHandler` (`StreamJoinOperatorHandler.hpp:29-50` overrides only
  `triggerSlices`, `emitSlicesToProbe`) nor by `AggregationOperatorHandler`
  (`AggregationOperatorHandler.hpp` — confirmed no override). So **a change to the handler's
  `checkAndTriggerWindows` is on the join's hot path too.** Both handlers reach it via the same
  `checkWindowsTriggerProxy` `dynamic_cast<WindowBasedOperatorHandler*>`
  (`WindowBuildPhysicalOperator.cpp:49-51`). This is the decisive blast-radius fact for OPEN #1.

---

## 4. The two candidate seams for applying L (OPEN #1)

### Seam (a): inside `SpillableTimeBasedSliceStore::getTriggerableWindowSlices` (LEADING)

**What it has access to:** the incoming `globalWatermark` (the true frontier, already its argument);
its own `lastSeenWatermark` (already the quiescence/cold signal, `:271-275`); and it can hold an
`emit_lag` member passed at construction. It already calls the base trigger and already separates
emit (line 267) from cold-tracking (lines 271-275) — the two are *literally adjacent statements in
this one method.*

**What would change (minimal):**
- Add an `emitLag` ctor parameter + member (event-time `Timestamp::Underlying`, default 0).
- Line 267: `DefaultTimeBasedSliceStore::getTriggerableWindowSlices(Timestamp(globalWatermark.getRawValue() - emitLag))`
  with a saturating subtraction (clamp at 0 so an early watermark < L does not underflow `Timestamp`).
- Lines 271-275: keep advancing `lastSeenWatermark` with the TRUE `globalWatermark` (unchanged).
- `getAllNonTriggeredSlices` (`:282-290`): unchanged — it ignores the watermark, so the terminate
  flush already drains the whole retained band (LOCKED #8 satisfied for free).
- Construction: `LowerToPhysicalWindowedAggregation.cpp:236-244` passes `conf.spill.emitLag` into the
  ctor (one new argument). No handler edit at all.

**Blast radius:** localized to the ONE file Increment B already owns
(`SpillableTimeBasedSliceStore.cpp`) + one ctor arg threaded through the lowering. **Does NOT touch
non-spill aggregation** (`DefaultTimeBasedSliceStore` untouched) and **does NOT touch joins** (joins
construct their own store and never instantiate `SpillableTimeBasedSliceStore` —
`grep` confirms the only construction site is the agg lowering). `OPEN #4` resolves cleanly: NO new
`setQuiescenceWatermark` setter is needed — the true frontier is the method's existing argument and
`lastSeenWatermark` already holds the quiescence value. Avoid the redundant setter.

**Caveat (the `final` keyword):** the class is currently declared `final`
(`SpillableTimeBasedSliceStore.hpp:88`). Seam (a) does not need to remove it (we are editing the class,
not subclassing it), so this is a non-issue for (a).

### Seam (b): in `WindowBasedOperatorHandler::checkAndTriggerWindows` (ALT — rejected)

**What it has access to:** `newGlobalWatermark` (`:76-77`). It would compute
`emitWatermark = newGlobalWatermark − L`, pass `emitWatermark` to `getTriggerableWindowSlices`, and
ALSO call a new `setQuiescenceWatermark(newGlobalWatermark)` on the store so the cold-pick still uses
the true frontier (because the store would otherwise only learn the lagged value).

**What would change:** the handler signature/body (shared with joins); a new virtual
`setQuiescenceWatermark` on `WindowSlicesStoreInterface` (or a `dynamic_cast` to the spillable store);
and the store's `getTriggerableWindowSlices` would have to STOP advancing `lastSeenWatermark` from its
argument (else it would learn the lagged value) — i.e. you'd still edit the store, plus the handler,
plus the interface. More moving parts, redundant state (two watermark inputs), and `OPEN #4`'s "avoid
redundant state" is violated.

**Blast radius:** HIGH. `checkAndTriggerWindows` is the join build hot path too (§3). Even with
`emit_lag=0` the handler now does an extra subtraction + an extra virtual/`dynamic_cast` per buffer for
EVERY window operator including joins. A bug here regresses joins, not just spill-agg. The DESIGN
spec's own "Mechanism" text (`DESIGN-OOC-EMIT-DECOUPLE.md:38-54`) leans toward the handler, but that
was written before this code-visible fact that the override already holds BOTH watermarks.

### Recommendation: **Seam (a).** Code evidence:
1. The override already receives the true frontier as its argument (`:265`).
2. The override already maintains the quiescence signal `lastSeenWatermark` as a *separate statement*
   (`:271-275`) from the emit call (`:267`) — applying L is a one-line change to the emit argument and
   zero change to the quiescence statement.
3. It is in the file Increment B owns; `DefaultTimeBasedSliceStore` and the join path are untouched
   (blast radius = the spill-agg store only).
4. No new interface method / no redundant watermark plumbing (resolves OPEN #4 in favor of "the
   argument already carries it").
5. `getAllNonTriggeredSlices` ignores the watermark, so the terminate flush is correct for free
   (LOCKED #8).

The only thing Seam (a) needs from outside the store is the `emit_lag` VALUE at construction
(`LowerToPhysicalWindowedAggregation.cpp:236-244`) — exactly the "no handler change beyond passing
`emit_lag` at store construction" the brief anticipated (DESIGN_BRIEF OPEN #1 leading candidate).

---

## 5. GC / probe-watermark consistency (OPEN #2)

**The probe GC watermark is INDEPENDENT of the build/emit watermark.** GC is driven by
`WindowProbePhysicalOperator::close` (`WindowProbePhysicalOperator.cpp:84-99`) →
`garbageCollectSlicesAndWindows(bufferMetaData)` → handler `WindowBasedOperatorHandler.cpp:59-71` →
`watermarkProcessorProbe->updateWatermark(...)` (`:61-62`, the *probe* processor over `outputOriginId`)
→ store `garbageCollectSlicesAndWindows(newGlobalWaterMarkProbe)`.

The probe watermark advances only as the PROBE consumes emitted buffers (the buffers
`AggregationOperatorHandler::triggerSlices` dispatches downstream,
`AggregationOperatorHandler.cpp:148`, carrying `windowInfo.windowStart` as their watermark, `:136`).
**A deferred window has not been emitted yet ⇒ no probe buffer for it ⇒ the probe watermark cannot have
advanced past it.** So the window-erase loop is safe two ways:
- It is gated on `EMITTED_TO_PROBE` (`DefaultTimeBasedSliceStore.cpp:227`); a deferred window is still
  `WINDOW_FILLING`, so the window loop skips it regardless of `newGlobalWaterMarkProbe`.
- The probe watermark structurally lags emit (emit produces the buffers that drive it).

**The real residual risk is the SLICE loop, not the window loop.** `DefaultTimeBasedSliceStore.cpp:249-258`
erases a slice when `sliceEnd + windowSize < newGlobalWaterMarkProbe` and is **NOT state-gated** (no
`EMITTED_TO_PROBE` check). For tumbling (slice ≡ window) this loop only fires once the probe watermark
is a full `windowSize` past the slice — which, since the probe watermark trails emit, trails the
deferred window's own emit by `windowSize`. So in normal operation it cannot delete a deferred slice
before emit. The path to verify (the OPEN #2 deliverable): confirm `watermarkProcessorProbe` can never
run ahead of the *lagged* emit. Since the probe processor is fed exclusively by emitted buffers
(`outputOriginId`), and emit is what the lag delays, the probe frontier is downstream of emit by
construction — **deferring emit by L pushes the probe watermark later by L too; it cannot overtake.**
The spillable override `garbageCollectSlicesAndWindows` (`SpillableTimeBasedSliceStore.cpp:340-387`)
only erases backend records for slices the base GC has *already dropped* (`:351-357` re-checks
`getSliceBySliceEnd`), so it inherits this safety and needs no change. **Net: no GC code change
required for L; add a test asserting probe-frontier ≤ lagged-emit-frontier.**

---

## 6. async-probe-drain invariant (LOCKED #7) — must NOT change

**Where the raw `HashMap*` is snapshotted into the async `EmittedAggregationWindow`:**
`AggregationOperatorHandler::triggerSlices` (`AggregationOperatorHandler.cpp:80-157`):
- `allHashMaps.emplace_back(hashMap)` snapshots the RAW pointer (`:104`).
- placement-new `EmittedAggregationWindow{windowInfo.windowInfo, std::move(finalHashMap), allHashMaps}`
  (`:144`).
- `pipelineCtx->emitBuffer(tupleBuffer)` dispatches it ASYNCHRONOUSLY to the probe (`:148`). The probe
  reads those raw pointers later and never returns to the store.

**Where emit unspills BEFORE the pointers are taken:** the store's
`getTriggerableWindowSlices` (`SpillableTimeBasedSliceStore.cpp:267-268`) calls
`unspillAndMarkEmitted(result)` (`:197-262`) — which, under `evictionMutex`, WAITs out any in-flight
spill of each `sliceEnd` (`:222-226`), inserts it into `emittedKeys` BEFORE releasing the mutex
(`:228`, so the spiller's `pickAndReserveColdSlice` can never re-pick it, `:527-531`), then unspills it
OUTSIDE the mutex if non-resident (`:233-258`). Only AFTER this returns does the handler call
`triggerSlices` (`WindowBasedOperatorHandler.cpp:87→88`) and snapshot the now-resident raw pointers.

**What MUST NOT change for Increment C:** the ordering `unspillAndMarkEmitted` (mark emitted + unspill)
→ return to handler → `triggerSlices` snapshots raw `HashMap*`. The lag changes only WHICH windows the
base returns at line 267 (those with `windowEnd < watermark−L`); every window that IS returned still
flows through `unspillAndMarkEmitted` → `emittedKeys` → resident-before-snapshot exactly as today. A
spilled slice in the retained band `[wm−L, wm)` is NEVER handed to the probe (it isn't triggered yet);
when the lagged frontier finally reaches it, it goes through `unspillAndMarkEmitted` first. So the
invariant holds by construction — **do not move the lag subtraction past line 268, and never spill an
`emittedKeys` slice.**

---

## 7. Late/straddling records with the widened retained band (OPEN #3)

The retained band `[wm−L, wm)` is now a population of windows that are **done-filling-from-the-emit
perspective but still resident-or-spilled and still reachable by the build path** — because the build
path has NO watermark/lateness gate (`EXPLORE-WINDOW-LIFECYCLE.md:196-232`; build uses the record's own
event time, `AggregationBuildPhysicalOperator.cpp:92-93`). So: **can a record land in a
retained-but-already-spilled slice?**

Trace of a late/straddling write to a slice in the band:
1. JIT build → `getDataStructureRef` → on cache MISS → `defaultTimeBasedSliceStoreRefCacheMissProxy`
   (`DefaultTimeBasedSliceStoreRef.cpp:40-66`) → `sliceStore->getSlicesOrCreate(...)` (`:59`).
2. Spillable override `getSlicesOrCreate` (`SpillableTimeBasedSliceStore.cpp:157-185`) delegates to
   the base (`:162`) and records the slice (`:169`) but **does NOT unspill** — there is no unspill call
   anywhere in this method.
3. Base `getSlicesOrCreate` (`DefaultTimeBasedSliceStore.cpp:64-69`): if the `sliceEnd` still exists in
   the `slices` map (cold-but-not-yet-GC'd), it returns the EXISTING (possibly spilled) `shared_ptr`
   via the early return — it does NOT hit the `:94-96` INVARIANT (that only fires on the create branch).
4. The extractor then calls `getHashMapPtrOrCreate(workerThreadId)`
   (`LowerToPhysicalWindowedAggregation.cpp:261`), whose body is
   `INVARIANT(resident, "getHashMapPtrOrCreate called on a spilled slice; unspill() first")`
   (`SpillableAggregationSlice.cpp:60`) → **`std::terminate` if the slice is spilled.**

**So YES, the path is reachable IN PRINCIPLE and it's a CRASH (terminate), not a UAF** — a late record
whose event-time falls in a spilled slice's `[start,end)` range, arriving while that slice is still in
the `slices` map (not yet probe-GC'd). With L=0 this is unreachable (a slice is spilled only after
emit, and after emit the late-write story is the pre-existing engine behavior — out of OOC's making).
With L>0 the band of spilled-but-still-in-`slices` slices is exactly what we deliberately created, so
the question is whether the workload actually delivers in-range records there.

**Mitigating semantics (why it may be unreachable in practice):** for the thesis workload, the
watermark advancing to `wm` means the source asserted no more records below `wm` — a record in
`[wm−L, wm)` would be late relative to the TRUE frontier. NES has no allowed-lateness knob
(`EXPLORE-WINDOW-LIFECYCLE.md:226-232`, grep = 0) and the SliceCache HIT path is JIT/range-keyed with
no residency check (`EXPLORE-SLICECACHE.md:228-233` — that fix is OUT of scope). The SLICE-CACHE
verdict (`EXPLORE-SLICECACHE.md:189-210`, RANK #1 option (iii)) is that spilling QUIESCENT (past-TRUE-
watermark) windows is safe because the engine's own watermark contract makes such records "late for a
finished window," which it refuses/terminates anyway.

**Recommendation:** the host-side **unspill-on-write guard belongs in
`SpillableTimeBasedSliceStore::getSlicesOrCreate`** (`:157-185`), inserted right after the base call
returns its (possibly-spilled) slice and BEFORE the worker writes — i.e. if the returned slice is a
`SpillableAggregationSlice` and `!isResident()`, unspill it (reusing the exact reservation/`evictionMutex`
discipline of `unspillAndMarkEmitted` `:233-258`, which the worker thread can run safely on its own
path per `EXPLORE-SLICECACHE.md:170-180`). This is host C++, no JIT (the miss-proxy → `getSlicesOrCreate`
path is plain C++, `EXPLORE-SLICECACHE.md:95-104`). **But add it ONLY IF a runtime experiment shows the
path is reachable** (DESIGN_BRIEF OPEN #3: "implement the guard only if it is"). The cheapest
characterization: drive late/straddling records into the band and watch for the
`getHashMapPtrOrCreate` `INVARIANT(resident,…)` terminate. If it never fires, document the residual and
ship without the guard; if it fires, the guard is the fix. NOTE the stale-HIT-after-spill case
(`EXPLORE-SLICECACHE.md:228-249`) is NOT closeable host-side (it's a JIT residency check) — if the
experiment shows reachable in-range HITs, that bounds how aggressively L can be shrunk and is a
declared limitation, not an in-scope fix.

---

## 8. Threading / lifetime — confirm unchanged, note lag interactions

All of Increment B's discipline is **orthogonal to L and needs no change** (seam (a) only changes
which watermark feeds the emit call; it touches none of the locks):

- **`evictionMutex` discipline** (`SpillableTimeBasedSliceStore.hpp:229-236`): guards ONLY in-memory
  decisions; ALL RocksDB I/O (`spill()`/`unspill()`) is OUTSIDE it
  (`SpillableTimeBasedSliceStore.cpp` `:250`, `:326`, `:460`, `:583`). The lag adds no I/O and no new
  shared state under the mutex.
- **`spillInProgress` reservation + `ReservationGuard` RAII** (`:63-94`, `:249`, `:325`, `:458`,
  `:565`): release-on-throw is preserved on every path. L introduces no new reserve/finalize site.
- **Background `std::jthread spiller`** (`:739-764`, declared LAST `hpp:255`): lazy-start in
  `setBufferProvider` via CAS `spillerStarted` (`:154`, `:741-742`), `stopSpiller` FIRST in both dtor
  (`:128`) and `deleteState` (`:394`). Unchanged.
- **`lastSeenWatermark` is `memory_order_relaxed` and a lagging value only under-selects cold slices
  (safe)** (`hpp:223-227`). Increment C keeps feeding it the TRUE frontier — which makes it advance
  *further/faster* than emit, so the cold band GROWS (the intended effect). It never advances ahead of
  the true frontier, so the safety argument ("never spills something the engine still considers hot")
  is PRESERVED: a slice with `sliceEnd ∈ [wm−L, wm)` is genuinely done-filling per the TRUE watermark.
- **One genuine new interaction to be aware of (not a change, a consequence):** with L>0 the spiller
  will now actually find cold candidates and spill them, so `residentBytes()`
  (`:640-671`, takes `evictionMutex`, skips in-flight) and `spill()`/`unspill()` will run on the real
  query path for the first time. The concurrency invariants were validated under exactly this load by
  the SPILL_LEAD stress test (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp:379-404`), which
  ARTIFICIALLY produces the same cold-but-unemitted band that L will produce in production via
  `setLastSeenWatermarkForTest`. **Increment C effectively turns that test's manufactured decoupling
  into the real pipeline behavior** — so the existing concurrency test is the strongest evidence the
  threading survives the lag. (Differential test for OPEN #5 should reuse this harness shape.)

---

## Key risks for emit-decouple

1. **R1 (LOW, well-understood) — terminate-path leak of deferred windows.** If the lag is applied at
   seam (a), `getAllNonTriggeredSlices` ignores the watermark and flushes the whole band → safe by
   construction. **The risk is only if a future refactor moves the lag into a shared path that the
   terminate route also traverses.** Guard with a test: `emit_lag` large, then EOS, then assert every
   deferred window emitted (LOCKED #8). Quote anchor: `DefaultTimeBasedSliceStore.cpp:179-190`,
   `SpillableTimeBasedSliceStore.cpp:282-290`.

2. **R2 (MEDIUM) — late/straddling write into a retained-but-SPILLED slice ⇒ `std::terminate`.**
   `getSlicesOrCreate` does NOT unspill (`SpillableTimeBasedSliceStore.cpp:157-185`); a late in-range
   record on a cache MISS reaches `getHashMapPtrOrCreate`'s `INVARIANT(resident,…)`
   (`SpillableAggregationSlice.cpp:60`) and crashes. The widened band makes this *possible*; whether
   it's *reachable* depends on the workload's lateness vs the TRUE watermark. **Action: run the OPEN #3
   reachability experiment; add the host-side unspill-on-write guard in `getSlicesOrCreate` only if it
   fires.** The stale-cache-HIT variant (`EXPLORE-SLICECACHE.md:228-249`) is NOT host-fixable (JIT) and
   bounds the minimum-safe lag if reachable — declare as limitation.

3. **R3 (LOW) — probe-side slice GC racing deferred emit.** The window-erase loop is `EMITTED_TO_PROBE`-
   gated (`DefaultTimeBasedSliceStore.cpp:227`) so deferred windows are safe; the slice-erase loop
   (`:252`) is NOT state-gated but trails emit by `windowSize` because the probe watermark is fed only
   by emitted buffers. **Verify probe-frontier ≤ lagged-emit-frontier with a test;** no code change
   expected. Anchor: probe processor `WindowBasedOperatorHandler.cpp:61-62`, fed by
   `AggregationOperatorHandler.cpp:132-148`.

4. **R4 (LOW) — `Timestamp` underflow when `globalWatermark < L`** (early in the stream). The emit
   subtraction at seam (a) must saturate at 0 (clamp), or an early small watermark wraps and triggers
   spuriously. Pure arithmetic guard in the one new line.

5. **R5 (LOW, the regression net) — L=0 must be byte-identical to today.** With L=0 the emit argument
   equals `globalWatermark`, line 267 is unchanged, lines 271-275 unchanged ⇒ provably today's path.
   The differential test (OPEN #5) `emit_lag=0` vs `>0` ⇒ identical result rows is the headline gate.
   Risk is only if the subtraction/clamp is written so L=0 still perturbs sequence numbering — it
   won't, because base sequence numbers are minted per-triggered-window in order
   (`DefaultTimeBasedSliceStore.cpp:132`) and L=0 triggers exactly the same set.

6. **R6 (MEDIUM, blast radius) — choosing seam (b) would put the lag on the JOIN hot path.**
   `checkAndTriggerWindows` is shared (`WindowBasedOperatorHandler.hpp:77`, not overridden by either
   subclass; `WindowBuildPhysicalOperator.cpp:49-51`). Seam (a) avoids this entirely. **Recommendation:
   reject seam (b); pick seam (a).** This is the single most consequential design choice and the code
   evidence (override already holds both watermarks as adjacent statements) is decisive.

7. **R7 (LOW) — first real exercise of the spill/unspill concurrency paths.** L>0 makes the cold band
   non-empty for the first time on a real query, so `spill()`/`unspill()`/`residentBytes()` run under
   real load. Mitigated by the existing SPILL_LEAD stress test
   (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp:379-404`) which already manufactures this exact
   regime; reuse its shape for the Increment C concurrency/differential gate. No new threading code.

### Bottom-line recommendation
Apply L at **seam (a)** — one clamped subtraction on the emit argument at
`SpillableTimeBasedSliceStore.cpp:267`, keep the true-frontier CAS at `:271-275`, thread `emit_lag`
from `SpillConfig` (`SpillConfig.hpp`) → `QueryExecutionConfiguration` (`spill` nested,
`QueryExecutionConfiguration.hpp:79`) → store ctor (`LowerToPhysicalWindowedAggregation.cpp:236-244`).
No new `setQuiescenceWatermark` setter (OPEN #4: the argument already carries the frontier). No GC code
change (OPEN #2: probe watermark structurally trails lagged emit). Add the OPEN #3 unspill-on-write
guard in `getSlicesOrCreate` only if the reachability experiment trips the residency INVARIANT. All of
Increment B's eviction-mutex / reservation / jthread discipline is untouched.
