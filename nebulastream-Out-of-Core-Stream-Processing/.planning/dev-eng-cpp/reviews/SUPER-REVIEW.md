# SUPER-REVIEW — Phase 1 (L1): Paced Terminal Flush, One Window at a Time

**Role:** Super-reviewer (cross-cutting consolidation), READ-ONLY.
**Diff:** `.planning/dev-eng-cpp/reviews/PHASE1.diff` (verified identical to working tree).
**Strictness:** strict (must-fix bar = Critical + High + Medium).
**Build/test state (per brief, accepted):** build clean; targeted 34/34 incl. concurrency race test; full regression 835 pass / 0 fail (baseline 831 + 4 new); REG_EXIT=0.
**Date:** 2026-05-23

---

## Final Verdict: **SHIP AS-IS**

No Critical, High, or Medium findings under `strict`. The change is surgical, the contract-change blast radius is provably contained to a single updated production caller, the async-probe-drain and eviction-mutex invariants are preserved verbatim by the extraction, ABI/override integrity is sound, and the test coverage exceeds the per-PR review's expectation (the addressed M1 fix actually exercises the spilled→paced-unspill terminal path end to end). One documentation-accuracy correction to a prior reviewer claim and a few optional LOW/NIT polish items are noted below — none gate the ship.

This verdict supersedes the stale `SUPER-REVIEW.md` previously in this directory (dated 00:17, which reviewed Phase 4b Increment A PRs 1-3, not Phase 1 L1).

---

## Top Priority Fixes (severity-ordered)

**Under `strict` the must-fix bar is Critical + High + Medium. There are none. The ship is unconditional.**

Optional polish (LOW / NIT — author's discretion, not ship-gating):

| # | Sev | File:line | Item | Suggested fix |
|---|-----|-----------|------|---------------|
| S1 | LOW | `tests/SpillableTimeBasedSliceStoreTest.cpp:817,834` (C-T3) | C-T3 `collect` **does** dereference the HashMap (`sortedEntries` → `getHashMapPtr`, line 208), contrary to the PR-1 review's claim that it "reads only `getSliceEnd()`". It is safe only because C-T3 never spills (1MB/2MB thresholds, no `forceSpill`) so every slice is always resident. Same latent shape as the (now-fixed) M1. | Optional: add `store->ensureWindowSlicesResident(slices)` inside `collect`, OR a one-line comment that C-T3 relies on "no spill ⇒ always resident". Documentation/robustness only; no current failure. |
| S2 | LOW | `src/SliceStore/SpillableTimeBasedSliceStore.cpp:251` | `NES_DEBUG` once per window on terminal flush (+ per-slice "Unspilling slice {}" DEBUG). 20-window band ⇒ ~20–40 DEBUG lines. | Lower the window-level summary to `NES_TRACE` if DEBUG is reserved for rarer events. (PR-1 L1.) |
| S3 | NIT | `src/SliceStore/SpillableTimeBasedSliceStore.cpp:228-239` | `unspillAndMarkEmitted` is now a thin delegating loop; the authoritative protocol comment lives on `ensureWindowSlicesResident`. | Comment already states the delegation; fine as-is. |
| S4 | NIT | `include/SliceStore/WindowSlicesStoreInterface.hpp:96` | `(void)windowSlices;` suppression vs C++23 `[[maybe_unused]]`. | `(void)` matches existing codebase convention — leave. |

**Note on S1:** This is the single substantive cross-cutting correction. It does not change the verdict (the test passes correctly and the contract it exercises is never violated at runtime), but the PR-1 review's *rationale* for C-T3 safety is wrong and should not be relied on by a future maintainer who adds a `forceSpill` to C-T3. Recorded here so the project record is accurate.

---

## Aggregate Stats

- **Files changed:** 6 (2 headers, 2 src, 2 tests). Verified via `git diff --stat HEAD` — no scope creep (no CMake, no `nes-query-compiler`, no other handler/store/slice).
- **Net:** +240 / −60.
- **Production callers of the contract-changed method `getAllNonTriggeredSlices()`:** 1 (`WindowBasedOperatorHandler::triggerAllWindows`) — updated.
- **Overrides of the new virtual `ensureWindowSlicesResident`:** 1 (`SpillableTimeBasedSliceStore`); base default = no-op; `DefaultTimeBasedSliceStore` inherits no-op.
- **Store subclasses (tree-wide):** exactly 2 (`DefaultTimeBasedSliceStore` non-final, `SpillableTimeBasedSliceStore final`). No others, no mocks/fakes.
- **New tests:** 4 (P1-T1…P1-T4) + 2 updated terminal-drain tests (C-T4, concurrency) + C-T3 unchanged-but-safe.
- **Regression delta:** 835 = 831 + 4. No pre-existing test removed.
- **Findings by severity:** Critical 0 · High 0 · Medium 0 · Low 2 · Nit 2.

---

## Dimension 1 — Contract-change blast radius (PRIMARY)

**VERIFIED CONTAINED.** Tree-wide grep (`getAllNonTriggeredSlices`, excluding `.planning/`) yields exactly these production sites:

- **Definitions:** interface pure-virtual (`WindowSlicesStoreInterface.hpp:89`), base impl (`DefaultTimeBasedSliceStore.cpp:150`), spillable override (`SpillableTimeBasedSliceStore.cpp:393`).
- **Sole production caller:** `WindowBasedOperatorHandler::triggerAllWindows` (`WindowBasedOperatorHandler.cpp:98`) — updated to per-window `ensureWindowSlicesResident` → `triggerSlices(single-entry map)`.
- **Internal:** the spillable override calls the base (`.cpp:395`); `unspillAndMarkEmitted` delegates to `ensureWindowSlicesResident`. No other consumer.

No join handler, query-compiler, runtime, or query-engine path reads `getAllNonTriggeredSlices` results. Confirmed there is **no other consumer that dereferences slice HashMap\*s assuming residency.**

**Join-handler isolation confirmed (the key safety argument):**
- `LowerToPhysicalHashJoin.cpp:290` → `make_unique<DefaultTimeBasedSliceStore>` (no-op hook).
- `LowerToPhysicalNLJoin.cpp:116` → `make_unique<DefaultTimeBasedSliceStore>` (no-op hook).
- `LowerToPhysicalWindowedAggregation.cpp:239` → `SpillableTimeBasedSliceStore` only when spill enabled, else `DefaultTimeBasedSliceStore` (:260).
- `triggerAllWindows` is `virtual` but **NOT overridden** by `AggregationOperatorHandler` or `StreamJoinOperatorHandler` (verified — only the base definition exists). So both join handlers' terminal flush calls the base `triggerAllWindows`, which calls the no-op `ensureWindowSlicesResident` → behaviour byte-unchanged for joins.

**Intra-store safety of the lazy contract:** the base `getAllNonTriggeredSlices` (`DefaultTimeBasedSliceStore.cpp:150-209`) only collects `shared_ptr<Slice>` and flips window state; it **never dereferences a HashMap\***, so returning still-spilled slices cannot trip the resident INVARIANT inside the store itself. The first (and only) deref happens later, inside `triggerSlices`, after `ensureWindowSlicesResident` has run for that window.

---

## Dimension 2 — Async-probe-drain invariant integrity (cross-PR)

**PRESERVED.** Per-window ordering in `triggerAllWindows`:
```
ensureWindowSlicesResident(slices)   // emittedKeys.insert(sliceEnd) UNDER evictionMutex, before return (.cpp:270)
triggerSlices(oneWindow, pipelineCtx) // first HashMap* deref happens here, AFTER the above returns
```
- `emittedKeys` insert (`.cpp:270`) happens under `evictionMutex` **before** the function returns, and the loop calls `triggerSlices` only after `ensureWindowSlicesResident` completes for that window. There is no inter-window parallelism in the loop. Mark-emitted therefore provably precedes any HashMap\* exposure to the JIT probe.
- Emitted slices remain un-respillable: `pickAndReserveColdSlice` skips `emittedKeys` (`.cpp:667`) and `forceSpill` throws `SpillStoreFailure` on an emitted slice (`.cpp:569-572`). P1-T4 asserts exactly this.
- GC still frees only after the output watermark advances: `garbageCollectSlicesAndWindows` is unchanged by this diff (the diff touches only the interface, the spillable store unspill/lazy-return, and the handler loop). The `windowSize` slack in the base GC is untouched.
- Nothing in the refactor reordered mark-emitted relative to deref — the extracted body is the original inner block verbatim, and the steady-state path (`getTriggerableWindowSlices` → `unspillAndMarkEmitted` → `ensureWindowSlicesResident`) still inserts `emittedKeys` before returning to its caller.

---

## Dimension 3 — Eviction-mutex discipline (cross-cutting)

**PRESERVED IN ORDER.** The extracted `ensureWindowSlicesResident` (`.cpp:241-310`) reproduces the original inner-per-slice body without reordering:

| Invariant | Site in new method | Status |
|---|---|---|
| `spillerCv.wait` while `spillInProgress.contains(sliceEnd)` | :264-268 | PRESERVED |
| `emittedKeys.insert` UNDER `evictionMutex` BEFORE any deref | :270 | PRESERVED |
| `pinnedKeys.erase` under `evictionMutex` | :273 | PRESERVED |
| `spillInProgress.insert` (footprint-reader fence) under lock before I/O | :281 | PRESERVED |
| RocksDB `unspill()` OUTSIDE `evictionMutex` | :296 | PRESERVED |
| `ReservationGuard` releases reservation on `unspill()` throw | :294 + dtor | PRESERVED |
| `recordUnspillSample` OUTSIDE `evictionMutex` (own latency lock — no nesting) | :299 | PRESERVED |
| `spilledKeys.wlock()->erase` then reservation erase under lock + `notify_all` | :300-306 | PRESERVED |
| `committed = true` after success so guard does not double-erase | :307 | PRESERVED |

`unspillAndMarkEmitted(map)` (`.cpp:228-239`) now simply loops `ensureWindowSlicesResident(slices)` per window, so the **steady-state path drives the identical concurrency-sensitive code path** as the terminal path — one maintained body, not two. `getTriggerableWindowSlices` (`.cpp:366-391`) is otherwise unchanged (still calls `unspillAndMarkEmitted(result)` at :378, then the watermark CAS-max and spiller notify). Metrics latency locks remain un-nested under `evictionMutex`. No new lock nesting introduced.

---

## Dimension 4 — Override / ABI integrity

**SOUND.**
- New virtual on the interface is **defaulted no-op** (`WindowSlicesStoreInterface.hpp:96`), inline body — single static binary, no cross-DSO ABI risk.
- Exactly **one override**: `SpillableTimeBasedSliceStore::ensureWindowSlicesResident(...) override` (`.hpp:131`) — `override` keyword present, signature matches the base verbatim (`const std::vector<std::shared_ptr<Slice>>&`). No signature drift.
- `DefaultTimeBasedSliceStore` does **not** declare it → inherits the no-op (correct for in-memory).
- `SpillableTimeBasedSliceStore` is `final` (`.hpp:88`); no further subclass exists (verified tree-wide). `DefaultTimeBasedSliceStore` is non-final (intentional, base for the spillable store).
- `getAllNonTriggeredSlices` override keyword present on both store decls. No `override` missing anywhere in the diff.

---

## Dimension 5 — Test coverage

**ADEQUATE — exceeds the per-PR review's expectation; do not gold-plate.**

The 4 new tests cover the mechanism:
- **P1-T1** `terminalFlushReturnsSlicesStillSpilled` — lazy return (spilled stays spilled, all windows still present).
- **P1-T2** `ensureWindowSlicesResidentUnspillsOnlyThatWindow` — peak-bounding (only the handed window unspills).
- **P1-T3** `pacedTerminalFlushFullDrainCorrectAndComplete` — full paced drain correct + complete (content == pre-spill).
- **P1-T4** `ensureWindowSlicesResidentMarksEmittedAndPreventsRespill` — mark-emitted preserved (re-spill rejected).

**Coverage strengthened beyond the PR-1 review's M1 ask:** C-T4 (`emitLagBacklogFullyFlushedOnTerminate`, :846) now **force-spills the entire backlog first** (:857-863) and then ensures per window (:876), so it actively exercises the spilled→paced-unspill terminal path with every window starting on disk — this closes the PR-1 review's coverage-gap #2 as well as M1.

**Concurrency:** the UAF-detector drain (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp:440-445`) correctly ensures per window before `foldTriggered` (which derefs via `getHashMapPtr`), and asserts `spillCountForTest() > 0` deterministically — so the lazy-drain + paced-unspill + spill-vs-trigger WAIT path is exercised under `-j8` load, not just single-threaded.

**Remaining gaps (noted, NOT blocking for a minimum-publishable result):**
1. No handler-level `triggerAllWindows` integration test — the loop is 3 lines; the store mechanism is the load-bearing piece + the Phase-0c e2e harness covers it end to end. Acceptable.
2. `ensureWindowSlicesResident` on an empty / zero-slice vector (multi-input deferred-flush) — behaviour is trivially correct: the base returns an **empty map** while `numberOfActiveInputPipelines > 0` (windows held in WAITING_ON_TERMINATION, `DefaultTimeBasedSliceStore.cpp:180-184`), so the handler loop body never executes. Verified by inspection; no explicit unit test, but trivially correct.
3. Unspill-throws-on-terminal-path `ReservationGuard` release — structurally identical to the existing tested `getSliceBySliceEnd`/steady-state throw paths (PR-3 `unspillThrowsReleasesReservation`); risk low. A `P1-T5` with a throwing backend would be the right follow-up but is not required this increment.
4. emit_lag=0 byte-identical no-op terminal path — the PLAN listed `emitLagZeroTerminalFlushUnchanged` but it was not added. emit_lag=0 is covered indirectly by C-T3's baseline run (emitLag 0, no spill) and the LOCKED guarantee that the hook is a pure no-op when nothing is spilled. Minor documentation gap; not material.

---

## Dimension 6 — Regression delta

**CONFIRMED.** 835 = baseline 831 + 4 new Phase-1 tests (P1-T1…P1-T4). No pre-existing test was deleted or renamed — the diff only *updates* C-T4 and the concurrency drain (same test names, assertions strengthened) and *adds* the 4 P1 tests. `git diff --stat` shows only the 6 in-scope files; no test file removed. REG_EXIT=0 accepted.

---

## Dimension 7 — Logging contract

**CLEAN.** All sites use structured `{}` placeholders (no printf), numeric `sliceEnd`/counts only — **no PII, no secrets.**

| Site | Level | Assessment |
|---|---|---|
| `ensureWindowSlicesResident` entry (:251) | NES_DEBUG | slightly chatty per-window on terminal flush — see S2 |
| "Trigger of slice {} waiting on in-flight spill" (:266) | NES_TRACE | appropriate |
| "Unspilling slice {} on trigger read" (:287) | NES_DEBUG | appropriate |
| `getAllNonTriggeredSlices` return (:403) | NES_TRACE | appropriate |
| Handler "Triggering N windows (paced…)" (:99) | NES_TRACE | appropriate |

---

## Integration / Commit Note

- **LOCKED scope respected:** HOST-ONLY; the async in-flight emitted set is intentionally NOT bounded (Option-2/backpressure deferred); the `getAllNonTriggeredSlices` non-resident contract is intentional + documented on the interface; M2's per-window map alloc is an accepted, documented tradeoff; emit_lag=0 no-op preserved. None flagged as bugs.
- **No CMake / cross-target change** required or made (matches BUILD.md / CONTRACTS.md: method added to existing hpp/cpp, RocksDB stays PRIVATE to nes-physical-operators).
- **Ready to commit.** Suggested message (per PLAN): `feat(spill): pace terminal flush — unspill one window at a time (Phase 1 L1)`. `--push=manual` per PREFLIGHT (user reviews; no co-author trailer).
- **Post-merge manual validation (NOT a ctest gate):** `.planning/eval-phase/phase0c/run-rss.sh F20_on320 320 on 16000 4 8 2000000 1 0 0` → success = rc 137→0, process peak ~310–315 MB. This is the actual proof of the OOM fix; the unit tests prove only the store-side mechanism.
