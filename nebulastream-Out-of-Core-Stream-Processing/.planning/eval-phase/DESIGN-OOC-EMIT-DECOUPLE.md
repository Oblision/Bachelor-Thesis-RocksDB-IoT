# DESIGN — OOC via emit-decouple (Option 1), the resume spec

Decided 2026-05-23 with the user, after E0 found the cold-only spiller never engages in a real query
(cold ≡ emitted, same instant) and after the window-lifecycle + SliceCache investigations. This is the
authoritative design to IMPLEMENT next (user will /compact, then implement). Branch
`feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Read alongside: `E0-SMOKE-RESULTS.md`,
`EXPLORE-WINDOW-LIFECYCLE.md`, `EXPLORE-SLICECACHE.md`, `EVAL-PLAN.md`.

## The decision
**Option 1 — single tumbling-aggregation operator with EMIT DECOUPLED FROM QUIESCENCE.** The operator
retains a backlog of COMPLETED (quiescent, done-filling) windows and spills that backlog to RocksDB;
results are emitted on a lag. Option 2 (chained minute→hour→day operators) was REJECTED: it keeps only
rolled-up results and loses the per-granularity history needed for complex cross-window-size historical
analysis, and it either avoids OOC entirely (small state) or relocates pressure to a high-cardinality
finest level (the out-of-scope writable case).

## Problem framing (the thesis narrative)
Two sources of "state > memory": **breadth** (many completed windows retained at once) and **depth**
(one window with more keys than memory). 
- **Breadth = IN SCOPE and is the target.** The user's workload: long-horizon analytics keeping the
  per-granularity history of many windows. The retained band of completed windows = the "history." OOC
  lets that band exceed RAM: without spill it OOMs; with spill it lives on RocksDB and is emitted later.
  This IS §3.3 (process tumbling state > memory without OOM).
- **Depth = OUT OF SCOPE** (declared). A single window bigger than memory is HOT/writable while filling;
  spilling it safely needs a residency check on the JIT-generated SliceCache hit-path = a Nautilus/codegen
  edit, which the proposal (§2.3.2) forbids. Document as a limitation / future work.

## Why this is in scope (SliceCache verdict, EXPLORE-SLICECACHE.md)
- SliceCacheEntry = `{sliceStart, sliceEnd, raw HashMap*}`, keyed by time range, NO generation field; the
  HIT path is JIT-generated (a hit never calls host; only a miss calls `getSlicesOrCreate`).
- Spilling QUIESCENT (cold, finished, not-emitted) windows is safe with NO JIT change: a stale cache hit
  would require a late tuple into a finished window, which NES already refuses/terminates
  (`DefaultTimeBasedSliceStore.cpp:94-96`). So quiescence + NES's existing no-late-write contract suffices.
- Optional cheap backstop: host-side invalidate (zero the entry's start/end → forces a miss) — but only
  race-free on the OWNING worker thread (cache is lock-free per-worker), so treat as optional, not the
  background spiller's job.

## Mechanism — the minimal-blast-radius implementation (LAGGED EMIT WATERMARK)
Today `WindowBasedOperatorHandler::checkAndTriggerWindows` → `getTriggerableWindowSlices(globalWatermark)`
both EMITS (windowEnd < watermark) and (in the spillable subclass) advances `lastSeenWatermark` in the
SAME call ⇒ cold ≡ emitted ⇒ empty spillable set. Decouple by introducing an **emit lag L**:
- **Emit path:** the handler calls the base trigger with a LAGGED watermark = `globalWatermark − L` (so a
  window emits only once it is L behind the frontier). The base `DefaultTimeBasedSliceStore` is UNCHANGED —
  it just receives a smaller watermark and therefore RETAINS slices with windowEnd in `[globalWatermark−L,
  globalWatermark)`.
- **Quiescence/spill signal:** tell the spillable store the TRUE `globalWatermark` (a new setter, e.g.
  `setQuiescenceWatermark`) so its cold-pick = slices with `windowEnd < globalWatermark` AND not emitted —
  exactly the retained band `[globalWatermark−L, globalWatermark)`. Now NON-EMPTY ⇒ the existing spiller
  (soft/hard thresholds, background jthread, all of Increment B) works AS-IS on it.
- Net: base store mostly unchanged; the change is in the HANDLER (compute L, pass lagged wm to emit + true
  wm to the spillable store) + a small setter on the spillable store. Host-side, no JIT.

This keeps Increment B's spiller, async-probe-drain invariant (emit still unspills before handing raw
HashMap* to the probe), eviction-mutex discipline, and lifecycle intact.

## Knobs
- **L = emit/retention lag** (the history horizon): how far behind the frontier results are held = how big
  the retained backlog can grow. Units TBD (event-time duration vs window-count vs memory-driven). This is
  also a clean RQ2 experimental axis (latency vs backlog size).
- Existing spill soft/hard thresholds: how much of the retained band is on disk vs RAM.
- The §3.3 demo: large L so the retained history exceeds RAM → spill OFF OOMs, spill ON survives + emits
  correctly.

## Open implementation questions (resolve during implementation)
1. **Where/how to compute L** and pass the lagged watermark — in `WindowBasedOperatorHandler`
   (`checkAndTriggerWindows`) vs the lowering. Add `emit_lag` to `SpillConfig`/`QueryExecutionConfiguration`.
2. **Terminate/flush:** `getAllNonTriggeredSlices` (end-of-stream) MUST flush the ENTIRE retained backlog
   (unspill + emit all), since deferred windows would otherwise never emit. Verify the terminate path drains
   the full band, not just `> globalWatermark−L`.
3. **GC / probe-watermark consistency:** ensure the probe-side GC watermark stays consistent with the
   deferred emit (don't GC a window before it's emitted).
4. **Result correctness:** deferring emit must NOT change values, only timing. Verify aggregates identical
   to the no-lag baseline (a differential test: same query, L=0 vs L>0 ⇒ same result rows, later).
5. **Late/straddling records (the SliceCache residual risk):** drive late/in-range records to confirm NES's
   existing refusal/termination still holds with the lag (a record landing in `[wm−L, wm)` — is it late or
   still acceptable? the lag widens the "finished but retained" band, so confirm the build path's INVARIANT
   isn't tripped by a record into a retained-but-spilled slice; if it can be, that slice must unspill-on-write
   in `getSlicesOrCreate` — host-side, still in scope).

## Validation (redo E0, then resume EVAL-PLAN)
- **Re-run E0 with the decoupled trigger:** demonstrate a real query SPILLS (RocksDB bytes > 0), survives a
  512 MB ceiling with large L where spill-OFF OOMs, and emits correct results. This is the gate that the
  design actually works end-to-end (the current build can't spill at all).
- Confirm late/straddling handling (open question 5) empirically.
- Then resume EVAL-PLAN Phase E1 (instrumentation: spill/restore timers + p95/p99 + the RocksDB
  wbuf/cache config surface) → E2 harness → E3 runs → E4 analysis, with the matrix from EVAL-PLAN
  (state-size × {OOC vs in-mem baseline} + threshold sensitivity + storage-efficiency), now driven by L to
  create the over-memory backlog.

## Resume entry point (after /compact)
Implement the emit-decouple per "Mechanism" above (handler lagged-emit-watermark + spillable
`setQuiescenceWatermark` + `emit_lag` config), via `/dev-eng-orchestra-cpp` (TDD + cpp-review, Docker
gates, regression ≥820). Then redo E0 to prove spilling works, then resume EVAL-PLAN E1+.
