# Super-Review — Increment C: Emit-Decouple (Lagged Emit Watermark)

**Scope:** cross-cutting integration audit over PR1 (`7b8d5f0301`), PR2+PR3 (`e64e085a25`), and the
review-fix commit (`1f30a75822`), reconciled against PLAN.md / SYNTHESIS.md / DESIGN_BRIEF.md (LOCKED) and
both per-PR reviews.
**Reviewer:** super-reviewer (concurrency + lifetime + ABI + integration pass)
**Date:** 2026-05-23
**Strictness:** `strict` (must clear Critical + High + Medium; Low/Nits permitted)
**Mode:** READ-ONLY — no code modified. Build/tests run in Docker `nebulastream/nes-development:local-rocksdb`.

---

## FINAL VERDICT: **SHIP AS-IS**

No Critical, High, or Medium issue found across the integration surface. The one Medium the per-PR pass
raised (PR2 **M1** — T8 did not exercise pin teardown through the emit path) is **CLEARED** by
`1f30a75822` (verified below). All ten LOCKED decisions are honored. The async-probe-drain invariant, the
eviction lock-order discipline, the RAII reservation contract, the re-spill race closure, the L=0
byte-identity, the terminate-flush behavior, and ABI/build stability all hold under independent
re-derivation from source. Regression gate is green at 828/828 (baseline 820 + 8 new), build is a verified
no-op rebuild against the committed sources.

The residual items are all Low/Nit and explicitly accepted by the LOCKED plan (key naming `emit_lag`,
`forceSpill` not consulting `pinnedKeys`, the documented "pin stays on unspill-throw" behavior, the
helper-triplication, and the deferred-to-redo-E0 reachability question). None block ship.

---

## M1 Clearance (confirmed)

**Claim:** PR2 **M1** ("T8 does not cover pin teardown via the emit path") is fixed in `1f30a75822`.

**Verified — CLEARED.** `SpillableTimeBasedSliceStoreTest.cpp:934-942` now follows the late write through
to emission:

- The store is built with `emitLag = 3·WINDOW_SIZE = 300`.
- After the late write + pin (true wm 400 ⇒ lagged cutoff 100 ⇒ `bandEnd = 100` retained, `100 ≮ 100`), the
  test advances the frontier to `getTriggerableWindowSlices(Timestamp(500))` ⇒ lagged cutoff `500−300 = 200`,
  and `100 < 200` ⇒ `bandEnd` now emits. The arithmetic is correct.
- It asserts `triggeredContains(emitted, bandEnd)` (the slice emitted), `isSliceResident(bandEnd)` (emit
  unspilled + kept it resident for the async probe), and `ASSERT_EXCEPTION_ERRORCODE(forceSpill(bandEnd),
  SpillStoreFailure)` — i.e. after emit the slice is in `emittedKeys` and the async-probe-drain guard now
  refuses to spill it.

This exercises the `pinnedKeys.erase(sliceEnd)` site in `unspillAndMarkEmitted` (cpp:244) on the
late-write slice and proves the pin→emitted hand-off. The fix-commit comment correctly notes the pin's
*direct* removal effect is masked by `emittedKeys` (both cause the same `pickAndReserveColdSlice` skip), so
the observable chosen is the emit-regime transition itself — the right call given there is no `pinnedKeys`
test hook. **M1 is genuinely closed, not papered over.** The companion **L1** fix (in-code comment in
`forceSpill` at cpp:530-533 explaining why `pinnedKeys` is intentionally not consulted) is also present.

---

## Aggregate Stats

| Metric | Value |
|---|---|
| Commits audited | 3 (`7b8d5f0301`, `e64e085a25`, `1f30a75822`) |
| Production files touched | 4 (`SpillConfig.hpp`, `SpillableTimeBasedSliceStore.{hpp,cpp}`, `LowerToPhysicalWindowedAggregation.cpp`) |
| Test files touched | 2 (`SpillConfigTests.cpp`, `SpillableTimeBasedSliceStoreTest.cpp`) |
| New production methods | 1 (`pinAndUnspillForLateWrite`, private) |
| New ctor params | 1 (`uint64_t emitLag = 0`, trailing, defaulted) |
| New members | 1 const (`emitLag`) + 1 set (`pinnedKeys`) |
| Existing virtual signatures changed | 0 (ADD-only) |
| New CMake lines | 0 (tests extend existing targets) |
| New tests | 8 store (C-T1..C-T8) + emit_lag assertions across 6 SpillConfig cases |
| Baseline (PREFLIGHT) | 820/820 |
| Now (verified this run, Docker, `-j8`, `-LE 'Systest|DockerCompose'`) | **828/828**, 0 failed, 1 pre-existing disabled |
| Targeted gate (`-R 'Spill\|EmitLag\|emitLag'`) | 65/65 passed |
| `ninja` state | "no work to do" (binaries match committed sources) |
| Cross-cutting Critical/High/Medium found by this pass | **0** |
| Medium cleared | 1 (PR2 M1) |
| Remaining must-fix at `strict` | **none** |

---

## Cross-Cutting Dimensions

### 1. Ownership & lifetime / use-after-free (async-probe-drain invariant)

**PASS.** The seam change (cpp:339-341) applies the saturating subtract **only** to the base-call argument
`laggedEmitWatermark`. Line 342 (`unspillAndMarkEmitted(result)`) runs on the unchanged `result`, and the
true-frontier CAS at 346-350 uses `globalWatermark.getRawValue()`, not the lagged value. The subtraction
did **not** move past the `unspillAndMarkEmitted` step — every slice handed onward is unspilled + marked
emitted (resident) before the handler can snapshot its raw `HashMap*` for the async probe. A spilled slice
in the retained band `[trueFrontier − L, trueFrontier)` is by construction **not** in the lagged-emit
result, so it is never handed to the probe half-freed.

The late-write guard `pinAndUnspillForLateWrite` (cpp:280-328) never inserts into `emittedKeys` (confirmed
by inspection — `emittedKeys` does not appear anywhere in the function), so it cannot mark a still-filling
slice emitted. The slice it pins stays `WINDOW_FILLING` and is protected by `pinnedKeys` (not `emittedKeys`)
until it genuinely emits, at which point `unspillAndMarkEmitted` performs the `emittedKeys.insert` +
`pinnedKeys.erase` atomically under `evictionMutex` (cpp:241-244). No UAF window. The real
`INVARIANT(resident, ...)` terminate hazard the guard closes is confirmed at
`SpillableAggregationSlice.cpp:53,60` (`getHashMapPtr{,OrCreate}` on a spilled slice).

### 2. Thread safety / lock order

**PASS — no deadlock or inversion introduced.** Verified against the declared invariant (hpp:246-253):
`evictionMutex` guards only in-memory decisions; all RocksDB I/O is outside it; lock order is always
`evictionMutex → (createdSlices | one folly set)`; base maps are never taken under `evictionMutex`.

- `pinnedKeys` is accessed **only** under `evictionMutex` at every site: insert (cpp:294), the
  `pickAndReserveColdSlice` skip-check (cpp:626, already inside the function's outer `evictionMutex` scan
  scope cpp:565), erase-on-emit (cpp:244, inside the per-slice `evictionMutex` block), erase-on-GC
  (cpp:449-453), and clear-on-deleteState (cpp:491-493). It is a plain `std::set`, correctly piggy-backing
  the existing serializer (no new lock added) — the right minimal choice.
- **New GC pin-erase block** (cpp:447-454) takes `evictionMutex` as its **own** scope, strictly **after**
  the `spilledKeys.wlock()` (cpp:425-438) and `emittedKeys.wlock()` (cpp:443) scopes have been released.
  It does not nest folly + `evictionMutex`. Conforms to the H1 "never hold two of our locks at once" rule.
- **New deleteState pin-clear** (cpp:491-493) runs after `stopSpiller()` has quiesced + joined the spiller
  (cpp:479) and is a standalone `evictionMutex` scope, separate from the `spilledKeys`/`emittedKeys` folly
  scopes above it. No nesting.
- `pinAndUnspillForLateWrite` releases `evictionMutex` (scope ends cpp:309) **before** taking any folly
  lock (`bufferProvider.copy()` at 312, `spilledKeys.wlock()` at 320) — it correctly does **not** repeat
  the pre-existing Increment-B `unspillAndMarkEmitted` nested pattern. The `spillerCv.wait` predicate
  (cpp:300) is the standard "wait out the in-flight spill" form, identical to the audited trigger/random
  paths.

No new lock-order edge is created. The single `pinnedKeys` predicate at the `pickAndReserveColdSlice`
chokepoint sits inside an already-held `evictionMutex` scan and adds no lock.

### 3. RAII / exception safety

**PASS.** `pinAndUnspillForLateWrite` constructs `ReservationGuard unspillGuard{*this, sliceEnd}`
(cpp:318) **before** the `unspill()` I/O (cpp:319) and sets `committed = true` only after
`spilledKeys.erase` + the manual `spillInProgress.erase` succeed (cpp:326). On an `unspill()` throw, the
guard's destructor calls `releaseReservation(sliceEnd)` (under `evictionMutex`, with `spillerCv.notify_all`)
— so the footprint-reader fence is released and no condvar waiter hangs. This matches the audited pattern in
`unspillAndMarkEmitted` (cpp:265) and `getSliceBySliceEnd` (cpp:400) byte-for-byte in structure.

The pin remaining on an unspill-throw path is documented (cpp:315-317) and is genuinely benign: the slice
stays spilled (its on-disk record is intact), so the pin merely suppresses re-spill of an
already-spilled slice — zero memory cost, and a later emit/GC drops it. The exception propagates out of
`getSlicesOrCreate` as a query error, **not** a process terminate — strictly better than the
`INVARIANT(resident)` terminate it replaces. Accepted as documented (PR2 N1).

### 4. The re-spill race closure

**PASS.** `pinnedKeys.contains(end)` is checked at exactly **one** production chokepoint:
`pickAndReserveColdSlice` (cpp:626). Both spill drivers route through it:
`spillerLoop → spillOneColdSlice → pickAndReserveColdSlice` (background) and
`evictUntilUnderHard → spillOneColdSlice → pickAndReserveColdSlice` (synchronous hard-eviction on the
worker path). `forceSpill` bypasses it but is the manual/test-only Increment-A driver (documented L1).

The pin is set **before** the function can return to the worker with no window: inside
`pinAndUnspillForLateWrite`, the single `evictionMutex` scope (cpp:289-309) does, in order, (1)
`pinnedKeys.insert`, (2) wait out any in-flight spill, (3) read `isResident`, (4) reserve in
`spillInProgress` if needed — without dropping the mutex. After the scope, the slice is either already
resident (pin set) or reserved in `spillInProgress` (pin set, spiller cannot pick it). There is **no**
interval where the slice is unspilled-but-unpinned and pickable.

Critically, the **call ordering** in `getSlicesOrCreate` is correct: `pinAndUnspillForLateWrite`
(cpp:175-178) runs **before** `recordCreatedSlice` (cpp:182) and **before** `evictUntilUnderHard`
(cpp:187). The pin is therefore established before any eviction decision in the same call could re-pick the
now-resident slice. Had the order been reversed, `evictUntilUnderHard` could re-spill the just-unspilled
slice before the pin took effect. As implemented, safe.

### 5. emit_lag == 0 byte-identity (LOCKED #6)

**PASS.** Three independent gates make L=0 a strict no-op:

- `getSlicesOrCreate` late-write guard is gated `emitLag > 0 && sliceEnd < lastSeenWatermark` (cpp:175). At
  L=0 the first conjunct is false ⇒ `pinAndUnspillForLateWrite` never runs ⇒ `pinnedKeys` stays empty.
- `getTriggerableWindowSlices`: `laggedEmitWatermark = Timestamp(rawWatermark >= 0 ? rawWatermark − 0 : 0)
  = Timestamp(rawWatermark)` — the base is called with the identical watermark as pre-Increment-C
  (cpp:339-341). The saturating subtract is the arithmetic identity at L=0.
- All other new code (`pinnedKeys.erase` in emit/GC, `pinnedKeys.clear` in deleteState, the
  `pickAndReserveColdSlice` skip) operates on a provably-empty `pinnedKeys` and is inert.

The only L=0 overhead is one integer compare + subtract per trigger and one `pinnedKeys.contains` per cold
candidate against an empty set — negligible and non-behavioral. Independently corroborated by C-T1
(identity at the boundary) and C-T6 (L=0 cold-set empty, no spill).

### 6. Terminate flush (LOCKED #8)

**PASS.** `getAllNonTriggeredSlices` (cpp:357-365) delegates to the **watermark-agnostic** base
`DefaultTimeBasedSliceStore::getAllNonTriggeredSlices()` (no argument), then `unspillAndMarkEmitted`. No
lag subtraction is applied anywhere in this method (confirmed by inspection), and it deliberately does
**not** bump `lastSeenWatermark` (commented cpp:361-363). The full retained backlog drains on EOS
regardless of `emitLag`. Covered by C-T4 (`emitLag = 10000`, `getAllNonTriggeredSlices` returns every
deferred window; union of triggered+drained == all created).

### 7. ABI / override integrity

**PASS — ADD-only.** No existing virtual signature changed. The four overrides
(`getSlicesOrCreate`, `getTriggerableWindowSlices`, `getAllNonTriggeredSlices`, `getSliceBySliceEnd`) keep
their exact signatures (hpp:114-123) — only bodies changed. The new surface is: a trailing **defaulted**
ctor param `uint64_t emitLag = 0` (hpp:104), one `const uint64_t emitLag` member (hpp:231), one
`std::set<SliceEnd> pinnedKeys` member (hpp:266), and one **private** non-virtual method
`pinAndUnspillForLateWrite` (hpp:178). The default on the ctor param keeps every existing/3-arg caller
compiling unchanged (no `-Woverloaded-virtual` risk, no signature ripple to the interface/handler/base).
The sole production ctor call site (`LowerToPhysicalWindowedAggregation.cpp:236`) passes the new arg; the
two test ctor sites pass it via the builder's defaulted `emitLag` param.

### 8. CMake / build

**PASS — no new CMake line needed.** All new tests extend the existing `SpillConfigTests` and
`SpillableTimeBasedSliceStoreTest` targets; no `add_nes_*_test` line was added (confirmed: the test diffs
touch only `.cpp` bodies, and the SYNTHESIS-noted optional fresh-file path was not taken). The `emitLag`
config field rides the existing `nes-query-compiler-interface` INTERFACE library; the value reaches the
store purely through the ctor. RocksDB stays a PRIVATE dep of `nes-physical-operators` (the lowering passes
only a `uint64_t`, never links rocksdb). `ninja` reports "no work to do" against the committed sources —
the build is consistent.

### 9. Regression delta

**PASS.** 828/828 this run vs 820/820 PREFLIGHT baseline — a clean +8 (C-T1..C-T8) with **zero** drops and
the same single pre-existing disabled test
(`SpecificSequenceTest.testDelimiterThatIsMoreThanOneCharacter`). Independently re-run in Docker
`nebulastream/nes-development:local-rocksdb`, `ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`. The 8
config-side `emit_lag` assertions are folded into the existing 6 `SpillConfigTest` cases (also green).

### 10. Reachability caveat (late-write guard necessity)

**PASS — documented as a known limitation, not a silent gap.** Whether NES actually delivers late records
into the store (making the guard's terminate-hazard *reachable*) is an open empirical question explicitly
deferred to redo-E0. This is documented in three places: PLAN.md OPEN #3 (cpp visibility, "Reachability
remains the open empirical question … the pin makes the increment robust either way"), SYNTHESIS.md R2 +
Open questions, and the PR2 commit message (`e64e085a25`: "Reachability of late records into the store
remains an empirical question for redo-E0; the pin makes it robust either way"). The framing is correct:
shipping a host-side guard against a possible `std::terminate` is the conservative choice, the guard is a
strict no-op when unreached (L=0 or hot path), and PR2's band-spilling rests on the same no-late-record
assumption the guard hardens. This is an acceptable, explicitly-scheduled known-limitation framing.

---

## Integration / Build Note

The three commits compose cleanly into a single coherent change: PR1 plumbs an inert config knob (provably
L=0-identity), PR2 applies the one-line saturating-subtract seam plus the late-write guard, and the
fix-commit closes M1 + adds the L1 rationale comment — all additive, all behind `emitLag > 0`. The build is
a verified no-op rebuild (sources and binaries agree), the targeted spill/emit gate is 65/65, and the full
regression gate is 828/828 with no baseline drop. The change is correctly committed per PR boundary and
**not** pushed, per LOCKED #10. No co-author trailer present (spot-checked the three commit messages).

---

## Residual Items (all Low/Nit — permitted at `strict`, none blocking)

| ID | Severity | Location | Status |
|----|----------|----------|--------|
| PR1 L1 | Low | `SpillConfig.hpp:60` — key `emit_lag` not suffixed `_ms` | INTENTIONAL — DESIGN_BRIEF LOCKED the key as `emit_lag`. Keep. |
| PR1 L2 | Low | `SpillConfig.hpp:59` — no range validator for `lag > window` | Future improvement; no functional consequence. |
| PR2 L1 | Low | `SpillableTimeBasedSliceStore.cpp:530` — `forceSpill` skips `pinnedKeys` | DOCUMENTED in-code (fix commit); manual/test-only path, `pinnedKeys` empty unless `emitLag>0`. Hardening flag for any future live wiring. |
| PR2 L2 | Low | `...cpp:175` — redundant guard invocation on already-pinned slice | Benign; idempotent insert + no-op unspill. No fix warranted. |
| PR2 N1-N3 | Nit | comment/log wording | Cosmetic. |
| (cross) | Low | `pinAndUnspillForLateWrite` triplicates the unspill block (`unspillAndMarkEmitted` / `getSliceBySliceEnd`) | ACCEPTED by PLAN (B's regression-risk argument); flagged as future cleanup, must not block. |

---

## Bottom line

Increment C is a minimal, well-scoped, idiom-consistent change that achieves the emit-decouple with a
single clamped subtraction on the emit cutoff plus a correctly-fenced late-write pin. Every LOCKED
invariant holds, the concurrency seams are sound under independent re-derivation, ABI/build are ADD-only,
the Medium is cleared, and the regression gate is green at 828/828 against the 820 baseline.

**SHIP AS-IS.**
