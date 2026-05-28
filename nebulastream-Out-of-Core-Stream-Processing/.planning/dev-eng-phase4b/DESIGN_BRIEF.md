# Phase 4b — OOC integration design brief (Route B + Option 1 budget)

Locked via design discussion 2026-05-22. HEAD `40de3f9a55` (Phase 4a). Branch `feat/ooc-phase1-spill-skeleton`, clean.
This brief is the authoritative context for the dev-eng-orchestra explore/synthesis/architect phases. Decisions marked
**LOCKED** are not to be re-litigated; items marked **OPEN (architect to resolve)** need full-code-visibility decisions.

## Goal
Wire the already-built, isolated spill components (Phases 1–4a) into the live aggregation path so a windowed-aggregation
query spills **closed** slices' hashmaps to RocksDB under a memory budget and unspills them on the trigger/probe read path,
completing where it would otherwise OOM. Then make the budget the controllable independent variable for evaluation.

## Already built (reuse, do not rebuild)
- `SpillConfig` (enabled / rocksdbPath / soft+hardThresholdMB / compression) — `nes-query-compiler/interface/SpillConfig.hpp`
- `SpillBackend` interface + `NullSpillBackend` + `InMemorySpillBackend` + `RocksDBSpillBackend` (durable, lz4, 12-byte BE key)
- `SliceSerializer` (ChainedHashMap ⇄ byte record, fixed-size entries)
- `SpillableAggregationSlice : HashMapSlice` with `isResident()/spill(backend)/unspill(backend, bufferProvider)/getNumberOfTuples()`
- Phase 1 fail-fast guard in `LowerToPhysicalWindowedAggregation::apply()` — to be REPLACED by real wiring.

## LOCKED decisions

### D1 — Route B seam (clean polymorphic accessor) — REFINED to THREE methods
Lift to **virtual on `HashMapSlice`** (`include/HashMapSlice.hpp`; `hashMaps` already `protected`):
```cpp
[[nodiscard]] virtual HashMap* getHashMapPtr(WorkerThreadId) const;        // base impl = current AggregationSlice logic
[[nodiscard]] virtual HashMap* getHashMapPtrOrCreate(WorkerThreadId);      // base impl = lazy-create (current AggregationSlice logic)
[[nodiscard]] virtual uint64_t getNumberOfTuples() const;                  // base impl = current accumulate (null-safe)
```
- `AggregationSlice` then **drops both `getHashMapPtr*` overrides** and inherits the base (signatures already identical — verified).
- `SpillableAggregationSlice` keeps the `getHashMapPtr*` overrides (adding `INVARIANT(resident, ...)`), and turns its existing
  `getNumberOfTuples()` **shadow into an `override`** (returns the spill-time snapshot while non-resident).
- **Why the 3rd method (exploration consensus):** D4 budget math reads tuple counts polymorphically via `HashMapSlice&`/`Slice&`.
  Today `getNumberOfTuples()` is non-virtual and merely *shadowed* by the spillable slice → a polymorphic call on a spilled slice
  would hit the base `accumulate` and return **0** (maps freed), corrupting the budget. Making it virtual fixes this.
- Consequence: every consumer operates on `HashMapSlice&`/`dynamic_pointer_cast<HashMapSlice>` — **no concrete-type
  dynamic_casts scattered through handler/lowering** (this is the whole point of Route B).

### D2 — Store: `SpillableTimeBasedSliceStore`
- Un-`final` `DefaultTimeBasedSliceStore` and make the read/create methods virtual: `getSlicesOrCreate`,
  `getTriggerableWindowSlices`, `getSliceBySliceEnd`, `getAllNonTriggeredSlices`, `garbageCollectSlicesAndWindows`.
- New `SpillableTimeBasedSliceStore : DefaultTimeBasedSliceStore`. **Owns the `SpillBackend`.** Reuses the parent's
  `createSliceStoreRef` + `SliceCache` + `DefaultTimeBasedSliceStoreRef` UNCHANGED (justified: build hot path only ever
  touches resident WINDOW_FILLING slices — see D5 — so the JIT ref needs no spill-awareness).

### D3 — Unspill-on-read lives in the STORE, not the handler — REFINED (both trigger feeders)
- The aggregation read path is the two methods that feed `triggerSlices`: **`getTriggerableWindowSlices()` AND
  `getAllNonTriggeredSlices()`** (the termination-flush path — easy to miss). Both must unspill any non-resident slice
  **before** returning it, because `triggerSlices` then snapshots raw `HashMap*` for the async probe.
- `getSliceBySliceEnd()` is used only by the join probe (out of 4b scope) per exploration; unspill it too for consistency,
  but the load-bearing feeders are the two above.
- The handler's `triggerSlices` then sees only resident slices and needs ONLY the D1 seam change
  (`dynamic_pointer_cast<HashMapSlice>` instead of `AggregationSlice`) — no spill logic in the handler read path.
- **O1 RESOLVED:** the store stashes `pipelineCtx->getBufferManager()` (a `shared_ptr<AbstractBufferProvider>`, query-lifetime
  stable) during `setupSliceStoreProxy` (`DefaultTimeBasedSliceStoreRef.cpp` ~:114, which already holds store + pipelineCtx),
  and uses it for unspill. The brief's earlier "setupSliceStore(pipelineCtx)" refers to the Nautilus *ref* setup, i.e. this proxy.

### D4 — Memory budget = Option 1 (per-store resident page math)
- Resident bytes of a slice = `Σ_workers ⌈map.getNumberOfTuples() / entriesPerPage⌉ × pageSize` (exact for fixed-size,
  which is all we support; equals exactly what `spill()` reclaims). Computed from live hashmap state — NO parallel
  byte counter (no drift). `entriesPerPage = pageSize / entrySize`.
- `softThresholdMB` → proactive spill of oldest closed slice(s) until back under soft. `hardThresholdMB` → backstop
  (block/refuse further growth, or aggressive spill — architect to choose semantics).
- Pool occupancy (`getNumberOfAvailableBuffers`) is explicitly REJECTED: aggregation state is unpooled
  (`ChainedHashMap` uses `getUnpooledBuffer`), so the pooled counter is blind to it.

### D5 — Eviction policy: oldest-COLD-first — REFINED 2026-05-22 (SUPERSEDES "EMITTED_TO_PROBE")
**Correction (exploration-driven, user-approved):** `EMITTED_TO_PROBE` is an UNSAFE spill target. `triggerSlices`
snapshots raw `HashMap*` into an `EmittedAggregationWindow` dispatched ASYNC to the probe
(`AggregationOperatorHandler.cpp:132,136`); the probe reads those pointers later. Spilling (freeing) an emitted slice = UAF.
- **Spill target = COLD slices: stopped filling (`sliceEnd < global watermark`, no active writer) but NOT yet
  triggered/emitted to the probe.** Read-only; only read again at trigger → the unspill-on-read hook (D3).
- An `EMITTED_TO_PROBE` slice MUST NOT be spilled — it stays resident until garbage collection (its maps may be in-flight).
- `WINDOW_FILLING` slices are never spilled (build hot path → always resident).
- Order spill candidates by `sliceEnd` ascending (oldest cold first).
- **Window-type note (document honestly):** for SLIDING windows (`slide < size`) the cold population spans
  ~`(windowSize − slideSize)` — the real OOC opportunity in large sliding IoT aggregations. For TUMBLING windows
  (`slide == size`) a slice triggers right when it stops filling, so the cold population is ~empty → little to spill.
  This is a stated limitation, not a bug; the spike's end-to-end proof should use a SLIDING window.

### D6 — Concurrency / lifetime invariants (the real risk — treat as hard constraints + test targets)
- **Only spill COLD slices** (per refined D5): `sliceEnd < watermark`, not WINDOW_FILLING, not yet emitted. Provably
  enforced, not assumed.
- **Never spill an emitted slice (async-probe-drain invariant).** Once `triggerSlices` snapshots a slice's raw `HashMap*`
  for the probe, those maps must stay alive until GC — they may be in-flight to the async probe. Spilling = UAF. This is
  the invariant that retires the original EMITTED_TO_PROBE target.
- **No dangling SliceCache entry.** The per-pipeline `SliceCache` holds raw `HashMap*` keyed by ts-range and is NEVER
  invalidated on close/spill (`SliceCacheSecondChance.cpp`). Its safety after a free relies on **watermark monotonicity**
  making a cold slice's entry unmatchable. Make this an explicit invariant + test: a spilled slice's range must never be
  re-looked-up by the build path; if that can't be guaranteed, invalidate on spill.
- **Unspill is serialized + idempotent.** Concurrent trigger reads of the same spilled slice must not double-rebuild.
  4a `unspill` is idempotent single-threaded; the store must serialize it (under its existing `folly::Synchronized`).

## Change surface (files)
NEW:
- `nes-physical-operators/include/SliceStore/SpillableTimeBasedSliceStore.hpp` + `src/.../SpillableTimeBasedSliceStore.cpp`
- tests: `SpillableTimeBasedSliceStoreTest.cpp`, plus an end-to-end query-level spill test (the spike, see Scope)
EDIT:
- `include/HashMapSlice.hpp` (+2 virtuals) ; `src/HashMapSlice.cpp` (move base impls in)
- `include/Aggregation/AggregationSlice.hpp` + `src/Aggregation/AggregationSlice.cpp` (drop overrides)
- `include/Aggregation/SpillableAggregationSlice.hpp` + `.cpp` (mark `override`)
- `include/SliceStore/DefaultTimeBasedSliceStore.hpp` (un-final, virtualize methods)
- `src/Aggregation/AggregationOperatorHandler.cpp` (`getCreateNewSlicesFunction`: factory branch spillable vs not when spill
  enabled; `triggerSlices`: seam cast only) ; `.hpp` (carry spill-enabled flag if the branch lives here)
- `src/LoweringRules/LowerToPhysical/LowerToPhysicalWindowedAggregation.cpp` (replace guard; construct
  `SpillableTimeBasedSliceStore` + `RocksDBSpillBackend` from `SpillConfig` when enabled; make extractor lambda cast to
  `HashMapSlice&`)

## OPEN (architect to resolve with full code visibility)
- **O1 — buffer provider at trigger time.** Unspill needs an `AbstractBufferProvider&`, but `getTriggerableWindowSlices`
  has none. Preferred: `SpillableTimeBasedSliceStore` stashes a durable provider during `setupSliceStore(pipelineCtx)`
  (the global BufferManager is query-lifetime stable). Confirm `pipelineCtx->getBufferManager()` yields something durable.
- **O2 — backend ownership/lifecycle/threading.** One `RocksDBSpillBackend` per store (per query)? Created from
  `SpillConfig` where? rocksdbPath uniqueness per query/operator. Error propagation via `SpillStoreFailure`.
- **O3 — slice factory location.** Branch inside `AggregationOperatorHandler::getCreateNewSlicesFunction` on a spill flag,
  vs. inject a factory. Handler is `final`; in-place branch is lower-risk (no un-final). Prefer in-place unless it forces
  the backend pointer into an awkward place.
- **O4 — hard-threshold semantics.** Block vs. aggressive-spill vs. spill-then-error when even all-closed-spilled is over budget.

## Test strategy
- Unit: seam polymorphism (base vs spillable dispatch); store create/trigger/getBySliceEnd unspill-on-read; budget math
  (`residentBytes` exactness vs known fills); eviction order (oldest-closed-first); double-trigger idempotency.
- Integration (the spike, gate of value): one windowed-aggregation query, small budget, enough keys to exceed it →
  assert correct aggregate results AND that spilling occurred (backend non-empty / resident bytes capped).
- Regression: full suite must stay green (baseline 797/797).

## Out of scope (4b)
- Variable-sized keys/values in spill (SliceSerializer fixed-size only — keep the guard).
- Join operator spilling (aggregation only).
- Global system-pressure trigger (Option 2 / `getUsedUnpooledBytes` accessor) — noted as a later realism stretch.

## Build/test gates (Docker — NOT native)
- Image: `nebulastream/nes-development:local-rocksdb`. Repo mounted at `/workspace`.
- Configure+build with ninja; `ctest` for the new + regression targets. Reviewer = `cpp-reviewer`. No frontend phase.

## Recommended scope split for the orchestra run
1. **Increment A (spike, single-threaded, de-risk):** D1 seam + D2/D3 store skeleton + D4 budget math + manual/forced spill
   + lowering wire-in, proven by ONE end-to-end query that spills and returns correct results, single worker thread.
   This kills the lifetime model uncertainty (D6) on the cheapest path before the concurrency work.
2. **Increment B (harden):** oldest-closed-first eviction under the real budget, multi-thread D6 invariants + contention
   tests, hard-threshold semantics (O4).
Rationale: D6 (concurrency/lifetime) is the only deep risk left; prove the path on a single thread first, then harden.
