# SYNTHESIS — Increment C: Emit-Decouple (Lagged Emit Watermark)

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Unified, plan-able understanding reconciled
from CORE.md (runtime/data-structures), BUILD.md (config/CMake plumbing), CONTRACTS.md
(interfaces/tests), and the authoritative DESIGN_BRIEF.md. The three explorations CONVERGED; every
line/site below was re-verified against source during synthesis (SpillConfig.hpp:41-60,
SpillableTimeBasedSliceStore.cpp:157-290, .hpp:95-218, LowerToPhysicalWindowedAggregation.cpp:226-244).

**One-sentence task.** Break the E0 coupling `emit ≡ quiescence` by driving the base emit cutoff with a
clamped lagged watermark `globalWatermark − emit_lag` while keeping the quiescence/cold signal
(`lastSeenWatermark`) on the TRUE `globalWatermark`, so the retained band `[globalWatermark − L,
globalWatermark)` becomes `{cold ∧ ¬emitted}` (non-empty) and Increment B's spiller finally has slices
to act on. `emit_lag` default 0 ⇒ byte-identical to today.

---

## Scope (exactly what changes in production)

1. **Add `emit_lag` config knob** to `SpillConfig` (`UIntOption`, default `"0"`). Auto-derives CLI key
   `--worker.default_query_execution.spill.emit_lag` and nested YAML `spill.emit_lag` — no parser code.
2. **Add `emit_lag` to the store ctor** (`SpillableTimeBasedSliceStore`) as a trailing
   `uint64_t emitLag = 0` parameter + `const uint64_t emitLag;` member.
3. **Apply the lag at ONE seam** — inside `SpillableTimeBasedSliceStore::getTriggerableWindowSlices`
   (cpp:264-280): call the base trigger with a clamped `globalWatermark − emitLag` at line 267; keep the
   CAS-max of `lastSeenWatermark` on the TRUE `globalWatermark` at lines 271-275 (unchanged).
4. **Thread the value through the lowering** — one new trailing argument in the
   `make_unique<SpillableTimeBasedSliceStore>(...)` call (LowerToPhysicalWindowedAggregation.cpp:236-244).

That is the entire production change. NO handler edit, NO interface/virtual change, NO new setter, NO
JIT/Nautilus edit, NO GC code change, NO terminate-path edit. (See "Open questions" for the single
conditional addition — the OPEN #3 unspill-on-write guard — which is the one decision left to architects.)

---

## Files affected

### Production

| # | File | Location | Change |
|---|------|----------|--------|
| 1 | `nes-query-compiler/interface/SpillConfig.hpp` | after line 54 (`compression`) | Add `UIntOption emitLag = {"emit_lag", "0", "<event-time lag L (ms); 0 = no lag>"};`. |
| 1 | `nes-query-compiler/interface/SpillConfig.hpp` | `getOptions()` line 59 | Append `&emitLag` to the returned vector. |
| 2 | `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` | ctor decl lines 95-103 | Add trailing param `uint64_t emitLag = 0`. |
| 2 | `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` | members, after `hardThresholdBytes` (line 214; before `spilledKeys` line 216) | Add `const uint64_t emitLag;`. |
| 3 | `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` | ctor def (~line 96) | Add `const uint64_t emitLag` param + `, emitLag(emitLag)` in the member-init list. |
| 3 | `nes-physical-operators/src/SliceStore/SpillableTimeBasedSliceStore.cpp` | line 267 (inside `getTriggerableWindowSlices`) | Replace base call arg with clamped `Timestamp(globalWatermark.getRawValue() >= emitLag ? globalWatermark.getRawValue() - emitLag : 0)`. **Lines 268 (`unspillAndMarkEmitted`) and 271-275 (CAS on TRUE wm) UNCHANGED.** |
| 4 | `nes-query-compiler/src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` | `make_unique<SpillableTimeBasedSliceStore>` call, lines 236-244 | Append trailing arg `conf.spill.emitLag.getValue()`. The `else` in-memory branch (252-253) UNCHANGED. |

**Explicitly NOT touched** (and reviewers must not expect them to be):
`DefaultTimeBasedSliceStore.{hpp,cpp}` (base is a pure function of its watermark arg — untouched),
`WindowBasedOperatorHandler.cpp` (join-shared — untouched), `WindowSlicesStoreInterface.hpp` (no new
virtual), `AggregationOperatorHandler.{hpp,cpp}` (not on the lag path under seam (a)),
`getAllNonTriggeredSlices` body (cpp:282-290 — watermark-agnostic, already correct), any Nautilus/codegen.

### Test (no new CMake line for any of these — they extend existing targets)

| File | Target / CMake line | New cases |
|------|---------------------|-----------|
| `nes-query-compiler/tests/UnitTests/SpillConfigTests.cpp` | `SpillConfigTests` (`nes-query-compiler/tests/CMakeLists.txt:21`) | `emit_lag` assertions in defaults (expect 0), CLI override, YAML round-trip, nested-in-QEC cases. |
| `nes-physical-operators/tests/SpillableTimeBasedSliceStoreTest.cpp` | `SpillableTimeBasedSliceStoreTest` (`nes-physical-operators/tests/CMakeLists.txt:28`) | T1 `emitLagZeroEmitsIdenticallyToBaseline` (LOCKED #6); T2 `emitLagDefersEmissionByLagBand`; T3 `emitLagDifferentialIdenticalResults` (OPEN #5 headline); T4 `emitLagBacklogFullyFlushedOnTerminate` (LOCKED #8); T5 `emitLagMakesColdSetNonEmptyAndSpillerEngages` (E0→EL proof, uses production override NOT `setLastSeenWatermarkForTest`); T6 `emitLagZeroColdSetEmptyNoSpill` (E0 empty-set / regression); T7 `emitLagUnderflowClampsToZeroEmit` (clamp boundary). Builders `makeStore`/`makeStoreWithThresholds`/`makeStoreWithBackend` append the default-0 `emitLag` arg. |
| `nes-physical-operators/tests/SpillableTimeBasedSliceStoreConcurrencyTest.cpp` | `SpillableTimeBasedSliceStoreConcurrencyTest` (`...CMakeLists.txt:29`) | OPTIONAL escalation: `emitLagConcurrentDifferentialChecksum` reusing the SPILL_LEAD harness (lines 379-425) but driving the cold band via production `emitLag` instead of `setLastSeenWatermarkForTest`; reuse Σ(triggered+drained)==Σ(inserted) checksum. Keep the synchronous non-starvable drive for the spill-happened assertion; never hard-assert jthread liveness. |

> If a fresh test file is preferred for the differential test, register it with one
> `add_nes_physical_operator_test(EmitDecoupleDifferentialTest EmitDecoupleDifferentialTest.cpp)` line at
> `nes-physical-operators/tests/CMakeLists.txt:30`. Default recommendation: keep in the existing file.

---

## Dependencies (build / include / link / rebuild order)

- **Config interface lib.** `SpillConfig.hpp` + `QueryExecutionConfiguration.hpp` live in
  `nes-query-compiler/interface/`, exposed by the INTERFACE library `nes-query-compiler-interface`
  (`nes-query-compiler/CMakeLists.txt:18-20`). Lower modules see them transitively.
- **Embedding chain (CLI key auto-derivation).**
  `WorkerConfiguration.defaultQueryExecution` (`nes-runtime/interface/Configuration/WorkerConfiguration.hpp:41`)
  → `QueryExecutionConfiguration.spill` (`QueryExecutionConfiguration.hpp:79`, registered :92)
  → `SpillConfig.<field>`. Adding `emitLag` to `SpillConfig::getOptions()` is sufficient; the dotted key
  and YAML form are derived structurally by `BaseConfiguration` — **no other config file changes.**
- **Store header → consumers.** `nes-query-compiler` links `nes-physical-operators` PUBLIC
  (`nes-query-compiler/CMakeLists.txt:24`), so the lowering sees the store header; RocksDB is a PRIVATE
  dep of `nes-physical-operators` (`:26`) — the lowering passes only `SpillConfig` values, never links rocksdb.
- **Rebuild fan-out (the wide one).** `SpillConfig.hpp` is pulled through
  `QueryExecutionConfiguration.hpp` → `WorkerConfiguration.hpp`, so editing it recompiles a LARGE set of
  TUs (lowering, every `WorkerConfiguration` consumer, both query-compiler test binaries, and more).
  **Treat it as a full-rebuild trigger: run the FULL `ninja`, never `ninja | tail` without
  `set -o pipefail`** (nes_cpp_idioms / LOCKED #9).
- **Narrower fan-out.** `SpillableTimeBasedSliceStore.hpp` → recompiles the store cpp, the lowering,
  `SpillableTimeBasedSliceStoreTest`, `SpillableTimeBasedSliceStoreConcurrencyTest`; relinks
  `nes-physical-operators` + `nes-query-compiler` + dependents. `SpillableTimeBasedSliceStore.cpp` /
  `LowerToPhysicalWindowedAggregation.cpp` → one TU each, relink dependents.
- **Build/test environment (LOCKED #9).** Docker image `nebulastream/nes-development:local-rocksdb`
  (base `:local` lacks RocksDB), build dir `cmake-build-debug`, repo mounted at `/workspace`. Inner loop:
  `ninja SpillConfigTests SpillableTimeBasedSliceStoreTest` then targeted
  `ctest -R "Spill|EmitLag|LowerToPhysicalWindowedAggregationSpillRouting"`. Gate: full `ninja` +
  `ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`, **must stay ≥ 820** (Increment B baseline; 820
  + new tests after RED→GREEN).

---

## Risks (lifetime / thread-safety / ABI / correctness)

1. **R1 — clamp underflow on `globalWatermark − emit_lag` (LOW, must-handle).** `Timestamp::operator-`
   exists, but `globalWatermark < emitLag` early in the stream would wrap and emit spuriously. The single
   new line at cpp:267 MUST be a **saturating subtract clamped to 0** (`gw >= L ? gw − L : 0`). Pure
   arithmetic; covered by test T7. With L=0 it is the identity ⇒ LOCKED #6 byte-identical.

2. **R2 — late/straddling write into a retained-but-SPILLED slice ⇒ `std::terminate` (MEDIUM, OPEN #3,
   reachability-gated).** `getSlicesOrCreate` (cpp:157-185, **verified: NO unspill call anywhere in this
   method**) returns an existing (possibly-spilled) slice on a cache miss; the extractor then calls
   `getHashMapPtrOrCreate` whose `INVARIANT(resident, …)` (SpillableAggregationSlice.cpp:60) terminates
   if the slice is spilled. The widened band `[wm−L, wm)` makes this **possible**; whether it is
   **reachable** depends on the workload's lateness vs the TRUE watermark (with L=0 it is unreachable —
   slices spill only after emit). **This is the one risk requiring an architect decision — see Open
   questions.** The stale-cache-HIT variant is JIT-only, NOT host-fixable, and bounds the minimum-safe L
   if reachable — declared limitation, not in-scope.

3. **R3 — do NOT move the subtraction past line 268 (CRITICAL ordering, async-probe-drain LOCKED #7).**
   The lag changes only WHICH windows the base returns at line 267. Line 268 `unspillAndMarkEmitted`
   marks every RETURNED slice emitted + resident BEFORE the handler snapshots its raw `HashMap*` for the
   async probe. A spilled slice in the retained band is never triggered yet, so never handed to the probe;
   when the lagged frontier reaches it, it goes through `unspillAndMarkEmitted` first. **The subtraction
   belongs ONLY on the line-267 base-call argument; nothing else in 268-279 changes.** Moving/duplicating
   the lag past 268, or spilling an `emittedKeys` slice, is a use-after-free.

4. **R4 — do NOT subtract L in `getAllNonTriggeredSlices` (LOCKED #8, correctness).** The terminate path
   (cpp:282-290) delegates to a **watermark-agnostic** base (`getAllNonTriggeredSlices()` takes no
   argument) that flushes EVERY `WINDOW_FILLING` window, then `unspillAndMarkEmitted`s them, and
   deliberately does NOT bump `lastSeenWatermark`. This already drains the whole retained band for free.
   Adding any `emit_lag` cutoff here would orphan deferred windows = lost results. Guard with test T4.

5. **R5 — signature stability: ADD, never change existing virtual signatures (ABI/build, `-Werror
   -Woverloaded-virtual`).** No new override and no signature change to any existing virtual is needed.
   `emit_lag` is plumbed via the **constructor** + a `const` member; the two override BODIES
   (`getTriggerableWindowSlices`, and the no-op-for-lag `getAllNonTriggeredSlices`) get body-only edits.
   Changing a virtual's parameter list would (a) break override-matching → `-Woverloaded-virtual` hard
   error, and (b) ripple to the interface + handler + base + both subclasses + ~10 test call sites. Any
   new test-only knob must be a brand-new non-virtual public method (e.g. `setEmitLagForTest`), mirroring
   `setLastSeenWatermarkForTest` (hpp:155).

6. **R6 — seam choice (resolved, but the highest-leverage decision).** Seam (b)
   (`WindowBasedOperatorHandler::checkAndTriggerWindows`) is the JOIN build hot path
   (`checkAndTriggerWindows` is virtual on the base handler, NOT overridden by `StreamJoinOperatorHandler`
   or `AggregationOperatorHandler`); it also forces redundant state + a new setter. **Seam (a) is locked**
   — the override already receives the true frontier as its argument AND maintains `lastSeenWatermark` as
   a separate adjacent statement, so the fix is one clamped subtraction with zero join blast radius and no
   redundant watermark plumbing (resolves OPEN #4).

7. **R7 — first real exercise of spill/unspill on a live query (LOW).** L>0 makes `{cold ∧ ¬emitted}`
   non-empty for the first time in production, so `spill()`/`unspill()`/`residentBytes()` run on the real
   path. All Increment B discipline (`evictionMutex` guards in-memory decisions only, ALL RocksDB I/O
   OUTSIDE it; `spillInProgress` reservation + `ReservationGuard` RAII release-on-throw; background
   `std::jthread` lifecycle, member declared LAST) is **orthogonal to L and unchanged**. The SPILL_LEAD
   stress test (`SpillableTimeBasedSliceStoreConcurrencyTest.cpp:379-425`) already manufactures exactly
   this cold-but-unemitted regime via `setLastSeenWatermarkForTest`; Increment C turns that manufactured
   decoupling into real pipeline behavior — reuse its shape for the concurrency gate.

8. **R8 — GC / probe-watermark consistency (LOW, no code change, OPEN #2 resolved).** The probe GC runs
   on a SEPARATE `watermarkProcessorProbe` fed exclusively by EMITTED buffers (`outputOriginId`), so the
   probe frontier structurally trails emit; deferring emit by L pushes it later by L too — it cannot
   overtake. The window-erase loop is `EMITTED_TO_PROBE`-gated (a deferred window is still
   `WINDOW_FILLING`, skipped). The non-state-gated slice-erase loop trails emit by a full `windowSize`.
   No GC change; add a test asserting probe-frontier ≤ lagged-emit-frontier (covered conceptually by T3/T4).

---

## Locked decisions (from DESIGN_BRIEF — reviewers MUST NOT flag these as bugs)

1. **Option 1 (emit-decouple), single tumbling-aggregation operator.** Option 2 (chained
   minute→hour→day) REJECTED (loses per-granularity history; relocates/avoids OOC).
2. **No Nautilus/JIT/codegen edit.** The SliceCache hit-path is JIT-generated and stays untouched.
   Spilling QUIESCENT windows is safe without a residency check because NES already refuses/terminates
   late tuples into finished windows (`DefaultTimeBasedSliceStore.cpp:94-96`).
3. **Tumbling windows ONLY.** No sliding windows, no variable-sized/general-purpose serialization.
4. **Mechanism = LAGGED EMIT WATERMARK.** Emit uses `globalWatermark − emit_lag`; the store learns the
   TRUE `globalWatermark` for quiescence/cold-pick. Net: base retains slices with `windowEnd ∈
   [globalWatermark − L, globalWatermark)`; the existing cold-pick finds them.
5. **`emit_lag` is an event-time DURATION** (same unit as the window/watermark, ms), NOT a window-count,
   NOT memory-driven. Surface on `SpillConfig`; CLI key
   `--worker.default_query_execution.spill.emit_lag=...`.
6. **`emit_lag` default = 0 ⇒ today's exact behavior** (emit wm == global wm, nothing retained). Every
   existing query and test behaves identically — the regression safety net.
7. **Preserve EVERY Increment B invariant (async-probe-drain is ABSOLUTE):** never spill an
   `EMITTED_TO_PROBE` slice; emit unspills before handing the raw `HashMap*` to the probe; one outer
   `evictionMutex` for in-memory decisions only (ALL RocksDB I/O OUTSIDE it); per-slice `spillInProgress`
   reservation released via `ReservationGuard` RAII on ALL paths incl. exceptions; background
   `std::jthread` spiller lifecycle unchanged, jthread member stays LAST.
8. **Terminate/flush is mandatory.** End-of-stream (`getAllNonTriggeredSlices`) MUST flush the ENTIRE
   retained backlog regardless of `emit_lag`. A deferred window that never emits is a correctness bug.
9. **Build/test ONLY in Docker** `nebulastream/nes-development:local-rocksdb`; build dir
   `cmake-build-debug`, `ninja`, gate `ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`. Baseline to
   beat: **820/820**. NEVER `ninja | tail` without `set -o pipefail`.
10. **Manual push policy.** Commit per PR boundary; do NOT push; NO co-author trailer.

---

## Open questions

**THE ONE DECISION FOR THE ARCHITECTS — OPEN #3: implement the late-record unspill-on-write guard
PROACTIVELY in this increment, or defer it to the redo-E0 reachability experiment?**

- **The hazard is real and is a `std::terminate`** (not a UAF): a late/straddling record whose event-time
  falls in a spilled slice's `[start,end)` range, arriving on a cache MISS while that slice is still in
  the base `slices` map (retained-but-spilled), reaches `getHashMapPtrOrCreate`'s `INVARIANT(resident,…)`
  and crashes the process. The widened band `[wm−L, wm)` is precisely the population this increment
  deliberately creates, so L>0 is what opens the door (verified: `getSlicesOrCreate` cpp:157-185 has no
  unspill).
- **Reachability is workload-dependent.** A record in `[wm−L, wm)` is late relative to the TRUE frontier;
  NES has no allowed-lateness knob and the watermark contract treats such records as "late for a finished
  window." Whether the thesis workload actually delivers in-range records into the band is an empirical
  question the redo-E0 step can answer cheaply.
- **The fix is small, safe, host-side, and in-scope.** Add, in
  `SpillableTimeBasedSliceStore::getSlicesOrCreate` (cpp:157-185), right after the base call returns and
  BEFORE the worker writes: if the returned slice is a `SpillableAggregationSlice` and `!isResident()`,
  unspill it reusing the exact `evictionMutex`/`spillInProgress`/`ReservationGuard` discipline of
  `unspillAndMarkEmitted` (cpp:233-258), which is safe to run on the worker thread. Plain C++, no JIT.

  **RECOMMENDATION: implement the guard PROACTIVELY in this increment, but behind a tight, well-tested
  path.** Rationale: (1) shipping a known reachable-in-principle `std::terminate` is a correctness
  liability — a single late record crashes the whole worker, and "we think the workload won't trigger it"
  is a fragile guarantee for a thesis demonstration that intentionally enlarges the retained band; (2) the
  guard is small, host-only, and reuses already-audited unspill machinery, so the marginal risk of adding
  it is low and self-contained; (3) it makes the increment robust to E0/E1 workloads we have not yet run,
  rather than coupling correctness to an experiment outcome. The DESIGN_BRIEF's "implement only if
  reachable" was written to avoid speculative code, but here the code is cheap and the failure mode is
  catastrophic — the asymmetry favors shipping the guard. **If the architects prefer to honor the brief
  literally (defer), the increment MUST then add a reachability probe to redo-E0 as a hard gate and treat
  any `INVARIANT(resident)` trip as a release blocker** — i.e. deferral is acceptable only with an
  explicit, scheduled reachability test, never as "ship and hope." Either way this is the single item the
  architects must settle before planning the PR boundaries.

**Resolved (recorded so reviewers don't re-litigate):**
- OPEN #1 (seam) → **seam (a)**, inside the store override. OPEN #2 (GC) → **no code change**. OPEN #4
  (setQuiescenceWatermark) → **no setter; the argument already carries the true frontier**. OPEN #5
  (differential correctness) → **unit test T3** (multiset of (windowEnd → per-key value) identical for
  L=0 vs L>0). LOCKED #8 (terminate flush) → **already satisfied; just must not add a lag cutoff**.

---

## Recommended implementation shape (the minimal diff)

Add `UIntOption emitLag = {"emit_lag", "0", …}` to `SpillConfig.hpp` and `&emitLag` to its `getOptions()`
(this alone wires the CLI/YAML key). Add a trailing `uint64_t emitLag = 0` ctor parameter and a `const
uint64_t emitLag;` member to `SpillableTimeBasedSliceStore` (.hpp:95-103 / after :214; .cpp ctor +
member-init). At `SpillableTimeBasedSliceStore.cpp:267` substitute a SATURATING `globalWatermark − emitLag`
(clamp to 0) into the single base `getTriggerableWindowSlices` call, leaving line 268
(`unspillAndMarkEmitted`) and the true-frontier CAS at 271-275 untouched; touch nothing in
`getAllNonTriggeredSlices`. Append `conf.spill.emitLag.getValue()` as the final argument to the
`make_unique<SpillableTimeBasedSliceStore>(...)` call at LowerToPhysicalWindowedAggregation.cpp:236-244.
Default 0 makes the whole change a provable identity to today (LOCKED #6). Add the seven unit tests (T1-T7)
to `SpillableTimeBasedSliceStoreTest.cpp` plus `emit_lag` assertions in `SpillConfigTests.cpp`; gate with
the full Docker `ninja` + `ctest -LE 'Systest|DockerCompose'` at ≥ 820. The ONLY conditional addition is
the OPEN #3 unspill-on-write guard in `getSlicesOrCreate` — recommended PROACTIVELY (small, host-side,
reuses `unspillAndMarkEmitted`'s discipline), pending the architects' decision.
