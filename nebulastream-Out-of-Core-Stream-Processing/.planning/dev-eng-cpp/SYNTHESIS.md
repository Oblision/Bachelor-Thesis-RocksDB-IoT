# SYNTHESIS — Phase 1 (L1 paced terminal-flush emit, Option 1: bound the unspill)

## Scope (exactly what changes)
Make the END-OF-STREAM terminal flush unspill the retained band ONE WINDOW AT A TIME (just-in-time, immediately before
each window's slices are dereferenced for emit) instead of unspilling the WHOLE band up front. Surgical: ONLY the
terminal path changes; the steady-state emit path (`getTriggerableWindowSlices`) is untouched (small per-watermark
batches, not the OOM source). JIT untouched.

## The mechanism today (the OOM point)
`WindowBasedOperatorHandler::triggerAllWindows` (WindowBasedOperatorHandler.cpp:91-96) calls
`getAllNonTriggeredSlices()` then `triggerSlices(wholeMap)`. The spillable override
`SpillableTimeBasedSliceStore::getAllNonTriggeredSlices` (SpillableTimeBasedSliceStore.cpp:383-391) calls
`unspillAndMarkEmitted(result)` over the ENTIRE band → re-materialises every retained window into the pool before any
emit. Measured (Phase 0c): process peak 496 MB / store peak 78 MB at the full 20-window band; OOMs at 320 MB where
in-mem-retain survives.

## The change (Option B seam, surgical terminal-only)
1. **New base virtual hook** on `WindowSlicesStoreInterface` (the type the handler holds):
   `virtual void ensureWindowSlicesResident(const std::vector<std::shared_ptr<Slice>>& windowSlices) {}` — DEFAULTED
   no-op. `DefaultTimeBasedSliceStore` inherits the no-op (in-memory: nothing to unspill). Only
   `SpillableTimeBasedSliceStore` overrides it. Zero ripple to joins (they use DefaultTimeBasedSliceStore) and to the
   JIT path.
2. **Factor the per-window unspill** out of `unspillAndMarkEmitted(map)` into the override
   `SpillableTimeBasedSliceStore::ensureWindowSlicesResident(slices)` = the existing inner per-slice body (wait-out
   in-flight spill -> insert emittedKeys under evictionMutex -> erase pinnedKeys -> reserve spillInProgress -> unspill
   OUTSIDE evictionMutex under ReservationGuard -> recordUnspillSample -> release reservation). `unspillAndMarkEmitted(map)`
   becomes a thin `for (auto& [w, slices] : map) ensureWindowSlicesResident(slices);` so the STEADY-STATE path
   (`getTriggerableWindowSlices` line 368) is byte-identical.
3. **Make `getAllNonTriggeredSlices()` lazy**: remove the eager `unspillAndMarkEmitted(result)` (line 386); return the
   base map with slices STILL spilled. (Contract change — see Risks.)
4. **Per-window loop in `triggerAllWindows`**: replace `triggerSlices(wholeMap)` with
   `for (auto& [windowInfo, slices] : map) { ensureWindowSlicesResident(slices); triggerSlices({{windowInfo, slices}}, ctx); }`
   so each window is unspilled -> emitted before the next is unspilled. (Empty map = no-op, handles the multi-input
   deferred-flush case.)

## Why this bounds the unspill peak
Today: all N windows resident simultaneously from RocksDB before any emit. After: the unspill I/O rebuilds ONE window
at a time. In the real engine the async probe drains earlier windows (GC frees them after the watermark advances) while
the next is being unspilled (RocksDB I/O ~40 ms/window per 0c metrics), so the resident set stays bounded if the probe
keeps up — which at the measured rates it does. Option 1 does NOT block on the probe (that is the Option-2 escalation,
only if the 320 MB cell still OOMs).

## Files affected
- `nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp` — add defaulted virtual.
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` — declare the override (public), keep
  `unspillAndMarkEmitted` private.
- `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` — factor per-window method; make
  getAllNonTriggeredSlices lazy.
- `nes-physical-operators/src/WindowBasedOperatorHandler.cpp` — per-window loop in triggerAllWindows.
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` — new RED tests + update the 2 terminal-drain
  tests (C-T3 emitLagDifferentialIdenticalResults, C-T4 emitLagBacklogFullyFlushedOnTerminate) to ensureWindowSlicesResident.
- `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` — update the terminal drain (~line 436)
  to ensureWindowSlicesResident before reading slice state.
NO CMakeLists change (all files already registered). NO rocksdb leak (factory stays in nes-physical-operators).

## Risks (called out)
- **CONTRACT CHANGE (the load-bearing risk):** `getAllNonTriggeredSlices()` no longer guarantees resident slices.
  Production: the ONLY caller is `triggerAllWindows`, updated to ensureWindowSlicesResident per window -> safe. Tests:
  3 tests call it directly and read slice state -> MUST update them (this is correct: they test the old eager contract).
  No other production caller (verified: explorers found only triggerAllWindows + the 3 tests). Document the new
  contract on the interface decl.
- **Thread-safety:** the per-window method runs the SAME eviction-mutex discipline as today (emittedKeys insert under
  evictionMutex BEFORE deref; unspill I/O outside; ReservationGuard on throw). No new locking. The async-probe-drain
  invariant (emittedKeys gate, GC frees only after watermark) is preserved — mark-emitted still happens before the
  window's HashMap* is exposed (ensureWindowSlicesResident runs before triggerSlices for that window).
- **Ordering:** ensureWindowSlicesResident for window K must complete before triggerSlices dereferences K's HashMap*
  (SpillableAggregationSlice::getHashMapPtr has INVARIANT(resident) -> terminate if not). The per-window loop guarantees
  this. The map is sorted by windowEnd -> per-window emit order == whole-map emit order (seq numbers already baked into
  the keys by the base getAllNonTriggeredSlices). N single-entry triggerSlices calls == 1 N-entry call (no per-call
  state in the loop body).
- **Spiller still running at terminal flush** (stopSpiller is in deleteState, later): preserved — emittedKeys claim
  under evictionMutex still fences the spiller per slice; the spillerCv.wait handles a mid-spill collision. Unchanged.

## Locked decisions (reviewers: do NOT flag)
HOST-ONLY, no JIT/sliding/varsized. Option 1 ONLY (do NOT add probe-wait/backpressure — accepted that the async
in-flight set is not bounded this increment). Preserve async-probe-drain + eviction-mutex discipline + late-write pin.
emit_lag=0 byte-identical. Terminal path still flushes the ENTIRE backlog.

## Success criteria
- Unit (the mechanism): getAllNonTriggeredSlices returns still-spilled slices; ensureWindowSlicesResident unspills ONLY
  the passed window; full flush (ensure all) is correct + complete; mark-emitted preserved (not re-spillable).
- Build green; regression >= 831 (existing terminal-drain tests updated to the new contract still pass).
- Manual e2e (orchestrator, post-merge): `run-rss.sh F20_on320 320 on 16000 4 8 2000000 1 0 0` rc 137 -> 0.

## Open questions
None blocking. (Handler-level integration test for triggerAllWindows is nice-to-have but the store-level mechanism
tests + the e2e 0c harness cover it; the handler change is a thin reviewable loop.)
