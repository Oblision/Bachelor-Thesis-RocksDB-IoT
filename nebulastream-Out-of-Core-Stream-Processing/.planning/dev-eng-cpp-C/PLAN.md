# Implementation Plan — Increment C: Emit-Decouple (Lagged Emit Watermark)

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Reconciled from PLAN-A + PLAN-B (which converged on
every core decision). Source-verified in SYNTHESIS.md. Build/test ONLY in Docker
`nebulastream/nes-development:local-rocksdb`, build dir `cmake-build-debug`. Baseline to beat: **820/820**.

## Summary

Break the E0 coupling `emit ≡ quiescence` so a backlog of COMPLETED tumbling windows is retained and becomes
spillable. Mechanism (seam (a), inside `SpillableTimeBasedSliceStore`): drive the base **emit** cutoff with a
**saturating `globalWatermark − emitLag`** while keeping the **quiescence/cold** signal (`lastSeenWatermark`) on
the **TRUE `globalWatermark`**. `emit_lag` is an event-time duration (ms), config-plumbed, default 0 ⇒
byte-identical to today (LOCKED #6). Plus a proactive host-side **unspill-on-write guard** closing a late-record
`std::terminate` hole that emit_lag>0 opens.

## Reconciled OPEN-item decisions (do not re-litigate)
- **OPEN #1 (seam):** (a) inside `getTriggerableWindowSlices` — the override already receives the true frontier
  AND maintains `lastSeenWatermark` adjacently; zero join blast radius (handler is join-shared).
- **OPEN #2 (GC):** no code change. Probe GC runs on a separate `watermarkProcessorProbe` fed only by emitted
  buffers and erase is `EMITTED_TO_PROBE`-gated → a deferred window can't be GC'd. Assert via T3/T4.
- **OPEN #3 (late-record guard): PROACTIVE — CORRECT (PIN) variant, user-confirmed.** Both architects chose
  proactive. During implementation a flaw was found in the *naive* unspill-only guard both proposed: after it unspills
  a still-filling band slice (it must NOT mark it emitted), the background spiller / hard-eviction can **re-spill** it
  before the worker's write, re-introducing the `std::terminate`; worse, the worker re-caches the slice's raw
  `HashMap*` in the per-worker SliceCache, and a later cache HIT is JIT-generated (uninterceptable) — so the slice must
  stay resident until it emits, not just for the one write. **Resolution (user chose "build a correct guard now"):**
  the guard unspills-on-write AND **PINS** the slice (`pinnedKeys`, checked in `pickAndReserveColdSlice`) so neither
  the spiller nor hard-eviction re-spills it until it emits — at which point `emittedKeys` takes over and the pin is
  dropped (also dropped on GC/deleteState). Gated on `emitLag > 0 && sliceEnd < trueFrontier` ⇒ strict no-op for L=0
  (byte-identity) and for the normal hot build path. This folds late-touched band slices into the existing
  "emitted slices are never spilled" safety regime. **Reachability remains the open empirical question** (does NES
  deliver late records to the store at all?) — redo-E0 answers it; the pin makes the increment robust either way.
  PR2's band-spilling itself also rests on the no-late-record assumption, so the guard hardens PR2 too.
- **OPEN #4 (setQuiescenceWatermark):** NO new setter — the argument carries the true frontier.
- **OPEN #5 (differential correctness):** unit test T3 (L=0 vs L>0 ⇒ identical aggregate rows).
- **LOCKED #8 (terminate flush):** already satisfied; `getAllNonTriggeredSlices` stays watermark-agnostic — must
  NOT add a lag cutoff there.

---

## Sections

### Section S1 — `nes-query-compiler/interface/SpillConfig.hpp`  (config knob; Agent: impl-1; PR1)
- After `compression` (~:54) add:
  ```cpp
  /// Event-time emit/retention lag L (same unit as the window/watermark, ms). Results emit on a watermark of
  /// globalWatermark - emit_lag; completed windows in [globalWatermark - L, globalWatermark) are RETAINED and
  /// become spillable. 0 = today's behavior (emit wm == global wm).
  UIntOption emitLag = {"emit_lag", "0", "Event-time emit/retention lag L (ms); retain+spill completed windows behind the watermark. 0 = no lag."};
  ```
- `getOptions()` (~:59): append `&emitLag`.
- Auto-derives CLI key `--worker.default_query_execution.spill.emit_lag` + nested YAML `spill.emit_lag` (no parser
  code; structural via `BaseConfiguration`). No logging (config struct).
- **Tests:** S6.

### Section S2 — `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp`  (Agent: impl-2; ctor+member in PR1, helper decl in PR3)
- Ctor decl (~:95-103): add trailing `uint64_t emitLag = 0` (default REQUIRED so existing callers/builders compile).
- Members: after `hardThresholdBytes` (~:214), before `spilledKeys` (~:216):
  ```cpp
  /// Event-time emit lag L (ms). Emit uses globalWatermark - emitLag; quiescence/cold-pick uses the TRUE
  /// globalWatermark. 0 = no lag (byte-identical to pre-Increment-C).
  const uint64_t emitLag;
  ```
- Private method decl (PR3, near `unspillAndMarkEmitted` ~:166):
  ```cpp
  /// Ensure a (possibly-spilled) slice is resident WITHOUT marking it emitted. Deadlock-safe: waits out any
  /// in-flight spill of this sliceEnd, reserves it, performs unspill() I/O OUTSIDE evictionMutex under a
  /// ReservationGuard. No-op if already resident or not a SpillableAggregationSlice. Used by the late-write guard.
  void unspillIfNeeded(const std::shared_ptr<Slice>& slice);
  ```

### Section S3 — `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp`  (Agent: impl-2; ctor in PR1, seam in PR2, guard in PR3)
- **Ctor (PR1):** add `const uint64_t emitLag` param; member-init `, emitLag(emitLag)` (after `hardThresholdBytes(...)` ~:110). Extend ctor `NES_DEBUG` (~:115-120) to include `emitLag`.
- **Lag seam — line 267 (PR2):** replace ONLY the base-call argument; lines 268 (`unspillAndMarkEmitted`),
  271-275 (CAS on TRUE `globalWatermark`), 278 (`notify_one`) UNCHANGED:
  ```cpp
  const auto laggedWatermark = Timestamp(
      globalWatermark.getRawValue() >= emitLag ? globalWatermark.getRawValue() - emitLag : 0);
  NES_TRACE("Emit-lagged trigger: true wm={}, emitLag={}, lagged emit wm={}", globalWatermark, emitLag, laggedWatermark);
  auto result = DefaultTimeBasedSliceStore::getTriggerableWindowSlices(laggedWatermark);  // was: (globalWatermark)
  ```
  (`NES_TRACE` per-trigger to avoid hot-path spam.)
- **`getAllNonTriggeredSlices` (~:282-290): UNCHANGED** (LOCKED #8). No edit; reviewers verify the absence of a lag cutoff.
- **OPEN #3 guard (PR3):** define `unspillIfNeeded` and call it inside `getSlicesOrCreate` AFTER the
  `INVARIANT(result.size()==1)` (~:165) and BEFORE `recordCreatedSlice` (~:169), wrapped in `if (emitLag > 0)`:
  ```cpp
  // Late/straddling write may land on a retained-but-spilled slice (band [wm-L, wm) under emit_lag>0). The build
  // path's getHashMapPtrOrCreate would trip INVARIANT(resident) -> std::terminate. Unspill it here, on the worker
  // thread, BEFORE the write. Gated on emitLag>0 so the L=0 default path is byte-identical (no cast, no branch body).
  if (emitLag > 0) { unspillIfNeeded(result[0]); }
  ```
  `unspillIfNeeded` body (modeled on the audited `getSliceBySliceEnd` block cpp:300-334 — unspill WITHOUT marking
  emitted; the slice is still WINDOW_FILLING and must stay spillable/triggerable):
  ```cpp
  void SpillableTimeBasedSliceStore::unspillIfNeeded(const std::shared_ptr<Slice>& slice) {
      auto spillable = std::dynamic_pointer_cast<SpillableAggregationSlice>(slice);
      if (spillable == nullptr) { return; }
      const auto sliceEnd = spillable->getSliceEnd();
      bool needsUnspill = false;
      {
          std::unique_lock lock(evictionMutex);
          if (spillInProgress.contains(sliceEnd)) {
              spillerCv.wait(lock, [&] { return !spillInProgress.contains(sliceEnd); });
          }
          needsUnspill = !spillable->isResident();
          if (needsUnspill) { spillInProgress.insert(sliceEnd); }   // reserve: fences residentBytes, blocks spiller
      }
      if (needsUnspill) {
          auto provider = bufferProvider.copy();
          INVARIANT(provider != nullptr, "buffer provider must be stashed before an unspill-on-write");
          NES_DEBUG("Unspilling slice {} on late write into retained band", sliceEnd);
          ReservationGuard unspillGuard{*this, sliceEnd};          // release-on-throw
          spillable->unspill(*backend, *provider);                 // RocksDB I/O OUTSIDE evictionMutex (LOCKED #7)
          spilledKeys.wlock()->erase(sliceEnd);
          { std::unique_lock lock(evictionMutex); spillInProgress.erase(sliceEnd); }
          spillerCv.notify_all();
          unspillGuard.committed = true;
      }
  }
  ```
  **Reconciled scope note:** add `unspillIfNeeded` as a NEW helper for the guard only. Do NOT refactor the existing
  audited `getSliceBySliceEnd` / `unspillAndMarkEmitted` to share it in this increment (B's regression-risk
  argument). The residual triplication is ACCEPTED here and flagged as future cleanup — reviewers should not BLOCK
  on it (a deliberate decision recorded in SYNTHESIS/PLAN). Must NOT insert into `emittedKeys`.
- **Logging added (S3):** ctor `NES_DEBUG`+emitLag; per-trigger `NES_TRACE` lag line; `NES_DEBUG` on an actual
  unspill-on-write.

### Section S4 — `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp`  (Agent: impl-1; PR1)
- In the `spillEnabled` branch, append a trailing arg to `make_unique<SpillableTimeBasedSliceStore>(...)` (after the
  `hardThresholdMB...*1024` arg ~:244): `conf.spill.emitLag.getValue())  // event-time L (ms); default 0`. Raw ms,
  unit-consistent with the `windowType->getSize().getTime()` already passed here.
- Extend the existing `NES_INFO` (~:245-248) to include `emit_lag={conf.spill.emitLag.getValue()}`.
- `else` in-memory branch + `AggregationOperatorHandler` ctor UNCHANGED.

---

## PR Boundaries  (commit per boundary; do NOT push; NO co-author trailer — LOCKED #10)

- **PR1 — inert plumbing (zero behavior change).** S1 + S2(ctor param/member) + S3(ctor) + S4 + S6 config tests +
  the test-builder `emitLag` overload. Provably L=0-identity; regression gate must stay 820. Reviewer:
  `everything-claude-code:code-reviewer` (config/CMake/build-mixed) + `everything-claude-code:cpp-reviewer`.
- **PR2 — the lag seam.** S3(line-267 saturating subtract + NES_TRACE; `getAllNonTriggeredSlices` left unchanged
  with a verifying comment) + tests T1-T7. The one behavioral change, isolated to one clamped subtraction.
  Reviewer: `everything-claude-code:cpp-reviewer` (flag the async-probe-drain ordering region 267-268 for the
  super-reviewer: confirm the subtraction did NOT move past line 268 — R3/LOCKED #7).
- **PR3 — OPEN#3 unspill-on-write guard (concurrency).** S2(helper decl) + S3(`unspillIfNeeded` + the gated call in
  `getSlicesOrCreate`) + test T8 + OPTIONAL concurrency T9. Reviewer: `everything-claude-code:cpp-reviewer` PLUS
  explicit super-reviewer concurrency scrutiny — verify: (a) all RocksDB I/O outside `evictionMutex`; (b) NO
  `emittedKeys` insertion; (c) no lock-order inversion (base maps never under `evictionMutex`); (d) `ReservationGuard`
  release-on-throw; (e) the `if (emitLag>0)` L=0 no-op property.

Strict ordering: PR1 → PR2 → PR3 (PR2 reads the member from PR1; PR3's T8 uses the band PR2 creates).

---

## Test Plan  (all extend EXISTING targets — NO new CMake line)

Builders: add an optional `emitLag` param (default 0) to `makeStoreWithThresholds`/`makeStoreWithBackend` so L>0
tests request it; existing calls unchanged. Do NOT use `setLastSeenWatermarkForTest` for the decouple proofs — it
bypasses the very coupling we fix; drive the production `getTriggerableWindowSlices`.

**S6 — `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp`** (target CMake:21; PR1): extend existing cases —
`emit_lag` default 0; CLI `--...spill.emit_lag=5000`; YAML `spill.emit_lag`; nested-in-QEC default/CLI/YAML.

**`nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp`** (target CMake:28):
- **T1 `emitLagZeroEmitsIdenticallyToBaseline`** (PR2, LOCKED #6) — L=0 emit set + folded values identical to baseline; `spillCountForTest()==0`.
- **T2 `emitLagDefersEmissionByLagBand`** (PR2) — L=3·WIN: trigger at F returns only `windowEnd < F−L`; band `[F−L,F)` absent; band still counted by `createdSlicesSizeForTest()`.
- **T3 `emitLagDifferentialIdenticalResults`** (PR2, OPEN#5 headline) — two stores L=0 vs L>0, identical stream; fold emitted+drained (incl. EOS `getAllNonTriggeredSlices`) per windowEnd→per-key; assert identical multiset; mid-stream checkpoint shows L>0 emitted strictly fewer.
- **T4 `emitLagBacklogFullyFlushedOnTerminate`** (PR2, LOCKED#8) — `incrementNumberOfInputPipelines()` + `getAllNonTriggeredSlices()` returns EVERY deferred window resident; union(triggered,drained)==all created.
- **T5 `emitLagMakesColdSetNonEmptyAndSpillerEngages`** (PR2, E0→fixed) — tiny thresholds, L>0, drive via production trigger then `spillColdSlicesUnderHardForTest()`; assert `spillCountForTest()>0`, `backend->sliceCount()>0`, a band window `!isSliceResident`.
- **T6 `emitLagZeroColdSetEmptyNoSpill`** (PR2, E0 empty-set regression) — same drive, L=0 ⇒ `spillCountForTest()==0`, `backend->sliceCount()==0`.
- **T7 `emitLagUnderflowClampsToZeroEmit`** (PR2, R1 clamp) — L large, trigger at wm<L ⇒ empty trigger map, no terminate/throw.
- **T8 `lateWriteIntoSpilledRetainedSliceDoesNotTerminate`** (PR3, OPEN#3) — spill a cold-unemitted band slice; late write via `getSlicesOrCreate(inRangeTs)` + `populate`; assert no terminate, slice now resident, folded contents = pre-spill ⊕ late tuples. RED against PR2-head (terminates) → GREEN with the guard.

**`nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp`** (target CMake:29; PR3 optional):
- **T9 `emitLagConcurrentDifferentialChecksum`** — clone the SPILL_LEAD harness but drive the cold band via production `emitLag` (not `setLastSeenWatermarkForTest`); reuse Σ(triggered+drained)==Σ(inserted) checksum; synchronous non-starvable spill-happened assertion; NEVER hard-assert jthread liveness.

### Gates
Inner loop: `set -o pipefail; ninja SpillConfigTests SpillableTimeBasedSliceStoreTest` (+ConcurrencyTest/RoutingTest
when touched) → `ctest -j8 --output-on-failure --timeout 120 -R "Spill|EmitLag|LowerToPhysicalWindowedAggregationSpillRouting"`.
After PR1 (wide `SpillConfig.hpp` fan-out): FULL `ninja`. Final: full `ninja` + `ctest -j8 -LE 'Systest|DockerCompose' --timeout 120` ≥ **820** + new tests.

---

## Parallel Groups
- **PR1 internal:** Group A = S1 + S6 (config); Group B = S2-ctor + S3-ctor + S4 + builder overload. A∥B; meet at the full link.
- **PR2:** one seam edit; T1-T7 authorable in parallel once it compiles.
- **PR3:** helper+call first, then T8 (RED→GREEN), then optional T9.
- Backbone is sequential: PR1 → PR2 → PR3.
