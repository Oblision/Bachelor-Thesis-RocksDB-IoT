# Implementation Plan — Phase 1 (L1 paced terminal-flush emit, Option 1)

## Summary
One logical change across 4 source files + 2 test files. Add a defaulted base virtual `ensureWindowSlicesResident`,
factor the per-window unspill out of `unspillAndMarkEmitted`, make `getAllNonTriggeredSlices()` lazy, and convert
`triggerAllWindows` to a per-window unspill-then-emit loop. Single PR (one cohesive concurrency-sensitive change) →
cpp-reviewer + super-reviewer.

## Sections

### Section 1: nes-physical-operators/include/SliceStore/WindowSlicesStoreInterface.hpp
- Add, after `getAllNonTriggeredSlices()` decl (line 84), a DEFAULTED virtual:
  `virtual void ensureWindowSlicesResident(const std::vector<std::shared_ptr<Slice>>& windowSlices) { (void)windowSlices; }`
- Doc comment: states the new contract — getAllNonTriggeredSlices/getTriggerableWindowSlices may return slices that are
  NOT yet resident (out-of-core stores); the caller MUST call ensureWindowSlicesResident(windowSlices) for a window
  before dereferencing that window's slice HashMap*s. No-op default = in-memory stores keep all slices resident.

### Section 2: SpillableTimeBasedSliceStore.hpp + .cpp
- HPP: declare `void ensureWindowSlicesResident(const std::vector<std::shared_ptr<Slice>>& windowSlices) override;`
  (public, near getAllNonTriggeredSlices override). Keep `unspillAndMarkEmitted` private.
- CPP:
  - Extract the INNER per-slice body of `unspillAndMarkEmitted` (cpp:240-299, the `for (auto& slice : slices)` block)
    into `ensureWindowSlicesResident(const std::vector<std::shared_ptr<Slice>>& windowSlices)`. Body: copy
    `bufferProvider` once, then the existing per-slice logic verbatim (wait-out spill → emittedKeys insert under
    evictionMutex → pinnedKeys erase → spillInProgress reserve → unspill outside lock under ReservationGuard →
    recordUnspillSample → release reservation). Preserve every comment/invariant.
  - `unspillAndMarkEmitted(map)` becomes: `for (auto& [windowInfo, slices] : windowSlices) { ensureWindowSlicesResident(slices); }`
    (keeps getTriggerableWindowSlices steady path byte-identical).
  - `getAllNonTriggeredSlices()` (cpp:383-391): REMOVE `unspillAndMarkEmitted(result);` (line 386). Return base result
    unspilled. Update comment: terminal flush is now LAZY/paced — slices unspilled per-window by the handler via
    ensureWindowSlicesResident (Option 1, bounds the unspill peak); mark-emitted moves to the per-window hook.
- Logging: `NES_DEBUG("ensureWindowSlicesResident: ensuring {} slices resident for one window", windowSlices.size());`
  at the top of the new method (keep the existing per-slice "Unspilling slice {} on trigger read"). Add
  `NES_TRACE("getAllNonTriggeredSlices: returning {} windows (lazy, unspilled per-window by handler)", result.size());`.

### Section 3: src/WindowBasedOperatorHandler.cpp
- `triggerAllWindows` (cpp:91-96): per-window loop:
  ```cpp
  auto slicesAndWindowInfo = sliceAndWindowStore->getAllNonTriggeredSlices();
  NES_TRACE("Triggering {} windows (paced, one at a time) for origin: {}", slicesAndWindowInfo.size(), outputOriginId);
  for (auto& entry : slicesAndWindowInfo) {
      sliceAndWindowStore->ensureWindowSlicesResident(entry.second);
      std::map<WindowInfoAndSequenceNumber, std::vector<std::shared_ptr<Slice>>> oneWindow{{entry.first, entry.second}};
      triggerSlices(oneWindow, pipelineCtx);
  }
  ```

### Section 4: tests
- SpillableTimeBasedSliceStoreTest.cpp — ADD (RED-first):
  - `terminalFlushIsLazyNotEagerUnspill`: spill 3 windows; incrementNumberOfInputPipelines(); getAllNonTriggeredSlices();
    assert every returned slice NOT resident. (RED today: eager unspill makes them resident.)
  - `ensureWindowSlicesResidentUnspillsOnlyThatWindow`: spill A,B,C; getAllNonTriggeredSlices; ensure A; assert A
    resident, B & C still NOT resident.
  - `terminalFlushFullDrainCorrectAndComplete`: spill N windows known contents; getAllNonTriggeredSlices; ensure all;
    assert all N present, contents == pre-spill (sortedEntries), none dropped/dup.
  - `ensureWindowSlicesResidentMarksEmittedAndPreventsRespill`: after ensure, slice in emittedKeys → forceSpill throws
    SpillStoreFailure (ASSERT_EXCEPTION_ERRORCODE).
  - `emitLagZeroTerminalFlushUnchanged`: emit_lag=0, no spill → ensure-all is a harmless no-op; window/content set
    unchanged.
- UPDATE: C-T4 emitLagBacklogFullyFlushedOnTerminate (~846) + C-T3 emitLagDifferentialIdenticalResults (~801): ensure
  per window before residency/content reads.
- SpillableTimeBasedSliceStoreConcurrencyTest.cpp — UPDATE terminal drain (~436): ensure per window before reading.

## Parallel Groups
Sequential (small, interdependent): S1 → S2 → S3 → S4. One author.

## PR Boundaries
Single PR: "feat(spill): pace terminal flush — unspill one window at a time (Phase 1 L1)". Reviewers: cpp-reviewer +
super-reviewer (contract change, async-drain invariant, eviction-mutex discipline).

## Test Plan
- RED: `ctest -R 'SpillableTimeBasedSliceStore' --timeout 200` → new tests FAIL pre-impl.
- GREEN: same pass + full regression `ctest -j8 -LE 'Systest|DockerCompose' --timeout 200` >= 831.
- Build: `ninja` UNPIPED under set -e in Docker (never `ninja | tail`).
- Manual e2e (post-merge, NOT ctest): `run-rss.sh F20_on320 320 on 16000 4 8 2000000 1 0 0` rc 137→0.
