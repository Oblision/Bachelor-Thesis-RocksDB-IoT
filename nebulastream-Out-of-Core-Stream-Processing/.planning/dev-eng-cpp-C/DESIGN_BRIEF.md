# DESIGN BRIEF — Increment C: Emit-Decouple (Lagged Emit Watermark)

**Branch:** `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f` (Increment B head).
**Working dir for this orchestra run:** `.planning/dev-eng-cpp-C/` (do NOT clobber A's `.planning/dev-eng-cpp/` or B's `.planning/dev-eng-cpp-B/`).
**Authoritative spec:** `.planning/eval-phase/DESIGN-OOC-EMIT-DECOUPLE.md` (read in full).
**Read alongside:** `.planning/eval-phase/E0-SMOKE-RESULTS.md`, `EXPLORE-WINDOW-LIFECYCLE.md`, `EXPLORE-SLICECACHE.md`, `EVAL-PLAN.md`.

---

## Why this increment exists (the problem)

E0 (code-verified) found that a real tumbling-aggregation query **spills nothing** (0 bytes to RocksDB
across 4 workload shapes); spill-ON OOMs at 512 MB exactly like spill-OFF. Root cause:
`SpillableTimeBasedSliceStore::getTriggerableWindowSlices` (cpp:264-280) marks the triggerable slices
**emitted** (`unspillAndMarkEmitted`, ~line 268) and then advances `lastSeenWatermark` (~lines 271-275)
in the **same call**. The spiller only evicts COLD slices (`sliceEnd < lastSeenWatermark` AND not-emitted).
So coldness and emitted-ness happen at the same instant ⇒ the `{cold AND not-emitted}` set is provably
empty in steady state ⇒ nothing is ever spillable. Increment B's machinery is correct but has nothing to act on.

**This increment makes spilling actually engage**, so the thesis §3.3 demonstration (process tumbling state
> memory without OOM) and the whole eval (E1+) become possible.

---

## Goal (one sentence)

Decouple **emit** from **quiescence** so a backlog of COMPLETED (done-filling) tumbling windows is **retained**
and becomes spillable: results emit on an **event-time lag `L` (`emit_lag`)** behind the watermark frontier,
while the spiller targets the now-non-empty retained band `[globalWatermark − L, globalWatermark)`.

---

## LOCKED decisions (hard constraints — reviewers MUST NOT flag these as bugs)

1. **Option 1 (emit-decouple).** Single tumbling-aggregation operator. Option 2 (chained
   minute→hour→day operators) is REJECTED (loses per-granularity history; relocates/avoids OOC).
2. **No Nautilus/JIT/codegen edit.** Proposal §2.3.2 forbids it. The SliceCache hit-path is JIT-generated and
   stays untouched. Spilling QUIESCENT windows is safe without a residency check because NES already
   refuses/terminates late tuples into finished windows (`DefaultTimeBasedSliceStore.cpp:94-96`) — see
   `EXPLORE-SLICECACHE.md`.
3. **Tumbling windows ONLY.** No sliding windows, no variable-sized/general-purpose serialization (proposal scope).
4. **Mechanism = LAGGED EMIT WATERMARK.** Emit uses a watermark of `globalWatermark − emit_lag`; the spillable
   store learns the TRUE `globalWatermark` for its quiescence/cold-pick. Net effect: the base store retains
   slices with `windowEnd ∈ [globalWatermark − L, globalWatermark)`; the existing cold-pick now finds them.
5. **`emit_lag` is an event-time DURATION** (same time unit as the window definition / watermark; e.g. ms), NOT
   a window-count and NOT memory-driven. Surface it on `SpillConfig` and `QueryExecutionConfiguration`
   (mirroring how `compression`/thresholds are already wired). CLI key under `--worker.default_query_execution.spill.emit_lag=...`.
6. **`emit_lag` default = 0 ⇒ today's exact behavior.** With L=0 the emit watermark equals the global watermark
   and nothing is retained — every existing query and test must behave identically. This is the regression safety net.
7. **Preserve EVERY Increment B invariant** (async-probe-drain is ABSOLUTE):
   - NEVER spill an `EMITTED_TO_PROBE` slice — `triggerSlices` snapshots the raw `HashMap*` into an
     `EmittedAggregationWindow` dispatched ASYNC to the probe; spilling it = use-after-free.
   - Emit path MUST unspill before handing the raw `HashMap*` to the probe (`unspillAndMarkEmitted`).
   - One outer `evictionMutex` serializes in-memory decisions only; ALL RocksDB I/O happens OUTSIDE it.
   - Per-slice `spillInProgress` reservation; a leaked reservation = deadlock; release via `ReservationGuard` RAII
     on ALL paths incl. exceptions.
   - Background `std::jthread` spiller lifecycle (lazy-start in `setBufferProvider`, stop+join in dtor/`deleteState`)
     is unchanged; the jthread member stays LAST.
8. **Terminate/flush is mandatory.** End-of-stream (`getAllNonTriggeredSlices`) MUST flush the ENTIRE retained
   backlog — unspill + emit ALL deferred windows regardless of `emit_lag`. A deferred window that never emits is a
   correctness bug, not an optimization.
9. **Build/test ONLY in Docker** image `nebulastream/nes-development:local-rocksdb` (base `:local` lacks RocksDB).
   Build dir `cmake-build-debug`, `ninja`, regression gate `ctest -j8 -LE 'Systest|DockerCompose' --timeout 120`.
   Baseline to beat: **820/820** (Increment B). NEVER `ninja | tail` without `set -o pipefail`.
10. **Manual push policy.** Commit per PR boundary; do NOT push; NO co-author trailer (attribution disabled globally).

---

## OPEN items (architects resolve WITH code visibility — justify the choice)

1. **Seam for applying L.** Two viable sites:
   - **(leading candidate) Inside `SpillableTimeBasedSliceStore::getTriggerableWindowSlices`** — it already
     overrides this and already receives the watermark from the handler. Apply lag locally: drive the *emit*
     decision with `watermark − L`, drive *quiescence/cold-tracking* (`lastSeenWatermark` / `setQuiescenceWatermark`)
     with the true `watermark`. Smallest blast radius; localized to the file Increment B already owns; no
     `WindowBasedOperatorHandler` change beyond passing `emit_lag` at store construction.
   - **(alt) In `WindowBasedOperatorHandler::checkAndTriggerWindows`** — handler computes the lagged watermark.
     Higher blast radius (handler is shared with joins); only choose if the store-override seam can't see what it
     needs (e.g. the true frontier).
   Pick one, justify against the actual code, and confirm it does NOT alter non-spill / join windowing.
2. **GC / probe-watermark consistency.** Emit is now deferred by L. Ensure the probe-side GC watermark does NOT
   GC (delete) a window before it has emitted. Trace where GC's watermark comes from relative to the lagged emit.
3. **Late/straddling records (SliceCache residual risk).** The retained band `[wm−L, wm)` widens the window of
   "finished but still around" slices. Confirm NES's existing late-tuple refusal still holds; if a record CAN land
   in a retained-but-already-spilled slice, the build path's residency INVARIANT would trip — in that case add a
   host-side **unspill-on-write in `getSlicesOrCreate`** (still in scope, no JIT). Determine empirically whether
   this path is reachable; implement the guard only if it is.
4. **`setQuiescenceWatermark` seam/naming.** Confirm whether a new setter is the cleanest way to feed the true
   frontier to the store, or whether the existing trigger argument already carries it (then the override just needs
   to keep both the emit-lagged and true values straight). Avoid redundant state.
5. **Differential correctness.** Add a test proving deferring emit changes only *timing*, not *values*: same query,
   `emit_lag=0` vs `emit_lag>0` ⇒ identical result rows (order/timing aside). This is the headline correctness gate.

---

## Scope boundaries

**IN scope (this increment):**
- `emit_lag` config plumbing (`SpillConfig` → `QueryExecutionConfiguration` → lowering → store construction).
- The lag mechanism at the chosen seam (OPEN #1).
- Terminate-path full-backlog flush (LOCKED #8).
- GC/probe-watermark consistency fix if needed (OPEN #2).
- Host-side unspill-on-write guard IF and ONLY IF late records can reach a spilled slice (OPEN #3).
- Unit + (where feasible without the heavy systest label) integration tests, incl. the differential test (OPEN #5).

**OUT of scope (declare as limitation / future work):**
- **Depth** (a single window with more keys than memory). It is HOT/writable while filling; spilling it safely
  needs a JIT residency check (forbidden). Document only.
- Sliding windows, varsized serialization, any Nautilus edit.
- The eval runs themselves (E0-redo / E1+) — those follow this increment, NOT part of it.

---

## Definition of done for this increment

- Build green in Docker; regression gate ≥ 820 with the new tests added (red→green).
- `emit_lag=0` differential-identical to baseline; `emit_lag>0` retains + spills (provable in a unit/integration
  test that the cold/spillable set becomes non-empty and the spiller acts on it).
- Terminate flushes the whole backlog (no lost windows).
- Super-reviewer SHIP (async-probe-drain + eviction discipline intact).
- Committed per PR boundary, NOT pushed.

After this lands: **redo E0** with the decoupled trigger (separate step) to prove a real query spills (RocksDB
bytes > 0, survives 512 MB where spill-OFF OOMs, correct results), then resume EVAL-PLAN E1+.
