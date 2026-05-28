# PLAN-A — Increment C (Architect A) — persisted by orchestrator

> Architect A had a read-only toolset; this is its plan persisted verbatim-in-substance.

Branch `feat/ooc-phase1-spill-skeleton` @ `5e4fd3cd9f`. Builds on SYNTHESIS.md (does not re-derive).

## Decision table
| Item | Decision | Basis |
|---|---|---|
| Seam | (a) inside `getTriggerableWindowSlices` line 267 | override already holds true frontier + quiescence as adjacent statements; zero join blast radius |
| Clamp | explicit saturating `rawWm >= emitLag ? rawWm - emitLag : 0` wrapped in `Timestamp(...)` | `Timestamp::operator-` wraps (Timestamp.hpp:37) |
| Quiescence wm | TRUE `globalWatermark`, CAS 271-275 UNCHANGED | E0 fix lever |
| Terminate path | `getAllNonTriggeredSlices` UNCHANGED | LOCKED #8 |
| setQuiescenceWatermark | NONE | OPEN #4 resolved |
| GC | NO code change | OPEN #2 resolved |
| **OPEN #3** | **PROACTIVE** unspill-on-write guard in `getSlicesOrCreate`, factored as `unspillIfNeeded` helper modeled on `getSliceBySliceEnd` (unspill, do NOT mark emitted) | catastrophic `std::terminate` asymmetry + cheap host-side reuse; in-scope per brief |
| New virtual/interface | NONE (ADD-only via ctor + const member) | -Werror -Woverloaded-virtual |
| New test setter | NONE (`emitLag` via builder param + const member) | avoid public-surface widening |
| New CMake lines | NONE (in-file additions) | both CMakeLists confirmed |
| PRs | **PR1 plumbing(config+ctor+lowering+config tests) → PR2 seam(line 267 + T1-T7) → PR3 guard(+T8)** | risk isolation |

## Distinctive contributions A brought
- **Insertion point precision:** the guard goes after the `INVARIANT(result.size()==1)` (cpp:165) and BEFORE `recordCreatedSlice` (cpp:169), so the budget check at 171 sees the now-resident footprint.
- **`unspillIfNeeded` shared helper** proposal (declared next to `unspillAndMarkEmitted` ~hpp:166); recommended optionally refactoring `getSliceBySliceEnd` to call it too for DRY (3 near-identical copies → 1). [RECONCILED: keep the new helper, but do NOT refactor the audited paths in this increment — see PLAN.md.]
- **Negative-control test framing:** run T8 against PR2-head first to confirm it `std::terminate`s, then add the guard to make it pass (documents reachability empirically).
- **Crucial semantic point:** the guard must NOT insert into `emittedKeys` (the slice is still WINDOW_FILLING and must remain spillable/triggerable) — mirror `getSliceBySliceEnd`, not `unspillAndMarkEmitted`.

Full 7-test plan T1-T7 + T8(late-record) in SpillableTimeBasedSliceStoreTest.cpp; emit_lag cases in SpillConfigTests.cpp; optional concurrency differential in the ConcurrencyTest. No new CMake line.
