# PLAN-B — Increment C (Architect B) — persisted by orchestrator

> Architect B had a read-only (Plan) toolset; this is its plan persisted verbatim-in-substance.

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Independent plan; converged with A on every core decision.

## Three-line verdict
1. **Seam (a)**, one clamped subtraction at cpp:267; everything else untouched.
2. **OPEN #3 → PROACTIVE.** Decisive new evidence: **`getSlicesOrCreate` already runs synchronous RocksDB spill I/O** via `evictUntilUnderHard()` at `SpillableTimeBasedSliceStore.cpp:174`. So a synchronous *unspill* in the same method is the structural inverse of work the worker already does — NOT a new latency/lock-order class. This dismantles the main objection to the guard.
3. The clamp must be **explicit** — `Timestamp::operator-` (Timestamp.hpp:37) does a raw uint64 subtraction, no saturation → `gw - L` wraps for `gw < L` (a spurious-emit bug).

## Distinctive contributions B brought (adopted into PLAN.md)
- **The cpp:174 evidence** (worker already does sync spill I/O) — the strongest argument that PROACTIVE adds no new risk class.
- **Explicit `if (emitLag > 0)` gate** wrapping the OPEN#3 guard → provably zero overhead and byte-identity on the L=0 default hot path (LOCKED #6), stronger than relying on a `dynamic_pointer_cast` short-circuit. **[ADOPTED]**
- **NES_TRACE (not DEBUG)** for the per-trigger lag line to avoid hot-path log spam. **[ADOPTED]**
- **Do NOT extract a shared helper / do NOT refactor the audited `getSliceBySliceEnd`** in this increment — widening the review surface on an Increment-B concurrency hot path is a regression risk; flag as future cleanup. **[PARTIALLY ADOPTED: new `unspillIfNeeded` helper for the new guard only; audited paths left untouched.]**
- **Test-local builder overload** taking `emitLag` (keeps the existing builders untouched since the ctor param defaults to 0). **[ADOPTED]**
- Unit consistency note: `windowType->getSize().getTime()` is already passed raw ms at the lowering seam, so `emitLag` (raw ms) needs no conversion.

## PR structure B proposed
PR1 config knob only → PR2 store plumbing+seam+T1-T7 → PR3 guard+T-LATE → PR4 optional concurrency.
[RECONCILED to A's 3-PR structure — PLAN.md folds config+ctor+lowering into PR1 as one provably-inert plumbing PR, since the ctor param/member is itself inert at L=0 and pairing it with the config knob keeps PR2 a pure one-line behavior change.]

Same test set as A (names harmonized in PLAN.md). No new CMake line.
